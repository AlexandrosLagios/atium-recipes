import json

import httpx
from notion_client import Client

from recipebot.notion import create_user_databases, load_schema_fixture


def test_create_user_databases_sends_the_schema_notion_actually_accepts():
    """The FakeDatabases/FakeDataSources fakes in test_notion_save.py accept
    any kwargs, so they never noticed the real SDK's DatabasesEndpoint.create
    silently drops a top-level `properties` kwarg (see api_endpoints.py's
    `pick(kwargs, "parent", "title", ...)`, which has no "properties" entry),
    or that a rollup/formula property forward-referencing a same-request
    relation would be rejected. This test intercepts the real HTTP layer the
    real SDK builds instead, so it can assert on the actual JSON body sent."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path == "/v1/databases":
            n = sum(1 for m, p, _ in requests if m == "POST" and p == "/v1/databases")
            return httpx.Response(200, json={"id": f"db{n}", "data_sources": [{"id": f"ds{n}"}]})
        if request.method == "GET" and request.url.path.startswith("/v1/blocks"):
            return httpx.Response(200, json={"results": [], "has_more": False, "next_cursor": None})
        if request.method == "GET" and request.url.path.startswith("/v1/data_sources"):
            # The reciprocal relation lookup after Recipes is created.
            return httpx.Response(
                200,
                json={"properties": {"Recipes": {"type": "relation", "relation": {"data_source_id": "ds2"}}}},
            )
        if request.method == "PATCH" and request.url.path.startswith("/v1/data_sources"):
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = Client(auth="fake-token", client=httpx.Client(transport=httpx.MockTransport(handler)))

    create_user_databases(client, "page-1", load_schema_fixture())

    creates = [body for method, path, body in requests if method == "POST" and path == "/v1/databases"]
    assert len(creates) == 2
    for body in creates:
        assert "properties" not in body, "the real API silently ignores a top-level properties key"
        assert "initial_data_source" in body
        for name, config in body["initial_data_source"]["properties"].items():
            assert config.get("type") not in ("rollup", "formula"), f"{name} forward-references a relation that doesn't exist yet"

    updates = [body for method, path, body in requests if method == "PATCH" and path.startswith("/v1/data_sources")]
    assert any("Used in" in body.get("properties", {}) for body in updates)
    assert any("Missing count" in body.get("properties", {}) for body in updates)
