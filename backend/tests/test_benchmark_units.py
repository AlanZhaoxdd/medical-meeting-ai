import pytest

from app.api.v1.benchmarks import _validate_params
from app.core.exceptions import AppException
from app.services.benchmark import _metrics, _rank, describe, percentiles
from app.services.ragas_eval import _sample_entries, load_qa_entries


def test_percentiles_and_describe() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentiles(values, (50, 95)) == {"p50": 3.0, "p95": 5.0}
    assert percentiles([], (50,)) == {"p50": 0.0}
    stats = describe([100.0, 200.0, 300.0])
    assert stats["mean_ms"] == 200.0
    assert stats["min_ms"] == 100.0
    assert stats["max_ms"] == 300.0
    assert stats["qps"] == 5.0
    assert describe([])["mean_ms"] == 0.0


def test_rank_and_metrics() -> None:
    assert _rank(["a", "b", "c"], "b") == 2
    assert _rank(["a"], "z") is None
    metrics = _metrics([1, 3, None, 10], [1, 3, 5, 10])
    assert metrics["n"] == 4
    assert metrics["hit@1"] == 0.25
    assert metrics["hit@3"] == 0.5
    assert metrics["hit@5"] == 0.5
    assert metrics["hit@10"] == 0.75
    assert metrics["mrr@10"] == round((1 + 1 / 3 + 0 + 1 / 10) / 4, 4)
    assert _metrics([], [5]) == {"n": 0}


def test_validate_benchmark_params() -> None:
    _validate_params("embedding_throughput", {"texts": ["a"]})
    _validate_params("search_latency", {"queries": ["q"]})
    _validate_params("retrieval_quality", {"entries": [{"query": "q"}]})
    _validate_params("ragas_quality", {"meeting_id": "m", "dataset_file": "x.json"})
    _validate_params("ragas_quality", {"meeting_id": "m", "entries": [{"question": "q"}]})
    for kind, params in (
        ("embedding_throughput", {}),
        ("search_latency", {}),
        ("retrieval_quality", {}),
        ("ragas_quality", {"meeting_id": "m"}),
    ):
        with pytest.raises(AppException) as excinfo:
            _validate_params(kind, params)
        assert excinfo.value.status_code == 422


def test_load_qa_entries_normalizes_aliases() -> None:
    entries = load_qa_entries(
        entries=[
            {"question": "问题一", "correctAnswer": "答案一", "questionType": "open_ended"},
            {"user_input": "问题二", "reference": "答案二"},
            {"question": "没有答案", "options": "A"},
        ]
    )
    assert [item["question"] for item in entries] == ["问题一", "问题二"]
    assert [item["reference"] for item in entries] == ["答案一", "答案二"]
    assert entries[0]["question_type"] == "open_ended"


def test_sample_entries_respects_limit_and_seed() -> None:
    entries = [{"index": index} for index in range(10)]
    assert _sample_entries(entries, max_items=0) == entries
    sampled = _sample_entries(entries, max_items=3, seed=42)
    assert len(sampled) == 3
    assert _sample_entries(entries, max_items=3, seed=42) == sampled
