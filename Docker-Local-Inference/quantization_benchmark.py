#!/usr/bin/env python3
"""
NLLB-200 Quantization Benchmark Script
Compares different CTranslate2 quantization methods and collects telemetry
"""

import os
import sys
import json
import time
import uuid
import logging
import psutil
import shutil
import subprocess
from pathlib import Path
import urllib.request
import tarfile
import xml.etree.ElementTree as ET

try:
    from opencensus.ext.azure.log_exporter import AzureLogHandler
except ImportError:
    AzureLogHandler = None

# Quantization methods to test
QUANTIZATION_METHODS = [
    # {"name": "float32", "compute_type": "float32", "requires_gpu": False},  # Already benchmarked
    {"name": "int8", "compute_type": "int8", "requires_gpu": False},
]

# Fallback test sentences
TEST_SENTENCES = [
    "Click OK to confirm.",
    "Loading, please wait...",
    "File not found. Please check the path and try again.",
    "Upload up to 5 files.",
    "Your password must be at least 8 characters long.",
    "The operation completed successfully.",
    "An error occurred while processing your request.",
    "Save changes before closing?",
    "Delete this item permanently?",
    "Connection timeout. Please try again.",
]
REFERENCE_SENTENCES = [] # Empty by default unless using FLORES

def load_flores_dataset(max_sentences=1000):
    """Download FLORES-200 and load the eng_Latn -> spa_Latn split"""
    flores_dir = Path("flores200_dataset")
    if not flores_dir.exists():
        url = "https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz"
        print(f"📦 Downloading FLORES-200 from {url}...")
        try:
            urllib.request.urlretrieve(url, "flores200_dataset.tar.gz")
            with tarfile.open("flores200_dataset.tar.gz", "r:gz") as tar:
                tar.extractall()
        except Exception as e:
            print(f"⚠️ Failed to download FLORES: {e}")
            return [], []
            
    eng_file = flores_dir / "dev" / "eng_Latn.dev"
    spa_file = flores_dir / "dev" / "spa_Latn.dev"
    
    if not eng_file.exists() or not spa_file.exists():
        return [], []
        
    with open(eng_file, "r") as f_eng, open(spa_file, "r") as f_spa:
        source_sentences = [line.strip() for line in f_eng.readlines()][:max_sentences]
        reference_sentences = [line.strip() for line in f_spa.readlines()][:max_sentences]
        
    return source_sentences, reference_sentences

MODEL_NAME = "facebook/nllb-200-distilled-600M"
TARGET_LANG = "es-ES"  # Spanish


def check_gpu_available():
    """Check if GPU is available"""
    try:
        import ctranslate2
        # Try to create a dummy translator on CUDA
        return ctranslate2.get_cuda_device_count() > 0
    except:
        return False


def setup_logging(config):
    """Set up Application Insights logging if connection string is provided"""
    logger = logging.getLogger("quantization_benchmark")
    logger.setLevel(logging.INFO)
    
    if logger.hasHandlers():
        logger.handlers.clear()
        
    c_handler = logging.StreamHandler(sys.stdout)
    c_handler.setFormatter(logging.Formatter('%(asctime)s [INFO] %(message)s'))
    logger.addHandler(c_handler)
    
    az_handler = None
    if AzureLogHandler and config and config.get('app_insights_connection_string'):
        try:
            az_handler = AzureLogHandler(connection_string=config['app_insights_connection_string'])
            logger.addHandler(az_handler)
            logger.info("✅ Azure Application Insights logging enabled.")
        except Exception as e:
            logger.warning(f"⚠️ Could not set up Azure logging: {e}")
    elif not AzureLogHandler:
        logger.warning("⚠️ opencensus-ext-azure not installed. Application Insights logging disabled.")
        
    return logger, az_handler


def get_model_size(model_path):
    """Get total size of model directory in MB"""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(model_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            total_size += os.path.getsize(filepath)
    return total_size / (1024 * 1024)  # Convert to MB


def convert_model(quantization, output_dir):
    """Convert model with specified quantization"""
    print(f"\n🔧 Converting model with {quantization} quantization...")
    
    cmd = [
        "ct2-transformers-converter",
        "--model", MODEL_NAME,
        "--output_dir", output_dir,
        "--quantization", quantization,
        "--force"
    ]
    
    start_time = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        conversion_time = time.time() - start_time
        
        if result.returncode == 0:
            print(f"✅ Conversion successful ({conversion_time:.1f}s)")
            return True, conversion_time
        else:
            print(f"❌ Conversion failed: {result.stderr}")
            return False, 0
    except subprocess.TimeoutExpired:
        print("❌ Conversion timeout")
        return False, 0
    except Exception as e:
        print(f"❌ Conversion error: {e}")
        return False, 0


def run_benchmark_inference(model_path, reference_sentences, compute_type, device):
    """Run inference benchmark and collect metrics"""
    try:
        import ctranslate2
        from transformers import AutoTokenizer
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        return None
    
    print(f"🚀 Running inference with {compute_type} on {device}...")
    
    # Load tokenizer and translator
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        translator = ctranslate2.Translator(model_path, device=device, compute_type=compute_type)
    except Exception as e:
        print(f"❌ Failed to load model: {e}")
        return None
    
    process = psutil.Process(os.getpid())
    
    # Warmup (first run is slower due to loading)
    try:
        warmup_tokens = tokenizer(TEST_SENTENCES[:2], return_tensors="pt", padding=True)
        warmup_input = [tokenizer.convert_ids_to_tokens(ids) for ids in warmup_tokens["input_ids"]]
        _ = translator.translate_batch(warmup_input, target_prefix=[["▁es"]] * len(warmup_input))
    except Exception as e:
        print(f"⚠️ Warmup failed: {e}")
    
    # Actual benchmark
    results = {
        "translations": [],
        "latencies_ms": [],
        "memory_mb": [],
        "total_tokens": 0,
    }
    
    for i, sentence in enumerate(TEST_SENTENCES):
        # Tokenize
        tokens = tokenizer([sentence], return_tensors="pt", padding=True)
        token_ids = tokens["input_ids"][0].tolist()
        source_tokens = tokenizer.convert_ids_to_tokens(token_ids)
        
        # Measure memory before inference
        mem_before = process.memory_info().rss / (1024 * 1024)
        
        # Translate with timing
        start = time.perf_counter()
        try:
            translation = translator.translate_batch(
                [source_tokens],
                target_prefix=[["▁es"]],
                beam_size=5,
                max_batch_size=1
            )
            latency_ms = (time.perf_counter() - start) * 1000
            
            # Measure memory after inference
            mem_after = process.memory_info().rss / (1024 * 1024)
            
            # Decode translation
            translated_tokens = translation[0].hypotheses[0]
            translated_text = tokenizer.decode(
                tokenizer.convert_tokens_to_ids(translated_tokens),
                skip_special_tokens=True
            )
            
            results["translations"].append(translated_text)
            results["latencies_ms"].append(latency_ms)
            results["memory_mb"].append(max(mem_before, mem_after))
            results["total_tokens"] += len(source_tokens)
            
            print(f"  [{i+1}/{len(TEST_SENTENCES)}] {latency_ms:.1f}ms - {sentence[:50]}...")
            
        except Exception as e:
            print(f"  ❌ Translation failed: {e}")
            results["translations"].append(None)
            results["latencies_ms"].append(0)
    
    # Calculate summary metrics
    valid_latencies = [l for l in results["latencies_ms"] if l > 0]
    if valid_latencies:
        summary = {
            "avg_latency_ms": sum(valid_latencies) / len(valid_latencies),
            "min_latency_ms": min(valid_latencies),
            "max_latency_ms": max(valid_latencies),
            "total_time_ms": sum(valid_latencies),
            "throughput_sentences_per_sec": len(valid_latencies) / (sum(valid_latencies) / 1000),
            "avg_memory_mb": sum(results["memory_mb"]) / len(results["memory_mb"]) if results["memory_mb"] else 0,
            "peak_memory_mb": max(results["memory_mb"]) if results["memory_mb"] else 0,
            "total_tokens": results["total_tokens"],
            "tokens_per_second": results["total_tokens"] / (sum(valid_latencies) / 1000),
            "successful_translations": len(valid_latencies),
            "failed_translations": len(TEST_SENTENCES) - len(valid_latencies),
            "bleu_score": 0.0,
            "chrf_score": 0.0,
            "comet_score": 0.0,
        }
        
        # Calculate evaluation metrics if references are provided
        if reference_sentences and len(valid_latencies) > 0:
            print("📊 Calculating translation quality metrics (BLEU, chrF, COMET)...")
            import sacrebleu
            
            valid_refs = []
            valid_hyps = []
            valid_srcs = []
            for i, hyp in enumerate(results["translations"]):
                if hyp is not None:
                    valid_hyps.append(hyp)
                    valid_refs.append(reference_sentences[i])
                    valid_srcs.append(TEST_SENTENCES[i])
                    
            bleu = sacrebleu.corpus_bleu(valid_hyps, [valid_refs])
            chrf = sacrebleu.corpus_chrf(valid_hyps, [valid_refs])
            summary["bleu_score"] = round(bleu.score, 2)
            summary["chrf_score"] = round(chrf.score, 2)
            
            try:
                from comet import download_model, load_from_checkpoint
                print("🚀 Loading COMET model for semantic evaluation...")
                # Download model once, it caches locally
                comet_model_path = download_model("Unbabel/wmt22-comet-da")
                comet_model = load_from_checkpoint(comet_model_path)
                comet_data = [{"src": src, "mt": mt, "ref": ref} for src, mt, ref in zip(valid_srcs, valid_hyps, valid_refs)]
                comet_output = comet_model.predict(comet_data, batch_size=8, gpus=0)
                summary["comet_score"] = round(comet_output.system_score, 4)
            except Exception as e:
                print(f"⚠️ COMET evaluation failed (could be out of memory): {e}")

        return summary
    else:
        return None


def main():
    # Load config if provided
    config = {}
    if len(sys.argv) > 1 and sys.argv[1].endswith('.json') and os.path.exists(sys.argv[1]):
        try:
            with open(sys.argv[1], 'r') as f:
                config = json.load(f)
        except Exception as e:
            print(f"⚠️ Failed to load config: {e}")
            
    logger, az_handler = setup_logging(config)
    
    global TEST_SENTENCES, REFERENCE_SENTENCES
    sources, refs = load_flores_dataset(200)
    if sources and refs:
        TEST_SENTENCES = sources
        REFERENCE_SENTENCES = refs
        logger.info(f"📚 Loaded {len(TEST_SENTENCES)} real sentences from FLORES-200 benchmark.")
    else:
        logger.warning("⚠️ FLORES-200 download failed. Falling back to 10 hardcoded sentences (Metrics disabled).")

    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║   NLLB-200 Quantization Benchmark - CTranslate2              ║")
    print("╚═══════════════════════════════════════════════════════════════╝\n")
    
    # Check GPU availability
    gpu_available = check_gpu_available()
    print(f"🔍 GPU Available: {'✅ Yes' if gpu_available else '❌ No (CPU only)'}\n")
    
    # Create benchmark results directory
    results_dir = Path("quantization_benchmark_results")
    results_dir.mkdir(exist_ok=True)
    
    # Store all results
    all_results = []
    
    # Test each quantization method
    for method in QUANTIZATION_METHODS:
        quantization = method["name"]
        compute_type = method["compute_type"]
        requires_gpu = method["requires_gpu"]
        
        print(f"\n{'='*70}")
        print(f"Testing: {quantization.upper()}")
        print(f"{'='*70}")
        
        # Skip GPU-only methods if no GPU
        if requires_gpu and not gpu_available:
            print(f"⏭️  Skipping {quantization} (requires GPU)")
            all_results.append({
                "quantization": quantization,
                "compute_type": compute_type,
                "status": "skipped",
                "reason": "requires_gpu"
            })
            continue
        
        # Convert model
        model_dir = results_dir / f"model_{quantization}"
        if model_dir.exists():
            shutil.rmtree(model_dir)
        
        success, conversion_time = convert_model(quantization, str(model_dir))
        
        if not success:
            all_results.append({
                "quantization": quantization,
                "compute_type": compute_type,
                "status": "conversion_failed"
            })
            continue
        
        # Get model size
        model_size_mb = get_model_size(model_dir)
        print(f"📦 Model size: {model_size_mb:.1f} MB")
        
        # Run benchmark
        device = "cuda" if (requires_gpu and gpu_available) else "cpu"
        benchmark_results = run_benchmark_inference(str(model_dir), REFERENCE_SENTENCES, compute_type, device)
        
        if benchmark_results:
            result_entry = {
                "quantization": quantization,
                "compute_type": compute_type,
                "device": device,
                "status": "success",
                "conversion_time_sec": conversion_time,
                "model_size_mb": model_size_mb,
                **benchmark_results
            }
            all_results.append(result_entry)
            print(f"✅ Benchmark complete")
            
            # Emit telemetry for Application Insights
            # App Insights RESEARCH_DATA Payload format
            metrics_payload = {
                "run_invocation_id": str(uuid.uuid4()), 
                "file_name": f"benchmark_{quantization}.xlf",
                "target_lang": TARGET_LANG,
                "throughput_seg_per_hr": int(benchmark_results["throughput_sentences_per_sec"] * 3600),
                "throughput_input_tokens_per_hr": round(benchmark_results["tokens_per_second"] * 3600, 2),
                "total_api_call_logically": len(TEST_SENTENCES),
                "actual_api_call": len(TEST_SENTENCES),
                "api_retries": benchmark_results["failed_translations"],
                "api_failures": benchmark_results["failed_translations"],
                "api_throttled_429": 0,
                "total_segments": len(TEST_SENTENCES),
                "total_tokens": benchmark_results["total_tokens"],
                "total_time": benchmark_results["total_time_ms"],
                "compute_time_ms": benchmark_results["total_time_ms"],
                "cold_start_init_ms": conversion_time * 1000,
                "model_load_ms": 0,
                "avg_network_latency_ms": 0,
                "avg_segment_process_ms": benchmark_results["avg_latency_ms"],
                "avg_translation_ms": benchmark_results["avg_latency_ms"],
                "cpu_usage_pct": psutil.Process(os.getpid()).cpu_percent(interval=None),
                "memory_mb": benchmark_results["peak_memory_mb"],
                "serverless_cost_gb_sec": round((benchmark_results["peak_memory_mb"] / 1024) * (benchmark_results["total_time_ms"] / 1000), 4),
                "bleu_score": benchmark_results["bleu_score"],
                "chrf_score": benchmark_results["chrf_score"],
                "comet_score": benchmark_results["comet_score"]
            }
            logger.info(f"📊 RESEARCH_DATA: {json.dumps(metrics_payload)}")
            if az_handler:
                try:
                    az_handler.flush()
                except:
                    pass
        else:
            all_results.append({
                "quantization": quantization,
                "compute_type": compute_type,
                "status": "benchmark_failed"
            })
            
        # Clean up the model directory after testing to free up disk space
        if model_dir.exists():
            print(f"🧹 Deleting {model_dir} to free up disk space...")
            shutil.rmtree(model_dir)
    
    # Save results to JSON
    output_file = results_dir / "benchmark_results.json"
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2)
    
    print("\n\n======================================================================")
    print("BENCHMARK RESULTS SUMMARY")
    print("======================================================================\n")
    
    print(f"{'Method':<15} {'Size(MB)':<12} {'Latency(ms)':<15} {'Throughput':<15} {'Memory(MB)':<12} {'BLEU':<6} {'COMET':<8} {'Status'}")
    print("-" * 100)
    for r in all_results:
        if r["status"] == "success":
            print(f"{r['quantization']:<15} {r['model_size_mb']:<12.1f} {r['total_time_ms']:<15.1f} {r['throughput_sentences_per_sec']:<15.2f} {r['peak_memory_mb']:<12.1f} {r.get('bleu_score', 0):<6.1f} {r.get('comet_score', 0):<8.4f} {r['status']}")
        else:
            print(f"{r['quantization']:<15} {'N/A':<12} {'N/A':<15} {'N/A':<15} {'N/A':<12} {'N/A':<6} {'N/A':<8} {r['status']}")
    print(f"\n✅ Full results saved to: {output_file}")
    print(f"📊 Test sentences: {len(TEST_SENTENCES)}")
    print(f"🎯 Target language: {TARGET_LANG}")
    
    if az_handler:
        print("⏳ Finalizing Azure telemetry...")
        logging.shutdown()


if __name__ == "__main__":
    main()
