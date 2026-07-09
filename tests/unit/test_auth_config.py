"""Token settings validation and registry resolution."""

import pytest
from pydantic import SecretStr, ValidationError

from refindery.api.auth import TokenRegistry
from refindery.config import Scope, Settings, TokenSpec


def make_settings(
    *,
    auth_token: SecretStr | None = None,
    auth_tokens: tuple[TokenSpec, ...] = (),
) -> Settings:
    return Settings(auth_token=auth_token, auth_tokens=auth_tokens)


class TestTokenSettings:
    def test_no_tokens_rejected(self, monkeypatch):
        monkeypatch.delenv("REFINDERY_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("REFINDERY_AUTH_TOKENS", raising=False)
        with pytest.raises(ValidationError, match="REFINDERY_AUTH_TOKEN"):
            Settings()

    def test_legacy_token_alone_is_full_access(self):
        settings = make_settings(auth_token=SecretStr("legacy"))
        (spec,) = settings.resolved_tokens()
        assert spec.name == "default"
        assert set(spec.scopes) == {Scope.READ, Scope.WRITE}

    def test_named_tokens_alone_are_valid(self):
        settings = make_settings(
            auth_tokens=(
                TokenSpec(name="agent", token=SecretStr("t"), scopes=(Scope.READ,)),
            )
        )
        assert settings.auth_token is None
        assert settings.resolved_tokens()[0].name == "agent"

    def test_write_implies_read(self):
        spec = TokenSpec(name="w", token=SecretStr("t"), scopes=(Scope.WRITE,))
        assert set(spec.scopes) == {Scope.READ, Scope.WRITE}

    def test_empty_scopes_rejected(self):
        with pytest.raises(ValidationError, match="at least one scope"):
            TokenSpec(name="none", token=SecretStr("t"), scopes=())

    def test_duplicate_names_rejected(self):
        with pytest.raises(ValidationError, match="unique"):
            make_settings(
                auth_tokens=(
                    TokenSpec(name="dup", token=SecretStr("a")),
                    TokenSpec(name="dup", token=SecretStr("b")),
                )
            )

    def test_json_env_parsing(self, monkeypatch):
        monkeypatch.setenv(
            "REFINDERY_AUTH_TOKENS",
            '[{"name": "agent", "token": "sekrit", "scopes": ["read"]}]',
        )
        monkeypatch.delenv("REFINDERY_AUTH_TOKEN", raising=False)
        settings = Settings()
        (spec,) = settings.resolved_tokens()
        assert spec.name == "agent"
        assert spec.token.get_secret_value() == "sekrit"
        assert spec.scopes == (Scope.READ,)


class TestTokenRegistry:
    def test_resolves_by_secret(self):
        settings = make_settings(
            auth_token=SecretStr("legacy"),
            auth_tokens=(
                TokenSpec(name="agent", token=SecretStr("t2"), scopes=(Scope.READ,)),
            ),
        )
        registry = TokenRegistry.from_settings(settings)
        legacy = registry.resolve("legacy")
        agent = registry.resolve("t2")
        assert legacy is not None
        assert legacy.name == "default"
        assert agent is not None
        assert agent.scopes == frozenset({Scope.READ})
        assert registry.resolve("nope") is None
