import asyncio
from pathlib import Path

import pytest

from xaelwiki.server import BearerAuth, WRITE_TOOLS, build_server

EXPECTED_TOOLS = ["search", "read", "capture", "append", "update", "tag", "move", "undo"]


def run(coro):
    return asyncio.run(coro)


def result_of(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content


def test_tool_surface_is_lean_and_terse(tmp_path: Path):
    mcp = build_server(tmp_path)

    async def check():
        tools = await mcp.list_tools()
        return tools

    tools = run(check())
    assert [t.name for t in tools] == EXPECTED_TOOLS
    assert all(len((t.description or "").split()) <= 6 for t in tools)


def test_resources_and_prompts_exposed(tmp_path: Path):
    mcp = build_server(tmp_path)

    async def check():
        resources = await mcp.list_resources()
        templates = await mcp.list_resource_templates()
        prompts = await mcp.list_prompts()
        return resources, templates, prompts

    resources, templates, prompts = run(check())
    uris = {str(r.uri) for r in resources}
    assert {"xael://index", "xael://tags", "xael://conventions", "xael://capture-log"} <= uris
    assert {t.uri_template for t in templates} == {"xael://note/{note_id}"}
    assert {p.name for p in prompts} == {"capture", "organize", "outline"}


def test_read_only_disables_write_tools(tmp_path: Path):
    mcp = build_server(tmp_path, read_only=True)

    async def check():
        return await mcp.list_tools()

    tools = run(check())
    names = {t.name for t in tools}
    assert {"search", "read"} <= names
    assert not (names & WRITE_TOOLS)


def test_bearer_auth_accepts_exact_token_only():
    async def noop_receive():
        return {"type": "http.request"}

    def make_auth(called):
        async def app(scope, receive, send_message):
            called.append(True)

        return BearerAuth(app, "sekrit")

    async def run_case(auth, headers):
        statuses = []

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        await auth({"type": "http", "headers": headers}, noop_receive, send)
        return statuses

    wrong_called: list = []
    wrong_statuses = asyncio.run(run_case(make_auth(wrong_called), [(b"authorization", b"Bearer wrong")]))
    assert wrong_statuses == [401] and not wrong_called

    empty_called: list = []
    empty_statuses = asyncio.run(run_case(make_auth(empty_called), []))
    assert empty_statuses == [401] and not empty_called

    ok_called: list = []
    ok_statuses = asyncio.run(run_case(make_auth(ok_called), [(b"authorization", b"Bearer sekrit")]))
    assert ok_statuses == [] and len(ok_called) == 1


def test_end_to_end_lifecycle(tmp_path: Path):
    mcp = build_server(tmp_path)

    async def lifecycle():
        cap = await mcp.call_tool(
            "capture",
            {"title": "Postgres row locking", "body": "Rows are locked.", "tags": ["db"]},
        )
        note_id = result_of(cap)["id"]

        found = await mcp.call_tool("search", {"query": "postgres"})
        assert len(result_of(found)["result"]) == 1

        read = await mcp.call_tool("read", {"note_id": note_id})
        revision = result_of(read)["revision"]

        await mcp.call_tool("append", {"note_id": note_id, "content": "MVCC applies."})

        with pytest.raises(Exception):
            await mcp.call_tool(
                "update", {"note_id": note_id, "content": "clobber", "revision": "stale"}
            )

        fresh = await mcp.call_tool("read", {"note_id": note_id})
        new_rev = result_of(fresh)["revision"]
        updated = await mcp.call_tool(
            "update", {"note_id": note_id, "content": "Rewritten.", "revision": new_rev}
        )
        assert result_of(updated)["revision"] != revision

        tagged = await mcp.call_tool("tag", {"note_id": note_id, "add": ["postgres"]})
        assert "postgres" in result_of(tagged)["tags"]

        moved = await mcp.call_tool("move", {"note_id": note_id, "folder": "30-resources"})
        assert result_of(moved)["folder"] == "30-resources"
        assert result_of(moved)["status"] == "evergreen"

        dry = await mcp.call_tool("undo", {"dry_run": True})
        assert result_of(dry)["changes"]

        undone = await mcp.call_tool("undo", {"steps": 1})
        assert result_of(undone)["reverted"]

        return note_id

    run(lifecycle())


def test_conventions_resource_readable(tmp_path: Path):
    meta = tmp_path / "notes" / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "AGENTS.md").write_text("# Notes conventions\n\nSearch before capture.\n", encoding="utf-8")
    mcp = build_server(tmp_path)

    async def check():
        return await mcp.read_resource("xael://conventions")

    result = run(check())
    assert result.contents[0].content.splitlines()[0] == "# Notes conventions"
