"""Verify that release archives contain only portable public project files."""

from __future__ import annotations

import argparse
import email
import gzip
import os
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

REPRODUCIBLE_EPOCH = int(os.getenv("SOURCE_DATE_EPOCH", "1580601600"))
REPRODUCIBLE_ZIP_TIME = (2020, 2, 2, 0, 0, 0)

FORBIDDEN_NAMES = (
    ".tarel/",
    "/agents.md",
    "/roadmap.md",
    "/docs/coding-conventions.md",
    "/docs/competitors.md",
    "/docs/harness-adaptation.md",
    "/src/tarel/harnesses/",
    "/tests/local_databases.toml",
    "/tools/create_legacy_adventureworks.py",
)
FORBIDDEN_SUFFIXES = (".gguf", ".log", ".pyc", ".sqlite")
FORBIDDEN_TEXT = (
    b"/" + b"home/",
    b".tarel/" + b"notes/",
    b"WORKSPACE_" + b"2026",
    b"matti" + b"wsl",
    b"wsl." + b"localhost",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, nargs="?", default=Path("dist"))
    args = parser.parse_args()
    wheels = sorted(args.directory.glob("*.whl"))
    sdists = sorted(args.directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit("Expected exactly one wheel and one source distribution.")
    _check_wheel(wheels[0])
    _check_sdist(sdists[0])
    print(f"Verified public distributions: {wheels[0].name}, {sdists[0].name}")


def _check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        names = [item.filename for item in members]
        _check_names(item.filename for item in members)
        _check_text((item.filename, archive.read(item)) for item in members)
        if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
            raise SystemExit("Wheel does not contain the MIT license file.")
        if not any(
            name.endswith(".dist-info/licenses/THIRD_PARTY_NOTICES.md") for name in names
        ):
            raise SystemExit("Wheel does not contain the third-party notices.")
        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")),
            None,
        )
        if metadata_name is None:
            raise SystemExit("Wheel metadata is missing.")
        metadata = email.message_from_bytes(archive.read(metadata_name))
        if metadata.get("License-Expression") != "MIT":
            raise SystemExit("Wheel metadata does not declare the MIT license expression.")
        if metadata.get("Author") != "MPS":
            raise SystemExit("Wheel metadata does not declare MPS as author.")
        project_urls = metadata.get_all("Project-URL") or []
        if not any(value.startswith("Repository, https://github.com/") for value in project_urls):
            raise SystemExit("Wheel metadata does not declare its GitHub repository URL.")
        invalid_times = {
            item.date_time for item in members if item.date_time != REPRODUCIBLE_ZIP_TIME
        }
    if invalid_times:
        raise SystemExit(f"Wheel contains non-reproducible timestamps: {sorted(invalid_times)}")


def _check_sdist(path: Path) -> None:
    with tarfile.open(path) as archive:
        members = [item for item in archive.getmembers() if item.isfile()]
        if not any(item.name.endswith("/LICENSE") for item in members):
            raise SystemExit("Source distribution does not contain the MIT license file.")
        if not any(item.name.endswith("/THIRD_PARTY_NOTICES.md") for item in members):
            raise SystemExit("Source distribution does not contain the third-party notices.")
        _check_names(item.name for item in members)
        payloads: list[tuple[str, bytes]] = []
        for item in members:
            extracted = archive.extractfile(item)
            if extracted is not None:
                payloads.append((item.name, extracted.read()))
        _check_text(payloads)
        invalid_times = {item.mtime for item in members if item.mtime != REPRODUCIBLE_EPOCH}
    if invalid_times:
        raise SystemExit(f"Source archive has non-reproducible timestamps: {sorted(invalid_times)}")
    with gzip.open(path, "rb") as compressed:
        compressed.peek(1)
        gzip_time = compressed.mtime
    if gzip_time != REPRODUCIBLE_EPOCH:
        raise SystemExit(f"Source archive gzip header has timestamp {gzip_time}.")


def _check_names(names: Iterable[str]) -> None:
    rejected = []
    for name in names:
        normalized = "/" + name.replace("\\", "/").lower().lstrip("/")
        if any(marker in normalized for marker in FORBIDDEN_NAMES) or normalized.endswith(
            FORBIDDEN_SUFFIXES
        ):
            rejected.append(name)
    if rejected:
        raise SystemExit(f"Distribution contains private or generated files: {sorted(rejected)}")


def _check_text(payloads: Iterable[tuple[str, bytes]]) -> None:
    rejected = []
    for name, payload in payloads:
        if b"\0" in payload or len(payload) > 2_000_000:
            continue
        if any(marker.lower() in payload.lower() for marker in FORBIDDEN_TEXT):
            rejected.append(name)
    if rejected:
        raise SystemExit(f"Distribution contains local path markers: {sorted(rejected)}")


if __name__ == "__main__":
    main()
