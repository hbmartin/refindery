# Deletion & blacklist

`POST /v1/forget` purges content **and** blacklists it in a single atomic
operation. Blacklisting matters because it tells upstream capture to stop wasting
work — a subsequent `POST /v1/pages` for a blacklisted pattern returns `403`
rather than a silent `202`.

## Forget

```bash
curl -s -X POST http://127.0.0.1:8000/v1/forget \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.com"}'      # or {"url": "https://example.com/page"}
```

The purge:

1. deletes the page row (cascading chunks, mentions, and cluster memberships);
2. deletes the page's vectors from **every** model's store;
3. recomputes entity counts and garbage-collects orphaned entities;
4. marks affected clusters stale.

Vector deletes are queued as `PURGE_VECTORS` jobs and reconciled by a periodic
`verify_tombstones` task (the tombstone moves `pending → deleted → verified`),
so a store that is briefly unavailable still converges. See
[Operations](../operations/index.md#job-lease-model).

!!! danger "Purge is irreversible"
    Forgetting deletes content permanently. Removing a blacklist rule later does
    **not** restore purged pages.

## Blacklist management

A forget adds a blacklist rule — an exact canonical URL or a domain suffix.
Manage rules directly:

```
GET    /v1/blacklist          list rules
DELETE /v1/blacklist/{id}     remove a rule (un-blacklist; does not restore content)
```

`write` scope is required for `forget` and un-blacklisting. See
[Authentication](../configuration/auth.md).

## Related

- [Upstream ingest API](../reference/upstream-ingest-api.md#post-v1forget--purge--blacklist) — full contract and the `403` blacklisted response.
- [Ingesting pages](ingest.md) — where the blacklist check happens.
- [Operations](../operations/index.md) — tombstone reconciliation and lease model.
