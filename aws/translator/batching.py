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


def plan_balanced(lengths, max_tokens, parts):
    """Like plan_token_batches (shortest first, at most max_tokens per batch), but a call small enough to
    fit in `parts` batches is split into `parts` batches of about equal tokens, so every engine worker
    gets one instead of one worker doing it all. Each string goes to the part its middle falls in."""
    total = sum(lengths)
    if total > max_tokens * parts or parts < 2 or not lengths:
        return plan_token_batches(lengths, max_tokens)
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    groups, before = [[] for _ in range(parts)], 0
    for i in order:
        groups[min(parts - 1, int((before + lengths[i] / 2) * parts / total))].append(i)
        before += lengths[i]
    groups = [g for g in groups if g]
    if any(sum(lengths[i] for i in g) > max_tokens for g in groups):
        return plan_token_batches(lengths, max_tokens)
    return groups


def decoding_cap(batch_lengths, factor=2, extra=10, limit=256):
    """Longest output allowed for a batch: factor x its longest source + extra, never above CTranslate2's default."""
    return min(limit, factor * max(batch_lengths) + extra)
