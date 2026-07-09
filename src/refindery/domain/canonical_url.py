"""Pure URL canonicalization.

Rules (per spec): lowercase scheme + host; strip default port; strip ``www.``;
strip fragment; strip tracking params (configurable glob patterns); sort the
remaining query params; strip trailing slash. Per-domain overrides can
restrict which query params survive (e.g. YouTube keeps only ``v``).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from urllib.parse import parse_qsl, urlencode, urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}

DEFAULT_TRACKING_PARAMS = ("utm_*", "fbclid", "gclid", "ref", "si")


@dataclass(frozen=True, slots=True)
class DomainRule:
    """Per-domain override: only ``keep_params`` survive canonicalization."""

    keep_params: frozenset[str]


DEFAULT_DOMAIN_RULES: Mapping[str, DomainRule] = {
    "youtube.com": DomainRule(keep_params=frozenset({"v"})),
}


@dataclass(frozen=True, slots=True)
class CanonicalizationRules:
    """Configurable canonicalization behavior."""

    tracking_params: tuple[str, ...] = DEFAULT_TRACKING_PARAMS
    domain_rules: Mapping[str, DomainRule] = field(
        default_factory=lambda: dict(DEFAULT_DOMAIN_RULES)
    )


@dataclass(frozen=True, slots=True)
class CanonicalUrl:
    """A canonicalized URL and its domain."""

    url: str
    domain: str


def _strip_www(host: str) -> str:
    return host.removeprefix("www.")


def _domain_rule_for(host: str, rules: Mapping[str, DomainRule]) -> DomainRule | None:
    for domain, rule in rules.items():
        if host == domain or host.endswith(f".{domain}"):
            return rule
    return None


def _is_tracking_param(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(name, pattern) for pattern in patterns)


def _canonical_query(query: str, *, host: str, rules: CanonicalizationRules) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    if (rule := _domain_rule_for(host, rules.domain_rules)) is not None:
        pairs = [(k, v) for k, v in pairs if k in rule.keep_params]
    else:
        pairs = [
            (k, v) for k, v in pairs if not _is_tracking_param(k, rules.tracking_params)
        ]
    return urlencode(sorted(pairs))


def canonicalize(url: str, rules: CanonicalizationRules | None = None) -> CanonicalUrl:
    """Canonicalize ``url`` and derive its domain.

    Raises ``ValueError`` for URLs without a scheme or host.
    """
    rules = rules or CanonicalizationRules()
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.hostname:
        msg = f"not an absolute http(s) URL: {url!r}"
        raise ValueError(msg)

    scheme = parts.scheme.lower()
    host = _strip_www(parts.hostname.lower())
    port = parts.port
    if port is None or _DEFAULT_PORTS.get(scheme) == port:
        netloc = host
    else:
        netloc = f"{host}:{port}"

    path = parts.path.rstrip("/")
    query = _canonical_query(parts.query, host=host, rules=rules)

    canonical = f"{scheme}://{netloc}{path}"
    if query:
        canonical = f"{canonical}?{query}"
    return CanonicalUrl(url=canonical, domain=host)
