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

## 10. Placeholder protection — `ec2-20261016t195057z` (2026-10-16)

**Flaw found in section 8:** about 12% of KDE4 strings with placeholders (`%1`, `%s`, `{name}`, `<tag>`) came back with a placeholder missing or changed, at every beam size. The model treats `%1` as ordinary text. In the localized software that is a broken message.

**What we changed:** `translator/placeholders.py`.
- **Protect:** before translation, each placeholder is swapped for a numbered marker, and after translation swapped back.
- **Restore** accepts what the model does to a marker: full-width brackets (`［0］`), spaces inside, and native digits (`[०]`, `[٠]`).
- **Check:** restore reports whether every marker came back exactly once.
- **Skip:** text that already contains something marker-like is sent unchanged.
- **Ruled out before testing:** `⟦0⟧` brackets, because those characters are not in the NLLB vocabulary and would be lost.
- **Fallback:** where the markers do not all come back, use the unprotected translation for that string.

**Test:** all 355 usable KDE4 placeholder strings × 12 languages = **4,260 translations per style**, beam 2, length-sorted 1024-token batches, same m5.large.

| Style | Example | Placeholders kept | With fallback | Fallback used | chrF vs references | Time (s) |
|---|---|---:|---:|---:|---:|---:|
| none (today) | `Could not find conduit %1.` | 87.70% | — | — | 61.79 | 649.3 |
| **square** | `Could not find conduit [0].` | **99.15%** | **99.88%** | 0.85% | 59.51 (with fallback 59.76) | 716.1 |
| section | `Could not find conduit §0§.` | 80.66% | 97.91% | 19.34% | 56.27 (with fallback 59.00) | 749.5 |
| curly | `Could not find conduit {0}.` | 88.78% | 97.37% | 11.22% | 58.74 (with fallback 60.27) | 747.8 |

**Per language** (placeholders kept, no protection → square):

| Lang | hi | ar | bn | de | es | fr | ja | mr | pa | ta | te | zh |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | 90.1% | 89.9% | 82.0% | 90.1% | 88.5% | 85.9% | 86.8% | 88.7% | 88.2% | 92.1% | 90.7% | 79.4% |
| square | 100% | 98.3% | 100% | 97.5% | 99.2% | 96.9% | 99.7% | 99.7% | 99.7% | 100% | 100% | 98.9% |

**What it shows:**
1. **Square markers fix the problem.**
   - Placeholders kept rise from 87.7% to 99.15%; with the fallback, to 99.88%. That is 5 broken strings out of 4,260 instead of about 524.
   - Every language reaches at least 96.9%; Chinese improves the most (79.4% → 98.9%).
   - **Why square works:** NLLB saw many citation marks like `[1]` in training and copies them unchanged.
2. **Section and curly markers do not work well.** The model often rewrites `§0§` and `{0}`: it translates around them, drops them, or changes the braces. Section was even worse than no protection.
3. **chrF against the spreadsheet drops about 2 points on these 355 strings** (61.79 → 59.76 with fallback). The cause is not yet confirmed. Two likely reasons:
   - The spreadsheet's own translations drop `%N` placeholders in 5–25% of rows (section 8), so an output that correctly keeps `%1` matches those references less well.
   - A marker can slightly change how the words around it are translated.

   This affects only strings that contain placeholders (3.5% of KDE4). Strings without placeholders are not touched by protection, so their translations are identical.
4. **Cost:** about 10% more time on placeholder strings, from slightly longer input. Negligible overall.

**Recommendation:** use **square** markers with the **fallback** in the pipeline. A placeholder broken in about 1 in 8 strings is a functional bug in the localized software; the ~2-point chrF change on 3.5% of strings is measured against references that themselves drop placeholders.

**Status:** in the production translator (`translator/translation.py`, `translate_local`): square markers, plus a second unmasked pass only for the strings whose markers did not all come back. Beam size and batching are unchanged (beam 5).

**Raw data:** `s3://<data-bucket>/runs/ec2-20261016t195057z/bench/results.json`.

**To repeat:**
```bash
RUNNER_TASK=batching-bench RUNNER_TIMEOUT_SECONDS=10800 \
  BENCH_ARGS="--kde4 s3://<data-bucket>/datasets/kde4/Dataset-Translated.xlsx --kde4-placeholders" \
  bash aws/infra/run_on_ec2.sh
```

## 11. Full KDE4 pipeline run with the optimized settings — `ec2-20261016t214343z` (2026-10-16/17)

**Why:** sections 8–10 measured each improvement in the benchmark, on samples of up to 1,000 strings, timing the model only. This run puts them all into the **real pipeline** (Phase 1 → 2 → 3, translation memory, S3, telemetry) and translates the **whole KDE4 dataset**.

**Flaws in the production translator before this run:**
- It translated **one batch at a time**, about 52 strings in each, in the order the strings appeared in the file, so one core sat partly idle (59% CPU).
- It used **beam 5**.
- It had **no output length cap**.
- It had **no Punjabi** entry.
- A string longer than 434 characters made the whole translation-memory save for its batch fail.

**What changed** (commits `bce8a39`, `8ae49dd`):

| Setting | Before | Now | Evidence |
|---|---|---|---|
| Order | file order | each file sorted shortest-first, then cut into 1,000-string chunks | section 4 |
| Batches | ~52 strings, estimated tokens, one at a time | ≤1,024 real tokens, every batch of a chunk queued at once on 2 engine workers (`inter_threads=2, intra_threads=1`) | section 9 |
| Output cap | none | 2 × longest input + 10 tokens per batch | section 9 |
| Beam | 5 | **2** | section 8 |
| Placeholders | sent as-is | `[0]` markers + unmasked fallback | section 10 |
| Languages | no Punjabi | `pa-IN` → `pan_Guru` | — |
| Memory keys > 434 chars | whole save failed | that string is translated but not saved | — |

**Run:** all **10,054** usable KDE4 strings × **12 languages = 120,648 translations**. Same m5.large (2 vCPU, 8 GB), 2 pinned cores, translation memory emptied first (0 memory hits, so every string went through the model). Input built with `tools/kde4.py resx`; output scored with `tools/kde4.py score`.

**Result: the run succeeded.** 12 of 12 files, 0 failures, 0 strings missing.

| Lang | Time (min) | Translations/hour | chrF | Placeholders kept |
|---|---:|---:|---:|---:|
| ar | 18.28 | 32,991 | 55.42 | 99.72% |
| bn | 18.38 | 32,828 | 64.38 | 100% |
| de | 17.83 | 33,841 | 64.22 | 99.44% |
| es | 17.62 | 34,232 | 71.50 | 100% |
| fr | 19.00 | 31,742 | 68.89 | 99.72% |
| hi | 16.12 | 37,429 | 64.75 | 100% |
| ja | 16.18 | 37,282 | 50.92 | 100% |
| mr | 17.02 | 35,434 | 58.45 | 100% |
| pa | 19.30 | 31,258 | 61.53 | 100% |
| ta | 18.77 | 32,144 | 64.10 | 100% |
| te | 19.15 | 31,502 | 65.28 | 100% |
| zh | 16.73 | 36,059 | 39.84 | 99.72% |
| **All** | **214.4 (3 h 34 min)** | **33,760** | **63.29** | **99.88%** |

- **Whole run:** 3 h 38 min from start to finish, including model restore, Phase 1 and Phase 3.
- **Where the time goes:** the model takes 99.6% of Phase 2 time. Translation-memory reads and writes took about 45 s in total, and XML and S3 a few seconds.
- **CPU:** EC2 CloudWatch showed 92–100% CPU during translation; it was 59% with the old one-batch-at-a-time engine.
  - The `cpu_usage_pct` column in the telemetry (57–76%) is a single psutil sample taken at the end of each file, not an average.
- **Memory:** 1.6–1.9 GB per file.

**What it shows:**
1. **Speed.**
   - The old settings (beam 5, one batch at a time) ran at 13,631 translations/hour in the section 8 benchmark. At that rate this dataset would take about **8.9 hours** of translation.
   - With the new settings the real pipeline did it in **3.6 hours**, about **2.5× faster**.
   - The 8.9 h is an estimate from the benchmark rate, not a measured run.
   - The benchmarks predicted about 2.8×. The real pipeline also tokenizes, protects placeholders, writes XML and saves to the database, and each 1,000-string chunk ends with a short tail where only one batch is left for the two cores.
2. **Placeholders: 99.88% kept on the full dataset**, the same as the section 10 test. 5 of the 4,260 placeholder translations are broken, against about 12% before protection.
3. **Quality: chrF 63.29 over all 120,648 translations.**
   - Section 8 gave 63.96 (beam 2) and 64.41 (beam 5), but on a 1,000-string sample, so the numbers are close but not directly comparable.
   - Japanese (50.9) and Chinese (39.8) score lowest, as in every earlier test.
   - The same caveat applies: the references look machine-made (section 8).
4. **Small issues found:**
   - 4 strings (1 Japanese, 3 Chinese) came back **empty** from the model. Phase 3 then keeps the English text, so nothing is missing, but those 4 are untranslated.
   - 3 strings over 434 characters were translated but not stored in the translation memory.

**Complete telemetry** (one `RESEARCH_DATA` record per language file, exported to CSV):

Run totals:

| Metric | Value |
|---|---|
| Translations | 120,648 (12 of 12 files; 0 engine errors, retries or throttling) |
| Phase 2 time | 214.4 min; model 213.6 min (99.6%) |
| Database + S3 time | 46.7 s (0.36%): memory reads/saves 43.6 s, commits 0.4 s, S3 download 1.5 s, upload 1.2 s |
| XML | read 0.25 s, write 0.77 s |
| Telemetry overhead | 67 ms |
| Model calls | 11 per language (1,000-string chunks; the last has 54 strings); 63–70 model batches per language, 782 in total, including the placeholder fallback pass |
| Memory hits | 0 (memory emptied first) |
| Peak memory | 1.89 GB |
| Cost | about $0.40 of m5.large time. `serverless_cost_gb_sec` (22,685 in total) is the Azure-era memory × time estimate, not money |

Time per language:

| Lang | Total (min) | Model (min) | Memory read+save (s) | Commit (ms) | S3 down / up (ms) | XML read / write (ms) |
|---|---:|---:|---:|---:|---:|---:|
| ar | 18.28 | 18.24 | 2.27 | 34 | 80 / 114 | 22 / 63 |
| bn | 18.38 | 18.33 | 2.25 | 30 | 115 / 107 | 20 / 63 |
| de | 17.83 | 17.78 | 2.70 | 36 | 106 / 107 | 21 / 63 |
| es | 17.62 | 17.55 | 3.99 | 37 | 128 / 99 | 20 / 63 |
| fr | 19.00 | 18.94 | 3.65 | 27 | 91 / 102 | 21 / 64 |
| hi | 16.12 | 16.05 | 3.76 | 50 | 166 / 107 | 20 / 65 |
| ja | 16.18 | 16.11 | 4.06 | 31 | 135 / 98 | 20 / 64 |
| mr | 17.02 | 16.95 | 3.90 | 28 | 114 / 104 | 21 / 65 |
| pa | 19.30 | 19.22 | 4.60 | 41 | 111 / 103 | 21 / 65 |
| ta | 18.77 | 18.69 | 4.30 | 30 | 157 / 98 | 22 / 66 |
| te | 19.15 | 19.08 | 4.12 | 32 | 105 / 101 | 21 / 66 |
| zh | 16.73 | 16.66 | 4.05 | 32 | 143 / 106 | 21 / 64 |

Text and token estimates (source: average 14.4 characters per string, about 36,287 estimated input tokens per language):

| Lang | Output/input length | Characters per token | Est. output tokens | Est. ms per token |
|---|---:|---:|---:|---:|
| ar | 0.94 | 2.32 | 85,009 | 9.0 |
| bn | 1.03 | 1.83 | 125,011 | 6.8 |
| de | 1.16 | 3.53 | 52,560 | 12.0 |
| es | 1.30 | 3.76 | 52,432 | 11.9 |
| fr | 1.31 | 3.76 | 52,841 | 12.8 |
| hi | 0.98 | 1.86 | 118,675 | 6.2 |
| ja | 0.53 | 4.13 | 17,466 | 18.0 |
| mr | 0.96 | 1.87 | 115,921 | 6.7 |
| pa | 0.94 | 1.88 | 113,216 | 7.7 |
| ta | 1.15 | 1.27 | 208,439 | 4.6 |
| te | 1.07 | 1.30 | 194,528 | 5.0 |
| zh | 0.36 | 4.18 | 10,868 | 21.2 |

**Reading the telemetry columns:**
- **Token columns are estimates.** They come from character counts × fixed per-language factors, not the model's real tokens. Time and translations/hour are exact.
- **`cpu_usage_pct`** is one psutil sample at the end of each file. EC2 CloudWatch averaged 92–100% during translation.
- **Old Azure-era names:** `hf_api_wait_ms` = model time; `azure_sql_*` = the RDS database (the query column also includes memory saves); `blob_*` = S3.
- **`total_sleep_time_ms` = 0:** the 2-second pause after each file is not counted in that file's row.

**Raw data:**
- Output: `s3://<data-bucket>/runs/ec2-20261016t214343z/output/<lang>/kde4.resx`.
- Telemetry CSV and scores: `s3://<data-bucket>/runs/ec2-20261016t214343z/results/{telemetry.csv,scores.json}`.
- Telemetry: CloudWatch `/cloud-loc/ec2-runner`, exported with `tools/export_telemetry.py`.
- Scores: `tools/kde4.py score`.

**To repeat:**
```bash
python aws/tools/kde4.py resx Dataset-Translated.xlsx kde4.resx
aws s3 cp kde4.resx s3://<data-bucket>/datasets/kde4/kde4.resx
RUNNER_TIMEOUT_SECONDS=21600 PIPELINE_CONFIG=aws/config.kde4.json \
  INPUT_S3=s3://<data-bucket>/datasets/kde4/kde4.resx bash aws/infra/run_on_ec2.sh
aws s3 cp s3://<data-bucket>/runs/<run-id>/output/ output/ --recursive
python aws/tools/kde4.py score Dataset-Translated.xlsx output/ --out scores.json
```

## 12. Fixing the issues found in the full run — check run `ec2-20261017t063722z` (2026-10-17)

### 12.1 Strings over 434 characters were never saved to the translation memory

**Flaw:**
- The memory used the English text itself as the database key.
- SQL Server keys hold at most 900 bytes, which is 434 characters, but KDE4 has strings up to 660 characters.
- Section 11 worked around this by skipping those saves, so long strings would be retranslated on every run.

**Fix:**
- The key is now a 32-byte **SHA-256 of the text** (`SourceHash BINARY(32)`), and the text sits in an unlimited `NVARCHAR(MAX)` column. Lookups and saves use the hash.
- `db_admin.py init` drops a table in the old layout and recreates it. The memory is a cache, so nothing of value is lost.

### 12.2 `<unk>`: characters the model cannot write

**Flaw:**
- 4 strings in the full run came back empty: `thistle` (ja), and `Candle`, `crab`, `goy Y` (zh).
- **Cause, reproduced with the same model locally:**
  - for these strings NLLB's best output is the `<unk>` (unknown) token;
  - decoding with `skip_special_tokens=True` deletes it, leaving an empty string;
  - unrelated to batching: beam 5 and the length cap give the same.
- **A full re-translation of the zh and ja sets showed the empty strings were the visible tip.** **614 of 10,054 Chinese outputs (6.1%)** and 8 Japanese contained `<unk>`. In most, a character was **silently deleted**, changing the meaning:
  - "New Folder" → 新文件 ("new file");
  - "Dolphin Toolbar" → 鱼工具 ("fish tool");
  - "Bitrate" → 率 ("rate").

**Options measured** on exactly those strings (chrF against the spreadsheet):

| Option | zh (614 strings) | ja (8 strings) | Behaviour |
|---|---:|---:|---|
| Keep the model's text (before) | 26.20 | 6.61 | missing characters, often wrong meaning |
| Retry with `disable_unk` | 25.97 | 33.18 | fixes some; others become "??" or wrong words ("Tunnels" → 突尼斯 "Tunisia", "thistle" → 炭水化物 "carbohydrate") |
| **Keep the English and report it (chosen)** | 7.70 | 31.71 | never a wrong word; listed in the log for a person to translate |

**Decision (2026-10-17):** keep the English and report it.
- In localized software a visibly untranslated string can be found and fixed; a wrong word cannot.
- The cost is a lower measured chrF for Chinese, because chrF counts English as untranslated.

**Fix:**
- Any output containing `<unk>` is discarded. The target stays empty, so Phase 3 keeps the English.
- The string is not saved to the memory, so a later run or a person can fill it.
- Each chunk logs `⚠️ N string(s) kept in English … [examples]`.

### 12.3 Check run

**Input:** 383 KDE4 strings: the 3 longest, 25 known `<unk>` cases and all 355 placeholder strings. Two identical files (`kde4-check-a/b.resx`, `config.kde4-check.json`), 12 languages.
- File **a** tests translation.
- File **b** tests that the memory finds every saved string again by its hash.

| | Result |
|---|---|
| Files | 24 of 24 succeeded, 0 failures, no SQL errors |
| File a: translation | 383 strings × 12 languages, 71–92 s per language |
| File b: memory | **100% hits in 10 languages** (0.1 s each, no model work); ja 98.2% (7 kept-English strings retried), zh 92.2% (30 retried, 18 s) |
| Same output from the memory | yes (zh file a == file b) |
| Long strings | saved and found by hash; all 3 translated (de); in zh one was kept in English because of `<unk>` |
| Kept in English | zh 30, ja 7, all listed in the log |
| Placeholders kept | zh 99.72%, de 99.44% (same as section 10) |

**Effect on the full KDE4 run:**
- The section 11 output still has the old behaviour.
- A rerun would keep about 614 Chinese and 8 Japanese strings in English, which would lower Chinese chrF from 39.84.
- Every other language is unchanged.

**Raw data:**
- `s3://<data-bucket>/runs/ec2-20261017t063722z/` (output, translated XLIFF).
- CloudWatch `/cloud-loc/ec2-runner`.
- The `<unk>` measurement was a one-off local script using the same model, beam 2, batching and length cap as production; numbers above.

**To repeat the check:**
```bash
RUNNER_TIMEOUT_SECONDS=3600 PIPELINE_CONFIG=aws/config.kde4-check.json \
  INPUT_S3="s3://<data-bucket>/datasets/kde4/kde4-check-a.resx s3://<data-bucket>/datasets/kde4/kde4-check-b.resx" \
  bash aws/infra/run_on_ec2.sh
```

## 13. Translation memory: keep, label, load once, reuse near-twins — experiment `ec2-20261017t175415z` (2026-10-17/18)

Semester task (Track 3, weeks 3–4): *"Build/update Translation Memory (TM) to reduce lookups"*. Phase 3 asks for *"token usage reduction, lookup speedups and cost savings"*.

### 13.1 Flaws before

1. **The memory was emptied before every run** (`RESET_TM=true` by default). In CI/CD every commit retranslated the whole app, although a commit changes only a few strings.
2. **No engine check.** Each entry recorded a model name, but lookups ignored it. After a change of model or settings, older translations would still be reused.
3. **Lookups: one database query per 1,000 strings per file**, and every file asked again.
4. **Exact match only.** "8 Cards" could not reuse "20 Cards", and "Border:" could not reuse "Border". 526 of the 10,054 KDE4 strings (5.2%) have such a near-twin.
5. **A 2 s pause after every file** (24 s per 12-language run) that only waited for Azure's log exporter.

### 13.2 What changed

| Fix | Change | Code |
|---|---|---|
| 1 | Memory kept between runs (`RESET_TM=false` default). Each entry records `EngineVersion` = model + beam + length cap + placeholder style + `<unk>` rule, and lookups match it. Key is `(TargetLang, EngineVersion, SourceHash)`. | `db_admin.py`, `translation.py` `engine_version()` |
| 3 | Each language's memory is loaded **once per run in one query** into a dictionary. Strings translated during the run are added as they are saved, so later files reuse them with no query. | `translation.py` `load_memory()`, `remember()` |
| 2 | **Near-twin reuse** (`templates.py`): a saved *model* translation is adapted to a string that differs only in numbers, placeholders or trailing punctuation (values swapped, punctuation in the target language's style). Twins inside one file go to the model once. Refused whenever the values cannot be put back cleanly. `TM_TWINS=off` disables it. | `templates.py`, `translation.py` |
| — | The pause runs only when an Azure exporter is connected. | `translation.py` |
| — | Telemetry: `tm_db_queries`, `tm_lookup_ms`, `tm_hits_in_run`, `tm_twin_hits`, `model_strings`, `real_input_tokens`, `real_output_tokens`, `padding_pct`. | `translation.py`, `export_telemetry.py` |

Not built: skipping unchanged files. A file served entirely from the memory already takes about 0.1 s (section 12.3), so a skip would save almost nothing.

### 13.3 Near-twin quality check (before turning it on)

**Test:** all 440 twin groups in the full KDE4 set (968 strings, 528 reusable pairs) × 12 languages, run locally with the production model and settings. For each pair, the adapted translation was compared with a fresh model translation of the same string, both scored against the references.

**First version:** punctuation style was guessed, using full-width `：` for Japanese. Japanese came out at 49.00 reused against 50.62 fresh.

**Fix:** counting how every KDE4 reference ends showed:
- only Chinese uses `：` (Japanese uses `:` 749 times against `：` 657);
- `...` is kept everywhere;
- Marathi keeps `.`.

`TAIL_STYLE` now follows those counts.

| Lang | Reused | Refused | chrF fresh | chrF reused | Identical |
|---|---:|---:|---:|---:|---:|
| hi | 506 | 22 | 67.12 | 70.62 | 48% |
| ar | 488 | 40 | 55.51 | 56.08 | 73% |
| bn | 454 | 74 | 64.10 | 65.34 | 26% |
| de | 526 | 2 | 66.12 | 66.83 | 69% |
| es | 527 | 1 | 66.82 | 68.04 | 71% |
| fr | 510 | 18 | 67.54 | 68.74 | 65% |
| ja (style fixed) | 525 | 3 | 50.62 | **51.14** | 67% |
| mr (style fixed) | 492 | 36 | 57.62 | 59.55 | 67% |
| pa | 527 | 1 | 60.24 | 62.60 | 32% |
| ta | 526 | 2 | 66.62 | 68.28 | 32% |
| te | 526 | 2 | 66.98 | 70.50 | 32% |
| zh (style fixed) | 477 | 51 | 33.78 | **43.82** | 46% |
| **All (first version)** | **6,084** | | **63.43** | **65.13** | 50% |


- **Placeholders kept: 100%** in every language, reused and fresh.
- **Refusals** are mostly the model writing numbers in local digits (Bengali, Arabic, Marathi). Those strings go to the model as before.
- **Decision:** near-twin reuse is **on** by default.

### 13.4 The experiment: app versions like CI/CD commits

**Design:**
- v1 = 2,000 random KDE4 strings (seed 2026).
- v2 / v3 / v4 = v1 + 20 / 100 / 200 **new** strings (1% / 5% / 10%), **3 repeats** each. Every new string is used in no other version, so a kept memory has never seen it.
- 12 languages. One m5.large ran all steps in a row (`RUNNER_TASK=tm-experiment`), keeping the memory between them.
- The last step reran v4 with the memory emptied **and** twin reuse off: the pipeline before this work (the "without the fixes" baseline).
- A smaller set was chosen to finish faster. The savings are percentages, so they do not depend on app size, and the full-size speed is known from section 11.

**Measured per step** (telemetry summed over the 12 language files; "work" = translating the files; "step" = the whole step including about 29 s of fixed start-up: image build check, model download and load):

| Step | Strings × langs | Exact hits | Twin hits | Sent to model | Work (s) | Step (s) | Real tokens in + out | DB queries | Lookup (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v1 (empty memory) | 24,000 | 0 | 365 | 23,650 | 2,492.7 | 2,578.9 | 261,431 | 12 | 43.8 |
| v2-r1 | 24,240 | 23,883 | 0 | 357 | 54.6 | 93.4 | 3,972 | 12 | 223.4 |
| v3-r1 | 25,200 | 23,883 | 59 | 1,261 | 155.5 | 186.9 | 13,706 | 12 | 271.7 |
| v4-r1 | 26,400 | 23,883 | 71 | 2,446 | 287.5 | 319.4 | 26,468 | 12 | 224.7 |
| v2-r2 | 24,240 | 23,883 | 12 | 345 | 55.6 | 86.9 | 4,051 | 12 | 229.6 |
| v3-r2 | 25,200 | 23,883 | 33 | 1,284 | 168.0 | 199.4 | 13,957 | 12 | 237.1 |
| v4-r2 | 26,400 | 23,883 | 81 | 2,436 | 316.7 | 348.4 | 27,646 | 12 | 253.5 |
| v2-r3 | 24,240 | 23,883 | 0 | 357 | 96.4 | 127.8 | 4,816 | 12 | 262.9 |
| v3-r3 | 25,200 | 23,883 | 12 | 1,305 | 152.6 | 184.0 | 13,664 | 12 | 254.6 |
| v4-r3 | 26,400 | 23,883 | 59 | 2,458 | 295.0 | 327.3 | 27,746 | 12 | 261.9 |
| **v4 without the fixes** | 26,400 | 0 | 0 | 26,415 | 2,761.1 | 2,794.2 | 290,622 | 12 | 46.4 |

0 failures in all 132 files.

**Savings** (average of the 3 repeats against a run without the fixes):
- v4: measured.
- v2 and v3: from the measured rate of the v4 baseline, 0.1046 s of work per string-language. v1 ran at 0.1039, which confirms that time grows in a straight line with strings when nothing is reused.
- Cost: m5.large on-demand $0.101/h (AWS price list, ap-south-1).

| Change | Without the fixes: step | With: step (avg) | **Speed-up** | Model tokens without → with | **Token reduction** | Cost per run without → with |
|---|---:|---:|---:|---:|---:|---:|
| v2: +1% | ≈2,568 s | 102.7 s | **25×** | ≈266,844 → 4,280 | **98.4%** | $0.072 → $0.0029 |
| v3: +5% | ≈2,669 s | 190.1 s | **14×** | ≈277,412 → 13,776 | **95.0%** | $0.075 → $0.0053 |
| v4: +10% | 2,794 s (measured) | 331.7 s | **8.4×** | 290,622 → 27,287 | **90.6%** | $0.078 → $0.0093 |

- **Translation work alone** (without the fixed ~30 s start-up): 37× (v2), 17× (v3), 9.2× (v4).
- **Quality** (`tools/kde4.py score --version`): v4 served from the memory chrF **63.38**, v4 without the fixes **63.37**, v1 **63.38**; placeholders 100% in all, 0 missing.

**Lookups:**
- **12 database queries per step**, one per language. The old per-file, 1,000-string queries would need 36 for these 2,020–2,200-string files (3 per language), and one more per extra file.
- Loading about 2,000 entries per language took 224–272 ms in total for all 12 languages. Each check after that is a dictionary lookup inside the program.

**Near-twins:**
- On the first release, 365 of 24,000 (1.5%) were reused instead of translated. That is fewer than the 5.2% in the full set, because twins become rarer in a 2,000-string sample.
- In later steps, 12–81 per step.

**Finding: strings kept in English are retried on every run.**
- Every step after v1 had 117 exact misses among the unchanged strings (23,883 hits of 24,000). These are the `<unk>` strings (section 12.2) that are deliberately not saved.
- They are sent to the model again each time, although with the same engine version the result cannot change. That is **33% of v2's model work** (117 of 357 strings).
- Remembering them per engine version would remove this.

**Raw data:**
- `s3://<data-bucket>/runs/ec2-20261017t175415z/experiment/steps.tsv` (step timings);
- runs `ec2-20261017t175415z-<step>` (output, telemetry in CloudWatch `/cloud-loc/ec2-runner`);
- twin and `<unk>` analyses in `s3://<data-bucket>/analysis/twin-quality/`.

**To repeat:**
```bash
python aws/tools/kde4.py versions Dataset-Translated.xlsx tm/
aws s3 cp tm/ s3://<data-bucket>/datasets/kde4/tm/ --recursive
RUNNER_TASK=tm-experiment RUNNER_TIMEOUT_SECONDS=14400 TM_INPUT_PREFIX=s3://<data-bucket>/datasets/kde4/tm \
  TM_STEPS="v1:reset v2-r1 v3-r1 v4-r1 v2-r2 v3-r2 v4-r2 v2-r3 v3-r3 v4-r3 v4-cold=v4-r1:reset:plain" \
  bash aws/infra/run_on_ec2.sh
```

## 14. Remaining bottlenecks: four fixes — runs `ec2-20261018t13…`–`t143618z` (2026-10-18)

**Flaws found** by timing a small commit end to end (section 13 ran all steps on one machine, so its step times left out the per-commit machine start):

| Part of a CI run | Time | Problem |
|---|---:|---|
| Start a fresh EC2 machine, then stop it | ~106 s | a new machine boots and installs Docker for every commit |
| Docker image build + model set-up | ~80 s | a fresh machine builds the image from scratch. The 629 MB model sat in the build folder, so every build also copied it in (5 s transfer + 8.6 s COPY + 2.6 s export). The model was downloaded and test-loaded every run. |
| Strings kept in English (`<unk>`) | ~23 s (+1% commit) | never saved, so all 117 went back to the model every run, with the same result |
| Small commits | 0.15–0.27 s per string, against 0.10 | ~30 new strings per language made one batch, so one of the two engine workers was idle |

**Fixes:**
1. **`translator/.dockerignore`** keeps the model folder out of the image build (it is mounted at run time).
2. **Kept-English strings remembered** per engine version: an empty translation saved as `nllb-600M-ct2+english`. Later runs skip the model and still list them. A new engine version retries them.
3. **Ready runner** (`run_on_ec2.sh`, `RUNNER_REUSE=true` by default):
   - one runner tagged `cloud-loc-runner=ready` is kept **stopped** between runs, so its disk keeps the built image and the model, and it is started for each run;
   - shutdown behaviour is *stop*, and the workspace is unpacked fresh while the model is kept;
   - a model already on the machine for the current `MODEL_KEY` skips the download and test load (`model_cache.py present/mark`);
   - `teardown.sh` terminates ready runners;
   - a runner still *stopping* from the previous run is waited for (bug found in the first measurement series, `d8bb6b1`).
4. **`plan_balanced`:** a model call small enough for two batches is split into two equal-token, length-sorted batches, one per engine worker. Large calls are unchanged.

**Measured: one +5% commit, end to end from the laptop** (launch → result), same day, same machine type:

| Part | Before: pre-fix code `4e90381`, fresh machine (`ec2-20261018t143022z`, v3-r2) | After: current code, ready runner (`ec2-20261018t143618z`, v3-r3) | Saved |
|---|---:|---:|---:|
| Machine start + stop | 106.1 s | 37.5 s | 68.6 s |
| Image + model set-up | 79.5 s | 18.7 s | 60.8 s |
| Translation work | 168.1 s | 127.2 s | 40.9 s |
| Phase 1 + 3 | 2.9 s | 2.8 s | — |
| **Total** | **356.6 s (5.9 min)** | **186.2 s (3.1 min)** | **1.9× faster** |
| Strings sent to the model | 1,284 (incl. 117 kept-English retries) | 1,188 | |
| Model tokens in + out | 13,957 | 12,233 | |
| Failures | 0 of 12 files | 0 of 12 files | |

The two commits need different new strings, because each must be new to the memory. v3-r3 is somewhat lighter than v3-r2: with the old code they took 152.6 s and 168.0 s of work (section 13). The matched comparison of translation work below removes that difference.

**Translation work on the same files, pre-fix code (section 13) vs current code:**

| Commit file | Sent to model before → after | Work before → after |
|---|---:|---:|
| v2-r1 (+1%) | 357 → 240 | 54.6 s → 38.2 s (−30%) |
| v2-r2 (+1%) | 345 → 228 | 55.6 s → 39.2 s (−29%) |
| v2-r3 (+1%) | 357 → 240 | 96.4 s → 74.5 s (−23%) |
| v3-r1 (+5%) | 1,261 → 1,144 | 155.5 s → 127.4 s (−18%) |
| v3-r3 (+5%) | 1,305 → 1,188 | 152.6 s → 127.2 s (−17%) |
| v4-r1 (+10%) | 2,446 → 2,329 | 287.5 s → 252.1 s (−12%) |

**Per fix:**
- **Fix 2:** exactly 117 fewer model strings per commit, and all 117 were found in the memory (`tm_kept_english` = 117).
- **Fix 4:**
  - time per model string fell about 10% on +5% commits (0.123 → 0.111 s) and 8% on +10% (0.118 → 0.108 s);
  - **no gain on +1% commits** (0.153 → 0.159 s), where per-file fixed costs dominate;
  - smaller than predicted (~40%).
- **Fixes 1 + 3:**
  - machine start + stop: 102–108 s on new machines, **37–44 s** on the ready runner (4 runs);
  - set-up: 77–80 s on new machines, **18.5–36 s** on the ready runner.

**Other measured commits with all fixes, ready runner, end to end:**
- +1% (v2-r3, its longer strings): **156.9 s**;
- +5% (v3-r1): **186.0 s**;
- +10% (v4-r1): **312.2 s**.

**Cost:**
- the +5% commit: $0.0100 → **$0.0052** of machine time (m5.large $0.101/h);
- the stopped ready runner's 40 GB gp3 disk: **$3.65/month** ($0.0912/GB-month, AWS price list) whether used or not.

The main gain is time.

**Raw data:** `aws-results/tm-fixes/` (wall times, step timings, telemetry) and runs `ec2-20261018t132556z` … `ec2-20261018t143618z` in S3 and CloudWatch.

## 15. Translation memory in S3 instead of RDS — runs `ec2-20261018t214617z`, `ec2-20261018t225738z` (2026-10-18/19)

**Why:**
- **Little use of the database:** since section 13, the memory is loaded once per language per run and then used from a dictionary, so the always-on RDS SQL Server (`db.t3.micro`, $0.029/h, about $21/month, plus about $2.30/month storage) is used for one bulk read per language and some writes.
- **Extra cost elsewhere:** it also needs the ODBC driver in the image, a secret and a firewall rule.

**What changed** (`translator/memory_store.py`, `TM_STORE=s3`, the default):
- **Layout:** `memory/<engine key>/<lang>/base-<n>.jsonl.gz` plus `runs/<run>-<k>.jsonl.gz`, gzipped JSON lines; `engine.txt` names the full engine version.
- **Concurrent runs:** each commit of a run writes its own file, so two runs at the same time cannot overwrite each other.
- **Merging:** a load that finds 8 unmerged run files merges them into the next base.
  - The new base is created only if absent (S3 `IfNoneMatch`), so of two runs merging at once one wins and the other carries on.
  - Run files already in an older base are then removed.
- **SQL kept as an option:** the SQL table is still available (`SqlMemory`, `TM_STORE=sql`) behind the same load/save/commit interface. Phase 2 skips database set-up in S3 mode and resets with `memory_store.py reset`.

**Real check:** the same three commits as the SQL runs of section 14 (v1 with the memory emptied, then +1% v2-r1 and +5% v3-r1), same translation code, same ready runner, store changed only.

| Commit | Store | To model | Memory hits | Twins | Kept English | Output files identical to SQL |
|---|---|---:|---:|---:|---:|---:|
| v1 | SQL | 23,650 | 0 | 365 | 0 | — |
| v1 | S3, first version | 23,650 | 0 | 365 | 0 | 12 / 12 |
| v1 | **S3, final** | 23,650 | 0 | 365 | 0 | **12 / 12** |
| +1% | SQL | 240 | 24,000 | 0 | 117 | — |
| +1% | S3, first version | 240 | 24,000 | 0 | 117 | 12 / 12 |
| +1% | **S3, final** | 240 | 24,000 | 0 | 117 | **12 / 12** |
| +5% | SQL | 1,144 | 24,000 | 59 | 117 | — |
| +5% | S3, first version | 1,145 | 24,000 | 58 | 117 | 11 / 12 (one Marathi string) |
| +5% | **S3, final** | 1,144 | 24,000 | 59 | 117 | **12 / 12** |

0 failures in all 72 S3 files.

**Flaw found by the first S3 run:** one Marathi string was reused as a near-twin with SQL but translated by the model with S3.
- **Cause:** when several model translations share a template, the twin index kept the first entry it saw. SQL returned entries ordered by hash (an order SQL does not guarantee), and S3 by file.
- **Fix:** entries are sorted by hash before indexing, so both stores choose the same twin source. This also removes a hidden order dependence from the SQL version.

**Speed: S3 is slower per request than the database:**

| Commit | SQL lookups | S3 first version | S3 final (parallel downloads) | Translation work SQL → S3 final |
|---|---:|---:|---:|---:|
| +1% | 0.21 s | 2.42 s | 1.37 s | 38.2 s → 40.1 s (+5%) |
| +5% | 0.24 s | 2.22 s | 2.29 s | 127.4 s → 131.1 s (+3%) |

- **Per-request latency:** each S3 request takes tens of milliseconds, against a few for the in-VPC database. Saving new entries also costs about 0.4–1.0 s per commit, against 0.03 s for SQL.
- **Net effect:** about **2–4 s more per commit** (3–5% of translation work).
- **Not yet done:** prefetching all languages' files at the start of a run, in parallel, would hide most of this.

**What it buys:**
- no always-on database (about **$23/month**) once RDS is removed;
- no ODBC driver in the image (faster builds on a fresh machine, not yet measured);
- no database secret or firewall rule;
- S3 durability.

These savings are realized only when the RDS instance and the ODBC driver are removed, which is not yet done; that removal is irreversible for the old SQL memory, which is only a cache.

**Raw data:**
- `aws-results/s3-memory/` and `aws-results/s3-memory-2/` (telemetry, step timings, outputs);
- runs `ec2-20261018t214617z-*` and `ec2-20261018t225738z-*` in S3 and CloudWatch.

## 16. BLEU, spBLEU and COMET (2026-10-20)

**Why:** sections 8–15 reported chrF and placeholder survival only. This section adds the lexical and neural metrics that the previous semester's report and the Track 1 team use, computed on the saved outputs with `tools/kde4.py score`.

**Method:**
- **BLEU:** SacreBLEU 2.4.3, with the `13a` tokenizer; `zh` for Chinese and `ja-mecab` (MeCab) for Japanese.
- **spBLEU:** BLEU on FLORES-200 SentencePiece pieces (`flores200`), the usual cross-language BLEU for NLLB.
- **COMET:** `Unbabel/wmt22-comet-da` (unbabel-comet 2.2.6, CPU), on the **same fixed random sample of 1,000 strings in every language** (seed 2026), 12,000 translations. The full 120,648 would take about 5–6 hours on CPU, at about 6 translations per second.
- **Not scorable:** the beam-size and placeholder benchmarks (sections 3–10) kept chrF scores only, not their outputs, so BLEU and COMET cannot be added for them without re-running them.

**Full KDE4 run (section 11), all 10,054 strings × 12 languages:**

| Lang | chrF | BLEU | spBLEU | COMET (1,000-string sample) |
|---|---:|---:|---:|---:|
| ar | 55.42 | 32.45 | 39.44 | 0.8296 |
| bn | 64.38 | 37.38 | 52.52 | 0.8866 |
| de | 64.22 | 39.92 | 45.88 | 0.8639 |
| es | 71.50 | 50.65 | 53.15 | 0.8704 |
| fr | 68.89 | 44.18 | 46.65 | 0.8495 |
| hi | 64.75 | 43.24 | 49.75 | 0.8526 |
| ja | 50.92 | 32.35 (MeCab) | 39.07 | 0.8643 |
| mr | 58.45 | 35.76 | 44.38 | 0.8249 |
| pa | 61.53 | 38.56 | 50.42 | 0.8689 |
| ta | 64.10 | 37.75 | 49.53 | 0.8840 |
| te | 65.28 | 40.85 | 53.82 | 0.8932 |
| zh | 39.84 | 42.48 (zh) | 42.55 | 0.8530 |
| **All** | **63.29** | 39.63 (mean of 12) | **47.90** | **0.8617** |

**Chinese:** chrF (39.84) ranks it far below every other language, but BLEU (42.48), spBLEU (42.55) and COMET (0.853) place it in the middle. chrF's character 6-grams are harsh on Chinese, where a word is one or two characters; COMET is the fairer comparison across scripts.

**Translation memory experiment (section 13), v4 = 2,200 strings × 12 languages:**

| Run | chrF | spBLEU | BLEU (mean of 12) |
|---|---:|---:|---:|
| v4 served from the memory | 63.38 | 48.07 | 40.52 |
| v4 from scratch (previous pipeline) | 63.37 | 48.04 | 40.45 |
| v1 first release | 63.38 | 48.18 | 40.88 |

- **How much differs:** the two v4 runs differ in only **170 of 26,400** translations, the strings adapted from near-twins.
- **COMET on exactly those 170:** **0.8654 from the memory against 0.8689 from scratch**; the memory version scores higher on 80, the scratch version on 81. chrF on the same strings is 58.06 against 55.25.

**Correction to section 13.3:**
- **What it said:** near-twin reuse scored *higher* than fresh translation, based on chrF.
- **What COMET shows:** **equal** quality, a 0.0035 difference and an even split.
- **Revised claim:** near-twin reuse matches fresh translation in quality, and makes the same term consistent across strings.

**Raw data:**
- `aws-results/ec2-20261016t214343z/scores-bleu.json` and `scores-comet.json`;
- `aws-results/ec2-20261017t175415z/scores-bleu-*.json` and `comet-differing.json`.
