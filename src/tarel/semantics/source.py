"""Exact source files and deterministic multi-file snapshots for semantic readers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tarel.semantics.contracts import SemanticFailure, SourceSnapshot

MAX_SEMANTIC_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SEMANTIC_SOURCE_FILES = 256


@dataclass(frozen=True, slots=True)
class SemanticSourceFile:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class SemanticSourceBundle:
    files: tuple[SemanticSourceFile, ...]

    @property
    def snapshot(self) -> SourceSnapshot:
        if len(self.files) == 1:
            item = self.files[0]
            media_type = (
                "application/json"
                if Path(item.path).suffix.casefold() == ".json"
                else "application/yaml"
            )
            return SourceSnapshot.from_content(item.content, media_type=media_type)
        content = json.dumps(
            {
                "files": [
                    {"content": item.content, "path": item.path}
                    for item in self.files
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return SourceSnapshot.from_content(
            content,
            media_type="application/vnd.tarel.semantic-source-bundle+json",
        )

    def only_file(self, *, format_name: str) -> SemanticSourceFile:
        if len(self.files) != 1:
            raise SemanticFailure(
                "invalid_semantic_source_bundle",
                f"{format_name} import requires exactly one source file.",
            )
        return self.files[0]


def read_semantic_source(
    path: Path,
    *,
    suffixes: frozenset[str],
) -> SemanticSourceBundle:
    try:
        if path.is_symlink():
            raise SemanticFailure(
                "unsafe_semantic_source",
                "Semantic source paths must not be symbolic links.",
            )
        if path.is_file():
            paths = (path,)
            root = path.parent
        elif path.is_dir():
            root = path
            paths = tuple(
                sorted(
                    (
                        item
                        for item in path.rglob("*")
                        if item.is_file() and item.suffix.casefold() in suffixes
                    ),
                    key=lambda item: item.relative_to(root).as_posix().casefold(),
                )
            )
        else:
            raise SemanticFailure(
                "semantic_source_not_found",
                f"Semantic source not found: {path}",
            )
        if not paths:
            raise SemanticFailure(
                "semantic_source_empty",
                f"No supported semantic source files found below: {path}",
            )
        if len(paths) > MAX_SEMANTIC_SOURCE_FILES:
            raise SemanticFailure(
                "semantic_source_too_large",
                f"Semantic source exceeds {MAX_SEMANTIC_SOURCE_FILES} files.",
            )

        files: list[SemanticSourceFile] = []
        total_bytes = 0
        for item in paths:
            if item.is_symlink():
                raise SemanticFailure(
                    "unsafe_semantic_source",
                    f"Semantic source file must not be a symbolic link: {item.name}",
                )
            size = item.stat().st_size
            total_bytes += size
            if total_bytes > MAX_SEMANTIC_SOURCE_BYTES:
                raise SemanticFailure(
                    "semantic_source_too_large",
                    f"Semantic source exceeds {MAX_SEMANTIC_SOURCE_BYTES} bytes.",
                )
            files.append(
                SemanticSourceFile(
                    path=item.relative_to(root).as_posix(),
                    content=item.read_bytes().decode("utf-8"),
                )
            )
        return SemanticSourceBundle(files=tuple(files))
    except SemanticFailure:
        raise
    except UnicodeDecodeError as exc:
        raise SemanticFailure(
            "invalid_semantic_source",
            "Semantic source files must be UTF-8 text.",
        ) from exc
    except OSError as exc:
        raise SemanticFailure(
            "semantic_source_read_failed",
            f"Could not read semantic source: {path}",
        ) from exc
