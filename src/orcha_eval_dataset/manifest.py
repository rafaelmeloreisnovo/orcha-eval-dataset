from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 1


class DatasetError(ValueError):
    """Dataset structure or content is invalid."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while chunk := fp.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _exact_path(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        matches = {
            unicodedata.normalize("NFC", entry.name): entry
            for entry in current.iterdir()
        }
        normalized = unicodedata.normalize("NFC", part)
        if normalized not in matches:
            raise DatasetError(f"Path does not exist with exact casing: {relative}")
        current = matches[normalized]
    if not current.is_file():
        raise DatasetError(f"Expected a file: {relative}")
    return current


def _entry(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": unicodedata.normalize("NFC", path.relative_to(root).as_posix()),
        "sha256": sha256(path),
        "size": path.stat().st_size,
    }


def build_manifest(root: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    metadata_dir = root / "metadata"
    files_dir = root / "files"
    if not metadata_dir.is_dir() or not files_dir.is_dir():
        raise DatasetError("Dataset must contain files/ and metadata/ directories")

    previous_path = manifest_path or root / "manifest.json"
    previous = load_manifest(previous_path)["records"] if previous_path.exists() else {}
    records: dict[str, Any] = {}
    referenced_documents: set[str] = set()
    required_documents: set[str] = set()
    for metadata_path in sorted(metadata_dir.glob("*.json")):
        record_id = metadata_path.stem
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        file_paths = metadata.get("file_paths")
        if not isinstance(file_paths, list) or len(file_paths) != 1:
            raise DatasetError(f"{record_id}: file_paths must contain one path")

        if not isinstance(file_paths[0], str):
            raise DatasetError(f"{record_id}: file_paths must contain a string")
        relative = PurePosixPath(file_paths[0])
        if relative.is_absolute() or ".." in relative.parts:
            raise DatasetError(f"{record_id}: invalid PDF path {relative}")
        if len(relative.parts) < 3 or relative.parts[0] != "files":
            raise DatasetError(f"{record_id}: document path must start with files/")
        if relative.suffix.casefold() not in {".pdf", ".png"}:
            raise DatasetError(f"{record_id}: document must be a PDF or PNG")
        document_relative = unicodedata.normalize("NFC", relative.as_posix())
        if document_relative in referenced_documents:
            raise DatasetError(
                f"Document referenced more than once: {document_relative}"
            )
        referenced_documents.add(document_relative)
        old_record = previous.get(record_id)
        if old_record and (
            old_record["document"]["path"] != document_relative
            or old_record["metadata"]["path"] != f"metadata/{record_id}.json"
        ):
            raise DatasetError(
                f"{record_id}: manifest disagrees with metadata file_paths"
            )
        if old_record and old_record["document"]["source"] == "remote":
            document = dict(old_record["document"])
        else:
            document = {
                "source": "dataset",
                **_entry(root, _exact_path(root, relative)),
            }
            required_documents.add(document_relative)
        records[record_id] = {
            "category": relative.parts[1],
            "document": document,
            "metadata": _entry(root, metadata_path),
        }

    if set(previous) - set(records):
        raise DatasetError("Manifest records have no matching metadata")
    all_documents = {
        unicodedata.normalize("NFC", path.relative_to(root).as_posix())
        for path in files_dir.rglob("*")
        if path.is_file()
    }
    if all_documents - referenced_documents or required_documents - all_documents:
        missing = sorted(all_documents - referenced_documents)
        absent = sorted(required_documents - all_documents)
        raise DatasetError(
            f"Document inventory mismatch; unreferenced={missing}, missing={absent}"
        )

    return {"schema_version": SCHEMA_VERSION, "records": records}


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise DatasetError(
            f"Unsupported manifest schema: {manifest.get('schema_version')}"
        )
    if not isinstance(manifest.get("records"), dict):
        raise DatasetError("Manifest records must be an object")
    paths = set()
    for record_id, record in manifest["records"].items():
        if not isinstance(record, dict):
            raise DatasetError(f"{record_id}: invalid record")
        for field in ("document", "metadata"):
            entry = record.get(field)
            if not isinstance(entry, dict):
                raise DatasetError(f"{record_id}: missing {field} entry")
            path = entry.get("path")
            if not isinstance(path, str):
                raise DatasetError(f"{record_id}: invalid path")
            relative = PurePosixPath(path)
            prefix = "files" if field == "document" else "metadata"
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.parts[:1] != (prefix,)
                or relative.as_posix() != path
                or unicodedata.normalize("NFC", path) != path
                or (
                    field == "document"
                    and (
                        len(relative.parts) < 3
                        or relative.suffix.casefold() not in {".pdf", ".png"}
                    )
                )
                or (field == "metadata" and path != f"metadata/{record_id}.json")
            ):
                raise DatasetError(f"{record_id}: invalid path {path}")
            if path in paths:
                raise DatasetError(f"Duplicate manifest path: {path}")
            paths.add(path)
            checksum, size = entry.get("sha256"), entry.get("size")
            if not isinstance(checksum, str) or not re.fullmatch(
                r"[0-9a-f]{64}", checksum
            ):
                raise DatasetError(f"{record_id}: invalid SHA-256")
            if type(size) is not int or size < 0:
                raise DatasetError(f"{record_id}: invalid size")
            if field == "metadata" and ("source" in entry or "url" in entry):
                raise DatasetError(
                    f"{record_id}: metadata cannot have a document source"
                )
            if field == "document":
                if entry.get("source") not in ("dataset", "remote"):
                    raise DatasetError(f"{record_id}: invalid document source")
                if entry["source"] == "remote":
                    url = entry.get("url")
                    try:
                        parsed = urlsplit(url) if isinstance(url, str) else None
                        valid = (
                            parsed is not None
                            and parsed.scheme in {"http", "https"}
                            and parsed.hostname
                            and not parsed.username
                            and not parsed.password
                            and not parsed.fragment
                            and not any(char.isspace() for char in url)
                        )
                        if parsed is not None:
                            parsed.port
                    except ValueError:
                        valid = False
                    if not valid:
                        raise DatasetError(f"{record_id}: invalid remote URL")
    return manifest


def check_manifest(root: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    path = manifest_path or root / "manifest.json"
    stored = load_manifest(path)
    actual = build_manifest(root, path)
    if stored != actual:
        raise DatasetError("manifest.json does not match the dataset")
    return stored


def manifest_paths(manifest: dict[str, Any]) -> list[str]:
    paths = []
    for record in manifest["records"].values():
        paths.append(record["metadata"]["path"])
        if record["document"]["source"] == "dataset":
            paths.append(record["document"]["path"])
    return sorted(paths)
