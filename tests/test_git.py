import subprocess
from pathlib import Path

import pytest

from xaelwiki.git import GitRepo
from xaelwiki.storage import NoteStore


def test_mutate_commits_changes(tmp_path: Path):
    store = NoteStore(tmp_path)
    git = GitRepo(tmp_path)
    note = store.capture("One", body="hello")
    git.mutate("capture one")
    assert git.log(5)[0]["subject"] == "capture one"


def test_revert_removes_last_change(tmp_path: Path):
    store = NoteStore(tmp_path)
    git = GitRepo(tmp_path)
    n1 = store.capture("One", body="keep me")
    git.mutate("capture one")
    n2 = store.capture("Two", body="remove me")
    git.mutate("capture two")

    result = git.revert(1)
    assert len(result["reverted"]) == 1

    fresh = NoteStore(tmp_path)
    ids = {note["id"] for note in fresh.all_notes()}
    assert n1["id"] in ids
    assert n2["id"] not in ids


def test_revert_multi_steps(tmp_path: Path):
    store = NoteStore(tmp_path)
    git = GitRepo(tmp_path)
    ids = []
    for title in ("A", "B", "C"):
        note = store.capture(title, body="x")
        git.mutate(f"capture {title}")
        ids.append(note["id"])

    git.revert(2)
    fresh = NoteStore(tmp_path)
    remaining = {note["id"] for note in fresh.all_notes()}
    assert remaining == {ids[0]}


def test_log_empty_repo(tmp_path: Path):
    git = GitRepo(tmp_path)
    assert git.log() == []
    assert git.revert(1)["reverted"] == []


def test_ensure_sets_identity_when_missing(tmp_path: Path):
    git = GitRepo(tmp_path)
    git.ensure()
    assert git._run("config", "--local", "--get", "user.name").stdout.strip() == "xaelwiki"
    assert git._run("config", "--local", "--get", "user.email").stdout.strip() == "xaelwiki@local"


def test_ensure_does_not_clobber_existing_identity(tmp_path: Path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-b", "main"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local", "user.name", "someone-else"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "--local", "user.email", "someone@else.dev"], capture_output=True)

    git = GitRepo(tmp_path)
    git.ensure()
    assert git._run("config", "--local", "--get", "user.name").stdout.strip() == "someone-else"
    assert git._run("config", "--local", "--get", "user.email").stdout.strip() == "someone@else.dev"
