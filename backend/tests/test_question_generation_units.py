from types import SimpleNamespace
from uuid import uuid4

from app.models.meeting import AiTaskStatus, AiTaskType, MeetingQuestionType
from app.schemas.meeting_review import QuestionGenerationRead
from app.schemas.question_generation import GeneratedQuestion, RetrievalPlan
from app.services.question_generation import (
    has_required_question_types,
    normalize_question_text,
    semantic_deduplicate,
    thread_id,
    validate_candidate_questions,
    validate_confirmed_transcript_evidence,
    validate_evidence,
)
from app.worker.question_graph import route_after_validation, validate_plan_grounding


def test_thread_id_is_stable_and_versioned() -> None:
    meeting_id = uuid4()
    assert thread_id(meeting_id, 3) == f"meeting:{meeting_id}:question-generation:v3"


def test_task_enums_expose_contract_values() -> None:
    assert AiTaskType.QUESTION_GENERATION.value == "QUESTION_GENERATION"
    assert AiTaskStatus.PENDING_REVIEW.value == "PENDING_REVIEW"


def test_question_generation_schema_rejects_invalid_progress() -> None:
    try:
        QuestionGenerationRead(task_id=uuid4(), status="RUNNING", current_stage="x", progress=101)
    except ValueError:
        return
    raise AssertionError("progress above 100 must be rejected")


def test_validate_evidence_checks_scope_publication_and_quote() -> None:
    org_id, kb_id = uuid4(), uuid4()
    valid = SimpleNamespace(
        organization_id=org_id,
        knowledge_base_id=kb_id,
        publication_status="PUBLISHED",
        content="authoritative quote",
    )
    wrong_org = SimpleNamespace(
        organization_id=uuid4(),
        knowledge_base_id=kb_id,
        publication_status="PUBLISHED",
        content="authoritative quote",
    )
    rows = validate_evidence(
        [
            {"chunk_id": "ok", "quote": "authoritative quote"},
            {"chunk_id": "wrong", "quote": "authoritative quote"},
            {"chunk_id": "ok", "quote": "not present"},
        ],
        valid_chunks={"ok": valid, "wrong": wrong_org},
        organization_id=org_id,
        knowledge_base_id=kb_id,
    )
    assert [row["chunk_id"] for row in rows] == ["ok"]


def test_confirmed_transcript_evidence_is_exact_and_draft_authorized() -> None:
    org_id, kb_id, meeting_id, document_id = (uuid4() for _ in range(4))
    valid = SimpleNamespace(
        organization_id=org_id,
        knowledge_base_id=kb_id,
        publication_status="DRAFT",
        document_id=document_id,
        content="transcript quote",
    )
    other_document = SimpleNamespace(**{**valid.__dict__, "document_id": uuid4()})
    evidence = [{
        "chunk_id": "ok",
        "document_id": str(document_id),
        "quote": "transcript quote",
        "source_type": "confirmed_transcript",
    }]
    assert validate_confirmed_transcript_evidence(
        evidence,
        valid_chunks={"ok": valid},
        organization_id=org_id,
        knowledge_base_id=kb_id,
        meeting_id=meeting_id,
        confirmed_document_id=document_id,
    )
    assert not validate_confirmed_transcript_evidence(
        evidence,
        valid_chunks={"ok": other_document},
        organization_id=org_id,
        knowledge_base_id=kb_id,
        meeting_id=meeting_id,
        confirmed_document_id=document_id,
    )


def test_generated_question_requires_authorized_evidence_and_answer_type() -> None:
    document_id = uuid4()
    base = {
        "question_type": MeetingQuestionType.OPEN_ENDED,
        "content": "请讨论治疗策略的关键依据？",
        "topic": "治疗策略",
        "rationale": "来自纪要与知识库",
        "expected_answer_type": "DISCUSSION",
        "support_level": "HIGH",
        "support_score": 0.8,
        "evidence": [
            {
                "chunk_id": "c1",
                "document_id": document_id,
                "quote": "依据",
                "evidence_summary": "依据摘要",
            }
        ],
    }
    assert GeneratedQuestion.model_validate(base).question_type is MeetingQuestionType.OPEN_ENDED
    bad = {**base, "expected_answer_type": "NUMBER"}
    try:
        GeneratedQuestion.model_validate(bad)
    except ValueError:
        return
    raise AssertionError("OPEN_ENDED must use DISCUSSION")


def test_question_normalization_and_semantic_deduplication() -> None:
    assert normalize_question_text(" FLOW 研究，结果如何？ ") == normalize_question_text(
        "flow研究,结果如何?"
    )
    items = [{"content": "a"}, {"content": "b"}, {"content": "c"}]
    kept = semantic_deduplicate(items, [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    assert [item["content"] for item in kept] == ["a", "c"]


def test_candidate_validation_rejects_forged_chunk_and_enriches_scores() -> None:
    document_id = uuid4()
    base = {
        "question_type": "cut_point",
        "content": "研究主要终点的风险降低比例是多少？",
        "topic": "主要终点",
        "rationale": "核验会议中的明确数值",
        "expected_answer_type": "PERCENTAGE",
        "support_level": "HIGH",
        "support_score": 0.9,
        "evidence": [
            {
                "chunk_id": "c1",
                "document_id": str(document_id),
                "quote": "主要终点风险降低 24%",
                "evidence_summary": "主要终点结果",
            }
        ],
    }
    available = [
        {
            "chunk_id": "c1",
            "document_id": document_id,
            "content": "研究显示主要终点风险降低 24%。",
            "dense_score": 0.8,
            "sparse_score": 0.7,
            "rerank_score": 0.95,
            "query_source": "主要终点 风险降低",
        }
    ]
    accepted, errors = validate_candidate_questions([base], available_chunks=available)
    assert not errors
    assert accepted[0]["evidence"][0]["rerank_score"] == 0.95
    duplicated = {**base, "evidence": [base["evidence"][0], base["evidence"][0]]}
    accepted, errors = validate_candidate_questions([duplicated], available_chunks=available)
    assert not errors
    assert len(accepted[0]["evidence"]) == 1
    forged = {**base, "evidence": [{**base["evidence"][0], "chunk_id": "forged"}]}
    accepted, errors = validate_candidate_questions([forged], available_chunks=available)
    assert not accepted
    assert errors == ["invalid_evidence:0"]


def test_validation_route_retries_then_fails_at_limit() -> None:
    assert route_after_validation({"validation_errors": []}) == "persist_questions"
    assert (
        route_after_validation(
            {"validation_errors": ["invalid_evidence"], "retry_count": 0, "max_retries": 2}
        )
        == "refine_retrieval_plan"
    )
    assert (
        route_after_validation(
            {"validation_errors": ["invalid_evidence"], "retry_count": 2, "max_retries": 2}
        )
        == "mark_failed"
    )


def test_retrieval_plan_rejects_branch_type_mismatch() -> None:
    payload = {
        "cutpoint_queries": [
            {
                "query": "FLOW 研究主要终点",
                "purpose": "核验研究终点",
                "topic": "FLOW 研究",
                "keywords": ["主要终点"],
                "question_type": "open_ended",
                "top_k": 8,
            }
        ]
    }
    try:
        RetrievalPlan.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("cutpoint branch must only contain CUT_POINT queries")


def test_cutpoint_rejects_discussion_answer_type() -> None:
    payload = {
        "question_type": "cut_point",
        "content": "FLOW 研究使用的目标剂量是多少？",
        "topic": "FLOW 研究",
        "rationale": "核验明确剂量",
        "expected_answer_type": "DISCUSSION",
        "support_level": "HIGH",
        "support_score": 0.9,
        "evidence": [
            {
                "chunk_id": "c1",
                "document_id": str(uuid4()),
                "quote": "目标剂量",
                "evidence_summary": "剂量证据",
            }
        ],
    }
    try:
        GeneratedQuestion.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("CUT_POINT must not use DISCUSSION")


def test_retrieval_plan_entities_must_be_grounded_in_confirmed_minutes() -> None:
    plan = RetrievalPlan.model_validate(
        {
            "drug_names": ["不存在药物"],
            "cutpoint_queries": [
                {
                    "query": "不存在药物 剂量",
                    "purpose": "核验剂量",
                    "topic": "不存在药物",
                    "question_type": "cut_point",
                }
            ],
        }
    )
    try:
        validate_plan_grounding(plan, {"confirmed_minutes": "会议仅讨论 FLOW 研究。"})
    except Exception as exc:
        assert getattr(exc, "code", None) == "retrieval_plan_ungrounded"
        return
    raise AssertionError("ungrounded entities must be rejected")


def test_persisted_counts_require_both_question_types() -> None:
    assert has_required_question_types(1, 1)
    assert not has_required_question_types(1, 0)
    assert not has_required_question_types(0, 1)
