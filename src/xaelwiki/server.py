"""FastMCP server exposing the xaelwiki note store."""

from __future__ import annotations

import argparse
import hmac
import os
from enum import Enum
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

from .git import GitError, GitRepo
from .storage import (
    FOLDER_STATUS,
    FOLDERS,
    STATUSES,
    NoteConflict,
    NoteError,
    NoteNotFound,
    NoteStore,
    ReadOnly,
)

INSTRUCTIONS = (
    "You manage a personal markdown wiki. Search before you capture. "
    "Captures go to the inbox. Rewrite only with the revision from a recent "
    "read. Never delete; archive. Read xael://conventions for the reasoning."
)

WRITE_TOOLS = {"capture", "append", "update", "tag", "move", "undo"}


class Folder(str, Enum):
    INBOX = "00-inbox"
    PROJECTS = "10-projects"
    AREAS = "20-areas"
    RESOURCES = "30-resources"
    ARCHIVE = "40-archive"


class Status(str, Enum):
    INBOX = "inbox"
    ACTIVE = "active"
    EVERGREEN = "evergreen"
    ARCHIVED = "archived"


class BearerAuth:
    """ASGI middleware requiring `Authorization: Bearer <token>`."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {
                k.lower(): v for k, v in scope.get("headers", [])
            }
            auth = headers.get(b"authorization", b"")
            expected = b"Bearer " + self.token
            if len(auth) != len(expected) or not hmac.compare_digest(auth, expected):
                body = b"unauthorized"
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"text/plain"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def build_server(
    root: Path,
    *,
    read_only: bool = False,
    auto_push: bool = True,
    auto_pull: bool = True,
) -> FastMCP:
    store = NoteStore(root, read_only=read_only)
    store.ensure_layout()
    git = GitRepo(root / "notes", auto_push=auto_push, auto_pull=auto_pull)
    git.ensure()

    mcp = FastMCP("xaelwiki", instructions=INSTRUCTIONS)

    @mcp.tool()
    def search(
        query: str | None = None,
        tags: list[str] | None = None,
        status: Status | None = None,
        folder: Folder | None = None,
        limit: int = 10,
    ) -> list[dict]:
        "Search notes."
        return store.search(
            query=query,
            tags=tags,
            status=status.value if status else None,
            folder=folder.value if folder else None,
            limit=limit,
        )

    @mcp.tool()
    def read(note_id: str, section: str | None = None) -> dict:
        "Read a note."
        return store.read(note_id, section=section)

    @mcp.tool()
    def capture(
        title: str,
        body: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> dict:
        "Save a note to the inbox."
        note = store.capture(title, body=body, tags=tags, source=source)
        git.mutate(f"capture {note['id']} {title}")
        return note

    @mcp.tool()
    def append(note_id: str, content: str, section: str | None = None) -> dict:
        "Add content to a note."
        note = store.append(note_id, content, section=section)
        git.mutate(f"append {note_id}")
        return note

    @mcp.tool()
    def update(
        note_id: str,
        content: str,
        revision: Annotated[
            str, Field(description="the revision from the last read of this note")
        ],
        title: str | None = None,
    ) -> dict:
        "Rewrite a note."
        note = store.update(note_id, content, revision, title=title)
        git.mutate(f"update {note_id}")
        return note

    @mcp.tool()
    def tag(
        note_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict:
        "Set a note's tags."
        note = store.set_tags(note_id, add=add, remove=remove)
        git.mutate(f"tag {note_id}")
        return note

    @mcp.tool()
    def move(
        note_id: str,
        folder: Folder | None = None,
        status: Status | None = None,
        title: str | None = None,
    ) -> dict:
        "Re-file a note."
        note = store.move(
            note_id,
            folder=folder.value if folder else None,
            status=status.value if status else None,
            title=title,
        )
        git.mutate(f"move {note_id}")
        return note

    @mcp.tool()
    def undo(
        steps: int = 1,
        dry_run: Annotated[
            bool, Field(description="just show recent changes, do not revert")
        ] = False,
    ) -> dict:
        "Revert recent changes."
        if dry_run:
            return {"changes": git.log(n=steps)}
        return git.revert(steps=steps)

    @mcp.resource("xael://note/{note_id}", mime_type="text/markdown")
    def note_resource(note_id: str) -> str:
        return store.raw(note_id)

    @mcp.resource("xael://index", mime_type="text/markdown")
    def index_resource() -> str:
        return store.meta_file("INDEX.md")

    @mcp.resource("xael://tags", mime_type="text/markdown")
    def tags_resource() -> str:
        return store.meta_file("TAGS.md")

    @mcp.resource("xael://conventions", mime_type="text/markdown")
    def conventions_resource() -> str:
        return store.meta_file("AGENTS.md")

    @mcp.resource("xael://capture-log", mime_type="text/markdown")
    def capture_log_resource() -> str:
        return store.meta_file("capture-log.md")

    @mcp.prompt(name="capture")
    def capture_prompt() -> str:
        return store.prompt_file("capture")

    @mcp.prompt(name="organize")
    def organize_prompt() -> str:
        return store.prompt_file("organize")

    @mcp.prompt(name="outline")
    def outline_prompt() -> str:
        return store.prompt_file("outline")

    if read_only:
        mcp.disable(names=WRITE_TOOLS)

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="xaelwiki",
        description="Shared markdown notes MCP server.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default=os.environ.get("XAEL_TRANSPORT", "stdio"),
    )
    parser.add_argument("--host", default=os.environ.get("XAEL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("XAEL_PORT", "8000")))
    parser.add_argument("--path", default=os.environ.get("XAEL_PATH", "/mcp"))
    parser.add_argument(
        "--root",
        default=os.environ.get("XAEL_ROOT", str(Path.cwd())),
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=os.environ.get("XAEL_READ_ONLY", "0") == "1",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        default=os.environ.get("XAEL_AUTO_PUSH", "1") == "0",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    token = os.environ.get("XAEL_AUTH_TOKEN", "")
    mcp = build_server(
        root,
        read_only=args.read_only,
        auto_push=not args.no_push,
    )

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    if not token:
        raise SystemExit(
            "error: HTTP transport requires XAEL_AUTH_TOKEN; "
            "set it or use --transport stdio"
        )

    middleware = [Middleware(BearerAuth, token=token)]
    mcp.run(
        transport=args.transport,
        host=args.host,
        port=args.port,
        path=args.path,
        middleware=middleware,
        host_origin_protection="auto",
    )


if __name__ == "__main__":
    main()
