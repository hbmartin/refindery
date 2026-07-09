"""Scoped bearer-token auth. Always required, even on loopback.

401 means unauthenticated (missing or unknown token); 403 means the token
is valid but lacks the required scope. Every router depends on the read
scope; mutating routes additionally depend on the write scope.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Self

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from refindery.config import Scope, Settings

_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    """Who authenticated: the token's name and what it may do."""

    name: str
    scopes: frozenset[Scope]


@dataclass(frozen=True, slots=True)
class TokenRegistry:
    """All configured tokens, resolvable without timing leaks."""

    entries: tuple[tuple[str, Principal], ...]

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Build the registry from every configured token."""
        return cls(
            entries=tuple(
                (
                    spec.token.get_secret_value(),
                    Principal(name=spec.name, scopes=frozenset(spec.scopes)),
                )
                for spec in settings.resolved_tokens()
            )
        )

    def resolve(self, presented: str) -> Principal | None:
        """Match the presented token against every entry, never exiting early.

        Scanning all entries with compare_digest keeps response timing
        independent of which (if any) entry matched.
        """
        matched: Principal | None = None
        for secret, principal in self.entries:
            if secrets.compare_digest(presented, secret):
                matched = principal
        return matched


def require_scope(scope: Scope) -> Callable[..., Principal]:
    """Build a FastAPI dependency enforcing one scope."""

    def dependency(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_scheme)],
    ) -> Principal:
        registry: TokenRegistry = request.app.state.token_registry
        if (
            credentials is None
            or (principal := registry.resolve(credentials.credentials)) is None
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
            )
        if scope not in principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token {principal.name!r} lacks the {scope} scope",
            )
        return principal

    return dependency


require_read = require_scope(Scope.READ)
require_write = require_scope(Scope.WRITE)
