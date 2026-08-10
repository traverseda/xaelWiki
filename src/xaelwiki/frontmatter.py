"""Frontmatter parsing and serialization for markdown notes."""

from __future__ import annotations

from typing import Any

import yaml

DELIM = "---"
FM_KEYS = [
    "id",
    "title",
    "created",
    "updated",
    "tags",
    "status",
    "folder",
    "links",
    "source",
]


def split(text: str) -> tuple[dict[str, Any] | None, str]:
    """Split markdown into (frontmatter dict, body).

    Returns (None, text) when the file has no leading frontmatter block or the
    block is unparsable.
    """
    if not text.startswith(DELIM):
        return None, text
    lines = text.split("\n")
    if not lines or lines[0].strip() != DELIM:
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == DELIM:
            raw = "\n".join(lines[1:i])
            try:
                data = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                return None, text
            if not isinstance(data, dict):
                return None, text
            body = "\n".join(lines[i + 1 :]).lstrip("\n")
            return data, body
    return None, text


def render(fm: dict[str, Any], body: str) -> str:
    """Serialize a note as a frontmatter block followed by the body."""
    ordered: dict[str, Any] = {}
    for key in FM_KEYS:
        value = fm.get(key)
        if value is not None and value != "" and value != []:
            ordered[key] = value
    block = yaml.safe_dump(
        ordered, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).rstrip("\n")
    body = body.strip("\n")
    if body:
        return f"{DELIM}\n{block}\n{DELIM}\n\n{body}\n"
    return f"{DELIM}\n{block}\n{DELIM}\n"
