"""Tests for content hashing and id generation."""

from refindery.domain.content_hash import content_hash
from refindery.domain.ids import new_page_id


def test_content_hash_is_stable_sha256_hex():
    h = content_hash("hello")
    assert h == content_hash("hello")
    assert len(h) == 64
    assert h != content_hash("hello ")


def test_page_ids_are_unique_and_time_ordered():
    ids = [new_page_id() for _ in range(50)]
    assert len(set(ids)) == 50
    assert ids == sorted(ids)
