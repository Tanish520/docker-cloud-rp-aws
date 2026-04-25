# End-Semester Report Draft: Cloud Localization Pipeline for Multilingual Resource Translation

> Report source document prepared from the previous mid-semester PDF, the current repository code, and the latest VM-local telemetry exports.
>
> Date: 25 April 2026
>
> Important scope note: `VM_MbartModel/` is intentionally ignored in this report because it is not part of the final evaluated implementation path.

## How To Use This Markdown

This file is meant to be the long-form source material for the final end-semester report. It is intentionally detailed and somewhat repetitive because the final report must carry forward the mid-semester work and then show the end-semester improvement clearly.

Recommended use:

1. Use Sections 1 to 4 as the report introduction and background.
2. Use Sections 5 to 8 as the implementation chapters.
3. Use Sections 9 to 13 as the evaluation and result chapters.
4. Paste Grafana screenshots into the placeholders in Section 14.
5. Use Section 18 as the page-by-page plan for a roughly 30-page final report.

## Instructions For The AI Model Generating The Final Report

This Markdown will be given to an AI model such as Claude to generate the final formatted report. The AI model should follow these instructions strictly:

1. Produce a detailed academic end-semester report of about 30 pages.
2. Preserve the project narrative as an evolution from **our earlier API version** to **current Hugging Face API**, then **Docker local inference**, then **VM-local inference**, and finally the **AWS multi-cloud mBART API comparison**.
3. Do not use authorship-based wording for the earlier version. Refer to it only as "our earlier API version", "the earlier implementation", or "the earlier API baseline".
4. Do not include real secrets, tokens, connection strings, SQL passwords, Azure keys, AWS keys, or Hugging Face tokens. Replace all such values with placeholders.
5. Ignore `VM_MbartModel/` completely.
6. Mention explicitly that the COMET model used for FLORES scoring is `Unbabel/wmt22-comet-da`.
7. Explain BLEU, chrF, and COMET in the quality evaluation section before showing the FLORES table.
8. Treat BLEU as the main lexical metric for Latin-script languages and chrF as the primary lexical metric for `ja-JP` and `zh-CN`.
9. Make clear that COMET is a neural/reference-based semantic quality metric and not just a surface-overlap score.
10. Make clear that `resx_file_10.*.xlf` Docker telemetry belongs to Docker local inference on Azure Pipelines, not to the VM-local D4as_v4 run.
11. Make clear that `azure_vm_D4as_v4.csv` is the actual VM-local Azure VM `D4as_v4` telemetry.
12. Include the seven current Hugging Face API improvements listed in Section 5.2.
13. Include the Docker improvements listed in Section 5.3, especially CTranslate2, NLLB, INT8, beam search, dual circuit breakers, pipeline cache, disk cleanup, bulk SQL, and special-token cleanup.
14. Include the VM-specific differences listed in Section 5.4, especially native VM execution, bootstrap, config-driven model loading, FastAPI option, systemd option, local-first bundle workflow, and experiment harness.
15. Insert images exactly at the positions specified in the image manifest below. If an image file is missing, leave a clear placeholder with the figure name and caption instead of inventing an image.
16. Use the tables in this Markdown as source-of-truth values. Do not recompute or alter the numbers unless explicitly instructed.
17. Maintain an academic tone but keep the explanation readable.

## Required Image Asset Manifest

Place uploaded screenshots/diagrams in a folder named `report_assets/` at the repository root. Use the exact filenames below so the final report generator can attach them automatically.

| Figure ID | Required filename | Insert after section | Image type | Required caption |
| --- | --- | --- | --- | --- |
| Figure 1 | `report_assets/fig01_end_to_end_architecture.png` | Section 4, after the shared cloud architecture list | Drawn architecture diagram | End-to-end cloud localization pipeline showing `.resx` input, XLIFF conversion, raw storage, Translation Memory lookup, inference provider, translated storage, localized `.resx` output, and telemetry. |
| Figure 2 | `report_assets/fig02_four_generation_evolution.png` | Section 5, before Section 5.1 | Drawn timeline diagram | Architectural evolution from our earlier API version to current Hugging Face API, Docker local inference, and VM-local inference. |
| Figure 3 | `report_assets/fig03_hf_api_architecture.png` | Section 5.2, after the seven HF improvement table | Drawn architecture diagram or screenshot | Current Hugging Face API architecture with token-aware batching, Translation Memory lookup, Hugging Face mBART API call, SQL MERGE, and Application Insights telemetry. |
| Figure 4 | `report_assets/fig04_docker_local_architecture.png` | Section 5.3, after the Docker improvement table | Drawn architecture diagram | Docker local inference architecture showing Azure Pipeline agent, Docker image, cached `nllb_ct2` model, CTranslate2 runtime, Azure SQL TM, Blob Storage, and telemetry. |
| Figure 5 | `report_assets/fig05_vm_local_architecture.png` | Section 5.4, after the VM-specific differences table | Drawn architecture diagram | VM-local architecture showing Azure VM, Python environment, persistent `nllb_ct2` model, native `translation.py`, optional FastAPI service, SQL, Blob Storage, App Insights, and Grafana. |
| Figure 6 | `report_assets/fig06_throughput_comparison.png` | Section 8.2, after the per-language throughput comparison table | Chart | Per-language throughput comparison across earlier API version, current HF API, Docker local inference, and VM-local D4as_v4. |
| Figure 7 | `report_assets/fig07_execution_time_comparison.png` | Section 8.3, after the execution time comparison table | Chart | Execution time comparison for current HF API, Docker local Azure Pipeline, and VM-local D4as_v4. |
| Figure 8 | `report_assets/fig08_docker_vs_vm_telemetry.png` | Section 9.2, after the VM-local aggregate table | Chart or dashboard screenshot | Corrected Docker Azure Pipeline vs Azure VM D4as_v4 local inference telemetry comparison. |
| Figure 9 | `report_assets/fig09_tm_cache_flow.png` | Section 10, before the TM seed rate table | Drawn flow diagram | Translation Memory cache flow showing SQL hit path, NMT miss path, write-back to TM, and cache-rate measurement. |
| Figure 10 | `report_assets/fig10_tm_cache_results.png` | Section 10, after the validated pilot summary table | Chart or Grafana screenshot | VM-local Translation Memory experiment showing `0%` vs `50%` cache impact on runtime and NMT calls. |
| Figure 11 | `report_assets/fig11_flores_quality_flow.png` | Section 11, after the FLORES workflow list | Drawn flow diagram | FLORES quality evaluation pipeline: FLORES source/reference, VM-local translation, hypothesis files, BLEU/chrF/COMET scoring, SQL/App Insights, and Grafana. |
| Figure 12 | `report_assets/fig12_flores_quality_dashboard.png` | Section 11, after the full FLORES results table | Grafana screenshot | FLORES quality dashboard showing BLEU, chrF, COMET, token throughput, and seconds per 1000 tokens. |
| Figure 13 | `report_assets/fig13_aws_azure_multicloud.png` | Section 12.1, after the cloud service mapping table | Drawn architecture comparison | Azure vs AWS API-based mBART deployment comparison with equivalent services and shared constants. |
| Figure 14 | `report_assets/fig14_aws_phase2_comparison.png` | Section 12.3, after the direct Azure vs AWS result summary table | Chart | Azure vs AWS Phase 2 comparison for runtime, throughput, HF API wait, SQL query time, and storage overhead. |
| Figure 15 | `report_assets/fig15_vm_translation_run_dashboard.png` | Section 13, after the dashboard artifacts table | Grafana screenshot | VM translation run analytics dashboard showing runtime, cache rate, NMT calls, TM hits, and per-language breakdown. |
| Figure 16 | `report_assets/fig16_observability_architecture.png` | Section 13, after the explanation of two dashboard families | Drawn architecture diagram | Observability architecture showing `RESEARCH_DATA`, `FLORES_QUALITY`, SQL tables, App Insights, and Grafana dashboards. |
| Figure 17 | `report_assets/fig17_bottleneck_inversion.png` | Section 15, after the bottleneck classification table | Concept diagram | Bottleneck inversion from API/network-bound execution to local compute-bound inference. |

Markdown image syntax to use in the generated report:

```markdown
![Figure X: Short title](report_assets/figXX_name.png)
```

The final report should reference each image in the text before showing it, for example: "Figure 5 shows the VM-local architecture used for the final implementation."

Files and sources used:

| Source | Purpose |
| --- | --- |
| `Cloud_Localization_Pipeline_Research_Report (1).pdf` | Previous 25-page report and mid-semester baseline |
| Older API baseline implementation | Our earlier mBART Hugging Face API implementation, used as the baseline |
| `API_based_HFace_AppInsight/` | Improved Hugging Face API implementation on Azure Pipelines |
| `Docker-Local-Inference/` | Docker local inference implementation using NLLB and CTranslate2 |
| `VM-Local-Inference/` | Final VM-local implementation and experiment harness |
| `VM-Local-Inference/experiments/results/azure_vm_D4as_v4.csv` | Actual uncached VM-local telemetry on Azure VM `D4as_v4` |
| `VM-Local-Inference/experiments/results/tm_pilot_for_dashboard/tm_experiment_summary.csv` | VM Translation Memory cache pilot with `0%` and `50%` seed rates |
| `VM-Local-Inference/experiments/results/tm_50pct_run/tm_experiment_summary.csv` | Validated VM `50%` cache run |
| `VM-Local-Inference/experiments/results/flores_full_quality_scores_enriched_20260424.csv` | Full FLORES quality and runtime benchmark |

## 1. Abstract

This project implements and evaluates a cloud localization pipeline for translating multilingual resource files. The pipeline begins with `.resx` files, converts them into `.xlf` XLIFF files, translates the text into multiple target languages, writes translations back into a Translation Memory stored in Azure SQL, uploads translated files to Azure Blob Storage, and emits detailed telemetry to Azure Application Insights and Grafana dashboards.

The work evolved through four meaningful implementation generations:

1. Our earlier implementation using the Hugging Face Inference API with `facebook/mbart-large-50-many-to-many-mmt`, fixed batching, raw SQL inserts, and limited telemetry.
2. A hardened Hugging Face API implementation on Azure Pipelines, still using mBART, but adding retry logic, token-aware batching, idempotent SQL `MERGE`, and a detailed `RESEARCH_DATA` telemetry payload.
3. A Docker local inference implementation on Azure Pipelines, where the model was moved from a remote API to a local CTranslate2 runtime using `facebook/nllb-200-distilled-600M` with INT8 quantization.
4. A VM-local inference implementation on Azure VM `D4as_v4`, preserving the optimized CTranslate2/NLLB path while removing Docker as a dependency and adding a full experiment layer for cache-hit studies, FLORES benchmarking, SQL-backed summaries, and Grafana dashboards.

The previous report already showed that moving from API-based inference to Docker-local inference improved throughput by removing external API wait and rate-limit behavior. The end-semester contribution extends this result by moving the local inference stack onto a dedicated Azure VM and adding controlled evaluation infrastructure. The actual VM-local `D4as_v4` telemetry shows an average uncached file time of `9.95 s` for 50-segment files and average throughput of `18,151.86 segments/hour`, compared with the corrected Docker Azure Pipeline export average of `19.07 s` and `9,462.43 segments/hour`. This is about a `1.92x` throughput improvement from Docker local inference on Azure Pipelines to VM-local inference on `D4as_v4`.

The VM experiment layer also validates the Translation Memory story. In a controlled VM-local cache pilot, moving from `0%` TM seeding to `50%` TM seeding reduced average file time from `10.0075 s` to `5.3327 s`, a `46.71%` reduction, and reduced total NMT calls from `7021` to `3514`, a `49.95%` reduction. Finally, the FLORES benchmark confirmed that the same VM-local stack can be evaluated not only for speed but also for translation quality, using BLEU, chrF, COMET, exact token counts, and runtime-normalized token throughput.

## 2. Introduction

Modern software products usually contain hundreds or thousands of user-facing strings. These strings are commonly stored in resource files such as `.resx`. For multilingual releases, every source string must be translated into all supported target languages. Manual translation is slow, expensive, and difficult to integrate into developer workflows. A CI/CD-driven localization pipeline can reduce this burden by automatically detecting source resource strings, converting them into a translation-friendly format, translating them, and producing localized output files.

The project focuses on a realistic enterprise localization problem:

- source strings are stored in `.resx` files
- intermediate translation files use `.xlf` XLIFF format
- Azure Blob Storage stores raw and translated files
- Azure SQL stores the Translation Memory cache
- Azure Pipelines or an Azure VM executes the translation workflow
- Application Insights and Grafana provide telemetry and dashboards

The main research question is not only whether translation can be automated. Our earlier implementation already proved that. The deeper question is how the architecture should evolve when the pipeline must become reliable, observable, faster, cheaper, and suitable for repeated research experiments.

The project therefore asks:

1. What are the bottlenecks in a remote API-based localization pipeline?
2. How much can production engineering improve the original API approach?
3. What changes when inference moves from a remote Hugging Face API to a local model?
4. Does running the local model directly on an Azure VM improve performance over Docker local inference on Azure Pipelines?
5. How much can Translation Memory cache hits reduce model inference cost?
6. Can performance telemetry and translation quality benchmarking be unified into a repeatable research workflow?

## 3. Repository Scope And Implementation Map

The final report should describe only the implementation paths that were actually relevant to the research evolution.

| Folder | Include in report? | Meaning |
| --- | --- | --- |
| Older API baseline implementation | Yes | Our earlier API-based baseline implementation |
| `API_based_HFace_AppInsight/` | Yes | Improved API-based Hugging Face implementation on Azure Pipelines |
| `Docker-Local-Inference/` | Yes | Docker-based local inference on Azure Pipelines |
| `VM-Local-Inference/` | Yes | Final VM-local approach and experiment framework |
| `VM_MbartModel/` | No | Explicitly ignored for the final report |

The wording in the report should be careful:

- "Earlier implementation" means our original API-based mBART implementation before the hardening and local-inference work.
- "API-based Hugging Face implementation" means `API_based_HFace_AppInsight/`.
- "Docker local inference" means `Docker-Local-Inference/`, where the model is downloaded/converted and then run locally through Docker.
- "VM-local inference" means `VM-Local-Inference/`, where the same local inference approach runs natively on Azure VM without requiring Docker.

## 4. End-To-End System Architecture

The pipeline is a three-stage localization system:

| Stage | File | Responsibility |
| --- | --- | --- |
| Phase 1 | `phase1.py` | Convert source `.resx` resources into `.xlf` files and upload raw XLIFF files |
| Phase 2 | `translation.py` | Translate each XLIFF segment, using TM cache first and NMT only on cache miss |
| Phase 3 | `phase3.py` | Merge translated XLIFF output back into localized `.resx` files |

The shared cloud architecture is:

1. The source repository contains English `.resx` resource files.
2. Phase 1 converts `.resx` files into XLIFF `.xlf` files.
3. Raw XLIFF files are uploaded to an Azure Blob Storage raw container.
4. Phase 2 downloads each raw `.xlf` file.
5. For each source segment, Azure SQL Translation Memory is queried.
6. On a cache hit, the stored translation is reused.
7. On a cache miss, the segment is sent to the selected inference provider.
8. The new translation is written back into Azure SQL.
9. The translated `.xlf` is uploaded to a translated Blob container.
10. Phase 3 merges the translated XLIFF back into localized `.resx`.
11. Telemetry is emitted to Application Insights and persisted into SQL tables for dashboards.

The inference provider changes across generations:

| Generation | Inference provider | Main bottleneck |
| --- | --- | --- |
| Earlier API version | Remote Hugging Face mBART API | Remote API wait and limited instrumentation |
| Improved API | Remote Hugging Face mBART API | Network I/O, API latency, rate-limit protection |
| Docker local | Local CTranslate2 NLLB inside Docker | Local CPU inference |
| VM local | Local CTranslate2 NLLB on Azure VM | Local CPU inference, now with better host control |

## 5. Architectural Evolution

### 5.1 Our Earlier API Implementation

The earlier implementation was a working proof of concept. It showed that resource strings could be translated end to end through Hugging Face. It used:

- Hugging Face Inference API
- `facebook/mbart-large-50-many-to-many-mmt`
- fixed `BATCH_SIZE = 50`
- direct `requests.post`
- raw SQL `INSERT`
- minimal metrics
- no cache-rate experiment harness
- no detailed micro-latency telemetry

Important short snippet:

```python
BATCH_SIZE = 50
for i in range(0, len(to_translate_texts), BATCH_SIZE):
    batch_texts = to_translate_texts[i : i + BATCH_SIZE]
    translations = translate_with_hf(batch_texts, target_lang, hf_token)
```

Source: older API baseline `translation.py`

The main limitation was not correctness but production readiness. A single API failure could lose a batch, repeated runs could fail on SQL primary-key conflicts, and the telemetry was too shallow to explain where time was spent.

### 5.2 Improved Hugging Face API Implementation

The next implementation preserved the remote API architecture but made it more production-like. It still used mBART through Hugging Face, but added:

- `BATCH_SIZE = 5`
- `TOKEN_LIMIT = 200`
- language token multipliers
- retry and fault handling for HTTP errors
- SQL `MERGE` for idempotent writes
- OpenCensus telemetry
- structured `RESEARCH_DATA` traces
- timing for SQL, API wait, blob I/O, XML parse/write, and telemetry overhead

Important short snippet:

```python
BATCH_SIZE = 5
TOKEN_LIMIT = 200
payload = {"inputs": texts, "parameters": {"src_lang": "en_XX", "tgt_lang": tgt_token}}
response = requests.post(api_url, headers=headers, json=payload, timeout=60)
```

Source: `API_based_HFace_AppInsight/translation.py`

The important design decision here was to improve reliability without changing the fundamental inference provider. This created a fair intermediate generation: the same remote API idea, but engineered better.

The current Hugging Face API implementation should be described through seven concrete engineering improvements:

| Improvement | What changed | Why it improved the pipeline |
| --- | --- | --- |
| Network resilience and retry logic | API calls were wrapped with retry handling for failed responses, timeouts, and removed endpoints. | A transient API problem no longer automatically loses a batch. |
| Dynamic token-aware batching | Fixed `BATCH_SIZE = 50` was replaced with smaller batches and `TOKEN_LIMIT = 200`. | Long strings no longer overload the API request payload. |
| Language-aware token heuristics | `LANG_TOKEN_MULTIPLIERS` estimate output token density for scripts such as Devanagari, Cyrillic, CJK, and Latin. | The batching logic becomes safer for languages that tokenize more densely. |
| Idempotent SQL writes | Raw `INSERT` was replaced with SQL `MERGE`. | Re-running the pipeline updates existing translations instead of failing on duplicate keys. |
| Enterprise observability | `PerformanceProfiler`, OpenCensus, and `RESEARCH_DATA` were added. | The system can explain where time is spent rather than only reporting total runtime. |
| XML structural safety | The code creates a missing `<target>` node before writing translation output. | Input XLIFF files with incomplete structure do not crash the run. |
| High-resolution micro-latency timing | SQL read/write, HF wait, XML parse/write, blob download/upload, sleep, and telemetry overhead are timed separately. | The bottleneck can be classified as API wait, SQL wait, storage wait, or compute. |

For the report, this section should make clear that the current Hugging Face implementation is still API-based, but it is not the same as the earlier version. It is a production-hardened API implementation whose main remaining limitation is external I/O wait.

### 5.3 Docker Local Inference

The Docker implementation changed the largest architectural variable: inference location. Instead of calling Hugging Face over the network, it downloaded and converted the NLLB model to CTranslate2 format, then ran inference locally.

Main changes:

- model changed to `facebook/nllb-200-distilled-600M`
- runtime changed to CTranslate2
- quantization changed to INT8
- inference moved from remote API to local CPU
- Docker packaged the runtime
- Azure Pipeline cache reused the converted model
- beam search was restored with `beam_size=5`
- `TOKEN_LIMIT` increased to `1024`
- `BATCH_SIZE` increased to `52`
- bulk SQL queries reduced per-segment SQL overhead
- `skip_special_tokens=True` prevented special-token leakage

The Docker implementation should be presented as a model-runtime redesign, not merely a packaging change. The important changes were:

| Docker change | Detail | Effect |
| --- | --- | --- |
| CTranslate2 runtime | Replaced API/PyTorch-style remote inference with a local C++ inference engine. | Removed network dependency during inference and made inference CPU-bound. |
| NLLB model upgrade | Switched from mBART-50 to `facebook/nllb-200-distilled-600M`. | Improved multilingual coverage and aligned with modern NLLB translation support. |
| INT8 quantization | Converted the CT2 model with `--quantization int8`. | Reduced memory footprint and made CPU inference practical. |
| Beam search restored | Used `beam_size=5` in CT2. | Improved translation search quality without the severe slowdown seen in less optimized paths. |
| Dual circuit breaker batching | Used both `TOKEN_LIMIT = 1024` and `BATCH_SIZE = 52`. | Protected against both long-sequence failures and padding explosion. |
| Pipeline model cache | Azure Pipeline `Cache@2` stores the converted `nllb_ct2` model. | Avoids repeated model conversion and reduces repeated build setup time. |
| Disk protection | Removes heavy PyTorch/Hugging Face cache after conversion. | Avoids build-agent disk exhaustion. |
| Bulk SQL lookup | Fetches many existing TM rows in one query. | Reduces SQL round trips compared with per-segment lookup. |
| Special-token cleanup | Uses truncation and `skip_special_tokens=True`. | Prevents model structural tokens from leaking into translated XLIFF. |

Important short snippet:

```python
tokenizer = AutoTokenizer.from_pretrained("facebook/nllb-200-distilled-600M")
translator = ctranslate2.Translator("nllb_ct2", device="auto", compute_type="int8")
```

Source: `Docker-Local-Inference/translation.py`

Docker local inference solved the API bottleneck. It removed API throttling, external endpoint availability risk, and per-token API cost. However, it still depended on Docker and Azure Pipeline agent characteristics. The final VM approach was added to remove that dependency and make the runtime more controllable.

### 5.4 VM-Local Inference

The VM-local implementation is the end-semester architecture. It keeps the same optimized model path as Docker but runs it directly on an Azure VM.

Main changes:

- no Docker dependency at runtime
- direct Python process on the VM
- same NLLB model and CT2 inference settings as Docker
- VM bootstrap script installs dependencies and converts the model
- optional FastAPI server mode exists through `vm_server.py`
- local-first bundle workflow prepares inputs before shipping them to the VM
- cache-rate experiments are controlled and repeatable
- FLORES quality benchmarking is integrated
- SQL and Application Insights telemetry are extended
- Grafana dashboards are built for run analytics and FLORES quality

What is different on the VM compared with Docker:

| VM difference | Explanation | Why it matters |
| --- | --- | --- |
| Native process instead of container | `translation.py` runs directly in the VM Python environment. | Removes Docker as a runtime dependency and simplifies VM operations. |
| Persistent model directory | The converted model lives at `VM-Local-Inference/nllb_ct2`. | Model setup does not need to happen from scratch for every experiment. |
| VM bootstrap script | `infra/bootstrap-vm.sh` installs packages, Python dependencies, ODBC, and converts the model if missing. | Makes VM setup reproducible. |
| Config-driven model loading | `ct2_model_path`, `hf_model_name`, `device`, and `compute_type` can come from `config.json`. | Easier to tune or verify settings for fair comparison. |
| Optional FastAPI service | `vm_server.py` exposes `/health` and `/translate`. | Supports a VM-hosted translation endpoint in addition to batch pipeline execution. |
| Systemd service option | `infra/vm-local-inference.service` defines a managed VM service for the API mode. | Allows the VM to run inference as a persistent service. |
| Local-first bundle workflow | Workload, TM seed corpus, and FLORES dataset can be prepared locally and uploaded as one archive. | Makes long experiments repeatable and less dependent on live downloads. |
| Experiment harness | TM cache sweeps and FLORES scoring live under `experiments/`. | Turns the VM implementation into a research platform, not only a translation script. |

Important short snippet:

```python
model_path = resolve_path(base_dir, config.get("ct2_model_path", "nllb_ct2"))
hf_model_name = config.get("hf_model_name", "facebook/nllb-200-distilled-600M")
translator = ctranslate2.Translator(model_path, device=device, compute_type=compute_type)
```

Source: `VM-Local-Inference/translation.py`

The VM approach is not just a port of Docker. It is an operational and experimental upgrade: the inference stack now runs on a persistent machine where we control setup, data movement, cached model state, experiment execution, and dashboards.

## 6. Fairness Of Docker vs VM Inference Settings

For a fair comparison, Docker local inference and VM-local inference must use the same NLLB settings. The current code does this.

| Setting | Docker local inference | VM-local inference | Fair comparison? |
| --- | --- | --- | --- |
| Model | `facebook/nllb-200-distilled-600M` | `facebook/nllb-200-distilled-600M` | Yes |
| Runtime | CTranslate2 | CTranslate2 | Yes |
| CT2 model path | `nllb_ct2` | `nllb_ct2` | Yes |
| Device | `auto` | `auto` | Yes |
| Compute type | `int8` | `int8` | Yes |
| `BATCH_SIZE` | `52` | `52` | Yes |
| `TOKEN_LIMIT` | `1024` | `1024` | Yes |
| Source language | `eng_Latn` | `eng_Latn` | Yes |
| Beam size | `5` | `5` | Yes |
| Batch type | `tokens` | `tokens` | Yes |
| Max batch size | `1024` | `1024` | Yes |
| Target prefix | NLLB target language token | NLLB target language token | Yes |
| Detokenization | Drop target prefix and skip special tokens | Same | Yes |

Important short snippet:

```python
results = translator.translate_batch(
    source_tokens,
    target_prefix=[[tgt_token]] * len(texts),
    beam_size=5,
    batch_type="tokens",
    max_batch_size=1024,
)
```

Source: `Docker-Local-Inference/translation.py` and `VM-Local-Inference/translation.py`

The difference is where settings are stored. Docker hardcodes more of them in `translation.py`, while the VM version reads model path, model name, device, and compute type from `config.json` or environment variables. Behavior is aligned.

## 7. Telemetry Framework

The telemetry framework is one of the strongest parts of the project. It changed the work from "we ran translation and it felt faster" into "we can prove where time went."

The main telemetry payload is emitted as:

```text
RESEARCH_DATA: {...}
```

The VM branch also emits benchmark-specific traces:

```text
FLORES_QUALITY: {...}
FLORES_QUALITY_SUMMARY: {...}
```

Major telemetry categories:

| Category | Example fields | Why it matters |
| --- | --- | --- |
| Run identifiers | `run_invocation_id`, `run_id`, `file_name`, `target_lang` | Correlates logs, SQL rows, files, and dashboards |
| Throughput | `total_time`, `throughput_seg_per_hr`, `throughput_input_tokens_per_hr` | Provides speed and scaling metrics |
| API resilience | `api_retries`, `api_failures`, `api_throttled_429`, `sleep_time_ms` | Shows remote API reliability and rate-limit behavior |
| Token profile | `tokens_processed_in_whole_language`, `char_per_token_ratio`, `text_expansion_ratio` | Explains language-specific cost |
| Cache efficiency | `nmt_calls`, `transational_memory_hits`, `cache_rate` | Measures TM value directly |
| Latency breakdown | `hf_api_wait_ms`, `sql_read_latency_ms`, `sql_write_latency_ms`, `blob_download_ms`, `blob_upload_ms` | Identifies bottlenecks |
| System health | `cpu_percent`, `memory_mb`, `serverless_cost_gb_sec` | Connects technical performance to cloud cost |

Short KQL-style structure:

```kusto
traces
| where message contains "RESEARCH_DATA"
| extend d = parse_json(tostring(split(message, "RESEARCH_DATA: ")[1]))
| project timestamp,
          target_lang = tostring(d.target_lang),
          total_time_s = todouble(d.total_time) / 1000.0,
          throughput = todouble(d.throughput_seg_per_hr),
          cache_rate = todouble(d.cache_rate)
```

This single telemetry shape allowed comparisons across API, Docker, and VM implementations. It also enabled the Grafana dashboards used for the final demonstration.

## 8. Performance Results Across Generations

### 8.0 Mid-Semester Baseline Tables Carried Forward

The final report should preserve the earlier report's baseline result tables because they explain where the end-semester work starts. These tables are especially important during evaluation because the examiner can see that the project did not begin directly with the VM solution. It moved through measurable generations.

Our earlier API implementation results:

| Language | Segments in earlier run | Throughput (seg/hr) | Avg latency (s) | Total time (s) |
| --- | ---: | ---: | ---: | ---: |
| `zh-CN` | `350` | `2641` | `1.1471` | `477.05` |
| `ru-RU` | `300` | `2571` | `1.1801` | `420.06` |
| `ja-JP` | `250` | `2529` | `1.2010` | `355.76` |
| `hi-IN` | `200` | `2456` | `1.2358` | `293.06` |
| `fr-FR` | `150` | `2469` | `1.2251` | `218.64` |
| `es-ES` | `100` | `2424` | `1.2409` | `148.46` |
| `de-DE` | `50` | `2682` | `1.0489` | `67.11` |

This is the "earlier implementation" baseline. The `zh-CN` run took `477.05 s`, which is the roughly `470 s` result that should be mentioned in the final report as the clearest example of the original slower architecture.

Improved Hugging Face API results from the previous report:

| Language | Time (s) | Throughput (seg/hr) | HF wait (s) | SQL (s) | Sleep (s) | Memory (MB) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `zh-CN` | `47.10` | `3821` | `35.66` | `8.85` | `2.0` | `60.37` |
| `ru-RU` | `54.91` | `3278` | `43.57` | `8.78` | `2.0` | `60.25` |
| `ja-JP` | `54.32` | `3313` | `42.89` | `8.86` | `2.0` | `60.25` |
| `hi-IN` | `65.86` | `2732` | `54.49` | `8.79` | `2.0` | `60.12` |
| `fr-FR` | `63.69` | `2825` | `52.31` | `8.75` | `2.0` | `60.12` |
| `es-ES` | `76.17` | `2363` | `64.74` | `8.78` | `2.0` | `60.00` |
| `de-DE` | `57.77` | `3115` | `46.30` | `8.78` | `2.0` | `59.75` |

Docker local inference results from the previous report:

| Language | Time (s) | Throughput (seg/hr) | Local inference (s) | Sleep (s) | Memory (MB) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `zh-CN` | `17.30` | `10402` | `16.58` | `0` | `1240.71` |
| `ru-RU` | `19.54` | `9211` | `18.83` | `0` | `1212.58` |
| `ja-JP` | `18.23` | `9874` | `17.51` | `0` | `1221.37` |
| `hi-IN` | `19.11` | `9420` | `18.39` | `0` | `1221.46` |
| `fr-FR` | `19.98` | `9010` | `19.26` | `0` | `1221.14` |
| `es-ES` | `18.74` | `9607` | `18.02` | `0` | `1250.36` |
| `de-DE` | `19.73` | `9123` | `18.98` | `0` | `1207.38` |

The final report should then transition from these mid-semester tables into the corrected end-semester Docker and VM telemetry in Sections 9 and 10.

### 8.1 High-Level Implementation Comparison

| Implementation | Folder | Model | Inference location | Avg throughput (seg/hr) | Avg 50-segment file time | Main bottleneck |
| --- | --- | --- | --- | --- | --- | --- |
| Earlier API baseline | Older API baseline implementation | mBART-50 | Hugging Face API | `2538.86` | varies by workload; de-DE `67.11 s` for 50 segments | API/network wait |
| Improved HF API | `API_based_HFace_AppInsight/` | mBART-50 | Hugging Face API | `3063.86` | `59.97 s` | API/network wait |
| Docker local inference | `Docker-Local-Inference/` | NLLB-200 distilled 600M | Docker on Azure Pipelines | `9462.43` | `19.07 s` | local CPU inference |
| VM-local inference | `VM-Local-Inference/` | NLLB-200 distilled 600M | Azure VM `D4as_v4` | `18151.86` | `9.95 s` | local CPU inference |

Key interpretation:

- Improved HF API is about `1.21x` average throughput over the earlier API baseline.
- Docker local inference is about `3.09x` average throughput over the improved HF API.
- VM-local inference is about `1.92x` average throughput over Docker local inference on Azure Pipelines.
- VM-local inference is about `5.92x` average throughput over the improved HF API.
- VM-local inference is about `7.15x` average throughput over the earlier API baseline.

### 8.2 Per-Language Throughput Comparison

| Language | Earlier API version seg/hr | Improved HF API seg/hr | Docker local Azure Pipeline seg/hr | VM-local D4as_v4 seg/hr |
| --- | ---: | ---: | ---: | ---: |
| `zh-CN` | `2641` | `3821` | `10335` | `19822` |
| `ru-RU` | `2571` | `3278` | `9221` | `17687` |
| `ja-JP` | `2529` | `3313` | `9943` | `19634` |
| `hi-IN` | `2456` | `2732` | `9359` | `18001` |
| `fr-FR` | `2469` | `2825` | `8953` | `16801` |
| `es-ES` | `2424` | `2363` | `9443` | `17823` |
| `de-DE` | `2682` | `3115` | `8983` | `17295` |
| **Average** | **`2538.86`** | **`3063.86`** | **`9462.43`** | **`18151.86`** |

### 8.3 Execution Time Comparison

The earlier implementation used varying segment counts in the previous report, so its total times should not be treated as perfectly apples-to-apples for every language. The later HF, Docker, and VM results are better aligned around 50-segment per-language XLIFF files.

| Language | Improved HF API time (s) | Docker local Azure Pipeline time (s) | VM-local D4as_v4 time (s) |
| --- | ---: | ---: | ---: |
| `zh-CN` | `47.10` | `17.42` | `9.08` |
| `ru-RU` | `54.91` | `19.52` | `10.18` |
| `ja-JP` | `54.32` | `18.10` | `9.17` |
| `hi-IN` | `65.86` | `19.23` | `10.00` |
| `fr-FR` | `63.69` | `20.10` | `10.71` |
| `es-ES` | `76.17` | `19.06` | `10.10` |
| `de-DE` | `57.77` | `20.04` | `10.41` |
| **Average** | **`59.97`** | **`19.07`** | **`9.95`** |

Interpretation:

- Docker reduced average file time from `59.97 s` to `19.07 s` compared with the improved HF API.
- VM-local D4as_v4 reduced average file time further from `19.07 s` to `9.95 s`.
- The VM-local time reduction compared with Docker Azure Pipeline is `47.82%`.
- The VM-local time reduction compared with improved HF API is `83.41%`.

## 9. Corrected Docker vs VM Telemetry Interpretation

The per-language table using `resx_file_10.*.xlf` rows was previously labeled as VM-local telemetry. That was incorrect. It is Docker local inference on Azure Pipelines.

Correct interpretation:

| Dataset | Actual meaning |
| --- | --- |
| `resx_file_10.*.xlf` table | Docker local inference on Azure Pipelines |
| `azure_vm_D4as_v4.csv` | VM-local inference on Azure VM `D4as_v4` |

### 9.1 Docker Local Inference On Azure Pipelines

| Language | File | Time (s) | Throughput (seg/hr) | Tokens | Time/token (ms) | NMT calls | TM hits | Cache rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `zh-CN` | `resx_file_10.zh-CN.xlf` | `17.42` | `10335` | `509.17` | `32.69` | `50` | `0` | `0%` |
| `ru-RU` | `resx_file_10.ru-RU.xlf` | `19.52` | `9221` | `1284.50` | `14.61` | `50` | `0` | `0%` |
| `ja-JP` | `resx_file_10.ja-JP.xlf` | `18.10` | `9943` | `580.23` | `29.96` | `50` | `0` | `0%` |
| `hi-IN` | `resx_file_10.hi-IN.xlf` | `19.23` | `9359` | `1761.67` | `10.51` | `50` | `0` | `0%` |
| `fr-FR` | `resx_file_10.fr-FR.xlf` | `20.10` | `8953` | `975.56` | `19.87` | `50` | `0` | `0%` |
| `es-ES` | `resx_file_10.es-ES.xlf` | `19.06` | `9443` | `969.17` | `18.92` | `50` | `0` | `0%` |
| `de-DE` | `resx_file_10.de-DE.xlf` | `20.04` | `8983` | `1005.31` | `19.13` | `50` | `0` | `0%` |

### 9.2 Azure VM D4as_v4 VM-Local Inference

| Language | File | Time (s) | Throughput (seg/hr) | Tokens | Time/token (ms) | NMT calls | TM hits | Cache rate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `zh-CN` | `resx_file_03.zh-CN.xlf` | `9.08` | `19822` | `512.08` | `17.53` | `50` | `0` | `0%` |
| `ru-RU` | `resx_file_03.ru-RU.xlf` | `10.18` | `17687` | `1271.25` | `7.92` | `50` | `0` | `0%` |
| `ja-JP` | `resx_file_03.ja-JP.xlf` | `9.17` | `19634` | `581.70` | `15.59` | `50` | `0` | `0%` |
| `hi-IN` | `resx_file_03.hi-IN.xlf` | `10.00` | `18001` | `1823.75` | `5.43` | `50` | `0` | `0%` |
| `fr-FR` | `resx_file_03.fr-FR.xlf` | `10.71` | `16801` | `964.31` | `11.01` | `50` | `0` | `0%` |
| `es-ES` | `resx_file_03.es-ES.xlf` | `10.10` | `17823` | `966.53` | `10.32` | `50` | `0` | `0%` |
| `de-DE` | `resx_file_04.de-DE.xlf` | `10.41` | `17295` | `996.56` | `10.32` | `50` | `0` | `0%` |

VM-local aggregate:

| Metric | Value |
| --- | ---: |
| Average total execution time | `9.9500 s` |
| Average throughput | `18151.8571 seg/hr` |
| Average tokens processed | `1016.5971` |
| Average time per token | `11.1600 ms` |
| Average compute time | `9.9014 s` |
| Average network wait | `0.0457 s` |
| Average local inference wait | `9.8414 s` |
| Average SQL query time | `0.0243 s` |
| Average memory | `1465.7357 MB` |
| Total NMT calls | `350` |
| Total TM hits | `0` |
| Cache rate | `0%` |

## 10. Translation Memory Cache Experiments

The VM-local branch adds a controlled Translation Memory experiment layer. This is important because a production localization system should not translate the same string repeatedly. Every cache hit avoids model inference, lowers latency, and lowers compute cost.

The experiment workflow:

1. Build a workload catalog from source `.resx` strings.
2. Build or reuse a full seed corpus of translations.
3. Prepare controlled TM seed rates.
4. Run the same pipeline with different cache states.
5. Persist results to SQL and emit dashboard telemetry.

Prepared seed rates:

| Requested seed rate | Seeded rows |
| ---: | ---: |
| `0%` | `0` |
| `10%` | `700` |
| `20%` | `1400` |
| `25%` | `1750` |
| `30%` | `2100` |
| `40%` | `2807` |
| `50%` | `3507` |

Validated pilot summary:

| Seed rate | Avg total time (s) | Avg compute time (s) | Avg cache rate (%) | Avg inference wait (s) | Total NMT calls | Avg total tokens |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `0%` | `10.0075` | `9.9600` | `0.0000` | `9.9028` | `7021` | `1007.9152` |
| `50%` | `5.3327` | `5.2939` | `49.9554` | `5.2347` | `3514` | `1007.9152` |

Interpretation:

- `50%` TM seeding reduced average file time by `46.71%`.
- `50%` TM seeding reduced NMT calls by `49.95%`.
- The realized cache rate, `49.9554%`, closely matched the requested `50%`.
- The unchanged token load shows that the workload remained comparable; the difference came from serving roughly half the segments from SQL instead of the model.

Per-language `50%` run:

| Language | Files processed | Avg total time (s) | Avg compute time (s) | Avg cache rate (%) | Avg inference wait (s) | Total NMT calls | Avg total tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `de-DE` | `20` | `5.5325` | `5.4812` | `49.9471` | `5.4198` | `502` | `994.4125` |
| `es-ES` | `20` | `5.3386` | `5.3039` | `49.9394` | `5.2425` | `502` | `957.1833` |
| `fr-FR` | `20` | `5.7369` | `5.6931` | `49.9606` | `5.6333` | `502` | `969.0306` |
| `hi-IN` | `20` | `5.3765` | `5.3321` | `49.9510` | `5.2838` | `502` | `1773.2250` |
| `ja-JP` | `20` | `5.0397` | `5.0080` | `49.9511` | `4.9471` | `502` | `576.7818` |
| `ru-RU` | `20` | `5.5182` | `5.4847` | `49.9779` | `5.4225` | `502` | `1283.3500` |
| `zh-CN` | `20` | `4.7868` | `4.7540` | `49.9607` | `4.6939` | `502` | `501.4229` |

Important short snippet:

```python
parser.add_argument("--rates", nargs="+", default=["0", "10", "20", "25", "30", "40", "50"])
parser.add_argument("--implementation-name", default="vm_local_ct2")
```

Source: `VM-Local-Inference/experiments/run_full_cache_workflow.py`

## 11. FLORES Quality Benchmark

Performance alone is not enough for a translation report. A model can be fast but poor in quality. The VM-local branch therefore adds FLORES benchmarking.

FLORES workflow:

1. Download FLORES source and reference text.
2. Run the VM-local NLLB/CTranslate2 model.
3. Save hypotheses per target language.
4. Score with BLEU, chrF, and COMET.
5. Compute exact source and hypothesis token counts.
6. Emit SQL and Application Insights telemetry.
7. Visualize results in Grafana.

Why use multiple metrics:

- BLEU is useful for Latin-script languages where tokenization aligns better with words.
- chrF is more useful for Chinese and Japanese because character-level overlap is more stable.
- COMET is a learned metric that better reflects semantic adequacy.
- Runtime per 1000 tokens makes quality evaluation comparable to performance telemetry.

Metric explanation for the final report:

| Metric | What it measures | How to interpret it in this project |
| --- | --- | --- |
| BLEU | N-gram overlap between machine translation and reference translation, usually at word/subword level. | Higher is better. It is useful for `de-DE`, `es-ES`, `fr-FR`, `hi-IN`, and `ru-RU`, but can understate quality for CJK languages. |
| chrF | Character n-gram F-score between hypothesis and reference. | Higher is better. It is better suited for `zh-CN` and `ja-JP` because character-level overlap is more meaningful than whitespace-token overlap. |
| COMET | Neural quality estimation/reference-based metric trained to correlate with human judgments. | Higher is better. It captures semantic adequacy better than simple overlap metrics, so it is useful when BLEU is low but the translation may still preserve meaning. |
| Seconds per 1000 combined tokens | Runtime normalized by exact source plus hypothesis token count. | Lower is better. It lets the report compare FLORES runtime with RESX runtime even though FLORES has far more sentences. |

The COMET model used in the scoring script is `Unbabel/wmt22-comet-da`. The scorer downloads it through COMET, loads it from checkpoint, and evaluates triples of source sentence, machine translation, and reference translation:

```python
parser.add_argument("--comet-model", default="Unbabel/wmt22-comet-da")
comet_inputs = [{"src": src, "mt": hyp, "ref": ref} for src, hyp, ref in zip(...)]
comet_output = comet_model.predict(comet_inputs, batch_size=8, gpus=0)
```

This is important because BLEU and chrF are surface-overlap metrics, while COMET is model-based. In the final report, BLEU/chrF should be presented as lexical similarity metrics and COMET should be presented as a semantic quality metric. For Chinese and Japanese, the report should emphasize `chrF` as the primary lexical metric and use COMET to support the interpretation.

Full FLORES results:

| Language | Sentences | BLEU | chrF | COMET | Primary metric | Elapsed (s) | Combined tokens | Tokens/sec | Sec/1000 tokens |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| `de-DE` | `1012` | `34.3683` | `61.7091` | `0.8588` | `BLEU` | `830.1997` | `71233` | `85.80` | `11.65` |
| `es-ES` | `1012` | `26.5293` | `54.7761` | `0.8531` | `BLEU` | `757.0881` | `68745` | `90.80` | `11.01` |
| `fr-FR` | `1012` | `47.0046` | `68.6165` | `0.8684` | `BLEU` | `856.4435` | `73119` | `85.38` | `11.71` |
| `hi-IN` | `1012` | `30.9825` | `56.4416` | `0.8007` | `BLEU` | `778.8081` | `70110` | `90.02` | `11.11` |
| `ja-JP` | `1012` | `0.2900` | `29.7967` | `0.8678` | `chrF` | `695.6388` | `64298` | `92.43` | `10.82` |
| `ru-RU` | `1012` | `27.2885` | `54.8241` | `0.8697` | `BLEU` | `832.6185` | `71732` | `86.15` | `11.61` |
| `zh-CN` | `1012` | `1.3562` | `24.6801` | `0.7869` | `chrF` | `598.8157` | `59729` | `99.75` | `10.03` |

Aggregate FLORES summary:

| Metric | Value |
| --- | ---: |
| Languages | `7` |
| Sentences per language | `1012` |
| Average BLEU | `23.9742` |
| Average chrF | `50.1206` |
| Average COMET | `0.8436` |
| Combined token runtime range | about `10-12 s` per 1000 combined tokens |

Important short snippet:

```python
primary_metric_name = "chrF" if target_lang in PRIMARY_CHRF_LANGS else "BLEU"
combined_tokens_exact = src_tokens_exact + hypothesis_tokens_exact
seconds_per_1000_combined_tokens = round((elapsed_seconds / combined_tokens_exact) * 1000, 4)
```

## 11.5 Automated Quantization Pipeline Benchmarking

To ensure that the INT8 quantization decision is continuously validated against the unquantized baseline, an automated benchmark stage was introduced into the Azure DevOps CI/CD pipeline. 

### Implementation Details:
1. **Dynamic FLORES-200 Integration:** The benchmark script (`quantization_benchmark.py`) dynamically pulls the official `flores200_dataset.tar.gz` from Meta's S3 bucket, bypassing deprecations in Hugging Face's dataset libraries. It extracts the `dev` split to evaluate 200 English-to-Spanish translation pairs.
2. **Quantization Comparison:** The script leverages CTranslate2 to convert the `facebook/nllb-200-distilled-600M` model on-the-fly. To save pipeline execution time, the benchmark strictly compares the full-precision `float32` model against the compressed `int8` model.
3. **Automated Quality Scoring:** The benchmark evaluates quality automatically using `sacrebleu` (to compute BLEU and chrF scores) and `unbabel-comet` (using the `wmt22-comet-da` model to predict semantic quality). 
4. **Agent Disk Cleanup Optimization:** The pipeline agent frequently suffered from disk space exhaustion (`Free disk space on / is lower than 5%`) due to the massive size of the NLLB and COMET models. Two optimizations were implemented to resolve this:
   - A pipeline-level step removes bloated pre-installed directories (like Android SDKs, Haskell, and .NET) before Python starts, freeing ~20GB of disk space.
   - The Python script explicitly performs garbage collection, deleting the heavy CTranslate2 model artifacts (`shutil.rmtree`) immediately after benchmarking each quantization level.
5. **Telemetry:** The `int8` and `float32` benchmark results—including `bleu_score`, `chrf_score`, `comet_score`, `throughput_sentences_per_sec`, and `peak_memory_mb`—are bundled into a unified JSON `RESEARCH_DATA` payload and flushed to Azure Application Insights. This allows for direct side-by-side visualization of quantization degradation versus performance gains within Grafana/Kusto.

Source: `VM-Local-Inference/experiments/flores/score_quality.py`

## 12. Multi-Cloud AWS Deployment Result For mBART Hugging Face API

After comparing the local Azure Pipeline and VM-local versions, the project also includes a multi-cloud deployment comparison for the mBART Hugging Face API version. This should be placed after the main architecture evolution and result sections because it answers a different research question: if the translation model and application logic are kept the same, can the cloud execution environment still influence performance?

The AWS result is based on:

- source workload: `resx_file_10.resx`
- target languages: `de-DE`, `es-ES`, `fr-FR`, `hi-IN`, `ja-JP`, `ru-RU`, `zh-CN`
- workload size: `50` segments per language
- model: `facebook/mbart-large-50-many-to-many-mmt`
- inference provider: Hugging Face Inference API
- batching strategy: token-budget batching
- token limit: `TOKEN_LIMIT = 200`
- cache state: `0%` TM hit rate, so every segment follows the NMT path

### 12.1 Cloud Service Mapping

| Pipeline function | Azure implementation | AWS implementation | Why comparable |
| --- | --- | --- | --- |
| CI/CD orchestration | Azure DevOps Pipeline | AWS CodePipeline + CodeBuild | Both automate the same three-phase localization workflow |
| Intermediate XLIFF storage | Azure Blob Storage | Amazon S3 | Both store raw and translated XLIFF artifacts between phases |
| Translation Memory | Azure SQL Database | Amazon RDS for SQL Server | Both provide exact-match SQL TM lookup using the same schema logic |
| Translation engine | Hugging Face Inference API mBART | Hugging Face Inference API mBART | Same model provider and same translation path |
| Phase execution | Python `phase1.py`, `translation.py`, `phase3.py` | Python `phase1.py`, `translation.py`, `phase3.py` | Same localization workflow and TM-first strategy |
| Final output | Localized `.resx` files | Localized `.resx` files | Same final artifact type |

### 12.2 Why The Azure vs AWS Comparison Is Fair

The comparison is functionally aligned because both clouds used:

- the same source file: `resx_file_10.resx`
- the same seven target languages
- the same Hugging Face mBART model
- the same API-based translation path
- the same exact-match Translation Memory policy
- the same token-budget batching with `TOKEN_LIMIT = 200`
- the same `0%` cache-hit state
- the same final localized `.resx` artifact type

The infrastructure products are not identical, but they are cloud-native equivalents. Therefore, the comparison is fair at the workflow level: the translation logic is held constant while the cloud execution environment changes.

### 12.3 Direct Azure vs AWS Result Summary

| Metric | Azure API mBART | AWS API mBART | Direction |
| --- | ---: | ---: | --- |
| Mean runtime (s) | `59.97` | `42.95` | AWS faster |
| Mean throughput (seg/hr) | `3063.86` | `4271.00` | AWS higher |
| Mean HF API wait (ms) | `48565.71` | `39943.45` | AWS lower |
| Mean SQL query time (ms) | `8841.43` | `795.90` | AWS much lower |
| Mean storage download (ms) | `91.54` | `46.72` | AWS lower |
| Mean storage upload (ms) | `102.84` | `82.23` | AWS lower |
| Mean TM hit rate (%) | `0.00` | `0.00` | Same |

Key findings:

- AWS achieved about `39.40%` higher throughput than Azure on the matched 50-segment `resx_file_10` workload.
- AWS reduced mean runtime by about `28.38%`.
- AWS reduced mean Hugging Face API wait time by about `17.75%`.
- AWS reduced mean SQL query time by about `91.00%`.
- Both clouds had `0%` TM hit rate, so cache reuse does not explain the performance gap.

### 12.4 AWS Per-Language Phase 2 Results

| Language | File | Runtime (s) | Throughput (seg/hr) | HF API wait (ms) | SQL query (ms) | Time/token (ms) | NMT calls | TM hits |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `de-DE` | `resx_file_10.de-DE.xlf` | `39.24` | `4586` | `36353.05` | `677.50` | `37.77` | `50` | `0` |
| `es-ES` | `resx_file_10.es-ES.xlf` | `54.93` | `3276` | `51576.50` | `1119.38` | `43.33` | `50` | `0` |
| `fr-FR` | `resx_file_10.fr-FR.xlf` | `42.13` | `4272` | `39003.16` | `915.28` | `40.15` | `50` | `0` |
| `hi-IN` | `resx_file_10.hi-IN.xlf` | `48.40` | `3719` | `45479.81` | `714.44` | `24.98` | `50` | `0` |
| `ja-JP` | `resx_file_10.ja-JP.xlf` | `40.54` | `4439` | `37714.21` | `629.71` | `64.97` | `50` | `0` |
| `ru-RU` | `resx_file_10.ru-RU.xlf` | `40.64` | `4428` | `37812.66` | `613.53` | `29.83` | `50` | `0` |
| `zh-CN` | `resx_file_10.zh-CN.xlf` | `34.77` | `5177` | `31664.78` | `901.46` | `62.32` | `50` | `0` |

### 12.5 Multi-Cloud Interpretation

The AWS result does not mean that the translation model changed. It means the same API-based mBART workflow behaved differently when the surrounding cloud services changed.

The largest gap was SQL query time. Azure mean SQL query time was `8841.43 ms`, while AWS mean SQL query time was `795.90 ms`. Since every language run performs Translation Memory lookup before translation, SQL lookup behavior directly affects total Phase 2 runtime.

The second gap was Hugging Face API wait. Azure mean HF wait was `48565.71 ms`, while AWS mean HF wait was `39943.45 ms`. Because the API-based architecture is wait-bound, even moderate differences in external API wait become visible in total throughput.

Storage overhead also moved in the same direction: AWS had lower mean download and upload times. Storage was not the largest contributor, but it reinforced the same runtime trend instead of canceling it.

The report conclusion for this section should be:

> In an API-driven localization pipeline, cloud-native execution environment can materially influence benchmark outcomes even when the translation model, source file, target languages, batching strategy, Translation Memory policy, and final artifact type are held constant.

Suggested image for this section:

- Draw a two-column Azure vs AWS architecture diagram.
- Left side: Azure DevOps, Blob Storage, Azure SQL, Hugging Face API, App Insights.
- Right side: CodePipeline/CodeBuild, S3, RDS SQL Server, Hugging Face API, CloudWatch or exported CSV telemetry.
- Put `resx_file_10.resx`, `mBART`, `TOKEN_LIMIT=200`, and `0% TM hit rate` in the center as shared constants.
- Use arrows to show that only cloud-native orchestration/storage/database services changed.

## 13. Dashboard And Observability Layer

The VM branch includes dashboard work because the final system is not just a script. It is an observable experiment platform.

Dashboard artifacts:

| Dashboard | File | Purpose |
| --- | --- | --- |
| Translation telemetry dashboard | `VM-Local-Inference/grafana/translation-telemetry-dashboard.json` | Per-file runtime, tokens, latency, CPU, memory |
| Translation run analytics dashboard | `VM-Local-Inference/grafana/translation-run-analytics-dashboard.json` | SQL-backed run and cache experiment summaries |
| FLORES quality dashboard | `VM-Local-Inference/grafana/flores-quality-dashboard.json` | BLEU, chrF, COMET, runtime, token throughput |

Why two dashboard families exist:

- RESX/XLF pipeline telemetry is run-centric and file-centric.
- FLORES telemetry is experiment-centric and benchmark-language-centric.
- Mixing them in one dashboard makes demos confusing.
- Separate dashboards make it clear whether the story is operational performance or translation quality.

### Screenshot Placeholder 1: VM Translation Run Dashboard

Paste Grafana screenshot here.

Suggested caption:

> Grafana run analytics dashboard showing VM-local pipeline execution time, cache rate, NMT calls, TM hits, and per-language runtime breakdown for the Azure VM implementation.

### Screenshot Placeholder 2: FLORES Quality Dashboard

Paste Grafana screenshot here.

Suggested caption:

> Dedicated FLORES quality dashboard showing BLEU, chrF, COMET, token throughput, and seconds per 1000 tokens for the VM-local NLLB/CTranslate2 model.

### Screenshot Placeholder 3: VM Resource Or Runtime Dashboard

Paste Grafana or Azure VM screenshot here if available.

Suggested caption:

> VM runtime and system resource view showing that local inference runs as a compute-bound process with stable memory usage and no external API throttling.

### Suggested Diagrams To Draw For The Final Report

The final PDF should include several drawn figures, not only screenshots. These can be made in draw.io, PowerPoint, Lucidchart, or any diagram tool.

| Figure | What to draw | Where to use it |
| --- | --- | --- |
| End-to-end localization pipeline | Source repo -> Phase 1 `.resx` to `.xlf` -> raw storage -> TM lookup -> inference provider -> translated storage -> Phase 3 localized `.resx` -> telemetry. | System architecture section |
| Four-generation evolution diagram | A horizontal timeline: earlier API version -> current HF API -> Docker local inference -> VM-local inference. Show the model/runtime and bottleneck under each stage. | Architecture evolution section |
| API vs local inference bottleneck diagram | Left: API path with large network wait arrow. Right: local NLLB path with large CPU compute block. | Bottleneck analysis section |
| Docker vs VM deployment diagram | Docker side: Azure Pipeline agent, Docker image, cached `nllb_ct2`. VM side: Azure VM, Python venv, persistent `nllb_ct2`, experiment scripts, Grafana/SQL outputs. | Docker and VM comparison section |
| Translation Memory cache flow | Source segment -> SQL TM lookup -> hit goes directly to target -> miss goes to NMT -> save translation -> target. | TM experiment section |
| FLORES evaluation flow | FLORES source/reference -> VM-local translation -> hypothesis files -> BLEU/chrF/COMET scoring -> SQL/App Insights -> Grafana. | Quality benchmark section |
| Multi-cloud comparison | Azure column and AWS column with matched constants in the center: `resx_file_10`, mBART, HF API, `TOKEN_LIMIT=200`, `0%` TM hits. | AWS multi-cloud section |
| Observability architecture | Translation run emits `RESEARCH_DATA`; FLORES emits `FLORES_QUALITY`; both go to SQL/App Insights; Grafana reads them. | Dashboard/observability section |

For the architecture diagrams, use consistent visual language:

- blue boxes for Azure services
- orange boxes for AWS services
- green boxes for model/inference components
- gray boxes for files/artifacts
- purple boxes for telemetry/dashboards
- dashed arrows for telemetry
- solid arrows for data flow

This will make the report look planned instead of becoming a collection of unrelated screenshots.

## 14. Engineering Decisions And Rationale

### 14.1 Why Move Away From The Earlier API Baseline?

The earlier implementation was useful because it proved feasibility. But it was not enough for production or research because:

- batching was fixed and not token-aware
- SQL writes used raw inserts
- retries were absent
- API failure handling was limited
- telemetry could not isolate bottlenecks
- cache behavior could not be studied cleanly

The goal was not to criticize the baseline. It was to use it as the starting point.

### 14.2 Why Improve The HF API Before Replacing It?

The improved Hugging Face API implementation was necessary because it separated two questions:

1. How fast is the API architecture if engineered properly?
2. How much faster is local inference when the API bottleneck is removed?

Without the improved API generation, Docker would only be compared against a weak baseline. The hardened HF implementation made the comparison fairer.

### 14.3 Why Use SQL MERGE?

The raw `INSERT` approach fails when a translation already exists. Localization pipelines are often rerun. A rerun should update or reuse state, not crash.

Short snippet:

```sql
MERGE INTO Translations AS target
USING (SELECT ? AS SourceText, ? AS TargetLang) AS source
ON (target.SourceText = source.SourceText AND target.TargetLang = source.TargetLang)
WHEN MATCHED THEN UPDATE SET TranslatedText = ?
WHEN NOT MATCHED THEN INSERT (...)
```

The design decision is idempotency. Repeated pipeline runs become safe.

### 14.4 Why Move To NLLB And CTranslate2?

mBART through Hugging Face was simple but slow and externally dependent. NLLB-200 distilled 600M with CTranslate2 gave:

- wider multilingual coverage
- local execution
- better CPU performance
- INT8 quantization support
- beam search at practical speed
- no API rate limit
- no per-token API billing

This was the main architectural turning point.

### 14.5 Why INT8 Quantization?

INT8 reduces memory footprint and improves CPU efficiency. In this project it allowed the local model to run inside pipeline/VM constraints with roughly 1.2 to 1.5 GB memory use. This makes self-hosted inference practical without requiring a GPU.

### 14.6 Why Keep Beam Size 5?

Beam search was expensive in the API/PyTorch path. CTranslate2 makes it practical. Keeping `beam_size=5` improves translation quality while retaining strong throughput.

### 14.7 Why Add Dual Circuit Breakers?

Batching can fail in two ways:

- a single long segment can exceed model sequence length
- too many short strings can create a large padded tensor

The solution is to guard both:

| Guard | Value | Controls |
| --- | ---: | --- |
| `TOKEN_LIMIT` | `1024` | maximum token length |
| `BATCH_SIZE` | `52` | maximum number of segments |

This avoids both long-sequence failures and padding explosion.

### 14.8 Why VM Instead Of Only Docker?

Docker solved the API problem but still left deployment and experiment constraints:

- Docker runtime availability can be an operational dependency.
- Azure Pipeline agents are ephemeral and less controllable.
- Large model setup and conversion need careful caching.
- Long experiment runs are easier to manage on a persistent VM.
- Cache and FLORES workflows need stable local storage and reproducible execution.

The VM approach keeps the optimized inference path while making the execution environment more controllable.

### 14.9 Why Local-First Bundle Workflow?

FLORES download, seed corpus preparation, and RESX workload packaging can be slow or network-sensitive. The local-first bundle workflow prepares inputs once and uploads a controlled archive to the VM.

Short snippet:

```bash
bash infra/run-cache-experiment-from-bundle.sh \
  ~/cloud_rp/VM-Local-Inference \
  /tmp/local_bundle.tar.gz \
  ~/cloud_rp/VM-Local-Inference/experiments/output/from_local_bundle \
  ~/cloud_rp/VM-Local-Inference/config.json
```

This makes experiments repeatable and reduces "it worked on one machine" uncertainty.

## 15. Bottleneck Analysis

The core finding across the project is bottleneck inversion.

| Implementation | Dominant wait | Classification | Scaling strategy |
| --- | --- | --- | --- |
| Earlier API version | Hugging Face API/network wait | I/O-bound | hard to scale without API throttling |
| Improved HF API | HF API wait plus rate-limit sleep | I/O-bound | limited by external service |
| Docker local inference | local model compute | compute-bound | scale with CPU/GPU and better host |
| VM-local inference | local model compute | compute-bound | scale VM size, CPU, GPU, batching, parallelism |

This matters because I/O-bound and compute-bound systems scale differently:

- I/O-bound API pipelines scale only until the external API throttles or fails.
- Compute-bound local pipelines scale by adding predictable hardware.
- The VM approach is therefore easier to reason about operationally.

The pipeline did not merely become faster. It changed category.

## 16. Cost And Reliability Interpretation

### 16.1 API-Based Cost And Reliability

API-based inference is easy to start but has hidden production costs:

- remote network latency
- model endpoint availability risk
- request throttling
- per-token or per-call billing risk
- limited control over model runtime
- harder reproducibility

The improved HF API implementation reduced failure risk through retries and telemetry, but it could not remove the external dependency.

### 16.2 Docker Local Cost And Reliability

Docker local inference removes per-token API cost and external throttling. Its costs become:

- model download/conversion time
- local CPU time
- memory footprint
- Docker image/runtime management
- build agent disk constraints

The Azure Pipeline cache and disk cleanup were added to control these costs.

### 16.3 VM-Local Cost And Reliability

VM-local inference makes the runtime persistent and controllable. Its costs become:

- VM compute cost
- disk for model and data bundles
- VM bootstrap and maintenance
- model conversion once per VM unless refreshed

Its reliability benefits:

- no dependency on remote inference API
- no Docker dependency for the main run
- stable model path
- controlled experiment inputs
- easier long-running experiments
- direct integration with SQL, App Insights, and Grafana

## 17. Security And Configuration Notes

The report should not paste real secrets. Some config files in the repository contain connection strings or tokens. In the final report, use placeholders:

```json
{
  "blob_connection_string": "<AZURE_STORAGE_CONNECTION_STRING>",
  "sql_conn_str": "<AZURE_SQL_CONNECTION_STRING>",
  "app_insights_connection_string": "<APP_INSIGHTS_CONNECTION_STRING>"
}
```

Azure Pipelines and VM scripts should be described as injecting secrets through environment variables or secure files. Do not include real access keys, SQL passwords, Hugging Face tokens, SSH private keys, or Application Insights connection strings in the submitted report.

## 18. Limitations And Honest Boundaries

The report should be honest about what has been validated.

Validated:

- earlier API baseline is documented
- improved HF API implementation is documented
- Docker local inference is documented
- Docker vs VM NLLB settings are aligned
- VM-local D4as_v4 uncached telemetry is available
- TM seed preparation exists for `0, 10, 20, 25, 30, 40, 50%`
- `0%` and `50%` VM cache pilot results are validated
- full `50%` VM run is validated
- full FLORES benchmark is validated for 7 languages
- SQL telemetry and Grafana dashboards are implemented

Boundaries:

- not every cache percentage has been executed end to end as a completed run
- VM GPU acceleration has not yet been evaluated
- quality metrics are automatic metrics, not human evaluation
- VM cost analysis should be treated as preliminary unless exact Azure billing data is attached
- Docker Azure Pipeline and VM D4as_v4 use the same model settings, but the host hardware differs

These limitations are acceptable. They make the report stronger because they show scientific discipline.

## 19. Suggested 30-Page Report Structure

This is a suggested page budget for the final PDF.

| Section | Pages | Content |
| --- | ---: | --- |
| Title, certificate, acknowledgement, abstract | 3 | Similar to previous report, updated to include VM-local |
| Introduction | 2 | Problem, motivation, research questions |
| System architecture | 3 | End-to-end pipeline, Azure services, `.resx` to `.xlf` to `.resx` |
| Previous work and earlier baseline | 2 | Earlier implementation, limitations, 470 s class result |
| Improved HF API implementation | 3 | Retry logic, token batching, MERGE, telemetry, results |
| Docker local inference | 3 | NLLB, CTranslate2, INT8, Docker pipeline, corrected Docker telemetry |
| VM-local inference | 4 | VM architecture, bootstrap, config, same model settings, D4as_v4 telemetry |
| TM cache experiments | 3 | Seed corpus, cache rates, 0% vs 50%, NMT calls reduction |
| FLORES quality benchmark | 3 | BLEU, chrF, COMET, token-normalized runtime |
| Dashboards and observability | 2 | Grafana screenshots, SQL tables, App Insights |
| Comparative analysis | 2 | Throughput, time, bottleneck inversion, cost/reliability |
| Limitations and future work | 1 | GPU, full cache sweep, parallelism |
| Conclusion and references | 2 | Final claims and citations |

Total: about 30 pages.

## 20. Figure And Table Checklist

Recommended figures:

| Figure | Suggested source |
| --- | --- |
| End-to-end architecture diagram | Reuse or redraw from previous report |
| Throughput comparison bar chart | Table in Section 8.2 |
| Execution time comparison bar chart | Table in Section 8.3 |
| Docker vs VM telemetry chart | Section 9 |
| TM cache effect chart | Section 10 |
| FLORES quality dashboard screenshot | Grafana |
| Translation run analytics screenshot | Grafana |
| Bottleneck inversion diagram | Section 14 |

Recommended tables:

| Table | Suggested source |
| --- | --- |
| Implementation map | Section 3 |
| Fairness settings for Docker vs VM | Section 6 |
| High-level implementation comparison | Section 8.1 |
| Per-language throughput comparison | Section 8.2 |
| Per-language VM D4as telemetry | Section 9.2 |
| TM seed preparation | Section 10 |
| TM 0% vs 50% result | Section 10 |
| FLORES benchmark result | Section 11 |
| Bottleneck classification | Section 14 |

## 21. Report-Ready Conclusion

This project evolved a cloud localization pipeline from a functional API-based proof of concept into a measured, self-hosted, VM-local translation and evaluation platform. The earlier implementation established that automated `.resx` to `.xlf` translation through Hugging Face was possible, but it lacked the reliability and observability needed for production-scale evaluation. The improved Hugging Face implementation added retries, token-aware batching, idempotent SQL writes, and detailed telemetry, proving that production engineering could improve the baseline while still remaining limited by remote API latency.

The Docker local inference generation then changed the architecture by moving inference into a local CTranslate2 runtime with NLLB-200 distilled 600M and INT8 quantization. This removed API throttling and external inference dependency, improving throughput to roughly `9,462 seg/hr` in the corrected Docker Azure Pipeline telemetry. The final VM-local approach preserved the same NLLB/CTranslate2 settings but moved execution onto Azure VM `D4as_v4`, where uncached throughput reached `18,151.86 seg/hr` and average 50-segment file time dropped to `9.95 s`.

The end-semester contribution is therefore not only a faster runtime. It is a more complete research platform. The VM branch adds controlled Translation Memory cache experiments, full FLORES quality benchmarking, SQL-backed summaries, Application Insights traces, and Grafana dashboards. The validated `50%` TM cache run nearly halves average file time and NMT calls, while the FLORES benchmark adds quality evidence through BLEU, chrF, COMET, and token-normalized runtime. Together, these results show a clear architectural progression: from remote API dependency, to local inference, to VM-native experiment-grade localization infrastructure.

## 22. References To Include

Suggested references for the final report:

1. OASIS XLIFF Version 1.2 Specification.
2. Meta AI, No Language Left Behind: Scaling Human-Centered Machine Translation.
3. CTranslate2 project documentation.
4. Hugging Face Inference API documentation.
5. Microsoft Azure Blob Storage documentation.
6. Microsoft Azure SQL documentation.
7. Azure Application Insights documentation.
8. OpenCensus Python instrumentation documentation.
9. Grafana dashboard documentation.
10. FLORES-200 benchmark documentation.
11. BLEU metric reference.
12. chrF metric reference.
13. COMET metric reference.
