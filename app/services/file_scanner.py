from __future__ import annotations

import re
from pathlib import Path

GGUF_EXTENSIONS = {".gguf"}
TEMPLATE_EXTENSIONS = {".jinja", ".jinja2", ".txt", ".tmpl"}
_PART_PATTERN = re.compile(
    r"(?P<base>.+?)(?:[-_.](?:part|split)[-_]?(?P<index>\d+)(?:-of-\d+)?|[-_.](?P<shard>\d+)-of-\d+)$",
    re.I,
)


def natural_key(value: str) -> list[object]:
    return [int(chunk) if chunk.isdigit() else chunk.casefold() for chunk in re.split(r"(\d+)", value)]


def scan_files(folder: str, extensions: set[str], *, recursive: bool = True) -> list[Path]:
    root = Path(folder)
    if not folder or not root.is_dir():
        return []
    iterator = root.rglob("*") if recursive else root.glob("*")
    files = [path for path in iterator if path.is_file() and path.suffix.casefold() in extensions]
    return sorted(files, key=lambda path: natural_key(str(path.relative_to(root))))


def scan_gguf_models(folder: str) -> list[Path]:
    """Return one loader entry for clear multipart GGUF sets (the first shard)."""
    result: list[Path] = []
    seen_sets: set[str] = set()
    for path in scan_files(folder, GGUF_EXTENSIONS):
        match = _PART_PATTERN.match(path.stem)
        if match:
            key = str(path.parent / match.group("base")).casefold()
            index = int(match.group("index") or match.group("shard"))
            if key in seen_sets or index != 1:
                continue
            seen_sets.add(key)
        result.append(path)
    return result


def scan_templates(folder: str) -> list[Path]:
    return scan_files(folder, TEMPLATE_EXTENSIONS)
