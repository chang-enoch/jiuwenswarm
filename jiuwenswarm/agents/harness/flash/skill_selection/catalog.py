"""Build bounded retrieval texts at startup/refresh, not on each query."""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

import yaml

from .types import SkillDocument
from .cards import capability_card
from .sources import read_sources, source_fingerprint
from .capability_metadata import declared_contract

logger = logging.getLogger(__name__)


def directory_id(directory: str | Path) -> str:
    # Resolve symlinks and normalize Windows casing so scope comparisons agree.
    import os

    return os.path.normcase(str(Path(directory).resolve()))


def _descriptions(metadata: dict) -> tuple[str, ...]:
    """Use authored descriptions only; do not translate or duplicate fields."""
    descriptions = []
    seen = set()
    for key in ("description", "description_zh", "description_en"):
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = " ".join(value.split())
        if normalized not in seen:
            descriptions.append(value.strip())
            seen.add(normalized)
    return tuple(descriptions)


def read_catalog(
    roots: Sequence[Path], *, text_max_chars: int, text_mode: str = "full",
    observed_files: dict | None = None,
) -> tuple[SkillDocument, ...]:
    documents: dict[str, SkillDocument] = {}
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            continue
        for directory in sorted(root.iterdir()):
            path = directory / "SKILL.md"
            if directory.name.startswith((".", "_")) or not path.is_file():
                continue
            # Shared roots must be registered explicitly, not reached by escaping links.
            if not path.resolve().is_relative_to(root):
                continue
            try:
                identity = directory_id(directory)
                if identity not in documents and len(documents) >= 10000:
                    raise RuntimeError(
                        "Local skill catalog exceeds the 10000 entry indexing limit"
                    )
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError("SKILL.md exceeds the 1 MiB local indexing limit")
                raw = path.read_text(encoding="utf-8-sig")
                metadata: dict = {}
                body = raw
                lines = raw.splitlines()
                if lines and lines[0].strip() == "---":
                    end = next(
                        (i for i in range(1, len(lines)) if lines[i].strip() == "---"),
                        None,
                    )
                    if end is not None:
                        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
                        body = "\n".join(lines[end + 1:])
                if not isinstance(metadata, dict):
                    raise ValueError("Skill front matter must be a mapping")
                name = str(metadata.get("name") or directory.name)
                # Headings retain later capabilities even when the body is very long.
                headings = "\n".join(
                    line for line in body.splitlines() if line.startswith("#")
                )
                descriptions = _descriptions(metadata)
                description = "\n".join(descriptions)
                parts = (
                    (name, description)
                    if text_mode == "description" and description.strip()
                    else (name, description, headings, body)
                )
                text = (
                    "\n".join(
                        (
                            capability_card(name, descriptions[0] if descriptions else "", body),
                            # Separate bounds keep the original three-sentence
                            # excerpt from dropping the other language fields.
                            *(" ".join(value.split())[:600] for value in descriptions[1:]),
                        )
                    )
                    if text_mode == "capability"
                    else "\n".join(parts)
                )[:text_max_chars]
                sources = read_sources(directory, raw, observed_files=observed_files)
                record = asdict(declared_contract(description[:16384], sources))
                record = {k: sorted(v) if isinstance(v, frozenset) else v for k, v in record.items()}
                documents[identity] = SkillDocument(
                    identity,
                    name,
                    str(directory.resolve()),
                    text,
                    source_fingerprint('', sources),
                    description[:4000],
                    record,
                )
            except (OSError, ValueError, yaml.YAMLError) as exc:
                logger.warning(
                    "[SkillSelection] skipping unreadable skill %s: %s", path, exc
                )
    return tuple(documents.values())
