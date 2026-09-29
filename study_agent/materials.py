"""Turn your notes, slides and textbook excerpts into Claude document blocks."""

from __future__ import annotations

import base64
from pathlib import Path

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".csv", ".tex", ".rst"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | PDF_SUFFIXES


def find_material_files(paths: list[str | Path]) -> list[Path]:
    """Expand files and directories into a sorted list of supported files."""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            found.extend(
                p
                for p in sorted(path.rglob("*"))
                if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
                and not any(part.startswith(".") for part in p.relative_to(path).parts)
            )
        elif path.is_file():
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise ValueError(
                    f"{path}: unsupported file type. Use one of: "
                    + ", ".join(sorted(SUPPORTED_SUFFIXES))
                )
            found.append(path)
        else:
            raise FileNotFoundError(f"{path}: no such file or directory")
    return found


def to_document_block(path: Path) -> dict:
    if path.suffix.lower() in PDF_SUFFIXES:
        source = {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(path.read_bytes()).decode("ascii"),
        }
    else:
        source = {
            "type": "text",
            "media_type": "text/plain",
            "data": path.read_text(encoding="utf-8", errors="replace"),
        }
    return {"type": "document", "source": source, "title": path.name}


def load_materials(paths: list[str | Path]) -> tuple[list[dict], list[Path]]:
    files = find_material_files(paths)
    return [to_document_block(p) for p in files], files
