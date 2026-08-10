from pathlib import Path

import pytest

from xaelwiki.storage import (
    FOLDERS,
    NoteConflict,
    NoteNotFound,
    NoteStore,
    ReadOnly,
    SectionNotFound,
    slugify,
)


def make_store(tmp_path: Path, **kwargs) -> NoteStore:
    return NoteStore(tmp_path, **kwargs)


def test_slugify():
    assert slugify("Postgres Row Locking!") == "postgres-row-locking"
    assert slugify("../../evil") == "evil"
    assert slugify("  Leading  Trailing  ") == "leading-trailing"
    assert slugify("!!!") == "note"


def test_capture_creates_inbox_note(tmp_path):
    store = make_store(tmp_path)
    note = store.capture("My new note", body="hello world", tags=["work"], source="claude")
    assert note["folder"] == "00-inbox"
    assert note["status"] == "inbox"
    assert note["tags"] == ["work"]
    assert note["source"] == "claude"
    assert note["id"].startswith("20")
    path = store.notes_dir / note["path"]
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "id:" in text and "title: My new note" in text


def test_capture_does_not_overwrite(tmp_path):
    store = make_store(tmp_path)
    n1 = store.capture("Same title", body="one")
    n2 = store.capture("Same title", body="two")
    assert n1["id"] != n2["id"]
    assert n1["slug"] != n2["slug"]


def test_capture_logs(tmp_path):
    store = make_store(tmp_path)
    store.capture("Logged note", body="x", source="hermes")
    log = (store.notes_dir / "_meta" / "capture-log.md").read_text(encoding="utf-8")
    assert "Logged note" in log and "hermes" in log


def test_read_roundtrip_with_revision(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="body text")
    out = store.read(n["id"])
    assert out["content"] == "body text\n"
    assert out["revision"] == n["revision"]
    assert out["title"] == "Note"


def test_unknown_id_raises(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(NoteNotFound):
        store.resolve("does-not-exist")


def test_read_section(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="# Intro\none\n## Details\ntwo\n## More\nthree\n")
    out = store.read(n["id"], section="details")
    assert out["content"].strip() == "## Details\ntwo"
    with pytest.raises(SectionNotFound):
        store.read(n["id"], section="missing")


def test_append_body_and_section(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="# Intro\na\n## Details\nold\n")
    store.append(n["id"], "more")
    out = store.read(n["id"])
    assert out["content"] == "# Intro\na\n## Details\nold\n\nmore\n"
    store.append(n["id"], "extra", section="details")
    out = store.read(n["id"])
    assert "## Details\nold\n\nmore\n\nextra" in out["content"]
    store.append(n["id"], "fresh", section="New Section")
    out = store.read(n["id"])
    assert "## New Section\n\nfresh" in out["content"]


def test_update_requires_fresh_revision(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="v1")
    stale = n["revision"]
    store.append(n["id"], "more")
    with pytest.raises(NoteConflict):
        store.update(n["id"], "v3", stale)
    fresh = store.read(n["id"])["revision"]
    updated = store.update(n["id"], "v3", fresh)
    assert updated["content"] == "v3\n"
    assert updated["revision"] != fresh


def test_update_can_retitle(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Old title", body="body")
    updated = store.update(n["id"], "new body", n["revision"], title="New title")
    assert updated["title"] == "New title"
    assert updated["slug"] == "old-title"  # slug unchanged unless moved


def test_tag_add_remove(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="x", tags=["a"])
    out = store.set_tags(n["id"], add=["b", "b"], remove=["a"])
    assert out["tags"] == ["b"]


def test_move_files_and_sets_status(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Project thing", body="x")
    moved = store.move(n["id"], folder="10-projects")
    assert moved["folder"] == "10-projects"
    assert moved["status"] == "active"
    assert (store.notes_dir / "10-projects" / "project-thing.md").exists()
    assert not (store.notes_dir / "00-inbox" / "project-thing.md").exists()


def test_move_archive_sets_status(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Done", body="x")
    moved = store.move(n["id"], folder="40-archive")
    assert moved["status"] == "archived"


def test_move_renames_slug_and_repairs_backlinks(tmp_path):
    store = make_store(tmp_path)
    a = store.capture("Alpha", body="content")
    b = store.capture("Beta", body="see [Alpha](alpha) and [Alpha](alpha.md) ref")
    store.move(a["id"], folder="30-resources", title="Alpha Prime")
    out = store.read(b["id"])
    assert out["content"] == "see [Alpha](alpha-prime) and [Alpha](alpha-prime.md) ref\n"


def test_move_does_not_touch_unrelated_links(tmp_path):
    store = make_store(tmp_path)
    a = store.capture("Alpha", body="content")
    b = store.capture("Beta", body="see [Alphabet](alphabet) ref")
    store.move(a["id"], folder="30-resources", title="Alpha Prime")
    out = store.read(b["id"])
    assert "(alphabet)" in out["content"]


def test_search_query_tags_status_folder(tmp_path):
    store = make_store(tmp_path)
    store.capture("Postgres locks", body="rows are locked", tags=["db"])
    store.capture("Rust ownership", body="borrow checker", tags=["rust"])
    store.capture("Server setup", body="postgres on the box", tags=["infra"])

    results = store.search(query="locks")
    assert [r["title"] for r in results] == ["Postgres locks"]
    results = store.search(query="postgres")
    assert {r["title"] for r in results} == {"Postgres locks", "Server setup"}
    results = store.search(tags=["rust"])
    assert [r["title"] for r in results] == ["Rust ownership"]
    results = store.search(query="postgres", tags=["db"])
    assert [r["title"] for r in results] == ["Postgres locks"]
    results = store.search(tags=["db", "rust"])
    assert results == []


def test_search_result_shape(tmp_path):
    store = make_store(tmp_path)
    store.capture("Postgres locks", body="rows are locked", tags=["db"])
    results = store.search(limit=10)
    assert results
    keys = set(results[0])
    assert {"id", "title", "status", "folder", "tags", "updated", "snippet"} <= keys


def test_read_only_blocks_writes(tmp_path):
    store = make_store(tmp_path, read_only=True)
    with pytest.raises(ReadOnly):
        store.capture("X")
    with pytest.raises(ReadOnly):
        store.move("anything", folder="30-resources")
    with pytest.raises(ReadOnly):
        store.append("anything", "x")


def test_folder_layout_created(tmp_path):
    store = make_store(tmp_path)
    for folder in FOLDERS:
        assert (store.notes_dir / folder).is_dir()
