"""Batch planning shared by the translator and the benchmark."""
import batching


def test_a_small_call_is_split_into_one_sorted_batch_per_worker():
    assert batching.plan_balanced([2, 4, 2, 4], max_tokens=1024, parts=2) == [[0, 2], [1, 3]]


def test_fewer_strings_than_workers_gives_one_batch_each():
    assert batching.plan_balanced([5], max_tokens=1024, parts=2) == [[0]]


def test_a_call_too_big_to_split_evenly_uses_full_batches():
    lengths = [3, 3, 3, 3, 3, 3]                          # 18 tokens > 2 workers x 4
    assert batching.plan_balanced(lengths, max_tokens=4, parts=2) == batching.plan_token_batches(lengths, 4)
