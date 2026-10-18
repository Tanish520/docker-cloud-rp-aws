# Translation Memory: Why We Rebuilt It, How We Tested It, and What We Got

**Project:** AWS localization pipeline (Track 3: CI/CD & Memory Optimization)
**Semester task:** *"Build / update Translation Memory (TM) to reduce lookups"* (weeks 3–4), then *"benchmark the optimized pipeline to quantify token usage reduction, lookup speedups and final cost savings"* (weeks 5–7)
**Experiment run:** `ec2-20261017t175415z`, 2026-10-17/18, AWS ap-south-1
**Code:** branch `aws-migration`, commits `d66d640` → `4e90381`; second round of fixes `6cd6562` → `d8bb6b1` (section 10)

---

## 1. The short version

The pipeline used to translate **everything, every time**, even when a developer had changed only a handful of strings. We changed the translation memory so the pipeline:

- **remembers** what it has already translated, between runs;
- **knows which model made each translation**, so a model change never reuses stale ones;
- **checks the memory in one database query per language**, not one per 1,000 strings per file;
- **reuses near-identical strings** ("8 Cards" from "20 Cards"), after a quality check showed this is safe.

We then simulated real software releases on the KDE4 dataset, 12 languages. These are the results against the previous pipeline:

| A commit that changes… | Previous pipeline | New pipeline | Faster | Model work saved | Cost per run |
|---|---|---|---|---|---|
| **1%** of the strings | ~43 min | **1.7 min** | **25×** | **98.4%** | $0.072 → $0.003 |
| **5%** of the strings | ~44 min | **3.2 min** | **14×** | **95.0%** | $0.075 → $0.005 |
| **10%** of the strings | 47 min (measured) | **5.5 min** | **8.4×** | **90.6%** | $0.078 → $0.009 |

**Translation quality did not change:** chrF 63.38 with the memory against 63.37 without, and placeholders were kept 100% in both.

**A second round of fixes** (section 10) then removed most of the remaining set-up time per CI run. Measured end to end, including starting the machine, a 5% commit went from **5.9 min to 3.1 min** (1.9× faster).

---

## 2. Background: what the pipeline does and what a translation memory is

### 2.1 The pipeline in one paragraph

Developers write the app's text in English, in `.resx` files. On every change, the CI/CD pipeline:
1. **extracts** the strings (Phase 1);
2. **translates** them into 12 languages with a machine-translation model (Phase 2: NLLB-200, running on a 2-CPU AWS machine);
3. **writes** the translated `.resx` files back (Phase 3).

Phase 2 is where almost all the time goes. In the full KDE4 run (`BATCHING_RESULTS.md` section 11), the model used **99.6%** of the translation time.

### 2.2 What a translation memory (TM) is

A translation memory is a **store of translations the pipeline has already made**. It's a table in a database:

| English | Language | Translation |
|---|---|---|
| Open file | German | Datei öffnen |
| Copy %1 to %2 | German | Kopiere %1 nach %2 |

Before asking the model to translate a string, the pipeline checks the memory:
- **Found (a "hit"):** reuse it instantly, with no model work.
- **Not found (a "miss"):** send it to the model, then save the result for next time.

### 2.3 What "reduce lookups" means

Two kinds of lookups cost time:
- **Model lookups:** asking the model to translate. This is slow (about 0.1 s per string per language) and is where the cost is.
- **Database lookups:** asking the memory whether it has a string. This is fast, but each trip to the database still takes time.

The task asks us to reduce **both**.

---

## 3. What was wrong with the previous pipeline

A working memory already existed (built in an earlier semester). It had five problems:

### Problem 1: the memory was wiped before every run

The setting `RESET_TM=true` was the default. We had used it on purpose, so every speed test started from zero. In real CI/CD, though, it means:

> **Monday:** the app has 10,000 strings → translate all 10,000 → **3.6 hours**.
> **Tuesday:** a developer adds 5 strings → memory wiped → translate all 10,005 again → **3.6 hours**.

For 5 new strings, that is almost entirely wasted work.

### Problem 2: no check of *which model* made a translation

Each memory entry stored a model name, but lookups never checked it. If we switched to a better model, or changed a setting such as beam size, the pipeline would keep reusing translations from the old setup indefinitely.

### Problem 3: too many database trips

Each language file asked the database separately, and each question could carry at most 1,000 strings. It's like going to the shop once per item instead of once with a shopping list.

### Problem 4: exact matches only

A string was found only if it was identical, character for character. These pairs were all treated as unrelated:

- "20 Cards" / "8 Cards"
- "Copy %1 to %2" / "Copy %3 to %4"
- "Border:" / "Border"

In KDE4, **526 of the 10,054 strings (5.2%)** have such a "near-twin".

### Problem 5: a pointless 2-second pause after every file

The code paused 2 seconds after each file, to give Azure's logging service time to send data. AWS has no such service, so the pause was pure waiting: **24 seconds per 12-language run**. It doesn't matter in a 3-hour run, but it would be most of a 30-second run served from memory.

---

## 4. What we changed

### Fix 1: keep the memory, and label each translation with its "engine"

- **Keep the memory.** It's no longer wiped by default (`RESET_TM=false`). Each run only translates strings that are new or changed.
- **Label each translation.** Every saved translation records its **engine version**: the model plus every setting that changes the output (beam size, output length cap, placeholder handling, the `<unk>` rule). For example:

  `facebook/nllb-200-distilled-600M | ct2-int8 | beam=2 | cap=2x+10 | placeholders=square | unk=english`

- **Match the label.** A translation is reused only if its label matches the current engine. Change the model or a setting and the old entries simply stop matching, so those strings are translated fresh. Nothing stale is ever reused, and nothing has to be deleted by hand.

### Fix 3: check the memory in one trip per language

- **At the start of a run,** the pipeline fetches each language's saved translations **in one database query** and keeps them in the program's own memory (a Python dictionary).
- **Every check after that** is a lookup inside the program, taking microseconds, with no network trip.
- **New translations are added to that dictionary** as they are made, so later files in the same run reuse them without asking the database at all.
- **The database table is now ordered by language first,** so "give me all German entries" is one smooth read.

### Fix 2: reuse near-twins

When the exact text isn't in the memory, the pipeline looks for a twin, a saved string that differs only in **numbers, placeholders or trailing punctuation**, and adapts its translation:

```
Memory has:  "20 Cards"      → "20 Karten"
New string:  "8 Cards"       → swap the number → "8 Karten"          (no model needed)

Memory has:  "Copy %1 to %2" → "Nach %2 kopieren: %1"
New string:  "Copy %3 to %4" → swap each placeholder → "Nach %4 kopieren: %3"

Memory has:  "Border"        → "边框"  (Chinese)
New string:  "Border:"       → add the colon the Chinese way → "边框："
```

**Safety rules:** the twin is **refused**, and the string goes to the model as normal, if:
- the number or placeholder doesn't appear exactly once in the saved translation. For example, the model wrote the number in Arabic or Bengali digits, or dropped it.
- the same value appears twice in the English ("1 of 1"), so it's unclear which one maps where.
- the saved translation was itself adapted. Only real model translations are used as a source, so changes never pile up.

**Twins inside one file:** if a single file contains both "20 Cards" and "8 Cards", only one goes to the model and the other is adapted from it.

### Also fixed

- **The 2-second pause** now happens only when an Azure logging service is actually connected.
- **New measurements** in the pipeline's telemetry, needed for the paper:
  - database queries and lookup time;
  - exact and twin memory hits;
  - strings sent to the model;
  - **real** model tokens in and out, and the padding share.

  The old token figures were guesses from character counts.

### Considered but not built: skipping unchanged files

The idea was to skip a file completely if it hadn't changed since the last run. The measurement showed it isn't worth it: a file served entirely from the memory already takes **about 0.1 seconds** (`BATCHING_RESULTS.md` section 12.3).

---

## 5. Before turning on twin reuse: is it safe?

Reusing translations is only useful if they are as good as the model's own. We checked this **before** switching it on.

### 5.1 How we tested it

- **Input:** every twin group in the full KDE4 dataset: 440 groups, 968 strings, **528 reusable pairs**, in all **12 languages**.
- **Two translations of each string:**
  1. **fresh:** the model translates it, with the exact production settings;
  2. **reused:** adapted from its twin's translation.
- **Scoring:** both were scored against the KDE4 reference translations with **chrF**, a standard score from 0 to 100 that counts how many character sequences match the reference. Higher is better.

### 5.2 A mistake we caught, and fixed with data

The first version **guessed** how each language writes trailing punctuation. It assumed Japanese uses the full-width colon `：`. On that guess, Japanese reused translations scored **lower** than fresh ones (49.00 against 50.62).

So we **counted** how the KDE4 references actually end, over every row that ends in `:` `...` `?` `!` or `.`:
- **Colon:** only Chinese uses `：` consistently. Japanese uses a plain `:` slightly more often (749 against 657).
- **Ellipsis:** `...` stays `...` in every language.
- **Full stop:** Hindi, Bengali and Punjabi use `।`, but Marathi keeps `.`.

We replaced the guesses with these counts. Japanese then rose to **51.14**, above fresh, and Chinese rose from 39.20 to **43.82**.

### 5.3 Result: reuse is as good as or better than fresh translation in every language

| Language | Reused | Refused (went to model) | chrF fresh | chrF reused |
|---|---:|---:|---:|---:|
| Hindi | 506 | 22 | 67.12 | **70.62** |
| Arabic | 488 | 40 | 55.51 | **56.08** |
| Bengali | 454 | 74 | 64.10 | **65.34** |
| German | 526 | 2 | 66.12 | **66.83** |
| Spanish | 527 | 1 | 66.82 | **68.04** |
| French | 510 | 18 | 67.54 | **68.74** |
| Japanese | 525 | 3 | 50.62 | **51.14** |
| Marathi | 492 | 36 | 57.62 | **59.55** |
| Punjabi | 527 | 1 | 60.24 | **62.60** |
| Tamil | 526 | 2 | 66.62 | **68.28** |
| Telugu | 526 | 2 | 66.98 | **70.50** |
| Chinese | 477 | 51 | 33.78 | **43.82** |

- **Placeholders** (`%1`, `{name}`, `<b>`) were kept **100%** in every language, both ways.
- **Why reuse can score *higher*:** the model translates each string on its own and is sometimes inconsistent. It might translate "Points" well in "42 Points" but poorly in "78 Points". Reuse copies the *same* good translation to every twin, so the app is also **more consistent**, which matters in software.
- **Refusals** were mostly cases where the model wrote numbers in local digits (Bengali, Arabic, Marathi), so they couldn't be swapped safely. Those strings simply went to the model.

**Decision:** twin reuse is **on** by default. It can be switched off with `TM_TWINS=off`.

---

## 6. The experiment: simulating real software releases

### 6.1 Why this experiment

Our speed tests so far each translated a dataset **once**. A translation memory only pays off **across runs**, when the same app is translated again after a change. To measure it honestly, the test has to copy what really happens in a project: **a first release, then commits that change a small part of the app.**

### 6.2 The design

From KDE4 we built app versions like a commit history:

| Version | Content | Like… |
|---|---|---|
| **v1** | 2,000 random KDE4 strings | the first release |
| **v2** | v1 + **20 new** strings (1%) | a small commit |
| **v3** | v1 + **100 new** strings (5%) | a medium commit |
| **v4** | v1 + **200 new** strings (10%) | a big commit |

- **Every version runs in all 12 languages,** so each string means 12 translations.
- **v2, v3 and v4 were each run 3 times** (r1, r2, r3) to show the numbers are stable and not a one-off.
- **Every "new" string is truly new.** Each version uses its own new strings, never used in any other version, so the memory has genuinely never seen them.
- **The new strings are real KDE4 strings** held back from v1, so they have reference translations for checking quality.

### 6.3 The comparison point: the previous pipeline

The final step reran **v4 with the memory emptied and twin reuse switched off**. That's the pipeline as it was before this work, with everything translated from scratch. It gives a **measured** "before" number on the same machine, on the same day.

- **v4 was measured directly.**
- **v2 and v3 "before" times were calculated** from the measured speed of that run: 0.1046 seconds of translation work per string per language.
- **Why calculating is fair:** when nothing is reused, time grows in a straight line with the number of strings. Our first-release step v1, which also translated everything, ran at **0.1039 s**, almost identical, which confirms the straight line.

### 6.4 Why a smaller app (2,000 strings) instead of all 10,054

- **Speed:** running everything at full size would have taken about 13 hours. The smaller set ran in **about 2.5 hours** on one machine.
- **Fairness:** the savings are **percentages**. A 1% change is 1% whether the app has 2,000 or 10,000 strings, and the memory either finds a string or doesn't. The full-size speed was already measured in the full KDE4 run (section 11 of `BATCHING_RESULTS.md`).
- **A bonus:** the small runs were cheap enough to **repeat 3 times**, which makes the result more reliable than one big run.

### 6.5 How it ran

- **One machine:** a single AWS m5.large (2 CPUs, 8 GB) ran all 11 steps in a row, keeping the memory between steps, as a real CI server would across commits.
- **Separate records:** each step was its own pipeline run (Phase 1 → 2 → 3) with its own telemetry.
- **Cost:** $0.101 per hour (AWS on-demand price for m5.large in ap-south-1).

---

## 7. Results

### 7.1 Every step, measured

*"Work"* is the time spent translating the files. *"Step"* also includes about 30 seconds of fixed start-up per run: checking the container image, downloading the model, loading it into memory.

| Step | Strings × languages | Found in memory | Twins reused | Sent to model | Work (s) | Step (s) | Model tokens (in + out) |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1, first release | 24,000 | 0 | 365 | 23,650 | 2,492.7 | 2,578.9 | 261,431 |
| v2-r1 (+1%) | 24,240 | 23,883 | 0 | 357 | 54.6 | 93.4 | 3,972 |
| v3-r1 (+5%) | 25,200 | 23,883 | 59 | 1,261 | 155.5 | 186.9 | 13,706 |
| v4-r1 (+10%) | 26,400 | 23,883 | 71 | 2,446 | 287.5 | 319.4 | 26,468 |
| v2-r2 | 24,240 | 23,883 | 12 | 345 | 55.6 | 86.9 | 4,051 |
| v3-r2 | 25,200 | 23,883 | 33 | 1,284 | 168.0 | 199.4 | 13,957 |
| v4-r2 | 26,400 | 23,883 | 81 | 2,436 | 316.7 | 348.4 | 27,646 |
| v2-r3 | 24,240 | 23,883 | 0 | 357 | 96.4 | 127.8 | 4,816 |
| v3-r3 | 25,200 | 23,883 | 12 | 1,305 | 152.6 | 184.0 | 13,664 |
| v4-r3 | 26,400 | 23,883 | 59 | 2,458 | 295.0 | 327.3 | 27,746 |
| **v4, previous pipeline** | 26,400 | 0 | 0 | 26,415 | 2,761.1 | 2,794.2 | 290,622 |

All **132 language files** (11 steps × 12) succeeded, with **0 failures**.

### 7.2 New pipeline against previous pipeline

Averages of the 3 repeats:

| Commit size | Previous: time per run | New: time per run | **Speed-up** | Previous → new model tokens | **Tokens saved** | Previous → new strings sent to model |
|---|---:|---:|---:|---:|---:|---:|
| +1% | ≈2,568 s (42.8 min) | 102.7 s (1.7 min) | **25×** | ≈266,844 → 4,280 | **98.4%** | ≈24,240 → 353 |
| +5% | ≈2,669 s (44.5 min) | 190.1 s (3.2 min) | **14×** | ≈277,412 → 13,776 | **95.0%** | ≈25,200 → 1,283 |
| +10% | 2,794 s (46.6 min), measured | 331.7 s (5.5 min) | **8.4×** | 290,622 → 27,287 | **90.6%** | 26,415 → 2,447 |

**Translation work alone,** without the fixed ~30 s start-up: **37×** (+1%), **17×** (+5%), **9.2×** (+10%).

### 7.3 Cost

| Commit size | Previous pipeline | New pipeline | Saving |
|---|---:|---:|---:|
| +1% | $0.072 | $0.0029 | 96% |
| +5% | $0.075 | $0.0053 | 93% |
| +10% | $0.078 | $0.0093 | 88% |

These are machine costs per run (m5.large, $0.101/h). The database costs the same either way. Small per run, but a busy project runs the pipeline on every commit, many times a day.

### 7.4 Quality: unchanged

Scored against the KDE4 references for the same 2,200 strings (v4) in 12 languages:

| Run | chrF | Placeholders kept | Missing translations |
|---|---:|---:|---:|
| v4 served from the memory (new pipeline) | **63.38** | 100% | 0 |
| v4 translated from scratch (previous pipeline) | **63.37** | 100% | 0 |
| v1 first release (new pipeline) | 63.38 | 100% | 0 |

The memory hands back exactly what the model produced earlier, so quality is the same. Twins add a tiny gain.

### 7.5 Database lookups

| | Previous pipeline | New pipeline |
|---|---|---|
| Database queries per run | 36 for these files (3 per language: one per 1,000 strings), plus more for every extra file | **12** (one per language, however many files) |
| Time to load the memory, all 12 languages | not measured separately | **224–272 ms** in total |
| Each string check after loading | a database round trip | a lookup inside the program (microseconds) |

### 7.6 Near-twins in practice

- **First release (v1):** **365 of 24,000** translations (1.5%) came from twins instead of the model. That's fewer than the 5.2% in the full dataset, because a random 2,000-string sample contains fewer twin pairs. At full size, expect more.
- **Later commits:** 12–81 twins per step, among the new strings.

---

## 8. Why the numbers look like this

### 8.1 Why smaller commits give bigger speed-ups

The previous pipeline's time is **fixed**: about 45 minutes, whatever changed. The new pipeline's time depends on **how much changed**: 20 new strings is little work, 200 is more. So the ratio is largest for small commits (25×) and smaller for bigger ones (8.4×).

That's exactly the behaviour CI/CD needs, because most commits are small.

### 8.2 Why a 1% change is "only" 25× faster, not 100×

Three things put a floor under the new pipeline's time:

1. **Start-up (~30 s per run):** downloading and loading the model happens on every run, even when there's almost nothing to translate. For a 1% commit, that's about a third of the run.
2. **Strings kept in English are retried on every run.** About 117 strings per run (mostly Chinese) are ones the model can't write correctly (`<unk>`, see `BATCHING_RESULTS.md` section 12.2). We deliberately don't save them, so they go back to the model every time, although with the same engine the answer can't change. That's **a third of the model work** in the +1% runs (117 of 357 strings).
3. **Tiny batches are less efficient.** With only ~30 new strings per language, the model's batches are small and poorly filled: 58–85% padding, against 16% when translating everything. So each string costs about **0.19 s instead of 0.10 s**.

All three were then improved; see section 10.

### 8.3 Why reuse doesn't hurt quality

A memory hit returns **the exact translation the same model made before**, and the engine label guarantees that. Twin reuse was checked separately (section 5) and is as good or better. So the speed comes from **not repeating work**, not from cutting corners.

---

## 9. Putting it in context: the whole semester's improvement

The memory work builds on the batching work done earlier (`BATCHING_RESULTS.md` sections 4–11). Taken together, for the "+10% commit" app (v4: 2,200 strings × 12 languages = 26,400 translations):

| Pipeline | Time for this commit | Compared with the start of the semester |
|---|---:|---:|
| Start of semester: beam 5, one batch at a time, memory wiped | ≈1.9 h (estimated from the measured benchmark speed of 13,631 translations/hour) | 1× |
| After the batching work: beam 2, sorted parallel batches, memory wiped | 46.6 min (measured) | ≈2.5× faster |
| **After the memory work (now)** | **5.5 min (measured)** | **≈21× faster** |

The first row is an estimate from the benchmark speed in `BATCHING_RESULTS.md` section 8, not a run of this exact app.

---

## 10. Second round: removing the remaining bottlenecks

### 10.1 Why a second round

The experiment in section 6 ran every step **on one machine that stayed on**. That measured the memory fairly, but it hid a cost that real CI pays on **every commit**: starting a machine and preparing it.

Timing one small commit end to end showed where the time really went:

| Part of one CI run | Time | What was wrong |
|---|---:|---|
| Start a fresh machine, and shut it down afterwards | ~106 s | a brand-new machine boots and installs Docker for every commit |
| Build the Docker image and set up the model | ~80 s | the image was built from scratch; the 629 MB model was also copied into the image every time, although it is never used from there; the model was downloaded and test-loaded every run |
| Strings kept in English (`<unk>`) | ~23 s per small commit | never saved, so the same 117 strings went back to the model on every run, always with the same result |
| Small commits | slower per string | ~30 new strings per language make **one** batch, so one of the machine's two engine workers sat idle |

### 10.2 The four fixes

1. **Keep the model out of the image build** (`.dockerignore`). The model is mounted when the translator runs, so copying it into the image was pure waste: about 15 s per build.
2. **Remember "kept in English" strings.**
   - These strings are saved with an empty translation and the engine label.
   - With the same model and settings they are skipped instantly; with a new model or settings they are retried automatically.
   - They are still listed in the log for a person to translate.
3. **A ready runner.**
   - Instead of a brand-new machine for every commit, one runner machine is kept **stopped** between runs and started when needed.
   - Its disk keeps the built Docker image and the model, so a run skips the image build and the model download.
   - It is stopped (not deleted) afterwards. A stopped machine costs only its disk: **$3.65 per month**.
4. **Give both engine workers a batch.** When a model call is small, its strings are split into two balanced, length-sorted batches, one per worker, instead of one batch with a worker idle. Large calls are unchanged.

### 10.3 Measured result: one 5% commit, before and after

Both runs were timed from start to finish on my laptop, including starting the machine:
- **Before:** the code before these fixes, on a fresh machine, as CI ran until now.
- **After:** the current code, on the ready runner.

| Part | Before | After | Saved |
|---|---:|---:|---:|
| Start + stop the machine | 106.1 s | 37.5 s | 68.6 s |
| Image and model set-up | 79.5 s | 18.7 s | 60.8 s |
| Translation work | 168.1 s | 127.2 s | 40.9 s |
| Phase 1 + 3 | 2.9 s | 2.8 s | — |
| **Whole run** | **356.6 s (5.9 min)** | **186.2 s (3.1 min)** | **1.9× faster** |
| Strings sent to the model | 1,284 | 1,188 | 117 kept-English no longer retried |
| Machine cost of the run | $0.0100 | $0.0052 | |

Both runs translated all 12 language files with 0 failures.

**Why two different commits:** each commit's new strings must be genuinely new to the memory, so the two runs used two different 5% versions (v3-r2 and v3-r3). With the old code, v3-r3 needed about 9% less translation work than v3-r2, so part of the "translation work" saving above comes from the files themselves. The next table compares the **same** files.

### 10.4 Translation work on the same commits, before and after

| Commit | Strings sent to model, before → after | Translation work, before → after |
|---|---:|---:|
| +1% (v2-r1) | 357 → 240 | 54.6 s → 38.2 s (**−30%**) |
| +1% (v2-r2) | 345 → 228 | 55.6 s → 39.2 s (**−29%**) |
| +1% (v2-r3) | 357 → 240 | 96.4 s → 74.5 s (**−23%**) |
| +5% (v3-r1) | 1,261 → 1,144 | 155.5 s → 127.4 s (**−18%**) |
| +5% (v3-r3) | 1,305 → 1,188 | 152.6 s → 127.2 s (**−17%**) |
| +10% (v4-r1) | 2,446 → 2,329 | 287.5 s → 252.1 s (**−12%**) |

### 10.5 What each fix contributed

- **Ready runner + image fix:**
  - starting and stopping the machine fell from about **106 s to 37–44 s**;
  - image and model set-up fell from about **80 s to 19–36 s** (measured over 4 runs on the ready runner);
  - together that's about **2 minutes saved on every CI run**, whatever the commit size. This was the biggest gain.
- **Remembering kept-English strings:** exactly **117 fewer strings** sent to the model on every commit. All 117 were found in the memory instead. That's most of the saving on small commits.
- **Balanced batches:** about **8–10% less time per translated string** on 5% and 10% commits, but **no gain on 1% commits**, where fixed per-file costs (reading and writing files, saving to the database) outweigh the model time.
  - This was smaller than the ~40% I had estimated beforehand. Measuring corrected the estimate.

### 10.6 Where a small commit stands now

| Commit size | Measured with all fixes, end to end |
|---|---:|
| +1% (v2-r3) | 156.9 s (2.6 min) |
| +5% (v3-r1, v3-r3) | 186.0 s, 186.2 s (3.1 min) |
| +10% (v4-r1) | 312.2 s (5.2 min) |

For comparison, the pipeline **before the translation memory work**, which wiped the memory and used a fresh machine, needed about **46 minutes** for the same 5% commit. That figure is the calculated translation time from section 7.2 plus the measured ~106 s machine start. Against that, the 5% commit is now about **15× faster**.

## 11. Limitations and honest caveats

- **The "before" times for +1% and +5% are calculated, not measured.** They come from the measured speed of the previous pipeline on v4, using a straight-line rule that the first-release step confirmed (0.1039 against 0.1046 s per string). The +10% comparison is fully measured.
- **One machine type and one dataset.** Results are for an m5.large and KDE4-style short UI strings (median about 2 words). Longer text would change the absolute times, not the logic.
- **The references may be machine-made** (`BATCHING_RESULTS.md` section 8). chrF here means "closeness to the spreadsheet's translations". The *comparison* between pipelines is still fair, because both are scored against the same references.
- **Twin reuse was measured on a smaller sample in the experiment** (1.5% of the first release). The full-dataset check (section 5) is the better guide to its effect.
- **The memory is loaded fully into the program for each language.** That's fine at this size (about 2,000–10,000 entries per language, a few MB). A very large memory (millions of entries) would need a different design, such as a quick "maybe present" filter (Bloom filter).

---

## 12. What to do next

1. ~~Remember "kept in English" strings per engine version.~~ Done (section 10).
2. ~~Cut start-up time.~~ Done with the ready runner (section 10).
3. **Run at full size once** (all 10,054 KDE4 strings) with a 1% commit, to confirm the percentages for the paper at production scale.

---

## 13. How to reproduce

```bash
# 1. Build the app versions from the KDE4 spreadsheet and upload them
python aws/tools/kde4.py versions Dataset-Translated.xlsx tm/
aws s3 cp tm/ s3://<data-bucket>/datasets/kde4/tm/ --recursive

# 2. Run all steps on one EC2 machine (memory kept between steps)
RUNNER_TASK=tm-experiment RUNNER_TIMEOUT_SECONDS=14400 \
  TM_INPUT_PREFIX=s3://<data-bucket>/datasets/kde4/tm \
  TM_STEPS="v1:reset v2-r1 v3-r1 v4-r1 v2-r2 v3-r2 v4-r2 v2-r3 v3-r3 v4-r3 v4-cold=v4-r1:reset:plain" \
  bash aws/infra/run_on_ec2.sh

# 3. Export telemetry per step and score quality
python aws/tools/export_telemetry.py --run-id <run>-v4-r1 --log-group /cloud-loc/ec2-runner --out v4-r1.csv
python aws/tools/kde4.py score Dataset-Translated.xlsx <downloaded output>/ --version tm/kde4tm-v4-r1.resx
```

In `TM_STEPS`, `:reset` empties the memory before that step, and `:plain` also switches twin reuse off (the previous pipeline).

**Where the raw data is:**
- Step timings: `s3://<data-bucket>/runs/ec2-20261017t175415z/experiment/steps.tsv`
- Per-step telemetry and scores: `s3://<data-bucket>/runs/ec2-20261017t175415z/experiment/results/`
- Per-step outputs: `s3://<data-bucket>/runs/ec2-20261017t175415z-<step>/output/`
- Twin and `<unk>` quality analyses: `s3://<data-bucket>/analysis/twin-quality/`
- Live logs: CloudWatch log group `/cloud-loc/ec2-runner`

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **Translation memory (TM)** | A database of translations already made, reused instead of translating again |
| **Hit / miss** | The memory has the string / doesn't have it |
| **Engine version** | A label for the model and settings that produced a translation; it must match for reuse |
| **Near-twin** | Two strings that differ only in numbers, placeholders or trailing punctuation |
| **Placeholder** | A slot the software fills in later, such as `%1`, `{name}` or `<b>`; it must survive translation unchanged |
| **Token** | A small piece of text the model reads or writes; model work and cost grow with tokens |
| **Padding** | Wasted model work when short and long strings share a batch |
| **chrF** | A 0–100 translation-quality score from matching character sequences against a reference translation |
| **`<unk>`** | The model's "unknown character" output for characters it can't write (mostly some Chinese ones); such strings are kept in English |
| **CI/CD** | Continuous integration / delivery: the pipeline runs automatically on every code change |
