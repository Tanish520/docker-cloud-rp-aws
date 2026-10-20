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
import hashlib

os.environ["APPLICATIONINSIGHTS_ROLE_NAME"] = "Local-Inference-Engine"

# IMPORT CTRANSLATE2 AND AUTOTOKENIZER INSTEAD OF MBART/PYTORCH
import ctranslate2
from transformers import AutoTokenizer
import aws_runtime
from s3_store import RunStore
from placeholders import protect, restore
from batching import decoding_cap, plan_balanced
from templates import adapt, template_of
from memory_store import Entry, S3Memory
from opencensus.ext.azure.log_exporter import AzureLogHandler
from opencensus.ext.azure import metrics_exporter
from opencensus.stats import aggregation as aggregation_module
from opencensus.stats import measure as measure_module
from opencensus.stats import stats as stats_module
from opencensus.stats import view as view_module
from opencensus.tags import tag_map as tag_map_module

# --- 1. CONFIGURATION ---
TWIN_REUSE = os.environ.get("TM_TWINS", "on") != "off"  # reuse near-twins (templates.py); TM_TWINS=off disables
TOKEN_LIMIT = 1024    # most real tokens per model batch (and per string, after truncation)
CHUNK_SIZE = 1000     # strings per model call; the memory is saved and progress printed after each
BEAM_SIZE = 2         # 2.2x faster than 5 for -0.45 chrF on KDE4 (BATCHING_RESULTS.md section 8)
ENGINE_WORKERS = 2    # batches translated at once, one per pinned core (PHASE2_CPUSET=0,1)

LANG_TOKEN_MULTIPLIERS = {
    "zh-CN": 1.2, "ja-JP": 1.1, "ru-RU": 0.5, "hi-IN": 0.3, 
    "te-IN": 0.2, "fr-FR": 0.9, "es-ES": 0.9, "de-DE": 0.8, "ar-SA": 0.4,
    "ta-IN": 0.2, "mr-IN": 0.3, "bn-IN": 0.3, "kn-IN": 0.2, "pa-IN": 0.3
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
    translator = ctranslate2.Translator(CT2_MODEL_PATH, device="auto", compute_type="int8",
                                       inter_threads=ENGINE_WORKERS, intra_threads=1)
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

MODEL_NAME = "nllb-600M-ct2"

def engine_version():
    """What produced a translation: the model plus every setting that changes the output. Stored with each
    memory entry and matched on lookup, so changing any of them never reuses older translations."""
    return f"{HF_MODEL_NAME}|ct2-int8|beam={BEAM_SIZE}|cap=2x+10|placeholders=square|unk=english"

def tm_key(text):
    """Translation-memory key: SHA-256 of the source text, so strings of any length fit (db_admin.py)."""
    return hashlib.sha256(text.encode("utf-8")).digest()

# Translation memory for this run: target_lang -> {SourceHash: translation}. Each language is loaded from the
# memory store (S3 files or the SQL table) once, the first time it is needed; strings translated during the run
# are added as they are saved, so later files reuse them without asking the store again.
memory = {}
twins = {}                    # target_lang -> {template_of(source): (source, translation)} of model translations
added_in_run = set()          # (target_lang, SourceHash) of entries translated during this run
file_extra = {}               # per-file counters reported in RESEARCH_DATA (reset by process_xlf_file)

def reset_file_extra():
    file_extra.clear()
    file_extra.update({"tm_db_queries": 0, "tm_lookup_ms": 0.0, "tm_hits_in_run": 0, "tm_twin_hits": 0, "tm_kept_english": 0, "model_strings": 0,
                       "real_input_tokens": 0, "real_output_tokens": 0, "padded_positions": 0, "real_positions": 0})

class SqlMemory:
    """Translation memory in the SQL Server table (db_admin.py), with the same interface as memory_store.S3Memory."""

    MERGE = """
        MERGE INTO Translations AS target
        USING (SELECT ? AS SourceHash, ? AS TargetLang, ? AS EngineVersion) AS source
        ON (target.SourceHash = source.SourceHash AND target.TargetLang = source.TargetLang
            AND target.EngineVersion = source.EngineVersion)
        WHEN MATCHED THEN UPDATE SET TranslatedText = ?
        WHEN NOT MATCHED THEN INSERT (SourceHash, SourceText, TargetLang, EngineVersion, TranslatedText, ModelName, LastUpdated)
        VALUES (?, ?, ?, ?, ?, ?, GETDATE());
        """

    def __init__(self, conn):
        self.conn = conn
        self.cursor = conn.cursor()

    def load(self, target_lang):
        self.cursor.execute("SELECT SourceHash, SourceText, TranslatedText, ModelName FROM Translations WHERE TargetLang=? AND EngineVersion=?",
                            [target_lang, engine_version()])
        return [(bytes(key), source, translation, model) for key, source, translation, model in self.cursor.fetchall()]

    def save(self, entries):
        version = engine_version()
        self.cursor.fast_executemany = True
        self.cursor.executemany(self.MERGE, [(e.key, e.lang, version, e.translation, e.key, e.source, e.lang, version,
                                              e.translation, e.model) for e in entries])

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

def load_memory(store, target_lang):
    profiler.counters["sql_reads"] += 1
    file_extra["tm_db_queries"] += 1
    known, index = {}, twins.setdefault(target_lang, {})
    # Sorted by hash: when several model translations share a template, the twin source must not depend on
    # the order the store happens to return entries in (SQL by key, S3 by file).
    for key, source, translation, model_name in sorted(store.load(target_lang)):
        known[key] = translation
        if model_name == MODEL_NAME and translation:   # adapt only from model translations, never from adapted ones
            index.setdefault(template_of(source), (source, translation))
    memory[target_lang] = known

KEPT_ENGLISH = MODEL_NAME + "+english"   # saved with an empty translation: the model cannot write this string

def remember(target_lang, source, translation, model_name):
    """Add an entry to this run's memory and return it for saving. model_name is MODEL_NAME (model
    translation), MODEL_NAME + "+twin" (adapted twin) or KEPT_ENGLISH (empty: keep the English)."""
    key = tm_key(source)
    memory.setdefault(target_lang, {})[key] = translation
    added_in_run.add((target_lang, key))
    if model_name == MODEL_NAME and translation:
        twins.setdefault(target_lang, {}).setdefault(template_of(source), (source, translation))
    return Entry(key, target_lang, source, translation, model_name)

def bulk_query_translations(store, source_texts, target_lang):
    if not source_texts: return {}, 0

    profiler.start_timer("sql_read_latency")
    translations_map = {}
    try:
        if target_lang not in memory:
            load_memory(store, target_lang)
        known = memory[target_lang]
        for text in set(source_texts):
            key = tm_key(text)
            if key in known:
                translations_map[text] = known[key]
                file_extra["tm_hits_in_run"] += (target_lang, key) in added_in_run
        lookup_ms = profiler.stop_timer("sql_read_latency")
        file_extra["tm_lookup_ms"] += lookup_ms
        return translations_map, lookup_ms
    except Exception as e:
        print(f"❌ SQL Bulk Query Error: {e}")
        return {}, profiler.stop_timer("sql_read_latency")

def bulk_save_translations(store, entries):
    if not entries: return 0

    profiler.counters["sql_writes"] += 1
    profiler.start_timer("sql_write_latency")
    try:
        store.save(entries)
        return profiler.stop_timer("sql_write_latency")
    except Exception as e:
        print(f"❌ Translation memory write error: {e}")
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
        "kn-IN": "kan_Knda", "pa-IN": "pan_Guru"
    }
    tgt_token = lang_map.get(target_lang, "eng_Latn")
    
    profiler.start_timer("hf_network_latency") 
    try:
        tokenizer.src_lang = "eng_Latn"
        
        def run_engine(batch):
            # 1. Tokenize inputs with TRUNCATION SAFETY to prevent C++ engine crashes
            source_tokens = []
            for text in batch:
                encoded_ids = tokenizer.encode(text, truncation=True, max_length=TOKEN_LIMIT)
                source_tokens.append(tokenizer.convert_ids_to_tokens(encoded_ids))
            lengths = [len(tokens) for tokens in source_tokens]
            file_extra["model_strings"] = file_extra.get("model_strings", 0) + len(batch)
            file_extra["real_input_tokens"] = file_extra.get("real_input_tokens", 0) + sum(lengths)

            # 2. Shortest-first batches of at most TOKEN_LIMIT tokens (little padding), all queued at
            # once so both engine workers stay busy. Output is capped at 2x the longest input + 10
            # tokens so one runaway string cannot hold up its batch (BATCHING_RESULTS.md section 9).
            pending = []
            # A small call (a small commit: ~30 strings per language) is split so each worker gets a batch.
            for group in plan_balanced(lengths, TOKEN_LIMIT, ENGINE_WORKERS):
                file_extra["padded_positions"] = file_extra.get("padded_positions", 0) + max(lengths[i] for i in group) * len(group)
                file_extra["real_positions"] = file_extra.get("real_positions", 0) + sum(lengths[i] for i in group)
                pending.append((group, translator.translate_batch(
                    [source_tokens[i] for i in group],
                    target_prefix=[[tgt_token]] * len(group),
                    beam_size=BEAM_SIZE,
                    max_decoding_length=decoding_cap([lengths[i] for i in group]),
                    asynchronous=True
                )))
            profiler.counters["total_batches"] += len(pending)

            # 3. Detokenize back to string (Fast CPU op), in the caller's order
            out = [None] * len(batch)
            for group, futures in pending:
                for i, future in zip(group, futures):
                    # Drop the language token prefix from the beginning
                    target_tokens = future.result().hypotheses[0][1:]
                    file_extra["real_output_tokens"] = file_extra.get("real_output_tokens", 0) + len(target_tokens)

                    # <unk> = a character missing from NLLB's vocabulary (some CJK words). Decoding would
                    # delete it and leave "" or a word with a hole, so the string is left untranslated.
                    if "<unk>" in target_tokens:
                        out[i] = ""
                        continue

                    # CRITICAL FIX: Skip special tokens so </s> doesn't leak into the translation
                    token_ids = tokenizer.convert_tokens_to_ids(target_tokens)
                    out[i] = tokenizer.decode(token_ids, skip_special_tokens=True)
            return out

        # Placeholders (%1, {name}, <b>) go to the model as [0], [1], ... which it copies far more
        # reliably (KDE4: 87.7% -> 99.15% kept); see BATCHING_RESULTS.md section 10.
        masked = [protect(text) for text in texts]
        translations, lost = [], []
        for i, (output, (_, originals)) in enumerate(zip(run_engine([m for m, _ in masked]), masked)):
            restored, ok = restore(output, originals)
            translations.append(restored)
            if not ok:
                lost.append(i)
        # Where a marker did not come back exactly once, use the translation of the text as written.
        if lost:
            for i, output in zip(lost, run_engine([texts[i] for i in lost])):
                translations[i] = output

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
    # conn: the translation memory store (memory_store.S3Memory or SqlMemory)
    profiler.start_timer("xml_parse")
    try:
        tree = ET.parse(local_path)
        root = tree.getroot()
        file_units = root.findall('.//trans-unit')
        xml_parse_ms = profiler.stop_timer("xml_parse")
    except ET.ParseError as e: return {"total_api_time_ms": 0, "total_sql_time_ms": 0, "total_commit_time_ms": 0}, 0, 0, 0, 0
    
    reset_file_extra()
    file_stats = {"total_api_time_ms": 0, "total_sql_time_ms": 0, "total_commit_time_ms": 0, "extra": file_extra}
    total_source_chars = total_translated_chars = 0  
    missing_units = [] 
    
    all_source_texts = []
    for unit in file_units:
        source_node = unit.find('source')
        if source_node is not None and source_node.text:
            all_source_texts.append(source_node.text)

    translations_map, read_time = bulk_query_translations(conn, all_source_texts, target_lang)
    remembered_english = []
    file_stats["total_sql_time_ms"] += read_time
    
    for i, unit in enumerate(file_units):
        source_node = unit.find('source')
        source_text = source_node.text if source_node is not None else None
        if not source_text: continue

        total_source_chars += len(source_text)
        cached_trans = translations_map.get(source_text)

        if cached_trans == "":
            # Remembered as kept in English for this engine: leave the target empty (Phase 3 keeps the English).
            profiler.add_segment_stats(tm_hit=True)
            file_extra["tm_kept_english"] += 1
            remembered_english.append(source_text)
        elif cached_trans:
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

    if remembered_english:
        print(f"⚠️ {len(remembered_english)} string(s) kept in English (remembered: the model has no characters for them): {remembered_english[:10]}")

    current_batch_texts = []
    current_batch_indices = []
    current_batch_tokens = 0

    async def flush_batch():
        nonlocal total_translated_chars, current_batch_texts, current_batch_indices, current_batch_tokens
        if not current_batch_texts: return
        
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
            untranslated = [src for src, trans in zip(current_batch_texts, translations) if trans == ""]
            if untranslated:
                print(f"⚠️ {len(untranslated)} string(s) kept in English (the model has no characters for them): {untranslated[:10]}")
                # Remembered for this engine version, so later runs skip the model for them.
                db_write_batch.extend(remember(target_lang, src, "", KEPT_ENGLISH) for src in untranslated)
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
                    db_write_batch.append(remember(target_lang, src, trans, MODEL_NAME))
                    
                    multiplier = LANG_TOKEN_MULTIPLIERS.get(target_lang, 0.5)
                    output_tokens = len(trans) / (4.0 * multiplier)
                    input_tokens = len(current_batch_texts[b_idx]) / 4.0
                    profiler.counters["tokens_processed"] += (input_tokens + output_tokens)
                    profiler.counters["total_input_tokens"] += input_tokens
                    profiler.counters["total_output_tokens"] += output_tokens

            if db_write_batch:
                write_time = bulk_save_translations(conn, db_write_batch)
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

    # Near-twins ("8 Cards" for a saved "20 Cards", "Border:" for "Border"): adapt a model translation instead
    # of calling the model. Twins of a string still to be translated in this file wait until it is done.
    twin_rows, later = [], []

    def twin_translation(source_text):
        saved = twins.get(target_lang, {}).get(template_of(source_text)) if TWIN_REUSE else None
        return adapt(saved[0], saved[1], source_text, target_lang) if saved else None

    def use_twin(unit_idx, source_text, translation):
        nonlocal total_translated_chars
        target_node = file_units[unit_idx].find('target')
        if target_node is not None:
            target_node.text = translation
        total_translated_chars += len(translation)
        file_extra["tm_twin_hits"] += 1
        twin_rows.append(remember(target_lang, source_text, translation, MODEL_NAME + "+twin"))

    if TWIN_REUSE:
        leaders, groups = [], set()
        for unit in missing_units:
            adapted = twin_translation(unit[1])
            if adapted is not None:
                use_twin(*unit, adapted)
            elif template_of(unit[1]) in groups:
                later.append(unit)
            else:
                groups.add(template_of(unit[1]))
                leaders.append(unit)
        missing_units = leaders

    async def translate_units(units):
        # Shortest first, so each chunk holds strings of similar length and its batches need little padding.
        nonlocal current_batch_tokens
        units = sorted(units, key=lambda unit: len(unit[1]))
        for start in range(0, len(units), CHUNK_SIZE):
            for original_unit_idx, source_text in units[start:start + CHUNK_SIZE]:
                current_batch_texts.append(source_text)
                current_batch_indices.append(original_unit_idx)
                current_batch_tokens += len(source_text) / 4.0
            await flush_batch()

    await translate_units(missing_units)
    retry = []
    for unit in later:
        adapted = twin_translation(unit[1])
        if adapted is not None:
            use_twin(*unit, adapted)
        else:
            retry.append(unit)
    await translate_units(retry)

    if twin_rows:
        file_stats["total_sql_time_ms"] += bulk_save_translations(conn, twin_rows)
        try:
            conn.commit()
        except Exception as e: print(f"⚠️ SQL Commit warning: {e}")

    profiler.start_timer("xml_write")
    tree.write(local_path, encoding='utf-8', xml_declaration=True)
    xml_write_ms = profiler.stop_timer("xml_write")
    
    mm = stats_module.stats.stats_recorder.new_measurement_map()
    mm.measure_float_put(m_overhead, profiler.counters["telemetry_overhead_ms"])
    profiler.measure_telemetry_overhead(mm.record, tag_map_module.TagMap())

    file_extra["padding_pct"] = round(100 * (1 - file_extra["real_positions"] / file_extra["padded_positions"]), 2) if file_extra["padded_positions"] else 0.0
    file_extra["tm_lookup_ms"] = round(file_extra["tm_lookup_ms"], 3)
    file_stats["extra"] = dict(file_extra)

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
        return 1

    with open(config_path, 'r') as f: config = json.load(f)
    logger, az_handler = setup_logging(config)
    failures = 0
    
    try:
        store = RunStore(os.environ['BUCKET'], os.environ['RUN_ID'])
        raw_container_name = store.raw_prefix
        
        profiler.start_timer("cold_start")
        # Where the translation memory lives: TM_STORE=s3 (files, memory_store.py; default) or sql (RDS table).
        if os.environ.get("TM_STORE", "s3") == "s3":
            conn = S3Memory(store.client, store.bucket, store.run_id, engine_version())
        else:
            sql_conn, _ = get_sql_connection(aws_runtime.sql_conn_str())
            conn = SqlMemory(sql_conn) if sql_conn else None
        cold_start_ms = profiler.stop_timer("cold_start")
        if not conn: 
            print("❌ Exiting due to SQL Connection Failure.")
            return 1
    except Exception as e:
        print(f"❌ Init Error: {e}")
        return 1

    try:
        exporter = metrics_exporter.new_metrics_exporter(connection_string=config['app_insights_connection_string'])
        register_metrics_views(exporter)
    except:
        exporter = None
        # No Application Insights on AWS: register the same views without an exporter so the
        # in-process recording work (and telemetry_overhead_ms) matches the Azure run.
        register_metrics_views(None)

    try:
        blobs = store.list_raw()
        
        if not blobs:
            logger.warning(f"⚠️ ZERO files found in '{raw_container_name}'. Are you sure Phase 1 ran?")
            # An empty raw folder is fine on a retry after full success, but not when nothing was translated either.
            if not store.list_translated():
                failures += 1
                logger.error(f"❌ Run {store.run_id} has no raw or translated files. Did Phase 1 run for this RUN_ID?")
        else:
            logger.info(f"🔎 Found {len(blobs)} files in '{raw_container_name}'.")
            # Load every language's memory at once in the background, so each is ready when its file comes up.
            if hasattr(conn, "prefetch"):
                conn.prefetch(sorted({b.name.split('.')[-2] for b in blobs
                                      if b.name.lower().endswith('.xlf') and len(b.name.split('.')) >= 3}))

        for blob in blobs:
            if blob.name.lower().endswith('.xlf'):
                logger.info(f"⬇️ Downloading {blob.name}...")
                
                profiler.start_timer("blob_download")
                local_safe_name = os.path.basename(blob.name)
                try:
                    store.download_raw(blob.name, local_safe_name)
                except Exception as download_e:
                    failures += 1
                    logger.error(f"❌ Could not download {blob.name}: {download_e}")
                    continue
                download_ms = profiler.stop_timer("blob_download")

                parts = blob.name.split('.')
                if len(parts) >= 3:
                    target_lang = parts[-2]
                else:
                    logger.warning(f"⚠️ Could not deduce language from filename {blob.name}. Skipping.")
                    failures += 1
                    if os.path.exists(local_safe_name): os.remove(local_safe_name)
                    continue

                succeeded = False
                try:
                    profiler.reset_file_metrics() 
                    profiler.start_timer(f"file_{local_safe_name}_{target_lang}")
                    
                    logger.info(f"Translating {local_safe_name} to {target_lang}...")
                    
                    file_stats, xml_parse_ms, xml_write_ms, total_source_chars, total_translated_chars = await process_xlf_file(local_safe_name, target_lang, conn, exporter)

                    # process_xlf_file and translate_local swallow failures; treat them as a failed file.
                    if profiler.counters["api_failures"] or not profiler.counters["total_segments"]:
                        raise RuntimeError(f"translation incomplete: {profiler.counters['api_failures']} failed batches, {profiler.counters['total_segments']} segments")

                    logger.info(f"⬆️ Uploading processed {blob.name} to translated folder...")
                    profiler.start_timer("blob_upload")
                    store.upload_translated(local_safe_name, blob.name)
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
                    metrics_payload.update(aws_runtime.extra_fields())
                    metrics_payload.update(file_stats.get("extra", {}))
                    
                    logger.info(f"📊 RESEARCH_DATA: {json.dumps(metrics_payload)}")
                    if az_handler:
                        try:
                            az_handler.flush()
                        except Exception as e:
                            pass
                        # Give Azure's exporter time to send; without one (AWS) the pause only slows the run.
                        s_start = time.perf_counter()
                        await asyncio.sleep(2)
                        profiler.counters["sleep_time_ms"] += (time.perf_counter() - s_start) * 1000
                    succeeded = True

                except Exception as loop_e:
                    failures += 1
                    logger.error(f"❌ Error processing {target_lang} for {blob.name}: {loop_e}")
            
                # Unlike Azure, keep the raw file when anything failed so a stage retry can redo it.
                if succeeded:
                    try:
                        store.delete_raw(blob.name)
                    except Exception as delete_e:
                        logger.warning(f"⚠️ Translated {blob.name} but could not delete its raw input: {delete_e}")
                if os.path.exists(local_safe_name): os.remove(local_safe_name)
                
                gc.collect()

    except Exception as e:
        failures += 1
        logger.error(f"❌ Pipeline Failure: {e}")

    if conn:
        # S3: waits for the background uploads of new memory entries and reports any that failed.
        failed_saves = conn.close()
        if failed_saves:
            logger.error(f"❌ {failed_saves} translation memory file(s) could not be saved; those strings will be translated again next run.")
    
    print("⏳ Finalizing telemetry...")
    logging.shutdown()
    return failures

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python translation.py config.json")
        sys.exit(2)
    sys.exit(1 if asyncio.run(main(sys.argv[1])) else 0)
