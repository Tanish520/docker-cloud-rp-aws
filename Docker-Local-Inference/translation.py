import os
import sys
import json
import logging
import pyodbc
import time
import asyncio
import psutil
import threading
import statistics
import xml.etree.ElementTree as ET
import uuid
import shutil
import gc  # Used to clear Python-side objects

os.environ["APPLICATIONINSIGHTS_ROLE_NAME"] = "Local-Inference-Engine"

# IMPORT CTRANSLATE2 AND AUTOTOKENIZER INSTEAD OF MBART/PYTORCH
import ctranslate2
from transformers import AutoTokenizer
from azure.storage.blob import BlobServiceClient
from opencensus.ext.azure.log_exporter import AzureLogHandler
from opencensus.ext.azure import metrics_exporter
from opencensus.stats import aggregation as aggregation_module
from opencensus.stats import measure as measure_module
from opencensus.stats import stats as stats_module
from opencensus.stats import view as view_module
from opencensus.tags import tag_map as tag_map_module

# --- 1. CONFIGURATION ---
BATCH_SIZE = 52
TOKEN_LIMIT = 1024

LANG_TOKEN_MULTIPLIERS = {
    "zh-CN": 1.2, "ja-JP": 1.1, "ru-RU": 0.5, "hi-IN": 0.3, 
    "te-IN": 0.2, "fr-FR": 0.9, "es-ES": 0.9, "de-DE": 0.8, "ar-SA": 0.4,
    "ta-IN": 0.2, "mr-IN": 0.3, "bn-IN": 0.3, "kn-IN": 0.2
}

# --- PRE-LOAD LOCAL CTRANSLATE2 ENGINE ---
print("🧠 Loading optimized CTranslate2 NLLB Model into RAM...")

# This assumes you ran 'ct2-transformers-converter' and created this folder
CT2_MODEL_PATH = "nllb_ct2" 
HF_MODEL_NAME = "facebook/nllb-200-distilled-600M"

try:
    # 1. Load the lightweight tokenizer rules from Hugging Face (~5MB)
    tokenizer = AutoTokenizer.from_pretrained(HF_MODEL_NAME)
    # Load C++ engine natively. We use "auto" device to detect CPU/GPU and int8 for max speed/lowest memory.
    translator = ctranslate2.Translator(CT2_MODEL_PATH, device="auto", compute_type="int8")
    print("✅ Model loaded successfully into CTranslate2 C++ Backend.")
except Exception as e:
    print(f"❌ Failed to load CTranslate2 model. Did you run ct2-transformers-converter? Error: {e}")
    sys.exit(1)


# --- 2. GLOBAL METRICS DEFINITION ---
m_latency = measure_module.MeasureFloat("hf_latency", "Latency of HF API", "ms")
m_throughput = measure_module.MeasureFloat("throughput", "Chars per second", "chars/sec")
m_overhead = measure_module.MeasureFloat("telemetry_overhead", "Time spent logging", "ms")
m_cpu = measure_module.MeasureFloat("cpu_usage", "CPU Usage", "percent")
m_memory = measure_module.MeasureFloat("memory_usage", "Memory Usage", "MB")

def register_metrics_views(exporter):
    try:
        view_manager = stats_module.stats.view_manager
        view_latency = view_module.View("HF Latency View", "Avg Latency", [], m_latency, aggregation_module.LastValueAggregation())
        view_overhead = view_module.View("Overhead View", "Telemetry Cost", [], m_overhead, aggregation_module.SumAggregation())
        view_cpu = view_module.View("CPU View", "CPU Load", [], m_cpu, aggregation_module.LastValueAggregation())
        
        view_manager.register_view(view_latency)
        view_manager.register_view(view_overhead)
        view_manager.register_view(view_cpu)
        
        if exporter: view_manager.register_exporter(exporter)
    except Exception as e: print(f"⚠️ Metrics registration warning: {e}")

class PerformanceProfiler:
    def __init__(self):
        self.process = psutil.Process(os.getpid())
        self.timings = {}
        self.reset_file_metrics() 
        
    def reset_file_metrics(self):
        self.start_ts = time.perf_counter()
        self.counters = {
            "telemetry_overhead_ms": 0.0, "api_calls": 0, "cache_hits": 0, "cache_misses": 0,
            "sql_reads": 0, "sql_writes": 0, "tokens_processed": 0.0,
            "total_input_tokens": 0.0, "total_output_tokens": 0.0,
            "api_retries": 0, "api_failures": 0,  "api_throttled": 0, 
            "total_batches": 0, "total_chars_processed": 0, "total_segments": 0,
            "total_tm_hits": 0, "total_nmt_calls": 0, "nmt_latencies": [], "sleep_time_ms": 0.0 
        }
    
    def start_timer(self, name): self.timings[name] = time.perf_counter()
    def stop_timer(self, name):
        if name in self.timings: return (time.perf_counter() - self.timings[name]) * 1000
        return 0

    def get_system_impact(self):
        return {"cpu_percent": self.process.cpu_percent(interval=None), "memory_mb": self.process.memory_info().rss / 1024 / 1024}

    def measure_telemetry(self, start_time):
        overhead = (time.perf_counter() - start_time) * 1000 
        self.counters["telemetry_overhead_ms"] += overhead
        return overhead

    def measure_telemetry_overhead(self, func, *args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        self.counters["telemetry_overhead_ms"] += (time.perf_counter() - start) * 1000
        return result

    def calculate_throughput(self, char_count, duration_ms):
        if duration_ms <= 0: return 0
        return char_count / duration_ms

    def add_segment_stats(self, tm_hit=False, nmt_latency=None, is_nmt_only=False):
        if is_nmt_only:
            if nmt_latency is not None:
                self.counters["total_nmt_calls"] += 1
                self.counters["nmt_latencies"].append(nmt_latency)
            return
        self.counters["total_segments"] += 1
        if tm_hit:
            self.counters["total_tm_hits"] += 1
            self.counters["cache_hits"] += 1 
        else: self.counters["cache_misses"] += 1

    def get_summary_stats(self):
        elapsed = time.perf_counter() - self.start_ts
        avg_nmt = statistics.mean(self.counters["nmt_latencies"]) if self.counters["nmt_latencies"] else 0.0
        throughput = (self.counters["total_segments"] / elapsed) * 3600 if elapsed > 0 else 0.0
        return {
            "total_segments": self.counters["total_segments"], "total_tm_hits": self.counters["total_tm_hits"],
            "total_nmt_calls": self.counters["total_nmt_calls"], "total_time_sec": round(elapsed, 2),
            "avg_nmt_latency_sec": round(avg_nmt / 1000, 4),  "overall_throughput_seg_per_hr": int(throughput)
        }

profiler = PerformanceProfiler()

def setup_logging(config):
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    if logger.hasHandlers(): logger.handlers.clear()
    c_handler = logging.StreamHandler(sys.stdout)
    c_handler.setFormatter(logging.Formatter('%(asctime)s [INFO] %(message)s'))
    logger.addHandler(c_handler)
    az_handler = None
    try:
        if config.get('app_insights_connection_string'):
            def init_azure():
                handler = AzureLogHandler(connection_string=config['app_insights_connection_string'])
                if not hasattr(handler, 'lock') or handler.lock is None: handler.lock = threading.RLock()
                return handler
            az_handler = profiler.measure_telemetry_overhead(init_azure)
            logger.addHandler(az_handler)
    except Exception as e: print(f"⚠️ Could not set up Azure logging: {e}")
    return logger, az_handler

def get_sql_connection(conn_str):
    try:
        profiler.start_timer("sql_connect")
        conn = pyodbc.connect(conn_str)
        profiler.stop_timer("sql_connect")
        return conn, 0
    except Exception as e: 
        print(f"❌ SQL Connection Error: {e}")
        return None, 0

def bulk_query_translations(cursor, source_texts, target_lang):
    if not source_texts: return {}, 0
    
    profiler.counters["sql_reads"] += 1
    profiler.start_timer("sql_read_latency")
    
    translations_map = {}
    try:
        unique_texts = list(set(source_texts))
        chunk_size = 1000
        for i in range(0, len(unique_texts), chunk_size):
            chunk = unique_texts[i:i + chunk_size]
            placeholders = ','.join(['?'] * len(chunk))
            
            query = f"SELECT SourceText, TranslatedText FROM Translations WHERE TargetLang=? AND SourceText IN ({placeholders})"
            params = [target_lang] + chunk
            
            cursor.execute(query, params)
            for row in cursor.fetchall():
                translations_map[row[0]] = row[1]
                
        return translations_map, profiler.stop_timer("sql_read_latency")
    except Exception as e: 
        print(f"❌ SQL Bulk Query Error: {e}")
        return {}, profiler.stop_timer("sql_read_latency")

def bulk_save_translations(cursor, translation_tuples):
    if not translation_tuples: return 0
    
    profiler.counters["sql_writes"] += 1
    profiler.start_timer("sql_write_latency")
    try:
        cursor.fast_executemany = True 
        query = """
        MERGE INTO Translations AS target
        USING (SELECT ? AS SourceText, ? AS TargetLang) AS source
        ON (target.SourceText = source.SourceText AND target.TargetLang = source.TargetLang)
        WHEN MATCHED THEN UPDATE SET TranslatedText = ?
        WHEN NOT MATCHED THEN INSERT (SourceText, TargetLang, TranslatedText, ModelName, LastUpdated)
        VALUES (?, ?, ?, ?, GETDATE());
        """
        cursor.executemany(query, translation_tuples)
        return profiler.stop_timer("sql_write_latency")
    except Exception as e: 
        print(f"❌ SQL Bulk Write Error: {e}")
        return profiler.stop_timer("sql_write_latency")

# --- COMPLETELY REWRITTEN FOR CTRANSLATE2 PERFORMANCE ---
def translate_local(texts, target_lang):
    if not texts: return [], 0, 0
    profiler.counters["api_calls"] += 1
    
    # NLLB Language mapping (Uses distinct codes from mBART)
    lang_map = {
        "ar-SA": "arb_Arab", "de-DE": "deu_Latn", "en-US": "eng_Latn", "es-ES": "spa_Latn",
        "fr-FR": "fra_Latn", "hi-IN": "hin_Deva", "ja-JP": "jpn_Jpan", "ru-RU": "rus_Cyrl",
        "zh-CN": "zho_Hans", "it-IT": "ita_Latn", "nl-NL": "nld_Latn", "pt-BR": "por_Latn",
        "ta-IN": "tam_Taml", "te-IN": "tel_Telu", "mr-IN": "mar_Deva", "bn-IN": "ben_Beng",
        "kn-IN": "kan_Knda"
    }
    tgt_token = lang_map.get(target_lang, "eng_Latn")
    
    profiler.start_timer("hf_network_latency") 
    try:
        tokenizer.src_lang = "eng_Latn"
        
        # 1. Tokenize inputs with TRUNCATION SAFETY to prevent C++ engine crashes
        source_tokens = []
        for text in texts:
            encoded_ids = tokenizer.encode(text, truncation=True, max_length=TOKEN_LIMIT)
            source_tokens.append(tokenizer.convert_ids_to_tokens(encoded_ids))
        
        # 2. Pure C++ Engine Inference
        results = translator.translate_batch(
            source_tokens,
            target_prefix=[[tgt_token]] * len(texts),
            beam_size=5, # Optimally fast while maintaining quality
            batch_type="tokens", # Dynamic padding removal
            max_batch_size=1024
        )
        
        # 3. Detokenize back to string (Fast CPU op)
        translations = []
        for result in results:
            # Drop the language token prefix from the beginning
            target_tokens = result.hypotheses[0][1:]
            
            # CRITICAL FIX: Skip special tokens so </s> doesn't leak into the translation
            token_ids = tokenizer.convert_tokens_to_ids(target_tokens)
            clean_text = tokenizer.decode(token_ids, skip_special_tokens=True)
            translations.append(clean_text)

        latency = profiler.stop_timer("hf_network_latency")

        return translations, latency, 0.0
    except Exception as e:
        print(f"❌ Local Inference Error: {e}")
        latency = profiler.stop_timer("hf_network_latency")
        profiler.counters["api_failures"] += 1
        return [None] * len(texts), latency, 0.0

async def translate_local_async(texts, target_lang):
    return await asyncio.to_thread(translate_local, texts, target_lang)

async def process_xlf_file(local_path, target_lang, conn, exporter):
    profiler.start_timer("xml_parse")
    try:
        tree = ET.parse(local_path)
        root = tree.getroot()
        file_units = root.findall('.//trans-unit')
        xml_parse_ms = profiler.stop_timer("xml_parse")
    except ET.ParseError as e: return {"total_api_time_ms": 0, "total_sql_time_ms": 0, "total_commit_time_ms": 0}, 0, 0, 0, 0
    
    cursor = conn.cursor()
    file_stats = {"total_api_time_ms": 0, "total_sql_time_ms": 0, "total_commit_time_ms": 0}
    total_source_chars = total_translated_chars = 0  
    missing_units = [] 
    
    all_source_texts = []
    for unit in file_units:
        source_node = unit.find('source')
        if source_node is not None and source_node.text:
            all_source_texts.append(source_node.text)

    translations_map, read_time = bulk_query_translations(cursor, all_source_texts, target_lang)
    file_stats["total_sql_time_ms"] += read_time
    
    for i, unit in enumerate(file_units):
        source_node = unit.find('source')
        source_text = source_node.text if source_node is not None else None
        if not source_text: continue

        total_source_chars += len(source_text)
        cached_trans = translations_map.get(source_text)

        if cached_trans:
            profiler.add_segment_stats(tm_hit=True) 
            total_translated_chars += len(cached_trans) 
            
            target_node = unit.find('target')
            if target_node is not None:
                target_node.text = cached_trans
            
            multiplier = LANG_TOKEN_MULTIPLIERS.get(target_lang, 0.5)
            output_tokens = len(cached_trans) / (4.0 * multiplier)
            input_tokens = len(source_text) / 4.0
            profiler.counters["tokens_processed"] += (input_tokens + output_tokens)
            profiler.counters["total_input_tokens"] += input_tokens
            profiler.counters["total_output_tokens"] += output_tokens
        else:
            profiler.add_segment_stats(tm_hit=False) 
            missing_units.append((i, source_text))

    current_batch_texts = []
    current_batch_indices = []
    current_batch_tokens = 0

    async def flush_batch():
        nonlocal total_translated_chars, current_batch_texts, current_batch_indices, current_batch_tokens
        if not current_batch_texts: return
        
        profiler.counters["total_batches"] += 1 
        translations, batch_latency, _ = await translate_local_async(current_batch_texts, target_lang)
        file_stats["total_api_time_ms"] += batch_latency
        
        db_write_batch = [] 
        
        if translations and any(t is not None for t in translations):
            telemetry_start = time.perf_counter()
            try:
                tmap = tag_map_module.TagMap()
                mm = stats_module.stats.stats_recorder.new_measurement_map()
                batch_chars = sum(len(t) for t in current_batch_texts)
                throughput = profiler.calculate_throughput(batch_chars, batch_latency)
                sys_stats = profiler.get_system_impact()
                mm.measure_float_put(m_latency, batch_latency)
                mm.measure_float_put(m_cpu, sys_stats['cpu_percent'])
                mm.measure_float_put(m_memory, sys_stats['memory_mb'])
                mm.record(tmap)
                profiler.measure_telemetry(telemetry_start)
            except Exception: pass

            per_item_lat = batch_latency / len(current_batch_texts)
            for b_idx, trans in enumerate(translations):
                if trans:
                    total_translated_chars += len(trans)
                    profiler.add_segment_stats(nmt_latency=per_item_lat, is_nmt_only=True) 
                    
                    idx_in_file = current_batch_indices[b_idx]
                    target_unit = file_units[idx_in_file]
                    
                    target_node = target_unit.find('target')
                    if target_node is not None:
                        target_node.text = trans
                    
                    src = current_batch_texts[b_idx]
                    db_write_batch.append((src, target_lang, trans, src, target_lang, trans, "nllb-600M-ct2"))
                    
                    multiplier = LANG_TOKEN_MULTIPLIERS.get(target_lang, 0.5)
                    output_tokens = len(trans) / (4.0 * multiplier)
                    input_tokens = len(current_batch_texts[b_idx]) / 4.0
                    profiler.counters["tokens_processed"] += (input_tokens + output_tokens)
                    profiler.counters["total_input_tokens"] += input_tokens
                    profiler.counters["total_output_tokens"] += output_tokens

            if db_write_batch:
                write_time = bulk_save_translations(cursor, db_write_batch)
                file_stats["total_sql_time_ms"] += write_time

            try:
                profiler.start_timer("sql_commit")
                conn.commit()
                file_stats["total_commit_time_ms"] += profiler.stop_timer("sql_commit")
            except Exception as e: print(f"⚠️ SQL Commit warning: {e}")
            
            print(f"📊 Batch: {len(current_batch_texts)} items ({current_batch_tokens:.1f} tokens) | Speed: {throughput:.2f} chars/ms")
        
        current_batch_texts.clear()
        current_batch_indices.clear()
        current_batch_tokens = 0

    for idx_in_list, (original_unit_idx, source_text) in enumerate(missing_units):
        segment_tokens = len(source_text) / 4.0
        
        if (current_batch_tokens + segment_tokens > TOKEN_LIMIT or len(current_batch_texts) >= BATCH_SIZE) and current_batch_texts:
            await flush_batch()
            
        current_batch_texts.append(source_text)
        current_batch_indices.append(original_unit_idx)
        current_batch_tokens += segment_tokens

    await flush_batch()

    profiler.start_timer("xml_write")
    tree.write(local_path, encoding='utf-8', xml_declaration=True)
    xml_write_ms = profiler.stop_timer("xml_write")
    
    mm = stats_module.stats.stats_recorder.new_measurement_map()
    mm.measure_float_put(m_overhead, profiler.counters["telemetry_overhead_ms"])
    profiler.measure_telemetry_overhead(mm.record, tag_map_module.TagMap())

    del translations_map
    del all_source_texts
    del file_units
    del root
    del tree
    gc.collect()

    return file_stats, xml_parse_ms, xml_write_ms, total_source_chars, total_translated_chars

async def main(config_path):
    profiler.start_timer("total_execution")
    if not os.path.exists(config_path): 
        print("❌ Config file not found!")
        return

    with open(config_path, 'r') as f: config = json.load(f)
    logger, az_handler = setup_logging(config)
    
    try:
        blob_service = BlobServiceClient.from_connection_string(config['blob_connection_string'])
        raw_container_name = config.get('raw_container', 'raw-files')
        trans_container_name = config.get('translated_container', 'trans-files')
        
        raw_container = blob_service.get_container_client(raw_container_name)
        trans_container = blob_service.get_container_client(trans_container_name)
        
        profiler.start_timer("cold_start")
        conn, _ = get_sql_connection(config['sql_conn_str'])
        cold_start_ms = profiler.stop_timer("cold_start")
        if not conn: 
            print("❌ Exiting due to SQL Connection Failure.")
            return
    except Exception as e:
        print(f"❌ Init Error: {e}")
        return

    try:
        exporter = metrics_exporter.new_metrics_exporter(connection_string=config['app_insights_connection_string'])
        register_metrics_views(exporter)
    except: exporter = None

    try:
        blobs = list(raw_container.list_blobs())
        
        if not blobs:
            logger.warning(f"⚠️ ZERO files found in container '{raw_container_name}'. Are you sure Phase 1 ran?")
        else:
            logger.info(f"🔎 Found {len(blobs)} files in '{raw_container_name}'.")

        for blob in blobs:
            if blob.name.lower().endswith('.xlf'):
                logger.info(f"⬇️ Downloading {blob.name}...")
                
                profiler.start_timer("blob_download")
                local_safe_name = os.path.basename(blob.name)
                with open(local_safe_name, "wb") as f:
                    f.write(raw_container.download_blob(blob.name).readall())
                download_ms = profiler.stop_timer("blob_download")

                parts = blob.name.split('.')
                if len(parts) >= 3:
                    target_lang = parts[-2]
                else:
                    logger.warning(f"⚠️ Could not deduce language from filename {blob.name}. Skipping.")
                    continue

                try:
                    profiler.reset_file_metrics() 
                    profiler.start_timer(f"file_{local_safe_name}_{target_lang}")
                    
                    logger.info(f"Translating {local_safe_name} to {target_lang}...")
                    
                    file_stats, xml_parse_ms, xml_write_ms, total_source_chars, total_translated_chars = await process_xlf_file(local_safe_name, target_lang, conn, exporter)
                    
                    logger.info(f"⬆️ Uploading processed {blob.name} to translated container...")
                    profiler.start_timer("blob_upload")
                    with open(local_safe_name, "rb") as data:
                        trans_container.upload_blob(blob.name, data, overwrite=True)
                    upload_ms = profiler.stop_timer("blob_upload")
                    
                    file_duration = profiler.stop_timer(f"file_{local_safe_name}_{target_lang}")
                    summary = profiler.get_summary_stats()
                    
                    total_tokens = profiler.counters["tokens_processed"]
                    hf_time = file_stats["total_api_time_ms"]
                    ms_per_token = round(hf_time / max(1, total_tokens), 2) if total_tokens else 0.0
                    avg_batch_size = round(total_tokens / max(1, profiler.counters["total_batches"]), 2) if profiler.counters["total_batches"] else 0.0
                    avg_segment_len = round(total_source_chars / max(1, summary["total_segments"]), 2)
                    
                    total_chars = total_source_chars + total_translated_chars
                    char_token_ratio = round(total_chars / max(1, total_tokens), 2) if total_tokens else 0.0
                    
                    sleep_time_ms = profiler.counters.get("sleep_time_ms", 0.0)
                    total_input_t = profiler.counters["total_input_tokens"]
                    total_output_t = profiler.counters["total_output_tokens"]
                    batches = max(1, profiler.counters["total_batches"])
                    elapsed_s = max(1, file_duration / 1000)
                    total_network_wait_ms = file_stats["total_sql_time_ms"] + file_stats["total_commit_time_ms"] + download_ms + upload_ms
                    compute_time_ms = max(0, file_duration - total_network_wait_ms - sleep_time_ms)
                    io_bound_ratio = round((total_network_wait_ms / max(1, file_duration)) * 100, 2)
                    
                    sys_stats = profiler.get_system_impact()
                    
                    metrics_payload = {
                        "run_invocation_id": str(uuid.uuid4()), 
                        "file_name": blob.name,
                        "target_lang": target_lang,
                        "throughput_seg_per_hr": summary['overall_throughput_seg_per_hr'],
                        "throughput_input_tokens_per_hr": round((total_input_t / elapsed_s) * 3600, 2),
                        "total_api_call_logically": profiler.counters["total_batches"], 
                        "actual_api_call": profiler.counters["api_calls"],    
                        "total_segments": summary["total_segments"],
                        "avg_segment_char_length": avg_segment_len,
                        "text_expansion_ratio": round(total_translated_chars / max(1, total_source_chars), 2),
                        "tokens_processed_in_whole_language": total_tokens,
                        "total_input_tokens_source": round(total_input_t, 2),
                        "total_output_tokens_target": round(total_output_t, 2),
                        "avg_input_tokens_per_batch": round(total_input_t / batches, 2),
                        "avg_output_tokens_per_batch": round(total_output_t / batches, 2),
                        "char_per_token_ratio": char_token_ratio,   
                        "total_token_input_plus_output_per_batch": avg_batch_size,
                        "time_per_token": ms_per_token,
                        "nmt_calls": summary["total_nmt_calls"],
                        "transational_memory_hits": summary["total_tm_hits"],
                        "cache_rate": (profiler.counters["cache_hits"] / max(1, profiler.counters["cache_hits"] + profiler.counters["cache_misses"])) * 100,
                        "total_time": file_duration,
                        "cold_start_init_ms": cold_start_ms,
                        "compute_time_ms": compute_time_ms,
                        "total_sleep_time_ms": sleep_time_ms, 
                        "network_wait_time_ms": total_network_wait_ms,
                        "io_bound_pipeline_pct": io_bound_ratio,
                        "hf_api_wait_ms": hf_time,
                        "azure_sql_query_ms": file_stats["total_sql_time_ms"],
                        "azure_sql_commit_ms": file_stats["total_commit_time_ms"],
                        "xml_parse_ms": xml_parse_ms,
                        "xml_disk_write_ms": xml_write_ms,
                        "blob_download_ms": download_ms, 
                        "blob_upload_ms": upload_ms,
                        "telemetry_overhead_ms": profiler.counters["telemetry_overhead_ms"],
                        "cpu_usage_pct": sys_stats['cpu_percent'],
                        "memory_mb": sys_stats['memory_mb'],
                        "serverless_cost_gb_sec": round((sys_stats['memory_mb'] / 1024) * (file_duration / 1000), 4),
                        "api_retries": profiler.counters["api_retries"],
                        "api_failures": profiler.counters["api_failures"],
                        "api_throttled_429": profiler.counters.get("api_throttled", 0)
                    }
                    
                    logger.info(f"📊 RESEARCH_DATA: {json.dumps(metrics_payload)}")
                    if az_handler:
                        try:
                            az_handler.flush()
                        except Exception as e:
                            pass
                    s_start = time.perf_counter()
                    await asyncio.sleep(2)
                    profiler.counters["sleep_time_ms"] += (time.perf_counter() - s_start) * 1000

                except Exception as loop_e:
                    logger.error(f"❌ Error processing {target_lang} for {blob.name}: {loop_e}")
            
                raw_container.delete_blob(blob.name)
                if os.path.exists(local_safe_name): os.remove(local_safe_name)
                
                gc.collect()

    except Exception as e:
        logger.error(f"❌ Pipeline Failure: {e}")

    if conn: conn.close()
    
    print("⏳ Finalizing Azure telemetry...")
    logging.shutdown()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python translation.py config.json")
    else:
        asyncio.run(main(sys.argv[1]))
