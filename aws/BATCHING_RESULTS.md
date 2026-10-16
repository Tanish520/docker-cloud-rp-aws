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

## 4. Benchmark run 2 — `ec2-20261016t103748z` (2026-10-16)

Same machine type, workload and method. Steps B (threads) and C (unsorted) were skipped because run 1 showed no effect from them, and the batch budgets went smaller instead of larger. The run completed; the engine sanity check matched production on 100/100 strings.

| Step | Change (one at a time) | Beam | Time (s) | Translations/hour | Speed-up | Batches | Padding | Peak memory | Identical to A | chrF vs A |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | Today's pipeline (baseline) | 5 | 434.84 | 8,867 | 1.00× | 21 | 30.1% | 1.8 GB | — | — |
| D-256 | Run-wide, sorted, 256-token batches | 5 | 451.10 | 8,547 | 0.96× | 37 | 1.2% | 1.8 GB | 100% | 100.0 |
| D-512 | Run-wide, sorted, 512-token batches | 5 | 432.62 | 8,912 | 1.01× | 19 | 2.4% | 2.2 GB | 100% | 100.0 |
| D-1024 | Run-wide, sorted, 1024-token batches | 5 | 425.78 | 9,055 | 1.02× | 10 | 4.3% | 3.5 GB | 100% | 100.0 |
| **F** | D-1024 + **beam 2** | 2 | **188.08** | 20,500 | **2.31×** | 10 | 4.3% | 3.5 GB | 77% | 92.5 |
| **G** | D-1024 + **beam 1** | 1 | **103.58** | 37,224 | **4.20×** | 10 | 4.3% | 3.5 GB | 59% | 86.1 |

**Quality against human references** (FLORES-200 dev, first 40 sentences per language, chrF; higher is better):

| Beam | All 7 | de-DE | es-ES | fr-FR | hi-IN | ja-JP | ru-RU | zh-CN | FLORES time |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 (today) | **56.69** | 62.71 | 55.54 | 68.75 | 57.65 | 33.83 | 58.35 | 28.58 | 400.1 s |
| 2 | 55.82 (−0.87) | 61.93 | 55.63 | 67.05 | 56.98 | 34.77 | 56.71 | 26.32 | 186.3 s |
| 1 | 55.29 (−1.40) | 60.08 | 55.73 | 67.76 | 55.03 | 33.39 | 57.33 | 25.07 | 109.6 s |

The "chrF vs A" column in the main table compares against today's *output*, not against a human translation. It only says how much the text changed. The FLORES table is the quality measure.

## 5. What we learned

1. **Batching layout is not the bottleneck.**
   - Run-wide batching, length sorting and any budget from 256 to 1024 tokens moved time by −4% to +2%, while padding went from 30% to 1–4%.
   - These changes never altered a single translation.
   - Run-to-run noise on identical work is about 2% (step A: 445 s in run 1, 435 s in run 2), so differences that small are not meaningful.
2. **Beam size is the bottleneck.**
   - Decoding 5 candidates per string is where the time goes.
   - Beam 2 is **2.3× faster** on our strings (2.15× on FLORES), for −0.87 chrF.
   - Beam 1 is **4.2× faster** (3.65× on FLORES), for −1.40 chrF.
   - The losses are largest for zh-CN (−2.3 and −3.5) and de-DE (−0.8 and −2.6). es-ES and ja-JP are unchanged or slightly better.
3. **Big batches cost memory without saving time.**
   - Peak memory rose from 1.8 GB (production-size batches) to 2.2 GB at 512 tokens and 3.5 GB at 1024.
   - That trend makes it likely that run 1's 4096-token step ran the 8 GB machine out of memory. This is not proven, because no out-of-memory message was captured.
4. **Telling CTranslate2 the core count does nothing.** It already uses the 2 pinned cores (run 1, step B).

**Limits of these results:**
- The quality numbers use 40 FLORES sentences per language, so differences under about 1 chrF may be noise. Confirm the chosen beam with the Track 1 metric suite (BLEU, chrF, COMET) on the full KDE4 test set before publishing.
- Timings are for inference on one m5.large.

## 6. Recommendation

- **Change the beam size, not the batching.**
  - **Beam 2** is the balanced choice: about 2.3× faster for under 1 chrF point overall.
  - **Beam 1** suits speed-first runs, about 4× faster, if the zh-CN/de-DE loss is acceptable after the full quality check.
- **Keep production's simple per-file batching.** Run-wide sorted batching gave at most 2% and roughly doubles peak memory, so it is not worth the complexity on its own.
- **Never use batches above about 1024 tokens** on this 8 GB machine.
- **Expected effect on the pipeline:** inference is 99% of Phase 2 time, so a 50-string file should drop from about 21 s to about 9 s at beam 2, and about 5 s at beam 1. Confirm with a full pipeline run after the change.

## 7. Decision (2026-10-16)

The pipeline stays at **beam 5** with today's batching for now. The beam-2 and beam-1 options wait until the Track 1 team has confirmed quality on the full KDE4 set (BLEU, chrF, COMET), because 40 FLORES sentences per language is a small sample. No production code changed in this work; only the benchmark was added.

**Raw data:**
- Run 1 (steps A–D): `s3://<data-bucket>/runs/ec2-20261016t095926z/` (CloudWatch `/cloud-loc/ec2-runner`).
- Run 2 (complete): `s3://<data-bucket>/runs/ec2-20261016t103748z/bench/results.json`.

**To repeat the benchmark:**
```bash
RUNNER_TASK=batching-bench RUNNER_TIMEOUT_SECONDS=10800 bash aws/infra/run_on_ec2.sh
```

## 8. KDE4 test — `ec2-20261016t115802z` (2026-10-16)

The FLORES check (sections 4–6) used only 40 general-domain sentences per language. This test repeats beam 5 / 2 / 1 on the **KDE4 software-localization dataset** supplied by the Track 1 team. That is the same kind of short UI text the pipeline translates, at a much larger size.

**Dataset:** `Dataset-Translated.xlsx`. English plus 12 languages: hi, ar, bn, de, es, fr, ja, mr, pa, ta, te, zh. It is stored privately in S3 at `datasets/kde4/` and is not committed.
- **Cleaning** (`clean_kde4`): 10,231 rows became **10,054 usable** strings. 177 rows were dropped:
  - one cell holding hundreds of strings glued together with line breaks (8,575 characters);
  - two cells Excel had turned into TRUE/FALSE booleans;
  - duplicate English strings.
- **Speed and quality sample:** a fixed random sample (seed 2026) of **1,000 strings × 12 languages = 12,000 translations** per beam. The strings are short: median 13 characters, about 2 words.
- **Placeholder check:** all **355** usable strings containing placeholders (`%1`, `%s`, `{name}`, `<tag>`) × 12 languages = 4,260 translations per beam. Kept separate so the sample stays representative.
- **⚠️ About the reference translations:** signs suggest they are **machine translations**, not KDE's human translations:
  - every row is filled for all 12 languages;
  - TRUE/FALSE became "WAHR"/"FALSCH";
  - Arabic "Gray Heading" is a transliteration;
  - the references themselves drop `%N` placeholders in 5–25% of rows.

  Until the Track 1 team confirms how they were produced, chrF here means *closeness to the spreadsheet's translations*. The placeholder check does not use the references, so it is valid either way.

**Setup:** same EC2 m5.large (2 vCPU), image, model and 2 pinned cores as production. Run-wide, length-sorted 1024-token batches, which give output identical to production's batching (section 4).

| Beam | Time, 12,000 translations | Translations/hour | Speed-up | chrF (all 12) | Placeholders kept exactly | Identical to beam 5 | Peak memory |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 (today) | 3,169 s | 13,631 | 1.00× | **64.41** | 88.0% | — | 5.2 GB |
| **2** | **1,442 s** | 29,949 | **2.20×** | **63.96 (−0.45)** | 87.7% | 84% | 5.2 GB |
| 1 | 915 s | 47,197 | 3.46× | 62.26 (−2.15) | 86.9% | 69% | 5.2 GB |

**Per language** (chrF / placeholders kept):

| Lang | Beam 5 | Beam 2 | Beam 1 |
|---|---|---|---|
| hi | 66.90 / 89.6% | 66.61 / 90.1% | 65.60 / 89.3% |
| ar | 57.15 / 91.0% | 55.74 / 89.9% | 53.77 / 89.9% |
| bn | 64.87 / 83.1% | 64.11 / 82.0% | 63.88 / 82.3% |
| de | 65.06 / 89.6% | 64.39 / 90.1% | 62.21 / 89.9% |
| es | 72.01 / 87.6% | 71.69 / 88.5% | 68.49 / 87.0% |
| fr | 70.23 / 85.9% | 69.48 / 85.9% | 68.16 / 84.5% |
| ja | 53.43 / 87.3% | 52.52 / 86.8% | 46.51 / 86.8% |
| mr | 59.34 / 89.0% | 59.08 / 88.7% | 58.76 / 88.7% |
| pa | 63.35 / 87.9% | 63.27 / 88.2% | 62.68 / 88.2% |
| ta | 64.70 / 92.4% | 65.07 / 92.1% | 63.70 / 91.8% |
| te | 66.07 / 92.1% | 65.86 / 90.7% | 64.26 / 90.4% |
| zh | 39.68 / 80.6% | 39.30 / 79.4% | 33.72 / 74.4% |

**What KDE4 adds:**
1. **Beam 2 is confirmed as the balanced choice on real localization text.**
   - It is 2.2× faster for −0.45 chrF overall, a smaller loss than on FLORES.
   - Every language stays within 1.4 points (Arabic −1.41 is the largest), and Tamil slightly improves.
2. **Beam 1 costs more on KDE4 than on FLORES:** −2.15 overall, with large drops for Japanese (−6.9) and Chinese (−6.0). It is not suitable for CJK languages.
3. **Placeholder breakage is a separate quality problem.**
   - At *every* beam size, about 12% of strings with placeholders come back with `%1`, `{name}` or tags missing or changed: 81% kept for Chinese, 92% for Tamil.
   - In the localized software that shows up as broken messages.
   - Beam size barely affects it, so it needs its own fix: protect placeholders before translation and restore them after.
4. **CPU use is about 59%, not 100%.**
   - During the runs CloudWatch showed the m5.large at about 59% average CPU, so CTranslate2 is not keeping both cores busy.
   - Run 1 (step B) showed `intra_threads=2` does not change this. The cause is not yet known and is worth investigating separately.
5. **Peak memory reached 5.2 GB** in this run. More and longer batches than the resx runs, plus the placeholder set, but it stays below the 8 GB limit.

**Updated recommendation:**
- **Beam 2** for production: about 2.2× faster translation for under half a chrF point on KDE4 and under 1 point on FLORES.
- Next quality work: **placeholder protection**.
- Have the Track 1 team confirm the KDE4 reference provenance, and add COMET, before the numbers go into the paper.

**Raw data:** `s3://<data-bucket>/runs/ec2-20261016t115802z/bench/results.json`.

**To repeat:**
```bash
RUNNER_TASK=batching-bench RUNNER_TIMEOUT_SECONDS=13800 \
  BENCH_ARGS="--kde4 s3://<data-bucket>/datasets/kde4/Dataset-Translated.xlsx --kde4-strings 1000" \
  bash aws/infra/run_on_ec2.sh
```

## 9. Parallel batches and a length cap — `ec2-20261016t142418z` (2026-10-16)

**Flaw found in section 8:** during translation the m5.large averaged only **~59% CPU**. CTranslate2 worked on **one batch at a time**, and part of the 2 cores sat idle. Setting `intra_threads=2` did not help (run 1, step B).

**What we changed, one step at a time** (KDE4, 500 strings × 12 languages = 6,000 translations, beam 2, length-sorted batches):
- **P1:** load the model with `inter_threads=2, intra_threads=1` and queue every batch at once (`asynchronous=True`), so **two batches are translated in parallel, one per core**.
- **P2:** P1 with smaller 512-token batches.
- **P3:** the faster of P1/P2, plus a per-batch output cap (`max_decoding_length` = 2 × the longest input in the batch + 10 tokens).

Batches are sorted by length, so the cap fits every string in the batch and keeps one runaway string from holding the batch up.

| Step | Change | Time (s) | Translations/hour | Speed-up | CPU busy | Batches | chrF | Identical to P0 | Peak memory |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| P0 | One batch at a time (today's engine) | 617.52 | 34,979 | 1.00× | 59.3% | 35 | 64.44 | — | 2.6 GB |
| **P1** | **2 batches at once, 1024-token** | 514.97 | 41,944 | **1.20×** | **99.6%** | 35 | 64.44 | 100% | 4.5 GB |
| P2 | P1 with 512-token batches | 539.68 | 40,024 | 1.14× | 99.3% | 69 | 64.44 | 99.9% | 4.5 GB |
| **P3** | **P1 + output length cap** | **488.14** | **44,250** | **1.27×** | 99.6% | 35 | 64.44 | **100%** | 4.5 GB |

**What it shows:**
1. **Parallel batches fix the idle CPU.** Usage went from 59% to 99.6%, for **1.20× faster** translation with identical output.
   - It is less than 2× because two cores sharing the work each run slower than one core alone.
   - The cost is memory, 2.6 → 4.5 GB, a second set of working buffers; still safe on 8 GB.
2. **Smaller batches do not help with parallel work.** 512-token batches were slower (1.14×) than 1024-token batches (1.20×): more batches means more per-batch overhead.
3. **The length cap adds another ~6%** (1.27× in total) **without changing a single translation** on this sample: output identical, chrF unchanged. It is a safety limit on runaway strings, so it costs nothing here.

**Combined effect, estimated, not yet measured end to end:**

| Setting | Speed-up |
|---|---:|
| Beam 2 (section 8) | 2.20× |
| Parallel batches + length cap (this section) | 1.27× |
| Both together | **about 2.8×** faster than today's beam 5, one batch at a time |

A 50-string file would drop from about 21 s to about 7.5 s. Beam 2 is still on hold until quality is confirmed (section 7). Parallel batches and the length cap gave identical output, so they could go into production now without waiting for that check.

**Raw data:** `s3://<data-bucket>/runs/ec2-20261016t142418z/bench/results.json`.

**To repeat:**
```bash
RUNNER_TASK=batching-bench RUNNER_TIMEOUT_SECONDS=10800 \
  BENCH_ARGS="--kde4 s3://<data-bucket>/datasets/kde4/Dataset-Translated.xlsx --kde4-strings 500 --kde4-parallel" \
  bash aws/infra/run_on_ec2.sh
```
