"""End-to-end: a PDF fetched by URL is extracted, cleaned, and indexed.

Closes the gap where the test container registered no PDF extractor: this drives
the real ``PypdfExtractor`` through fetch_and_index and asserts the persisted
body text is cleaned (headers/footers stripped) end-to-end.
"""

import asyncio
from pathlib import Path

import httpx

from refindery.api.app import create_app
from refindery.application.ports.content_extractor import FetchResult
from refindery.domain.ids import PageId
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings
from tests.fakes.extraction import FakeFetcher

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pdf"


async def test_pdf_fetched_by_url_is_extracted_and_indexed(tmp_path):
    url = "https://example.test/headers_footers.pdf"
    fetcher = FakeFetcher(
        {
            url: FetchResult(
                url=url,
                final_url=url,
                status_code=200,
                content_type="application/pdf",
                charset=None,
                body=(FIXTURES / "headers_footers.pdf").read_bytes(),
            )
        }
    )
    container = build_test_container(tmp_path, fetcher=fetcher)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            response = await http.post("/v1/pages", json={"url": url}, headers=AUTH)
            assert response.status_code == 202
            page_id = response.json()["page_id"]
            async with asyncio.timeout(30):
                while True:
                    got = await http.get(f"/v1/pages/{page_id}/status", headers=AUTH)
                    if got.json()["status"] == "indexed":
                        break
                    await asyncio.sleep(0.05)
            page = await container.store.get_page(PageId(page_id))

    assert page is not None
    assert page.body_text is not None
    assert "Body text for section number 1 here." in page.body_text
    assert "ACME CONFIDENTIAL" not in page.body_text  # running header stripped
    assert "Page 1 of 4" not in page.body_text  # page-number footer stripped
