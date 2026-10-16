"""The batching benchmark's planning logic (no model needed)."""
import batching_bench as bb
from helpers import write_resx


def test_load_workload_reads_every_string_with_its_file(tmp_path):
    write_resx(tmp_path / "a.resx", ["One", "Two"])
    write_resx(tmp_path / "b.resx", ["Three"])
    assert bb.load_workload(str(tmp_path), ["a.resx", "b.resx"]) == [("a.resx", "One"), ("a.resx", "Two"), ("b.resx", "Three")]


def test_production_plan_flushes_at_52_strings():
    batches = bb.plan_production_batches(["short"] * 60)
    assert [len(b) for b in batches] == [52, 8]


def test_production_plan_flushes_when_the_chars_over_4_estimate_passes_1024():
    # 1000 chars = 250 estimated tokens each, so only 4 fit under 1024.
    batches = bb.plan_production_batches(["x" * 1000] * 5)
    assert [len(b) for b in batches] == [4, 1]


def test_token_plan_respects_the_budget_and_keeps_every_item_once():
    lengths = [5, 30, 12, 7, 30, 3]
    batches = bb.plan_token_batches(lengths, max_tokens=40, sort=False)
    assert sorted(i for b in batches for i in b) == list(range(6))
    assert all(sum(lengths[i] for i in b) <= 40 for b in batches)
    assert batches == [[0, 1], [2, 3], [4, 5]]


def test_sorted_token_plan_groups_similar_lengths():
    lengths = [30, 3, 29, 4]
    assert bb.plan_token_batches(lengths, max_tokens=40, sort=True) == [[1, 3, 2], [0]]  # 3+4+29 = 36 fits


def test_a_single_item_over_budget_gets_its_own_batch():
    assert bb.plan_token_batches([50, 2], max_tokens=10, sort=False) == [[0], [1]]


def test_padding_ratio_is_the_share_of_wasted_positions():
    lengths = [10, 2, 10, 10]
    assert bb.padding_ratio([[0, 1]], lengths) == 0.4      # padded 2x10=20, real 12
    assert bb.padding_ratio([[0, 2, 3]], lengths) == 0.0


def test_identical_fraction():
    assert bb.identical_fraction(["a", "b", "c", "d"], ["a", "x", "c", "d"]) == 0.75


def test_report_is_saved_and_uploaded_after_each_step(s3, tmp_path, monkeypatch):
    # A run that dies part-way must still leave the finished steps in S3.
    monkeypatch.setenv("BUCKET", "test-bucket")
    monkeypatch.setenv("RUN_ID", "run-1")
    out = tmp_path / "bench.json"
    bb.save_report({"strategies": [{"name": "A_production"}]}, str(out))
    assert out.exists()
    body = s3.get_object(Bucket="test-bucket", Key="runs/run-1/bench/results.json")["Body"].read()
    assert b"A_production" in body


def test_report_is_only_saved_locally_without_bucket_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("BUCKET", raising=False)
    out = tmp_path / "bench.json"
    bb.save_report({"strategies": []}, str(out))
    assert out.exists()


def test_default_budgets_go_smaller_not_larger():
    # 4096-token batches stopped the first benchmark run; the default sweep stays at or below production's 1024.
    assert bb.parse_args(["--workload", "w", "--out", "o"]).budgets == [256, 512, 1024]
