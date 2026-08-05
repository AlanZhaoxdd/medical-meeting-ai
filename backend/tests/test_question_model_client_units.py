import json
import sys
from types import SimpleNamespace

import pytest

from app.services import question_model_client
from app.services.question_model_client import QuestionGenerationModelClient


class _RawJsonModel:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def with_structured_output(self, *_: object, **__: object) -> "_RawJsonModel":
        return self

    async def ainvoke(self, prompt: str) -> dict[str, object]:
        self.prompts.append(prompt)
        return {
            "parsed": None,
            "raw": SimpleNamespace(
                content=json.dumps(
                    {
                        "retrieval_plan": {
                        "meeting_topics": ["肥胖管理"],
                        "medical_entities": [],
                        "study_names": [],
                        "drug_names": [],
                        "fact_check_queries": [
                            {"entity": "肥胖", "query": "肥胖管理现状", "type": "疾病"}
                        ],
                        "clinical_discussion_queries": [
                            {"topic": "治疗", "query": "肥胖管理讨论", "type": "临床讨论"}
                        ],
                        "suggested_specialties": [],
                        "version": "retrieval-plan-v2",
                        },
                    },
                    ensure_ascii=False,
                )
            ),
        }


@pytest.mark.asyncio
async def test_deepseek_raw_json_is_parsed_when_langchain_parsed_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model = _RawJsonModel()
    monkeypatch.setattr(
        question_model_client,
        "get_settings",
        lambda: SimpleNamespace(
            llm_base_url="https://api.deepseek.com",
            resolved_llm_api_key="test-key",
            llm_model="deepseek-chat",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "langchain_openai",
        SimpleNamespace(ChatOpenAI=lambda **_: fake_model),
    )

    result = await QuestionGenerationModelClient().build_plan({"meeting": "test"})

    assert result.cutpoint_queries[0].query == "肥胖管理现状"
    assert result.open_question_queries[0].query == "肥胖管理讨论"
    assert "JSON" in fake_model.prompts[0]
