# xaelwiki

A shared, markdown-native notes MCP server for AI agents. Agents read, search,
and capture notes over MCP; a git history makes every change reversible; a
separate organizer agent keeps the wiki structured over time.

## Design

- **One MCP surface, two layers.** A lean action layer (8 tools) that is
  loaded in every agent session, and an on-demand knowledge layer
  (`xael://conventions`, `xael://index`, `xael://tags`, the `capture` /
  `organize` / `outline` prompts) that carries the *why*, not the *how*.
- **Human-readable markdown.** Notes are plain `.md` files with YAML
  frontmatter, stored in a git repo. Open them in vim, Obsidian, or a browser;
  `git` is the backup and the undo.
- **Safe by construction.** No delete tool (archive instead), every mutation
  auto-commits, rewrites require the current revision, and `undo` reverts
  through git.

## Layout

```
notes/
  00-inbox/      every capture lands here
  10-projects/   time-boxed, has a deliverable
  20-areas/      ongoing responsibilities
  30-resources/  evergreen atomic notes, structure notes
  40-archive/    inactive / done (never deleted)
  _meta/         AGENTS.md, INDEX.md, TAGS.md, capture-log.md, template.md
prompts/         capture.md, organize.md, outline.md
```

Each note:

```markdown
---
id: 20260810-a1b2c3
title: Postgres row locking
created: '2026-08-10'
updated: 2026-08-10 14:38 UTC
tags:
- database
status: evergreen
folder: 30-resources
---

# Postgres row locking

Rows are locked when updated.
```

The `id` is stable and canonical; filenames are readable slugs that the
organizer may rename freely (links are repaired automatically).

## Tools

| Tool | Description |
|---|---|
| `search(query?, tags?, status?, folder?, limit)` | Search notes. |
| `read(note_id, section?)` | Read a note. |
| `capture(title, body?, tags?, source?)` | Save a note to the inbox. |
| `append(note_id, content, section?)` | Add content to a note. |
| `update(note_id, content, revision, title?)` | Rewrite a note. |
| `tag(note_id, add?, remove?)` | Set a note's tags. |
| `move(note_id, folder?, status?, title?)` | Re-file a note. |
| `undo(steps?, dry_run?)` | Revert recent changes. |

`update` refuses to overwrite a note unless you pass the `revision` returned
by the last `read`. `undo` is git-backed and reverts the last `steps` commits.

### Resources (loaded on demand)

- `xael://note/{note_id}` — a note as raw markdown
- `xael://index` — generated index of notes
- `xael://tags` — generated tag index
- `xael://conventions` — the conventions/why file (`notes/_meta/AGENTS.md`)
- `xael://capture-log` — the append-only capture ledger

### Prompts (loaded on demand)

- `capture` — when and how to record something (the *why* of a good capture)
- `organize` — the archivist workflow, for the organizing agent
- `outline` — when a structure note earns its keep

## Install

One-liner (installs `uv` if missing, clones into `~/.local/share/xaelwiki`):

```sh
curl -fsSL https://raw.githubusercontent.com/traverseda/xaelWiki/main/install.sh | bash
```

Overridable via env: `XAEL_INSTALL_DIR`, `XAEL_NOTES_DIR`,
`XAEL_BRANCH`, `XAEL_REPO_URL`. Run it again to update. On first run it
generates a bearer token, saves it to `~/.config/xaelwiki/env` (mode 600),
and installs a systemd **user** service (`~/.config/systemd/user/xaelwiki.service`,
auto-enabled):

## Run

```sh
uv sync

# local, stdio (for Claude Desktop / cursor-style MCP configs)
xaelwiki

# shared, over HTTP (refuses to start without a token)
export XAEL_AUTH_TOKEN=changeme
xaelwiki --transport streamable-http --host 0.0.0.0 --port 8000
```

The HTTP transport **requires** `XAEL_AUTH_TOKEN` — the server exits rather
than start unauthenticated. Never commit the token; keep it in a 0600 file.

Configuration is environment-driven: `XAEL_ROOT` (repo root, defaults to
cwd), `XAEL_AUTH_TOKEN`, `XAEL_READ_ONLY=1`, `XAEL_AUTO_PUSH=0`,
`XAEL_TRANSPORT` / `XAEL_HOST` / `XAEL_PORT` / `XAEL_PATH`.

### Client config (HTTP)

```json
{
  "mcpServers": {
    "xaelwiki": {
      "type": "streamable-http",
      "url": "https://notes.example.com/mcp",
      "headers": { "Authorization": "Bearer changeme" }
    }
  }
}
```

### Deploy

The installer sets up a systemd **user** service:

```sh
systemctl --user status xaelwiki
journalctl --user -u xaelwiki -f
```

It runs from `~/.config/systemd/user/xaelwiki.service` (generated from
`deploy/xaelwiki.user.service`), loads the token from
`~/.config/xaelwiki/env`, and listens on `0.0.0.0:8000/mcp`. Terminate TLS
with a reverse proxy of your choice.

`deploy/xaelwiki.service` is an alternative **system** unit for root-managed
deployments: point it at the repo and set the token in a 0600 env file, then
`sudo systemctl enable --now xaelwiki`.

## The organizer agent

The wiki gets *more* structured over time because an organizer agent drains
the inbox on a schedule. Run it however you run your other autonomous agents;
this repo provides the job in `prompts/organize.md`. Load that prompt and
give the agent the same MCP surface:

1. Read `xael://conventions` and `xael://index`.
2. Drain the inbox: dedupe, merge (by append), promote, or retire.
3. Standardize frontmatter, tags, links; spawn structure notes when a cluster
   needs an entry point.
4. Regenerate `_meta/INDEX.md` and `_meta/TAGS.md`.

Guardrails are baked into the prompt: never overwrite a fresh revision, never
delete (archive instead), work in small batches.

## Safety model

- **No delete.** Retire via `move` → `40-archive`.
- **Git on every mutation.** Each write commits; `undo` restores the tree to
  the state before the reverted commits, as one new commit.
- **Optimistic concurrency.** `update` requires the current `revision`.
- **Read-only mode.** `XAEL_READ_ONLY=1` removes every write tool from the
  surface.
- **Bearer auth** on the HTTP transport (**required** — the server refuses
  to start over HTTP without `XAEL_AUTH_TOKEN`). Tokens are compared in
  constant time. On stdio the token is not required (local pipe only).
- **Path/ID validation.** Notes are addressed by registry-checked ids; slugs
  are sanitized, so a hostile title cannot escape the vault.

## Develop

```sh
uv sync --extra dev
uv run pytest
```
