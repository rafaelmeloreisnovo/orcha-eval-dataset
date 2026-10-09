import json
import subprocess
import sys
import unicodedata
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from orcha_eval_dataset.archive import build_archive
from orcha_eval_dataset.manifest import (
    DatasetError,
    build_manifest,
    check_manifest,
    load_manifest,
    manifest_bytes,
)


def make_dataset(root: Path, pdf_path: str = "files/Articles/ART_1.pdf") -> None:
    (root / "files/Articles").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "files/Articles/ART_1.pdf").write_bytes(b"pdf")
    (root / "metadata/ART_1.json").write_text(
        json.dumps({"title": "Example", "file_paths": [pdf_path]}),
        encoding="utf-8",
    )


def test_manifest_records_content_hashes(tmp_path: Path) -> None:
    make_dataset(tmp_path)

    manifest = build_manifest(tmp_path)

    assert manifest == {
        "schema_version": 1,
        "records": {
            "ART_1": {
                "category": "Articles",
                "document": {
                    "source": "dataset",
                    "path": "files/Articles/ART_1.pdf",
                    "sha256": (
                        "c35b21d6ca39aa7cc3b79a705d989f1a6e88b99ab43988d7404879"
                        "9e3db926a3"
                    ),
                    "size": 3,
                },
                "metadata": {
                    "path": "metadata/ART_1.json",
                    "sha256": (
                        "1e7582f23fa5389a1b2808238fd7c7e40534c3648ea010a081b4b0"
                        "4557894131"
                    ),
                    "size": 64,
                },
            }
        },
    }


def test_manifest_rejects_wrong_path_case(tmp_path: Path) -> None:
    make_dataset(tmp_path, "Files/Articles/ART_1.pdf")

    with pytest.raises(DatasetError, match="must start with files/"):
        build_manifest(tmp_path)


def test_manifest_normalizes_paths_to_nfc(tmp_path: Path) -> None:
    composed = "files/Articles/ART_1__Muñoz.pdf"
    decomposed = unicodedata.normalize("NFD", composed)
    (tmp_path / "files/Articles").mkdir(parents=True)
    (tmp_path / "metadata").mkdir()
    (tmp_path / composed).write_bytes(b"pdf")
    (tmp_path / "metadata/ART_1.json").write_text(
        json.dumps({"title": "Example", "file_paths": [decomposed]}),
        encoding="utf-8",
    )

    manifest = build_manifest(tmp_path)

    assert manifest["records"]["ART_1"]["document"]["path"] == composed


def test_archive_is_byte_stable(tmp_path: Path) -> None:
    make_dataset(tmp_path)
    manifest = build_manifest(tmp_path)
    (tmp_path / "manifest.json").write_bytes(manifest_bytes(manifest))

    first = build_archive(tmp_path, manifest, tmp_path / "first.zip")
    second = build_archive(tmp_path, manifest, tmp_path / "second.zip")

    assert first.read_bytes() == second.read_bytes()


@pytest.fixture
def mixed_dataset(tmp_path: Path) -> tuple[Path, dict]:
    make_dataset(tmp_path)
    manifest = build_manifest(tmp_path)
    metadata = tmp_path / "metadata/REMOTE.json"
    metadata.write_text(json.dumps({"file_paths": ["files/Reports/Muñoz.pdf"]}))
    manifest["records"]["REMOTE"] = {
        "category": "Reports",
        "document": {
            "source": "remote",
            "path": "files/Reports/Muñoz.pdf",
            "sha256": "a" * 64,
            "size": 123,
            "url": "https://example.org/download/paper-v2.pdf",
        },
        "metadata": {
            "path": "metadata/REMOTE.json",
            "sha256": "b" * 64,
            "size": 0,
        },
    }
    (tmp_path / "manifest.json").write_bytes(manifest_bytes(manifest))
    rebuilt = build_manifest(tmp_path)
    (tmp_path / "manifest.json").write_bytes(manifest_bytes(rebuilt))
    return tmp_path, rebuilt


def test_mixed_manifest_rebuild_and_archive(mixed_dataset) -> None:
    root, manifest = mixed_dataset
    remote = dict(manifest["records"]["REMOTE"]["document"])
    assert not (root / remote["path"]).exists()
    assert check_manifest(root) == manifest
    assert build_manifest(root)["records"]["REMOTE"]["document"] == remote
    (root / "files/Articles/ART_1.pdf").write_bytes(b"changed hosted bytes")
    (root / "metadata/REMOTE.json").write_text(
        json.dumps({"file_paths": ["files/Reports/Muñoz.pdf"], "title": "Updated"})
    )
    rebuilt = build_manifest(root)
    assert rebuilt["records"]["REMOTE"]["document"] == remote
    assert (
        rebuilt["records"]["ART_1"]["document"]["sha256"]
        != (manifest["records"]["ART_1"]["document"]["sha256"])
    )
    assert (
        rebuilt["records"]["REMOTE"]["metadata"]["sha256"]
        != (manifest["records"]["REMOTE"]["metadata"]["sha256"])
    )
    (root / "manifest.json").write_bytes(manifest_bytes(rebuilt))
    assert check_manifest(root) == rebuilt
    first = build_archive(root, rebuilt, root / "first.zip")
    second = build_archive(root, rebuilt, root / "second.zip")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "files/Articles/ART_1.pdf",
            "metadata/ART_1.json",
            "metadata/REMOTE.json",
        ]
        assert json.loads(archive.read("manifest.json")) == rebuilt
        assert archive.read("files/Articles/ART_1.pdf") == b"changed hosted bytes"


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "unknown"),
        ("source", None),
        ("source", []),
        ("url", None),
        ("url", "ftp://example.org/paper.pdf"),
        ("url", "https:///paper.pdf"),
        ("url", "https://example.org:bad/paper.pdf"),
        ("url", "https://user:password@example.org/paper.pdf"),
        ("path", "files/Reports/../paper.pdf"),
        ("path", "files/Reports/Mun\u0303oz.pdf"),
        ("size", True),
        ("size", -1),
        ("sha256", "not-a-hash"),
    ],
)
def test_manifest_rejects_invalid_descriptors(mixed_dataset, field, value) -> None:
    root, manifest = mixed_dataset
    manifest["records"]["REMOTE"]["document"][field] = value
    (root / "manifest.json").write_bytes(manifest_bytes(manifest))
    with pytest.raises(DatasetError):
        load_manifest(root / "manifest.json")


def test_manifest_rejects_duplicate_and_mismatched_paths(mixed_dataset) -> None:
    root, manifest = mixed_dataset
    remote = manifest["records"]["REMOTE"]["document"]
    remote["path"] = manifest["records"]["ART_1"]["document"]["path"]
    (root / "manifest.json").write_bytes(manifest_bytes(manifest))
    with pytest.raises(DatasetError, match="Duplicate"):
        load_manifest(root / "manifest.json")
    remote["path"] = "files/Reports/wrong.pdf"
    (root / "manifest.json").write_bytes(manifest_bytes(manifest))
    with pytest.raises(DatasetError, match="file_paths"):
        build_manifest(root)


@pytest.mark.parametrize("case", ["match", "hash", "size", "not-found"])
def test_check_dataset_fetch_remote(mixed_dataset, tmp_path: Path, case: str) -> None:
    root, manifest = mixed_dataset
    served = tmp_path / "downloads"
    served.mkdir()
    (served / "paper.pdf").write_bytes(b"pdf")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(served))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        document = manifest["records"]["REMOTE"]["document"]
        filename = "missing.pdf" if case == "not-found" else "paper.pdf"
        document.update(
            url=f"http://127.0.0.1:{server.server_port}/{filename}",
            sha256="c35b21d6ca39aa7cc3b79a705d989f1a6e88b99ab43988d74048799e3db926a3",
            size=3,
        )
        if case == "hash":
            document["sha256"] = "a" * 64
        if case == "size":
            document["size"] = 4
        before = manifest_bytes(manifest)
        (root / "manifest.json").write_bytes(before)
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts/check_dataset.py"),
            "--root",
            str(root),
        ]
        offline = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert offline.returncode == 0, offline.stderr
        assert "Checking REMOTE" not in offline.stdout
        result = subprocess.run(
            [*command, "--fetch-remote"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if case == "match":
            assert result.returncode == 0, result.stderr
            assert "Verified REMOTE: size=3, sha256=" in result.stdout
        else:
            assert result.returncode != 0
            expected = (
                "remote download failed"
                if case == "not-found"
                else "remote document mismatch"
            )
            assert f"REMOTE: {expected}" in result.stderr
            assert "Validated" not in result.stdout
        assert (root / "manifest.json").read_bytes() == before
        assert not (root / document["path"]).exists()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
