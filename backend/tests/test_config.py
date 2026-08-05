from app.core.config import Settings


def test_provider_api_key_is_used_when_generic_key_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    settings = Settings(_env_file=None, llm_api_key="")

    assert settings.resolved_llm_api_key == "deepseek-test-key"


def test_generic_api_key_takes_precedence_over_provider_fallback(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    settings = Settings(
        _env_file=None,
        llm_api_key="generic-test-key",
    )

    assert settings.resolved_llm_api_key == "generic-test-key"


def test_embedding_identity_includes_model_and_version() -> None:
    settings = Settings(
        _env_file=None,
        embedding_model="BAAI/test-model",
        embedding_version="test-v2",
    )

    assert settings.embedding_identity == (
        "test-v2@BAAI/test-model:single_pass_pool"
    )
    assert Settings(
        _env_file=None,
        embedding_model="BAAI/test-model",
        embedding_version="test-v2",
        bge_embedding_strategy="two_pass",
    ).embedding_identity == "test-v2@BAAI/test-model:two_pass"
