"""How the translator groups strings into model batches (shared by translation.py and batching_bench.py)."""


def plan_token_batches(lengths, max_tokens, sort=True):
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


def decoding_cap(batch_lengths, factor=2, extra=10, limit=256):
    """Longest output allowed for a batch: factor x its longest source + extra, never above CTranslate2's default."""
    return min(limit, factor * max(batch_lengths) + extra)
