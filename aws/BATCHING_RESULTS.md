# Batching optimization — results record

Track 3 (CI/CD & memory optimization): making Phase 2 faster by improving how sentences are batched for the translation model. This file records each experiment: what was wrong before, what we changed, how it was measured, and the measured result.

## 1. The flaw before: how batching worked

Phase 2 (`translator/translation.py`) batched like this:

1. Work through **one file and one language at a time**.
2. Fill a batch with untranslated strings in file order until it reaches **52 strings**, or about **1024 tokens**. Tokens were *estimated* as characters ÷ 4, not counted.
3. Send the batch to CTranslate2 with **beam size 5** (5 candidate translations per string), then the next batch, then the next file. Each file is followed by a fixed 2-second pause.

What the live AWS run (`ec2-20261015t233954z`) showed:
- **Model inference was 99.3% of each file's time** (about 21 s of 21.4 s). S3, SQL and XML took about 0.15 s per file.
- Every 50-string file fit in **one batch**, so the batch limits never applied and the batching did nothing for this workload.
- Mixing short and long strings in one batch wasted **30% of the model's input positions on padding** (benchmark step A below).
- `LANG_TOKEN_MULTIPLIERS` looked like part of batching but is only used for telemetry token counts.

## 2. How we measure

- **Tool:** `translator/batching_bench.py`, started with `RUNNER_TASK=batching-bench bash infra/run_on_ec2.sh`.
- **Machine:** the same EC2 m5.large (2 vCPU / 8 GB, Intel Xeon Platinum 8259CL), Docker image, NLLB-200 600M INT8 model and 2 pinned cores as production.
- **Workload:** `resx_file_10`, `resx_file_19` and `resx_file_20` (153 strings; files 19 and 20 have the longer strings) × 7 languages = **1,071 translations**.
- **What is timed:** inference only (tokenize + translate + detokenize).
- **Baseline:** step A calls production's own `translate_local`. A sanity check confirms the benchmark's engine gives identical output to production (100/100 strings).
- **One change at a time:** each step changes one thing, starting from the best configuration so far.
- **Quality checks:**
  - Batching-only changes must give *identical* translations; this is reported as "identical".
  - Beam-size changes are scored with chrF against FLORES-200 human references.
- **Raw results:** `s3://<data-bucket>/runs/<run-id>/bench/results.json`, uploaded after every step.

## 3. Benchmark run 1 — `ec2-20261016t095926z` (2026-10-16)

| Step | Change (one at a time) | Time (s) | Translations/hour | Speed-up | Batches | Padding | Identical |
|---|---|---:|---:|---:|---:|---:|---:|
| A | Today's pipeline (baseline) | 445.08 | 8,663 | 1.00× | 21 | 30.1% | — |
| B | A + `intra_threads=2` | 445.48 | 8,655 | 1.00× | 21 | 30.1% | 100% |
| C | All files and languages in one plan, real token counts, 1024-token batches | 435.49 | 8,853 | 1.02× | 10 | 30.7% | 100% |
| D | C + sorted by length | 433.43 | 8,896 | 1.03× | 10 | **4.3%** | 100% |
| E | D + 4096-token batches | — | — | — | — | — | — |

**What run 1 shows:**
- **Threads:** telling CTranslate2 it has 2 cores changed nothing, so the default was already using them.
- **Run-wide batching:** fewer, fuller batches gave only 2%.
- **Sorting by length:** padding fell from 30% to 4%, but time improved by only 3%. **Padding and batch layout are not the bottleneck.** The cost is decoding itself: about 0.41 s per translation with 5 candidates each.
- **4096-token batches (E):** the run stopped **within a minute of starting E**. SSM marked it `TimedOut` at 10:33:49, 34 minutes after the start, and the machine stopped reporting. We suspect memory (4096 tokens × 5 beam candidates on an 8 GB machine already holding two copies of the model), but no out-of-memory message was captured, so the **cause is not confirmed**. Very large batches are unsafe on this machine either way.
- **Translations:** every batching change produced identical output (chrF 100 against the baseline).

**Flaws in the first version of the benchmark, now fixed:**
- Results were only saved at the end, so the run that stopped lost steps E–G. They are now saved and uploaded after every step.
- There was no memory reading. Each step now records peak memory.
- Steps couldn't be skipped. Batch budgets and the thread/unsorted steps are now options; run 2 tries **smaller** budgets (256, 512, 1024) instead of larger.
- The SSM time limit is now configurable (`RUNNER_TIMEOUT_SECONDS`).
