"""Batching benchmark: time NLLB/CTranslate2 inference under different batching strategies.

Runs inside the translator image, next to translation.py:
    python batching_bench.py --workload /workload --out bench-results.json

Only model inference is timed (tokenize + translate + detokenize). S3, SQL and XML are left out
because they are about 1% of Phase 2 time. Strategies are tried one change at a time, each step
starting from the fastest configuration found so far, so every gain can be traced to its cause.
"""
import argparse
import json
import os
import random
import re
import resource
import tarfile
import time
import urllib.request
import xml.etree.ElementTree as ET

from batching import decoding_cap, plan_token_batches

LANGS = ["de-DE", "es-ES", "fr-FR", "hi-IN", "ja-JP", "ru-RU", "zh-CN"]   # production order (sorted blob names)
NLLB_CODES = {"de-DE": "deu_Latn", "es-ES": "spa_Latn", "fr-FR": "fra_Latn", "hi-IN": "hin_Deva",
              "ja-JP": "jpn_Jpan", "ru-RU": "rus_Cyrl", "zh-CN": "zho_Hans"}
# KDE4 spreadsheet columns (Dataset-Translated.xlsx) -> NLLB codes.
KDE4_CODES = {"hi": "hin_Deva", "ar": "arb_Arab", "bn": "ben_Beng", "de": "deu_Latn", "es": "spa_Latn",
              "fr": "fra_Latn", "ja": "jpn_Jpan", "mr": "mar_Deva", "pa": "pan_Guru", "ta": "tam_Taml",
              "te": "tel_Telu", "zh": "zho_Hans"}
CODES = {**NLLB_CODES, **KDE4_CODES}
PLACEHOLDER = re.compile(r"%\d+|%[sdif]|\{[^}]*\}|<[^>]+>")
DEFAULT_FILES = ["resx_file_10.resx", "resx_file_19.resx", "resx_file_20.resx"]
PROD_BATCH_SIZE = 52      # translation.py BATCH_SIZE
PROD_TOKEN_LIMIT = 1024   # translation.py TOKEN_LIMIT (also CTranslate2 max_batch_size there)
FLORES_URL = "https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz"


# ---------- planning (pure, unit-tested) ----------

def load_workload(folder, files):
    """(file, text) for every non-empty <value> in the given .resx files, in file order."""
    segments = []
    for name in files:
        for data in ET.parse(os.path.join(folder, name)).getroot().findall("data"):
            value = data.find("value")
            if value is not None and value.text:
                segments.append((name, value.text))
    return segments


def plan_production_batches(texts):
    """translation.py's rule: flush at 52 strings, or when the chars/4 estimate would pass 1024."""
    batches, current, tokens = [], [], 0.0
    for i, text in enumerate(texts):
        estimate = len(text) / 4.0
        if current and (tokens + estimate > PROD_TOKEN_LIMIT or len(current) >= PROD_BATCH_SIZE):
            batches.append(current)
            current, tokens = [], 0.0
        current.append(i)
        tokens += estimate
    if current:
        batches.append(current)
    return batches


def padding_ratio(batches, lengths):
    """Share of source positions that are padding when each batch is padded to its longest item."""
    padded = sum(max(lengths[i] for i in batch) * len(batch) for batch in batches)
    real = sum(lengths[i] for batch in batches for i in batch)
    return round(1 - real / padded, 4) if padded else 0.0


def clean_kde4(rows, langs):
    """(english, {lang: reference}) for usable rows: every cell text, no glued multi-line cells,
    English not repeated (first kept)."""
    seen, items = set(), []
    for row in rows:
        english, refs = row[0], dict(zip(langs, row[1:]))
        cells = [english, *refs.values()]
        if not all(isinstance(c, str) and c.strip() for c in cells):
            continue                      # empty cell, or Excel turned the text into TRUE/FALSE/number
        if "\n" in english or english in seen:
            continue                      # many strings glued into one cell, or a duplicate
        seen.add(english)
        items.append((english, refs))
    return items


def sample(items, n, seed=2026):
    """A fixed random subset in original order (all items when n >= len(items))."""
    if n >= len(items):
        return list(items)
    return [items[i] for i in sorted(random.Random(seed).sample(range(len(items)), n))]


def placeholder_items(items):
    """Every item whose English contains a placeholder (too few land in a random sample to judge)."""
    return [item for item in items if PLACEHOLDER.search(item[0])]


def placeholder_kept_fraction(sources, outputs):
    """Among sources with placeholders (%1, %s, {name}, <tag>), the share whose output keeps exactly the same ones."""
    checked = [(s, o) for s, o in zip(sources, outputs) if PLACEHOLDER.search(s)]
    if not checked:
        return None
    kept = sum(sorted(PLACEHOLDER.findall(s)) == sorted(PLACEHOLDER.findall(o)) for s, o in checked)
    return round(kept / len(checked), 4)


def cpu_utilization(cpu_seconds, wall_seconds, cores):
    """Share of the available core time actually used (1.0 = every core busy the whole time)."""
    return round(cpu_seconds / (wall_seconds * cores), 4) if wall_seconds > 0 else 0.0


def process_cpu_seconds():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def with_fallback(protected, ok, unprotected):
    """The pipeline's policy: the protected translation where every marker came back, else the unprotected one."""
    return [p if good else u for p, good, u in zip(protected, ok, unprotected)]


def identical_fraction(a, b):
    return round(sum(x == y for x, y in zip(a, b)) / len(a), 4) if a else 0.0


# ---------- model-dependent ----------

class Engine:
    """Tokenize/translate/detokenize exactly like translation.translate_local, with settings exposed."""

    def __init__(self, translator, tokenizer):
        self.translator = translator
        self.tokenizer = tokenizer
        self._token_cache = {}

    @classmethod
    def load(cls, model_dir, tokenizer, intra_threads, inter_threads=1):
        import ctranslate2
        return cls(ctranslate2.Translator(model_dir, device="auto", compute_type="int8",
                                          intra_threads=intra_threads, inter_threads=inter_threads), tokenizer)

    def tokens(self, text):
        if text not in self._token_cache:
            ids = self.tokenizer.encode(text, truncation=True, max_length=PROD_TOKEN_LIMIT)
            self._token_cache[text] = self.tokenizer.convert_ids_to_tokens(ids)
        return self._token_cache[text]

    def translate_tokens(self, token_lists, langs, beam_size, batch_type, max_batch_size):
        results = self.translator.translate_batch(
            token_lists, target_prefix=[[CODES[lang]] for lang in langs], beam_size=beam_size,
            batch_type=batch_type, max_batch_size=max_batch_size)
        return [self.tokenizer.decode(self.tokenizer.convert_tokens_to_ids(r.hypotheses[0][1:]),
                                      skip_special_tokens=True) for r in results]

    def translate_texts(self, texts, lang, beam_size=5, batch_type="tokens", max_batch_size=PROD_TOKEN_LIMIT):
        self._token_cache.clear()   # per-file strategies tokenize every call, like production
        return self.translate_tokens([self.tokens(t) for t in texts], [lang] * len(texts),
                                     beam_size, batch_type, max_batch_size)


def build_pairs(segments, files):
    """(file, text, lang) in production order: each file, then each language."""
    by_file = {name: [text for f, text in segments if f == name] for name in files}
    return [(name, text, lang) for name in files for lang in LANGS for text in by_file[name]]


def run_per_file(pairs, translate_fn):
    """Production scope: one (file, language) at a time, production batch rule."""
    outputs, batches = [None] * len(pairs), []
    start = time.perf_counter()
    i = 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0] and pairs[j][2] == pairs[i][2]:
            j += 1
        texts = [pairs[k][1] for k in range(i, j)]
        for batch in plan_production_batches(texts):
            out = translate_fn([texts[k] for k in batch], pairs[i][2])
            for k, text in zip(batch, out):
                outputs[i + k] = text
            batches.append([i + k for k in batch])
        i = j
    return outputs, time.perf_counter() - start, batches


def run_global(pairs, engine, max_tokens, sort, beam_size):
    """Whole-run scope: every (string, language) pair planned together by real token count."""
    engine._token_cache.clear()
    start = time.perf_counter()
    token_lists = [engine.tokens(text) for _, text, _ in pairs]
    lengths = [len(t) for t in token_lists]
    batches = plan_token_batches(lengths, max_tokens, sort)
    outputs = [None] * len(pairs)
    for batch in batches:
        out = engine.translate_tokens([token_lists[i] for i in batch], [pairs[i][2] for i in batch],
                                      beam_size, batch_type="examples", max_batch_size=0)
        for i, text in zip(batch, out):
            outputs[i] = text
    return outputs, time.perf_counter() - start, batches


def run_global_parallel(pairs, engine, max_tokens, beam_size, cap_length=False):
    """Like run_global, but every batch is queued at once (asynchronous=True) so a translator with
    inter_threads > 1 works on several batches in parallel. Optionally caps output length per batch."""
    engine._token_cache.clear()
    start = time.perf_counter()
    token_lists = [engine.tokens(text) for _, text, _ in pairs]
    lengths = [len(t) for t in token_lists]
    batches = plan_token_batches(lengths, max_tokens, sort=True)
    pending = []
    for batch in batches:
        options = {"max_decoding_length": decoding_cap([lengths[i] for i in batch])} if cap_length else {}
        futures = engine.translator.translate_batch(
            [token_lists[i] for i in batch], target_prefix=[[CODES[pairs[i][2]]] for i in batch],
            beam_size=beam_size, batch_type="examples", max_batch_size=0, asynchronous=True, **options)
        pending.append((batch, futures))
    outputs = [None] * len(pairs)
    for batch, futures in pending:
        for i, future in zip(batch, futures):
            hypothesis = future.result().hypotheses[0][1:]
            outputs[i] = engine.tokenizer.decode(engine.tokenizer.convert_tokens_to_ids(hypothesis), skip_special_tokens=True)
    return outputs, time.perf_counter() - start, batches


def chrf(hypotheses, references):
    try:
        import sacrebleu
    except ImportError:
        return None
    return round(sacrebleu.corpus_chrf(hypotheses, [references]).score, 2)


def load_flores(per_language):
    folder = "/tmp/flores200_dataset"
    if not os.path.isdir(folder):
        urllib.request.urlretrieve(FLORES_URL, "/tmp/flores.tar.gz")
        with tarfile.open("/tmp/flores.tar.gz") as tar:
            tar.extractall("/tmp")

    def read(code):
        with open(os.path.join(folder, "dev", f"{code}.dev"), encoding="utf-8") as f:
            return [line.strip() for line in f][:per_language]

    return read("eng_Latn"), {lang: read(code) for lang, code in NLLB_CODES.items()}


def read_kde4(path):
    """Header languages and data rows of the KDE4 spreadsheet (first column English). s3:// paths are downloaded."""
    if path.startswith("s3://"):
        import boto3
        bucket, key = path[5:].split("/", 1)
        boto3.client("s3").download_file(bucket, key, "/tmp/kde4.xlsx")
        path = "/tmp/kde4.xlsx"
    import openpyxl
    rows = list(openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True))
    return list(rows[0][1:]), rows[1:]


def kde4_sample(args):
    langs, rows = read_kde4(args.kde4)
    items = clean_kde4(rows, langs)
    return langs, rows, items, sample(items, args.kde4_strings)


def run_kde4_parallel(args, engine, translation, report):
    """Beam 2 on a KDE4 sample: one batch at a time (P0) vs two batches in parallel (P1/P2) vs + length cap (P3)."""
    import aws_runtime
    langs, rows, items, chosen = kde4_sample(args)
    pairs = [("kde4", english, lang) for lang in langs for english, _ in chosen]
    refs = [r[lang] for lang in langs for _, r in chosen]
    cores = aws_runtime.cpu_limit()
    report["kde4_parallel"] = {"source": args.kde4, "usable_rows": len(items), "strings": len(chosen),
                               "languages": langs, "pairs": len(pairs), "beam_size": 2, "cores": cores, "steps": []}
    steps = report["kde4_parallel"]["steps"]

    def measure(name, change, run):
        cpu_before = process_cpu_seconds()
        out, seconds, batches = run()
        entry = {"name": name, "change": change, "seconds": round(seconds, 2),
                 "pairs_per_hour": round(len(pairs) / seconds * 3600),
                 "cpu_utilization": cpu_utilization(process_cpu_seconds() - cpu_before, seconds, cores),
                 "batches": len(batches), "chrf_all": chrf(out, refs), "peak_memory_mb": peak_memory_mb()}
        if steps:
            entry["speedup_vs_p0"] = round(steps[0]["seconds"] / seconds, 2)
            entry["identical_to_p0"] = identical_fraction(out, measure.p0)
        else:
            measure.p0 = out
        steps.append(entry)
        print("PARALLEL_RESULT: " + json.dumps(entry), flush=True)
        save_report(report, args.out)
        return entry

    measure("P0_one_batch_at_a_time", "today's engine (default threads), sorted 1024-token batches, beam 2",
            lambda: run_global(pairs, engine, PROD_TOKEN_LIMIT, True, beam_size=2))
    engine.translator.unload_model()   # free memory before loading the parallel translator
    parallel = Engine.load(translation.CT2_MODEL_PATH, translation.tokenizer, intra_threads=1, inter_threads=cores)
    parallel.translate_texts(["Warm up the model."], "de-DE", beam_size=2)
    p1 = measure("P1_parallel_batches", f"{cores} batches at once (inter_threads={cores}, intra_threads=1), 1024-token batches",
                 lambda: run_global_parallel(pairs, parallel, PROD_TOKEN_LIMIT, beam_size=2))
    p2 = measure("P2_parallel_512", "P1 with 512-token batches",
                 lambda: run_global_parallel(pairs, parallel, 512, beam_size=2))
    budget = PROD_TOKEN_LIMIT if p1["seconds"] <= p2["seconds"] else 512
    measure("P3_length_cap", f"best of P1/P2 ({budget}-token batches) + output capped at 2x longest input + 10 tokens",
            lambda: run_global_parallel(pairs, parallel, budget, beam_size=2, cap_length=True))


def run_kde4_placeholders(args, engine, report):
    """Beam 2 on every KDE4 placeholder string: no protection vs each marker style (+ the fallback policy)."""
    import placeholders as ph
    langs, rows, items, _ = kde4_sample(args)
    marked = placeholder_items(items)
    english = [e for e, _ in marked]
    pairs = [("kde4", e, lang) for lang in langs for e in english]
    refs = [r[lang] for lang in langs for _, r in marked]
    n = len(marked)
    report["kde4_placeholders"] = {"source": args.kde4, "strings": n, "languages": langs, "pairs": len(pairs),
                                   "beam_size": 2, "styles": {}}

    def score(final, seconds, extra):
        per_lang = {lang: {"kept": placeholder_kept_fraction(english, final[i * n:(i + 1) * n]),
                           "chrf": chrf(final[i * n:(i + 1) * n], refs[i * n:(i + 1) * n])} for i, lang in enumerate(langs)}
        entry = {"kept_all": placeholder_kept_fraction([p[1] for p in pairs], final), "chrf_all": chrf(final, refs),
                 "seconds": round(seconds, 2), "peak_memory_mb": peak_memory_mb(), **extra, "per_language": per_lang}
        return entry

    unprotected, seconds, _ = run_global(pairs, engine, PROD_TOKEN_LIMIT, True, beam_size=2)
    report["kde4_placeholders"]["styles"]["none"] = score(unprotected, seconds, {})
    print("PLACEHOLDER_RESULT none: kept", report["kde4_placeholders"]["styles"]["none"]["kept_all"], flush=True)
    save_report(report, args.out)
    for style in ("square", "section", "curly"):
        masked = [ph.protect(text, style) for _, text, _ in pairs]
        masked_pairs = [(f, m, lang) for (f, _, lang), (m, _) in zip(pairs, masked)]
        out, seconds, _ = run_global(masked_pairs, engine, PROD_TOKEN_LIMIT, True, beam_size=2)
        restored = [ph.restore(o, originals, style) for o, (_, originals) in zip(out, masked)]
        final, ok = [r for r, _ in restored], [good for _, good in restored]
        entry = score(final, seconds, {"masked_share": round(sum(1 for _, o in masked if o) / len(masked), 4),
                                       "markers_restored_all": round(sum(ok) / len(ok), 4)})
        combined = with_fallback(final, ok, unprotected)
        entry["with_fallback"] = {"kept_all": placeholder_kept_fraction([p[1] for p in pairs], combined),
                                  "chrf_all": chrf(combined, refs),
                                  "fallback_share": round(1 - sum(ok) / len(ok), 4)}
        report["kde4_placeholders"]["styles"][style] = entry
        print(f"PLACEHOLDER_RESULT {style}: " + json.dumps({k: v for k, v in entry.items() if k != "per_language"}), flush=True)
        save_report(report, args.out)


def run_kde4(args, engine, report):
    """Beam 5 / 2 / 1 on a fixed KDE4 sample: speed, chrF against the spreadsheet, placeholder survival."""
    langs, rows, items, chosen = kde4_sample(args)
    pairs = [("kde4", english, lang) for lang in langs for english, _ in chosen]
    marked = placeholder_items(items)
    marked_pairs = [("kde4", english, lang) for lang in langs for english, _ in marked]
    refs = [r[lang] for lang in langs for _, r in chosen]
    report["kde4"] = {"source": args.kde4, "rows_in_file": len(rows), "usable_rows": len(items),
                      "strings": len(chosen), "languages": langs, "pairs": len(pairs),
                      "placeholder_strings": len(marked),
                      "batching": f"run-wide, sorted by length, {PROD_TOKEN_LIMIT}-token batches", "beams": {}}
    save_report(report, args.out)
    beam5 = None
    for beam in (5, 2, 1):
        out, seconds, batches = run_global(pairs, engine, PROD_TOKEN_LIMIT, True, beam_size=beam)
        per_lang = {}
        for i, lang in enumerate(langs):
            part = slice(i * len(chosen), (i + 1) * len(chosen))
            per_lang[lang] = {"chrf": chrf(out[part], refs[part]),
                              "placeholders_kept": placeholder_kept_fraction([e for e, _ in chosen], out[part])}
        p_out, p_seconds, _ = run_global(marked_pairs, engine, PROD_TOKEN_LIMIT, True, beam_size=beam)
        for i, lang in enumerate(langs):
            part = slice(i * len(marked), (i + 1) * len(marked))
            per_lang[lang]["placeholders_kept"] = placeholder_kept_fraction([e for e, _ in marked], p_out[part])
        beam5 = beam5 or out
        entry = {"seconds": round(seconds, 2), "pairs_per_hour": round(len(pairs) / seconds * 3600),
                 "speedup_vs_beam5": round(report["kde4"]["beams"]["beam5"]["seconds"] / seconds, 2) if beam != 5 else 1.0,
                 "chrf_all": chrf(out, refs),
                 "placeholders_kept_all": placeholder_kept_fraction([p[1] for p in marked_pairs], p_out),
                 "placeholder_check_seconds": round(p_seconds, 2),
                 "identical_to_beam5": identical_fraction(out, beam5), "batches": len(batches),
                 "peak_memory_mb": peak_memory_mb(), "per_language": per_lang}
        report["kde4"]["beams"][f"beam{beam}"] = entry
        print(f"KDE4_RESULT beam {beam}: " + json.dumps({k: v for k, v in entry.items() if k != "per_language"}), flush=True)
        save_report(report, args.out)


def save_report(report, path):
    """Write the report locally, and to s3://BUCKET/runs/RUN_ID/bench/results.json when those are set,
    so the steps already finished survive if the run dies part-way."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    bucket, run_id = os.environ.get("BUCKET"), os.environ.get("RUN_ID")
    if bucket and run_id:
        try:
            import boto3
            boto3.client("s3").upload_file(path, bucket, f"runs/{run_id}/bench/results.json")
        except Exception as error:
            print(f"⚠️ Could not upload benchmark results: {error}", flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workload", required=True, help="Folder with the source .resx files.")
    parser.add_argument("--files", nargs="+", default=DEFAULT_FILES)
    parser.add_argument("--out", required=True)
    parser.add_argument("--budgets", nargs="+", type=int, default=[256, 512, 1024],
                        help="Token budgets for the sorted run-wide batching (run 1: 4096 stopped the run).")
    parser.add_argument("--with-threads", action="store_true", help="Also time intra_threads=<cores> (run 1: no effect).")
    parser.add_argument("--with-unsorted", action="store_true", help="Also time unsorted run-wide batching.")
    parser.add_argument("--flores-sentences", type=int, default=40)
    parser.add_argument("--kde4", help="KDE4 spreadsheet (path or s3:// URI): run only the beam 5/2/1 test on it.")
    parser.add_argument("--kde4-strings", type=int, default=1000, help="English strings sampled from KDE4.")
    parser.add_argument("--kde4-placeholders", action="store_true",
                        help="With --kde4: compare placeholder protection styles on every placeholder string (beam 2).")
    parser.add_argument("--kde4-parallel", action="store_true",
                        help="With --kde4: test parallel batches and a length cap at beam 2 instead of beam 5/2/1.")
    return parser.parse_args(argv)


def peak_memory_mb():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)   # Linux reports KiB


def main(argv=None):
    args = parse_args(argv)

    import translation   # loads production's model and tokenizer exactly as Phase 2 does
    import aws_runtime

    pairs = build_pairs(load_workload(args.workload, args.files), args.files)
    report = {"workload": {"files": args.files, "languages": LANGS, "pairs": len(pairs)},
              "machine": {"cpu_limit": aws_runtime.cpu_limit(), "host_cpu_count": os.cpu_count(),
                          "cpu_model": aws_runtime.cpu_model()},
              "settings": {"budgets": args.budgets, "with_threads": args.with_threads,
                           "with_unsorted": args.with_unsorted},
              "strategies": []}
    production = Engine(translation.translator, translation.tokenizer)
    production.translate_texts(["Warm up the model."], "de-DE")
    if args.kde4:
        if args.kde4_placeholders:
            run_kde4_placeholders(args, production, report)
        elif args.kde4_parallel:
            run_kde4_parallel(args, production, translation, report)
        else:
            run_kde4(args, production, report)
        report["complete"] = True
        save_report(report, args.out)
        return 0
    lengths = [len(production.tokens(text)) for _, text, _ in pairs]

    def record(name, change, outputs, seconds, batches, baseline=None, beam_size=5):
        entry = {"name": name, "change": change, "beam_size": beam_size, "seconds": round(seconds, 2),
                 "pairs_per_hour": round(len(pairs) / seconds * 3600),
                 "batches": len(batches), "avg_batch_pairs": round(len(pairs) / len(batches), 1),
                 "padding_ratio": padding_ratio(batches, lengths), "peak_memory_mb": peak_memory_mb()}
        if baseline is not None:
            entry["speedup_vs_baseline"] = round(report["strategies"][0]["seconds"] / seconds, 2)
            entry["identical_to_baseline"] = identical_fraction(outputs, baseline)
            entry["chrf_vs_baseline"] = chrf(outputs, baseline)
        report["strategies"].append(entry)
        print("BENCH_RESULT: " + json.dumps(entry), flush=True)
        save_report(report, args.out)
        return entry

    # Sanity: the benchmark's Engine reproduces production's translate_local on one file and two languages.
    sample = [p for p in pairs if p[0] == args.files[0]][:100]
    prod_out = [t for lang in ("de-DE", "es-ES") for t in
                translation.translate_local([p[1] for p in sample if p[2] == lang], lang)[0]]
    eng_out = [t for lang in ("de-DE", "es-ES") for t in production.translate_texts([p[1] for p in sample if p[2] == lang], lang)]
    report["sanity_engine_matches_production"] = identical_fraction(eng_out, prod_out)
    print(f"sanity: engine matches production on {len(prod_out)} strings: {report['sanity_engine_matches_production']}", flush=True)

    # A: production as it is today.
    baseline, seconds, batches = run_per_file(pairs, lambda texts, lang: translation.translate_local(texts, lang)[0])
    record("A_production", "Today's pipeline: per file and language, 52 strings / 1024 chars-over-4, beam 5, default threads",
           baseline, seconds, batches)

    engine = production
    if args.with_threads:
        cores = aws_runtime.cpu_limit()
        threaded = Engine.load(translation.CT2_MODEL_PATH, translation.tokenizer, intra_threads=cores)
        threaded.translate_texts(["Warm up the model."], "de-DE")
        out, seconds, batches = run_per_file(pairs, lambda texts, lang: threaded.translate_texts(texts, lang))
        b = record("B_threads", f"A + intra_threads={cores}", out, seconds, batches, baseline)
        if b["seconds"] < report["strategies"][0]["seconds"]:
            engine = threaded

    # Run-wide batching by real token counts, at each budget (sorted by length; unsorted only if asked).
    candidates = []
    if args.with_unsorted:
        out, seconds, batches = run_global(pairs, engine, PROD_TOKEN_LIMIT, False, beam_size=5)
        candidates.append((record("C_global", "all files and languages together, real tokens, 1024-token batches",
                                  out, seconds, batches, baseline), PROD_TOKEN_LIMIT, False))
    for budget in args.budgets:
        out, seconds, batches = run_global(pairs, engine, budget, True, beam_size=5)
        candidates.append((record(f"D_sorted_{budget}", f"all files and languages together, sorted by length, {budget}-token batches",
                                  out, seconds, batches, baseline), budget, True))
    best, best_tokens, best_sort = min(candidates, key=lambda c: c[0]["seconds"])
    report["chosen_batching"] = best["name"]

    # Fewer beam candidates on the best batching.
    for beam in (2, 1):
        out, seconds, batches = run_global(pairs, engine, best_tokens, best_sort, beam_size=beam)
        record(f"{'F' if beam == 2 else 'G'}_beam{beam}", f"{best['name']} + beam {beam}", out, seconds, batches,
               baseline, beam_size=beam)

    # Quality against human references (FLORES-200 dev) for the beam sizes.
    try:
        english, references = load_flores(args.flores_sentences)
        flores_pairs = [("flores", text, lang) for lang in LANGS for text in english]
        refs = [ref for lang in LANGS for ref in references[lang]]
        report["flores"] = {"sentences_per_language": args.flores_sentences}
        for beam in (5, 2, 1):
            out, seconds, _ = run_global(flores_pairs, engine, best_tokens, best_sort, beam_size=beam)
            per_lang = {lang: chrf(out[i * len(english):(i + 1) * len(english)], references[lang])
                        for i, lang in enumerate(LANGS)}
            report["flores"][f"beam{beam}"] = {"chrf_all": chrf(out, refs), "chrf_per_language": per_lang,
                                                "seconds": round(seconds, 2)}
            print(f"FLORES beam {beam}: chrF {report['flores'][f'beam{beam}']['chrf_all']} in {seconds:.1f}s", flush=True)
            save_report(report, args.out)
    except Exception as error:   # quality check is best-effort; timing results still stand
        report["flores"] = {"error": str(error)}

    report["complete"] = True
    save_report(report, args.out)
    print(f"Wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
