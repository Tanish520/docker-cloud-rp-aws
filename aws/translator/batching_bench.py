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
import tarfile
import time
import urllib.request
import xml.etree.ElementTree as ET

LANGS = ["de-DE", "es-ES", "fr-FR", "hi-IN", "ja-JP", "ru-RU", "zh-CN"]   # production order (sorted blob names)
NLLB_CODES = {"de-DE": "deu_Latn", "es-ES": "spa_Latn", "fr-FR": "fra_Latn", "hi-IN": "hin_Deva",
              "ja-JP": "jpn_Jpan", "ru-RU": "rus_Cyrl", "zh-CN": "zho_Hans"}
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


def plan_token_batches(lengths, max_tokens, sort):
    """Cut items into batches of at most max_tokens real tokens; optionally shortest-first."""
    order = sorted(range(len(lengths)), key=lambda i: lengths[i]) if sort else list(range(len(lengths)))
    batches, current, used = [], [], 0
    for i in order:
        if current and used + lengths[i] > max_tokens:
            batches.append(current)
            current, used = [], 0
        current.append(i)
        used += lengths[i]
    if current:
        batches.append(current)
    return batches


def padding_ratio(batches, lengths):
    """Share of source positions that are padding when each batch is padded to its longest item."""
    padded = sum(max(lengths[i] for i in batch) * len(batch) for batch in batches)
    real = sum(lengths[i] for batch in batches for i in batch)
    return round(1 - real / padded, 4) if padded else 0.0


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
    def load(cls, model_dir, tokenizer, intra_threads):
        import ctranslate2
        return cls(ctranslate2.Translator(model_dir, device="auto", compute_type="int8",
                                          intra_threads=intra_threads), tokenizer)

    def tokens(self, text):
        if text not in self._token_cache:
            ids = self.tokenizer.encode(text, truncation=True, max_length=PROD_TOKEN_LIMIT)
            self._token_cache[text] = self.tokenizer.convert_ids_to_tokens(ids)
        return self._token_cache[text]

    def translate_tokens(self, token_lists, langs, beam_size, batch_type, max_batch_size):
        results = self.translator.translate_batch(
            token_lists, target_prefix=[[NLLB_CODES[lang]] for lang in langs], beam_size=beam_size,
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workload", required=True, help="Folder with the source .resx files.")
    parser.add_argument("--files", nargs="+", default=DEFAULT_FILES)
    parser.add_argument("--out", required=True)
    parser.add_argument("--flores-sentences", type=int, default=40)
    args = parser.parse_args(argv)

    import translation   # loads production's model and tokenizer exactly as Phase 2 does
    import aws_runtime

    pairs = build_pairs(load_workload(args.workload, args.files), args.files)
    report = {"workload": {"files": args.files, "languages": LANGS, "pairs": len(pairs)},
              "machine": {"cpu_limit": aws_runtime.cpu_limit(), "host_cpu_count": os.cpu_count(),
                          "cpu_model": aws_runtime.cpu_model()},
              "strategies": []}
    production = Engine(translation.translator, translation.tokenizer)
    production.translate_texts(["Warm up the model."], "de-DE")
    lengths = [len(production.tokens(text)) for _, text, _ in pairs]

    def record(name, change, outputs, seconds, batches, baseline=None, beam_size=5):
        entry = {"name": name, "change": change, "beam_size": beam_size, "seconds": round(seconds, 2),
                 "pairs_per_hour": round(len(pairs) / seconds * 3600),
                 "batches": len(batches), "avg_batch_pairs": round(len(pairs) / len(batches), 1),
                 "padding_ratio": padding_ratio(batches, lengths)}
        if baseline is not None:
            entry["speedup_vs_baseline"] = round(report["strategies"][0]["seconds"] / seconds, 2)
            entry["identical_to_baseline"] = identical_fraction(outputs, baseline)
            entry["chrf_vs_baseline"] = chrf(outputs, baseline)
        report["strategies"].append(entry)
        print("BENCH_RESULT: " + json.dumps(entry), flush=True)
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

    # B: same, with CTranslate2 told how many cores it has.
    cores = aws_runtime.cpu_limit()
    threaded = Engine.load(translation.CT2_MODEL_PATH, translation.tokenizer, intra_threads=cores)
    threaded.translate_texts(["Warm up the model."], "de-DE")
    out, seconds, batches = run_per_file(pairs, lambda texts, lang: threaded.translate_texts(texts, lang))
    b = record("B_threads", f"A + intra_threads={cores} (the cores the container may use)", out, seconds, batches, baseline)
    engine = threaded if b["seconds"] < report["strategies"][0]["seconds"] else production
    report["chosen_threads"] = cores if engine is threaded else "default"

    # C-E: one run-wide plan by real token counts instead of one file and language at a time.
    candidates = []
    for name, change, max_tokens, sort in (
            ("C_global", "all files and languages planned together, real tokens, 1024-token batches", 1024, False),
            ("D_global_sorted", "C + sorted by length (less padding)", 1024, True),
            ("E_global_sorted_4096", "D + 4096-token batches", 4096, True)):
        out, seconds, batches = run_global(pairs, engine, max_tokens, sort, beam_size=5)
        candidates.append((record(name, change, out, seconds, batches, baseline), max_tokens, sort))
    best, best_tokens, best_sort = min(candidates, key=lambda c: c[0]["seconds"])
    report["chosen_batching"] = best["name"]

    # F-G: fewer beam candidates on the best batching.
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
    except Exception as error:   # quality check is best-effort; timing results still stand
        report["flores"] = {"error": str(error)}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
