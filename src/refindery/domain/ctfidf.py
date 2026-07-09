"""Class-based TF-IDF keywords for cluster labeling (pure, in-house).

The formula from BERTopic's c-TF-IDF, over one pseudo-document per cluster,
without the BERTopic dependency. Keywords are always populated regardless of
whether an LLM label lands later.
"""

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z][a-z0-9'-]{2,}")

_STOPWORDS = frozenset([
    "a", "about", "above", "after", "again", "all", "also", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "can", "did", "do",
    "does", "doing", "down", "during", "each", "few", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "him",
    "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself",
    "just", "me", "more", "most", "my", "no", "nor", "not", "now", "of",
    "off", "on", "once", "only", "or", "other", "our", "ours", "out", "over",
    "own", "same", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "theirs", "them", "then", "there", "these", "they",
    "this", "those", "through", "to", "too", "under", "until", "up", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who",
    "whom", "why", "will", "with", "you", "your", "yours",
])  # fmt: skip


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def compute_ctfidf(
    docs_by_cluster: dict[str, str], *, top_n: int = 10
) -> dict[str, list[str]]:
    """Top c-TF-IDF terms per cluster from one pseudo-document each."""
    counts = {cid: Counter(_tokens(doc)) for cid, doc in docs_by_cluster.items()}
    total_freq: Counter[str] = Counter()
    for counter in counts.values():
        total_freq.update(counter)
    if not total_freq:
        return {cid: [] for cid in docs_by_cluster}

    average_words = sum(total_freq.values()) / max(len(counts), 1)
    keywords: dict[str, list[str]] = {}
    for cid, counter in counts.items():
        scored = [
            (
                term,
                (freq / max(sum(counter.values()), 1))
                * math.log1p(average_words / total_freq[term]),
            )
            for term, freq in counter.items()
        ]
        scored.sort(key=lambda item: (-item[1], item[0]))
        keywords[cid] = [term for term, _ in scored[:top_n]]
    return keywords
