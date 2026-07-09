"""Entity domain: taxonomy, surface-form normalization, blocking.

Canonicalization is corpus-internal (no Wikidata): normalization + blocking
here, matching/merging in the canonicalization service. ``inflect`` (pure
Python) is the one allowed non-stdlib dependency for singularization.
"""

import string
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

import inflect

from refindery.domain.ids import EntityId

_inflect = inflect.engine()

_PUNCT_TABLE = str.maketrans(string.punctuation, " " * len(string.punctuation))


class EntityType(StrEnum):
    """Fixed extraction taxonomy."""

    PERSON = "person"
    ORG = "org"
    PRODUCT = "product"
    TECHNOLOGY = "technology"
    CONCEPT = "concept"
    PLACE = "place"
    WORK = "work"


@dataclass(slots=True)
class Entity:
    """A canonical entity aggregated over the corpus."""

    id: EntityId
    canonical_form: str
    type: EntityType
    mention_count: int = 0
    page_count: int = 0
    idf: float | None = None


def normalize_surface_form(surface: str) -> str:
    """Casefold, strip diacritics and punctuation, singularize the last word."""
    text = unicodedata.normalize("NFKD", surface.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(_PUNCT_TABLE)
    words = text.split()
    if words and (singular := _inflect.singular_noun(words[-1])):
        words[-1] = singular
    return " ".join(words)


def block_key(normalized: str) -> str:
    """Blocking key for candidate matching: the first token."""
    return normalized.split(" ", maxsplit=1)[0] if normalized else ""


def normalized_edit_distance(a: str, b: str) -> float:
    """Levenshtein distance normalized to [0, 1] (0 = identical).

    Small pure implementation; the canonicalization service uses rapidfuzz
    when available and falls back to this.
    """
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (ca != cb),
                )
            )
        previous = current
    return previous[-1] / max(len(a), len(b))
