# Admin UI

Refindery ships a browser admin UI — the **cockpit** — served at `/admin` on the
same origin as the API. It is a read-and-operate console over the endpoints you
already use: identity (`/v1/whoami`), pages, search, jobs, clusters, models, and
the admin query log and config.

## Opening it

With the server running, open <http://127.0.0.1:8000/admin>. On first load, paste
a token with the scopes you need into **Settings**; it is stored in the browser's
`localStorage` and sent as `Authorization: Bearer …` on every request. Because the
UI is same-origin with the API, there is no CORS configuration and no separate
login — the token *is* the session. Use a read-scoped token for a view-only
console and a write-scoped one to operate. See [Authentication](../configuration/auth.md).

The UI itself is unauthenticated static content — it contains no secrets, and
every `/v1` call it makes is enforced by the token independently, exactly like a
`curl`. Serving it does not widen your auth surface.

## How it ships

The cockpit is developed in a separate repository
([`refindery-cockpit`](https://github.com/hbmartin/refindery-cockpit)) and built
into a static single-page app. Published refindery wheels embed that build under
the package (`refindery/api/static/admin`), so `pip install refindery` is all a
user needs — `refindery serve` then exposes `/admin` automatically, in the same
process as the API. Client-side routing is supported: deep links such as
`/admin/search` and hard refreshes resolve to the app shell.

Building **from a source checkout** does not include the bundle (it is fetched
from a pinned cockpit release only at wheel-publish time), so a `uv build` or
`refindery serve` from the repo runs the API with **no** `/admin` mount — the
rest of the server is unaffected. To populate it locally, run
`scripts/fetch-cockpit-ui.sh` (needs the `gh` CLI); it downloads the version
pinned in `COCKPIT_UI_VERSION` into `refindery/api/static/admin`.

For running the cockpit as its own server instead of the bundled static build,
see [Deployment profiles](../configuration/deployment-profiles.md#two-process-cockpit-ssr).
