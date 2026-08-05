from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException
from app.worker import extraction


class _FakeStructured:
    def __init__(self, *, fail: bool = False) -> None:
        self.prompts: list[str] = []
        self.fail = fail

    async def ainvoke(self, prompt: str) -> dict[str, object]:
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("maximum context length exceeded")
        return {
            "parsed": {
                "items": [
                    {
                        "item_type": "insight",
                        "title": "结论",
                        "normalized_content": "结论内容",
                        "source_refs": [{"chunk_id": "c1", "quote": "原文"}],
                        "confidence": 0.8,
                    }
                ]
            }
        }


class _FakeChat:
    def __init__(self, structured: _FakeStructured) -> None:
        self.structured = structured

    def with_structured_output(self, *_: object, **__: object) -> _FakeStructured:
        return self.structured


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        resolved_llm_api_key="test-key",
        llm_base_url="https://llm.test",
        llm_model="test-model",
        model_service_max_input_characters=50000,
    )


@pytest.mark.asyncio
async def test_extraction_batches_and_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeStructured()
    monkeypatch.setattr(extraction, "get_settings", _settings)
    monkeypatch.setattr(extraction, "ChatOpenAI", lambda **_: _FakeChat(fake))
    result = await extraction.extract_knowledge(
        [
            {"chunk_id": "c1", "content": "a" * 20000},
            {"chunk_id": "c2", "content": "b" * 20000},
        ],
        ["insight"],
    )
    assert len(fake.prompts) == 2
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_provider_length_error_maps_to_stable_app_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeStructured(fail=True)
    monkeypatch.setattr(extraction, "get_settings", _settings)
    monkeypatch.setattr(extraction, "ChatOpenAI", lambda **_: _FakeChat(fake))
    with pytest.raises(AppException) as caught:
        await extraction.extract_knowledge([{"chunk_id": "c1", "content": "text"}], [])
    assert caught.value.code == "llm_completion_limit"
