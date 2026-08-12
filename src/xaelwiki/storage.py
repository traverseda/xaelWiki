"""Note store: id registry, CRUD, section handling, slugs, backlink repair."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import frontmatter

FOLDERS = ("00-inbox", "10-projects", "20-areas", "30-resources", "40-archive")
STATUSES = ("inbox", "active", "evergreen", "archived")
DEFAULT_FOLDER = "00-inbox"
META_DIR = "_meta"
CAPTURE_LOG = "capture-log.md"
INDEX_FILE = "INDEX.md"
TAGS_FILE = "TAGS.md"

FOLDER_STATUS = {
    "00-inbox": "inbox",
    "10-projects": "active",
    "20-areas": "active",
    "30-resources": "evergreen",
    "40-archive": "archived",
}

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
LINK_RE = re.compile(r"\]\((?P<slug>[^)\s]+?)(?P<ext>\.md)?(?P<anchor>#[^)\s]*)?\)")
LINK_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


class NoteError(Exception):
    """Base error for the note store."""


class NoteNotFound(NoteError):
    pass


class NoteConflict(NoteError):
    pass


class SectionNotFound(NoteError):
    pass


class ReadOnly(NoteError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def slugify(title: str) -> str:
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "note"


STOPWORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to",
    "was", "what", "when", "where", "which", "who", "why", "with", "you",
}

FIELD_WEIGHTS = (("title", 100), ("tags", 50), ("slug", 40), ("body", 10))
FTS_COLUMNS = ("title", "tags", "slug", "body")
FTS_WEIGHTS = (100.0, 50.0, 40.0, 10.0)

EXPLICIT_RE = re.compile(r'["*]|\b(AND|OR|NOT)\b|:')


def _tokens(text: str | None) -> list[str]:
    return [t for t in re.split(r"[^\w_]+", (text or "").lower()) if t]


class NoteStore:
    def __init__(self, root: Path, read_only: bool = False):
        self.root = Path(root)
        self.notes_dir = self.root / "notes"
        self.read_only = read_only
        self._cache: dict[Path, tuple[int, int, dict[str, Any]]] = {}
        self._notes: dict[str, dict[str, Any]] = {}
        self.ensure_layout()

    # ---------------------------------------------------------------- layout

    def ensure_layout(self) -> None:
        for folder in FOLDERS:
            (self.notes_dir / folder).mkdir(parents=True, exist_ok=True)
        (self.notes_dir / META_DIR).mkdir(parents=True, exist_ok=True)
        for name in (CAPTURE_LOG, INDEX_FILE, TAGS_FILE):
            (self.notes_dir / META_DIR / name).touch(exist_ok=True)

    def _require_write(self) -> None:
        if self.read_only:
            raise ReadOnly("server is read-only")

    # ---------------------------------------------------------------- scanning

    def _scan(self) -> dict[str, dict[str, Any]]:
        notes: dict[str, dict[str, Any]] = {}
        seen: set[Path] = set()
        for folder in FOLDERS:
            folder_dir = self.notes_dir / folder
            if not folder_dir.is_dir():
                continue
            for path in sorted(folder_dir.glob("*.md")):
                seen.add(path)
                try:
                    st = path.stat()
                    key = (st.st_mtime_ns, st.st_size)
                except OSError:
                    continue
                cached = self._cache.get(path)
                if cached is not None and cached[0] == key[0] and cached[1] == key[1]:
                    note = cached[2]
                else:
                    try:
                        note = self._parse(path)
                    except Exception:
                        continue
                    self._cache[path] = (key[0], key[1], note)
                if note.get("id"):
                    notes[note["id"]] = note
        for path in list(self._cache):
            if path not in seen:
                del self._cache[path]
        self._notes = notes
        return notes

    def _parse(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        fm, body = frontmatter.split(text)
        rel = path.relative_to(self.notes_dir)
        folder = rel.parts[0]
        slug = path.stem
        data: dict[str, Any] = dict(fm) if fm else {}
        data.setdefault("id", "x" + hashlib.sha1(str(rel).encode()).hexdigest()[:10])
        data.setdefault("title", self._title_from(body, slug))
        data.setdefault("created", now_iso())
        data.setdefault("updated", now_iso())
        data.setdefault("tags", [])
        data.setdefault("status", FOLDER_STATUS.get(folder, "inbox"))
        data.setdefault("folder", folder)
        data.setdefault("links", [])
        data.setdefault("source", "")
        data["slug"] = slug
        data["path"] = str(rel)
        data["_text"] = text
        data["body"] = body
        return data

    @staticmethod
    def _title_from(body: str, slug: str) -> str:
        for line in body.splitlines():
            m = HEADING_RE.match(line)
            if m:
                return m.group(2).strip()
        return slug.replace("-", " ").strip().title()

    def all_notes(self) -> list[dict[str, Any]]:
        self._scan()
        return list(self._notes.values())

    def resolve(self, note_id: str) -> dict[str, Any]:
        notes = self._scan()
        note = notes.get(note_id)
        if note is None:
            raise NoteNotFound(f"no note with id {note_id!r}")
        return note

    def revision(self, note: dict[str, Any]) -> str:
        return hashlib.sha1(note["_text"].encode("utf-8")).hexdigest()

    def raw(self, note_id: str) -> str:
        return self.resolve(note_id)["_text"]

    # ---------------------------------------------------------------- helpers

    def _unique_id(self) -> str:
        while True:
            candidate = today().replace("-", "") + "-" + secrets.token_hex(3)
            if candidate not in self._notes:
                return candidate

    def _unique_slug(self, slug: str, folder: str, exclude: str | None = None) -> str:
        base = slug
        n = 2
        while True:
            rel = f"{folder}/{base}.md"
            if (self.notes_dir / rel).exists() and rel != exclude:
                base = f"{slug}-{n}"
                n += 1
            else:
                return base

    def _public(self, note: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in note.items():
            if key.startswith("_"):
                continue
            if key == "body":
                out["content"] = value
            else:
                out[key] = value
        return out

    def _final(self, note: dict[str, Any]) -> dict[str, Any]:
        result = self._public(note)
        result["revision"] = self.revision(note)
        return result

    def meta_file(self, name: str) -> str:
        path = self.notes_dir / META_DIR / name
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return "(none)"

    def prompt_file(self, name: str) -> str:
        path = self.root / "prompts" / f"{name}.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return f"(no {name} prompt)"

    def reindex(self) -> None:
        """Regenerate _meta/INDEX.md and _meta/TAGS.md from the vault."""
        self._require_write()
        notes = self._scan()

        def row(note: dict[str, Any]) -> str:
            tags = ", ".join(f"`{t}`" for t in note.get("tags") or [])
            suffix = f" · {tags}" if tags else ""
            return (
                f"- [{note['title']}](../{note['folder']}/{note['slug']}.md)"
                f" — {note['status']}{suffix} · {note.get('updated', '')}"
            )

        index: list[str] = ["# Index", ""]
        by_tag: dict[str, list[dict[str, Any]]] = {}
        for folder in FOLDERS:
            grouped = sorted(
                (n for n in notes.values() if n.get("folder") == folder),
                key=lambda n: (n.get("title") or "").lower(),
            )
            if not grouped:
                continue
            index.append(f"## {folder}")
            index.extend(f"{row(note)}\n" for note in grouped)
            index.append("")
            for note in grouped:
                for tag in note.get("tags") or []:
                    by_tag.setdefault(tag, []).append(note)

        tags: list[str] = ["# Tags", ""]
        for tag in sorted(by_tag, key=str.lower):
            tagged = sorted(by_tag[tag], key=lambda n: (n.get("title") or "").lower())
            tags.append(f"## {tag}")
            tags.extend(
                f"- [{n['title']}](../{n['folder']}/{n['slug']}.md) — `{n['folder']}`\n"
                for n in tagged
            )
            tags.append("")

        self._write_meta(INDEX_FILE, "\n".join(index).rstrip("\n") + "\n")
        self._write_meta(TAGS_FILE, "\n".join(tags).rstrip("\n") + "\n")

    def _write_meta(self, name: str, content: str) -> None:
        (self.notes_dir / META_DIR / name).write_text(content, encoding="utf-8")

    # ---------------------------------------------------------------- capture

    def capture(
        self,
        title: str,
        body: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        notes = self._scan()
        note_id = self._unique_id()
        folder = DEFAULT_FOLDER
        slug = self._unique_slug(slugify(title), folder)
        fm = {
            "id": note_id,
            "title": title,
            "created": today(),
            "updated": now_iso(),
            "tags": [t for t in (tags or []) if t],
            "status": "inbox",
            "folder": folder,
            "links": [],
            "source": source or "",
        }
        path = self.notes_dir / folder / f"{slug}.md"
        body = body or ""
        self._reject_broken_links(folder, body)
        path.write_text(frontmatter.render(fm, body), encoding="utf-8")
        self._append_capture_log(note_id, title, source)
        result = self._final(self._parse(path))
        self.reindex()
        return result

    def _append_capture_log(self, note_id: str, title: str, source: str | None) -> None:
        path = self.notes_dir / META_DIR / CAPTURE_LOG
        line = f"- {now_iso()} `{note_id}` {title}"
        if source:
            line += f" ({source})"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # ---------------------------------------------------------------- read

    def read(self, note_id: str, section: str | None = None) -> dict[str, Any]:
        note = self.resolve(note_id)
        content = note["body"]
        if section:
            content = self._extract_section(content, section)
        result = self._public(note)
        result["content"] = content
        result["revision"] = self.revision(note)
        return result

    def _extract_section(self, body: str, name: str) -> str:
        lines = body.splitlines()
        target = name.strip().lower()
        for i, line in enumerate(lines):
            m = HEADING_RE.match(line)
            if not m:
                continue
            if m.group(2).strip().lower() == target:
                level = len(m.group(1))
                out = [line]
                for following in lines[i + 1 :]:
                    fm = HEADING_RE.match(following)
                    if fm and len(fm.group(1)) <= level:
                        break
                    out.append(following)
                return "\n".join(out)
        headings = [
            m.group(2).strip()
            for line in lines
            if (m := HEADING_RE.match(line))
        ]
        listing = ", ".join(headings) or "none"
        raise SectionNotFound(f"no section {name!r}; headings: {listing}")

    # ---------------------------------------------------------------- writes

    def _write(
        self,
        note: dict[str, Any],
        body: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        folder: str | None = None,
        status: str | None = None,
        source: str | None = None,
        bump_updated: bool = True,
    ) -> dict[str, Any]:
        fm: dict[str, Any] = {
            "id": note["id"],
            "title": title if title is not None else note["title"],
            "created": note["created"],
            "updated": now_iso() if bump_updated else note["updated"],
            "tags": tags if tags is not None else note["tags"],
            "status": status if status is not None else note["status"],
            "folder": folder if folder is not None else note["folder"],
            "links": note["links"],
            "source": source if source is not None else note.get("source", ""),
        }
        path = self.notes_dir / note["path"]
        text = frontmatter.render(fm, body)
        path.write_text(text, encoding="utf-8")
        return self._parse(path)

    def append(self, note_id: str, content: str, section: str | None = None) -> dict[str, Any]:
        self._require_write()
        note = self.resolve(note_id)
        body = note["body"]
        block = content.strip("\n")
        if not block:
            raise NoteError("nothing to append")
        if section:
            body = self._append_section(body, section, block)
        elif body:
            body = body.rstrip("\n") + "\n\n" + block + "\n"
        else:
            body = block + "\n"
        self._reject_broken_links(note["folder"], body, old_text=note["body"])
        result = self._final(self._write(note, body))
        self.reindex()
        return result

    def _append_section(self, body: str, name: str, block: str) -> str:
        target = name.strip().lower()
        lines = body.splitlines()
        for i, line in enumerate(lines):
            m = HEADING_RE.match(line)
            if not m:
                continue
            if m.group(2).strip().lower() == target:
                level = len(m.group(1))
                insert_at = len(lines)
                for j in range(i + 1, len(lines)):
                    fm = HEADING_RE.match(lines[j])
                    if fm and len(fm.group(1)) <= level:
                        insert_at = j
                        break
                rebuilt = lines[:insert_at] + [""] + block.splitlines() + lines[insert_at:]
                return "\n".join(rebuilt).strip("\n") + "\n"
        return body.rstrip("\n") + f"\n\n## {name}\n\n{block}\n"

    def update(
        self,
        note_id: str,
        content: str,
        revision: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        note = self.resolve(note_id)
        current = self.revision(note)
        if current != revision:
            raise NoteConflict(
                f"revision mismatch: note changed since it was read "
                f"(expected {revision[:8]}…, current {current[:8]}…)"
            )
        if title in (None, note["title"]) and content == note["body"]:
            return self._final(note)
        self._reject_broken_links(note["folder"], content, old_text=note["body"])
        result = self._final(self._write(note, content, title=title))
        self.reindex()
        return result

    def set_tags(
        self,
        note_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        note = self.resolve(note_id)
        tags = list(note["tags"] or [])
        for tag in add or []:
            tag = tag.strip()
            if tag and tag not in tags:
                tags.append(tag)
        for tag in remove or []:
            tag = tag.strip()
            if tag in tags:
                tags.remove(tag)
        if tags == note["tags"]:
            return self._final(note)
        result = self._final(self._write(note, note["body"], tags=tags))
        self.reindex()
        return result

    def move(
        self,
        note_id: str,
        folder: str | None = None,
        status: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        note = self.resolve(note_id)
        new_folder = folder if folder in FOLDERS else note["folder"]
        new_status = status if status in STATUSES else FOLDER_STATUS.get(new_folder, note["status"])
        new_title = title if title is not None else note["title"]
        new_slug = slugify(new_title) if title is not None else note["slug"]
        new_slug = self._unique_slug(new_slug, new_folder, exclude=note["path"])
        new_path = f"{new_folder}/{new_slug}.md"
        old_path = note["path"]

        if new_path == old_path and new_status == note["status"]:
            return self._final(note)

        if new_path != old_path:
            self._repair_backlinks(
                note["folder"], note["slug"], new_folder, new_slug, exclude=note_id
            )

        body = note["body"]
        if new_path != old_path and body:
            body = self._canonicalize_links(
                note, new_folder=new_folder, new_slug=new_slug
            )
            self._reject_broken_links(
                new_folder,
                body,
                old_text=note["body"],
                old_folder=note["folder"],
                self_ref=(new_folder, new_slug),
            )

        parsed = self._write(
            note,
            body,
            title=new_title,
            folder=new_folder,
            status=new_status,
        )
        if new_path != old_path:
            (self.notes_dir / new_path).write_text(parsed["_text"], encoding="utf-8")
            (self.notes_dir / old_path).unlink()
            result = self._final(self._parse(self.notes_dir / new_path))
            self.reindex()
            return result
        result = self._final(parsed)
        self.reindex()
        return result

    def _repair_backlinks(
        self,
        old_folder: str,
        old_slug: str,
        new_folder: str,
        new_slug: str,
        *,
        exclude: str | None = None,
    ) -> None:
        """Rewrite links that point at a moved note so they stay valid.

        Links in the note's own folder use the bare `](<slug>)` form; links
        elsewhere are root-relative `](../<folder>/<slug>.md)`. Every match is
        normalized to the canonical `](../<new_folder>/<new_slug>.md)` form,
        which resolves from any folder.
        """
        if new_folder == old_folder and new_slug == old_slug:
            return
        bare = re.compile(
            r"\]\(" + re.escape(old_slug) + r"(\.md)?(#[^)\s]*)?\)"
        )
        canonical = re.compile(
            r"\]\(\.\./" + re.escape(old_folder) + r"/" + re.escape(old_slug)
            + r"(\.md)?(#[^)\s]*)?\)"
        )

        def to_canonical(match: re.Match[str]) -> str:
            anchor = match.group(2) or ""
            return f"](../{new_folder}/{new_slug}.md{anchor})"

        for other in self._notes.values():
            if other["id"] == exclude:
                continue
            text = other["_text"]
            if other["folder"] == old_folder:
                new_text = bare.sub(to_canonical, text)
                new_text = canonical.sub(to_canonical, new_text)
            else:
                new_text = canonical.sub(to_canonical, text)
            if new_text != text:
                path = self.notes_dir / other["path"]
                path.write_text(new_text, encoding="utf-8")

    # ---------------------------------------------------------------- links

    def _find_note(self, folder: str, slug: str) -> dict[str, Any] | None:
        for note in self._notes.values():
            if note["folder"] == folder and note["slug"] == slug:
                return note
        return None

    @staticmethod
    def _link_parts(folder: str, target: str) -> tuple[str, str] | None:
        """Map a link target to (folder, slug) of the note it names.

        Accepts the canonical root-relative form `../<folder>/<slug>` (with or
        without `.md`) and the bare `<slug>` form, which resolves within the
        note's own folder. External URLs return None.
        """
        if LINK_SCHEME.match(target):
            return None
        if target.startswith("../"):
            parts = target[3:].split("/")
        elif "/" in target:
            parts = target.split("/")
        else:
            return folder, target
        if len(parts) == 2:
            return parts[0], parts[1]
        return None

    def _broken_links(self, folder: str, text: str, self_ref: tuple[str, str] | None = None) -> list[str]:
        """Return the note links in `text` that do not name a real note."""
        known = {(n["folder"], n["slug"]) for n in self._notes.values()}
        if self_ref:
            known.add(self_ref)
        broken = []
        for m in LINK_RE.finditer(text):
            parts = self._link_parts(folder, m.group("slug"))
            if parts is None or parts not in known:
                broken.append(m.group(0))
        return broken

    def _reject_broken_links(
        self,
        folder: str,
        new_text: str,
        old_text: str = "",
        old_folder: str | None = None,
        self_ref: tuple[str, str] | None = None,
    ) -> None:
        """Raise if a write introduces note links that do not resolve.

        Only links that are new (not already present in `old_text`) are
        reported, so pre-existing breakage never blocks a write.
        """
        new_broken = set(self._broken_links(folder, new_text, self_ref=self_ref))
        old_broken = set(
            self._broken_links(old_folder or folder, old_text, self_ref=self_ref)
        )
        added = new_broken - old_broken
        if not added:
            return
        listed = ", ".join(sorted(added))
        raise NoteError(
            f"broken note link(s): {listed}. Note links must name a real note; "
            f"use ](../<folder>/<slug>.md) to link across folders."
        )

    def _canonicalize_links(
        self,
        note: dict[str, Any],
        *,
        new_folder: str | None = None,
        new_slug: str | None = None,
    ) -> str:
        """Rewrite bare `](<slug>)` links to the root-relative form.

        `](slug)` becomes `](../<folder>/<slug>.md)` where <folder> is where
        the linked note actually lives; unresolvable links are left untouched.
        Self-references use the note's new location when it is moving.
        """
        def replace(match: re.Match[str]) -> str:
            target = match.group("slug")
            parts = self._link_parts(note["folder"], target)
            if parts is None:
                return match.group(0)
            resolved = self._find_note(parts[0], parts[1])
            if resolved is None:
                return match.group(0)
            if resolved["id"] == note["id"]:
                folder, slug = new_folder or resolved["folder"], new_slug or resolved["slug"]
            else:
                folder, slug = resolved["folder"], resolved["slug"]
            anchor = match.group("anchor") or ""
            return f"](../{folder}/{slug}.md{anchor})"

        return LINK_RE.sub(replace, note["body"])

    # ---------------------------------------------------------------- search

    def search(
        self,
        query: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        folder: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        notes = self._scan()
        raw = (query or "").strip()
        q = raw.lower()
        want_tags = {t.strip().lower() for t in (tags or []) if t.strip()}

        def passes(note: dict[str, Any]) -> bool:
            if status and note.get("status") != status:
                return False
            if folder and note.get("folder") != folder:
                return False
            if want_tags:
                have = {t.lower() for t in (note.get("tags") or [])}
                if not want_tags <= have:
                    return False
            return True

        ordered: list[dict[str, Any]] = []
        if q and EXPLICIT_RE.search(raw):
            ordered = [
                notes[i] for i in self._fts_hits(raw) if i in notes and passes(notes[i])
            ]
        elif q:
            tokens = [t for t in _tokens(q) if t not in STOPWORDS]
            if tokens:
                ranked = []
                for note_id in self._fts_hits(" OR ".join(tokens)):
                    note = notes.get(note_id)
                    if note is None or not passes(note):
                        continue
                    ranked.append((self._coverage(note, tokens), note))
                ranked.sort(key=lambda item: item[1].get("updated", ""), reverse=True)
                ranked.sort(key=lambda item: item[0], reverse=True)
                ordered = [note for _, note in ranked]
            else:
                ordered = [n for n in notes.values() if passes(n)]
        else:
            ordered = [n for n in notes.values() if passes(n)]
            ordered.sort(key=lambda note: note.get("updated", ""), reverse=True)

        return self._results(ordered, q, limit)

    def _fts_hits(self, expr: str) -> list[str]:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE notes_fts USING fts5("
                "title, tags, slug, body, note_id UNINDEXED, tokenize='unicode61')"
            )
            conn.executemany(
                "INSERT INTO notes_fts VALUES (?,?,?,?,?)",
                (
                    (
                        note.get("title") or "",
                        " ".join(note.get("tags") or []),
                        note.get("slug") or "",
                        note.get("body") or "",
                        note_id,
                    )
                    for note_id, note in self._notes.items()
                ),
            )
            weights = ", ".join(f"{w:.1f}" for w in FTS_WEIGHTS)
            rows = conn.execute(
                f"SELECT note_id FROM notes_fts WHERE notes_fts MATCH ? "
                f"ORDER BY bm25(notes_fts, {weights})",
                (expr,),
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _coverage(note: dict[str, Any], tokens: list[str]) -> int:
        score = 0
        for field, weight in FIELD_WEIGHTS:
            values = note.get(field)
            if field == "tags":
                values = " ".join(values or [])
            if not values:
                continue
            terms = set(_tokens(values))
            for token in tokens:
                if token in terms:
                    score += weight
        return score

    def _results(self, ordered: list[dict[str, Any]], q: str, limit: int) -> list[dict[str, Any]]:
        results = []
        for note in ordered[:limit]:
            results.append(
                {
                    "id": note["id"],
                    "title": note.get("title"),
                    "status": note.get("status"),
                    "folder": note.get("folder"),
                    "tags": note.get("tags") or [],
                    "updated": note.get("updated"),
                    "snippet": self._snippet(note, q),
                }
            )
        return results

    @staticmethod
    def _snippet(note: dict[str, Any], q: str) -> str:
        body = note.get("body") or ""
        tokens = [t for t in _tokens(q) if t not in STOPWORDS]
        if tokens:
            low = body.lower()
            hits = [(low.find(t), t) for t in tokens]
            hits = [(i, t) for i, t in hits if i >= 0]
            if hits:
                idx, token = min(hits)
                start = max(0, idx - 40)
                end = min(len(body), idx + len(token) + 80)
                segment = body[start:end].strip()
                prefix = "…" if start else ""
                suffix = "…" if end < len(body) else ""
                return prefix + segment + suffix
        first = body.strip().splitlines()[0] if body.strip() else ""
        return first[:140] + ("…" if len(first) > 140 else "")
