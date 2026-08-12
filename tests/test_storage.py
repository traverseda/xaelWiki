from pathlib import Path

import pytest

from xaelwiki.storage import (
    FOLDERS,
    NoteConflict,
    NoteError,
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


def test_tag_noop_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="x", tags=["a"])
    path = store.notes_dir / n["path"]
    before = path.read_text(encoding="utf-8")
    out = store.set_tags(n["id"], add=["a"], remove=["missing"])
    assert out["revision"] == n["revision"]
    assert path.read_text(encoding="utf-8") == before


def test_update_identical_content_noop(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="x")
    path = store.notes_dir / n["path"]
    before = path.read_text(encoding="utf-8")
    updated = store.update(n["id"], "x", n["revision"], title="Note")
    assert updated["revision"] == n["revision"]
    assert path.read_text(encoding="utf-8") == before


def test_move_noop_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="x")
    path = store.notes_dir / n["path"]
    before = path.read_text(encoding="utf-8")
    moved = store.move(n["id"], folder="00-inbox")
    assert moved["revision"] == n["revision"]
    assert path.read_text(encoding="utf-8") == before


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
    assert (
        out["content"]
        == "see [Alpha](../30-resources/alpha-prime.md) and [Alpha](../30-resources/alpha-prime.md) ref\n"
    )


def test_move_does_not_touch_unrelated_links(tmp_path):
    store = make_store(tmp_path)
    a = store.capture("Alpha", body="content")
    store.capture("Alphabet", body="alphabetical")
    b = store.capture("Beta", body="see [Alphabet](alphabet) ref")
    store.move(a["id"], folder="30-resources", title="Alpha Prime")
    out = store.read(b["id"])
    assert "(alphabet)" in out["content"]


def test_capture_rejects_broken_link(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(NoteError, match="broken note link"):
        store.capture("Note", body="see [Ghost](ghost.md)")


def test_capture_rejects_bare_cross_folder_link(tmp_path):
    store = make_store(tmp_path)
    r = store.capture("Resource thing", body="x")
    store.move(r["id"], folder="30-resources")
    with pytest.raises(NoteError, match="broken note link"):
        store.capture("New", body="see [Resource thing](resource-thing.md)")


def test_capture_allows_valid_canonical_link(tmp_path):
    store = make_store(tmp_path)
    r = store.capture("Resource thing", body="x")
    store.move(r["id"], folder="30-resources")
    n = store.capture("New", body="see [Resource thing](../30-resources/resource-thing.md)")
    assert n["id"]


def test_capture_allows_valid_same_folder_bare_link(tmp_path):
    store = make_store(tmp_path)
    store.capture("Alpha", body="x")
    n = store.capture("Beta", body="see [Alpha](alpha)")
    assert n["id"]


def test_append_rejects_new_broken_link(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="ok")
    with pytest.raises(NoteError, match="broken note link"):
        store.append(n["id"], "see [Ghost](ghost.md)")


def test_append_allows_grandfathered_broken_link(tmp_path):
    store = make_store(tmp_path)
    b = store.capture("Beta", body="plain")
    path = store.notes_dir / b["path"]
    text = path.read_text(encoding="utf-8").replace("plain", "see [Ghost](ghost.md)")
    path.write_text(text, encoding="utf-8")
    out = store.append(b["id"], "more")
    assert "more" in out["content"]
    assert "ghost.md" in store.read(b["id"])["content"]


def test_update_rejects_new_broken_link(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="ok")
    with pytest.raises(NoteError, match="broken note link"):
        store.update(n["id"], "see [Ghost](ghost.md)", n["revision"])


def test_move_canonicalizes_own_links(tmp_path):
    store = make_store(tmp_path)
    r = store.capture("Resource thing", body="x")
    m = store.capture("Move me", body="see [Resource thing](resource-thing.md)")
    store.move(r["id"], folder="30-resources")
    out = store.move(m["id"], folder="10-projects")
    assert "](../30-resources/resource-thing.md)" in out["content"]


def test_meta_layout_created(tmp_path):
    store = make_store(tmp_path)
    for name in ("INDEX.md", "TAGS.md", "capture-log.md"):
        assert (store.notes_dir / "_meta" / name).exists()


def test_capture_regenerates_index_and_tags(tmp_path):
    store = make_store(tmp_path)
    store.capture("Postgres row locking", body="rows are locked", tags=["db", "sql"])
    store.capture("Rust ownership", body="borrow checker", tags=["rust"])

    index = (store.notes_dir / "_meta" / "INDEX.md").read_text(encoding="utf-8")
    tags = (store.notes_dir / "_meta" / "TAGS.md").read_text(encoding="utf-8")

    assert "## 00-inbox" in index
    assert "[Postgres row locking](../00-inbox/postgres-row-locking.md)" in index
    assert "[Rust ownership](../00-inbox/rust-ownership.md)" in index
    assert "## db" in tags
    assert "## rust" in tags
    assert "[Rust ownership](../00-inbox/rust-ownership.md) — `00-inbox`" in tags


def test_move_regenerates_index(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Project thing", body="x")
    store.move(n["id"], folder="10-projects")
    index = (store.notes_dir / "_meta" / "INDEX.md").read_text(encoding="utf-8")
    assert "## 10-projects" in index
    assert "[Project thing](../10-projects/project-thing.md)" in index


def test_tag_regenerates_tags(tmp_path):
    store = make_store(tmp_path)
    n = store.capture("Note", body="x")
    store.set_tags(n["id"], add=["fresh"])
    tags = (store.notes_dir / "_meta" / "TAGS.md").read_text(encoding="utf-8")
    assert "## fresh" in tags


def test_reindex_read_only_blocked(tmp_path):
    store = make_store(tmp_path, read_only=True)
    with pytest.raises(ReadOnly):
        store.reindex()


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


def test_search_word_boundaries_not_substrings(tmp_path):
    store = make_store(tmp_path)
    store.capture("Lock picking", body="a hobby about locksmithing")
    store.capture("Postgres locking", body="row locks")
    assert [r["title"] for r in store.search(query="lock")] == ["Lock picking"]
    assert [r["title"] for r in store.search(query="locking")] == ["Postgres locking"]


def test_search_punctuation_does_not_break_tokens(tmp_path):
    store = make_store(tmp_path)
    store.capture("Postgres, locks", body="rows, are, locked")
    assert [r["title"] for r in store.search(query="postgres")] == ["Postgres, locks"]
    assert [r["title"] for r in store.search(query="locks")] == ["Postgres, locks"]


def test_search_natural_language_phrase_recalls_ranked(tmp_path):
    store = make_store(tmp_path)
    store.capture("Postgres row locking", body="rows are locked on commit")
    store.capture("Server setup", body="postgres on the box")
    results = store.search(query="how to lock rows in postgres")
    assert [r["title"] for r in results] == ["Postgres row locking", "Server setup"]


def test_search_stopword_only_query_lists_all(tmp_path):
    store = make_store(tmp_path)
    store.capture("Alpha", body="x")
    store.capture("Beta", body="y")
    assert {r["title"] for r in store.search(query="how to in the")} == {"Alpha", "Beta"}


def test_search_phrase_query_exact(tmp_path):
    store = make_store(tmp_path)
    store.capture("Postgres locks", body="rows are locked")
    store.capture("Postgres setup", body="locking rows is a thing")
    results = store.search(query='"postgres locks"')
    assert [r["title"] for r in results] == ["Postgres locks"]


def test_search_explicit_and_query(tmp_path):
    store = make_store(tmp_path)
    store.capture("Postgres locks", body="rows are locked", tags=["db"])
    store.capture("Postgres setup", body="postgres on the box")
    results = store.search(query="postgres AND db")
    assert [r["title"] for r in results] == ["Postgres locks"]


def test_search_not_query(tmp_path):
    store = make_store(tmp_path)
    store.capture("Postgres locks", body="rows are locked", tags=["db"])
    store.capture("Postgres setup", body="postgres on the box")
    results = store.search(query="postgres NOT db")
    assert [r["title"] for r in results] == ["Postgres setup"]


def test_search_prefix_query(tmp_path):
    store = make_store(tmp_path)
    store.capture("Borrow checker", body="rust ownership")
    store.capture("Bound check", body="other stuff")
    results = store.search(query="borrow*")
    assert [r["title"] for r in results] == ["Borrow checker"]


def test_search_column_filter_query(tmp_path):
    store = make_store(tmp_path)
    store.capture("Postgres locks", body="rows are locked")
    store.capture("Server setup", body="postgres on the box")
    results = store.search(query="title:postgres")
    assert [r["title"] for r in results] == ["Postgres locks"]


def test_search_snippet_highlights_matched_token(tmp_path):
    store = make_store(tmp_path)
    store.capture("Alpha", body="unrelated stuff here that goes on and on about things\nlocked row in here")
    result = store.search(query="row")[0]
    assert "row" in result["snippet"]


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
