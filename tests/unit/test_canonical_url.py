"""Table-driven tests for URL canonicalization."""

import pytest

from refindery.domain.canonical_url import (
    CanonicalizationRules,
    DomainRule,
    canonicalize,
)

CASES = [
    # scheme + host lowercasing
    ("HTTPS://Example.COM/Path", "https://example.com/Path"),
    # strip default ports
    ("https://example.com:443/a", "https://example.com/a"),
    ("http://example.com:80/a", "http://example.com/a"),
    # non-default port kept
    ("http://example.com:8080/a", "http://example.com:8080/a"),
    # strip www.
    ("https://www.example.com/a", "https://example.com/a"),
    # strip fragment
    ("https://example.com/a#section-2", "https://example.com/a"),
    # strip trailing slash (incl. root)
    ("https://example.com/a/", "https://example.com/a"),
    ("https://example.com/", "https://example.com"),
    # tracking params stripped
    (
        "https://example.com/a?utm_source=x&utm_medium=y&id=3",
        "https://example.com/a?id=3",
    ),
    ("https://example.com/a?fbclid=abc", "https://example.com/a"),
    ("https://example.com/a?gclid=abc&ref=hn&si=zz", "https://example.com/a"),
    # remaining params sorted
    ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
    # blank values preserved
    ("https://example.com/a?q=", "https://example.com/a?q="),
    # YouTube domain rule: keep only v
    (
        "https://www.youtube.com/watch?v=abc123&t=42s&feature=share",
        "https://youtube.com/watch?v=abc123",
    ),
    ("https://m.youtube.com/watch?v=abc&list=PL1", "https://m.youtube.com/watch?v=abc"),
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_canonicalize(raw, expected):
    assert canonicalize(raw).url == expected


def test_domain_is_www_stripped_host():
    assert canonicalize("https://www.Example.com/x").domain == "example.com"


def test_domain_keeps_subdomains():
    assert canonicalize("https://blog.example.com/x").domain == "blog.example.com"


def test_rejects_relative_url():
    with pytest.raises(ValueError, match="absolute"):
        canonicalize("/just/a/path")


def test_rejects_schemeless_url():
    with pytest.raises(ValueError, match="absolute"):
        canonicalize("example.com/a")


def test_custom_tracking_params():
    rules = CanonicalizationRules(tracking_params=("mc_*",))
    got = canonicalize("https://example.com/a?mc_eid=1&utm_source=x", rules=rules)
    assert got.url == "https://example.com/a?utm_source=x"


def test_custom_domain_rule():
    rules = CanonicalizationRules(
        domain_rules={"shop.example": DomainRule(keep_params=frozenset({"sku"}))}
    )
    got = canonicalize("https://shop.example/p?sku=9&color=red", rules=rules)
    assert got.url == "https://shop.example/p?sku=9"


def test_idempotent():
    once = canonicalize("https://WWW.Example.com:443/a/?b=2&a=1&utm_source=x#frag")
    twice = canonicalize(once.url)
    assert once.url == twice.url
