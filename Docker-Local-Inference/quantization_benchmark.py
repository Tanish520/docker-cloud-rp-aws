#!/usr/bin/env python3
"""
NLLB-200 Quantization Benchmark Script
Compares different CTranslate2 quantization methods and collects telemetry
"""

import os
import sys
import json
import time
import psutil
import shutil
import subprocess
from pathlib import Path

# Quantization methods to test
QUANTIZATION_METHODS = [
    {"name": "float32", "compute_type": "float32", "requires_gpu": False},
    {"name": "float16", "compute_type": "float16", "requires_gpu": True},
    {"name": "int16", "compute_type": "int16", "requires_gpu": False},
    {"name": "int8", "compute_type": "int8", "requires_gpu": False},
    {"name": "int8_float16", "compute_type": "int8_float16", "requires_gpu": True},
    {"name": "int8_bfloat16", "compute_type": "int8_bfloat16", "requires_gpu": True},
]

# Test sentences (UI strings from actual localization)
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


def run_inference_benchmark(model_path, compute_type, device):
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
        }
        return summary
    else:
        return None


def main():
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
        benchmark_results = run_inference_benchmark(str(model_dir), compute_type, device)
        
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
        else:
            all_results.append({
                "quantization": quantization,
                "compute_type": compute_type,
                "status": "benchmark_failed"
            })
    
    # Save results to JSON
    output_file = results_dir / "benchmark_results.json"
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2)
    
    print(f"\n\n{'='*70}")
    print("BENCHMARK RESULTS SUMMARY")
    print(f"{'='*70}\n")
    
    # Print comparison table
    print(f"{'Method':<15} {'Size(MB)':<12} {'Latency(ms)':<15} {'Throughput':<15} {'Memory(MB)':<12} {'Status':<10}")
    print("-" * 90)
    
    for result in all_results:
        if result["status"] == "success":
            print(f"{result['quantization']:<15} "
                  f"{result['model_size_mb']:<12.1f} "
                  f"{result['avg_latency_ms']:<15.1f} "
                  f"{result['throughput_sentences_per_sec']:<15.2f} "
                  f"{result['peak_memory_mb']:<12.1f} "
                  f"{result['status']:<10}")
        else:
            print(f"{result['quantization']:<15} "
                  f"{'N/A':<12} "
                  f"{'N/A':<15} "
                  f"{'N/A':<15} "
                  f"{'N/A':<12} "
                  f"{result['status']:<10}")
    
    print(f"\n✅ Full results saved to: {output_file}")
    print(f"📊 Test sentences: {len(TEST_SENTENCES)}")
    print(f"🎯 Target language: {TARGET_LANG}")


if __name__ == "__main__":
    main()
