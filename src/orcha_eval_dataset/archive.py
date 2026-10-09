from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .manifest import manifest_bytes, manifest_paths


def _info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build_archive(root: Path, manifest: dict, destination: Path) -> Path:
    """Create a byte-stable ZIP containing the manifest and dataset files."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)

    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            archive.writestr(_info("manifest.json"), manifest_bytes(manifest))
            for relative in manifest_paths(manifest):
                with (root / relative).open("rb") as source:
                    with archive.open(_info(relative), "w", force_zip64=True) as target:
                        shutil.copyfileobj(source, target, length=1 << 20)
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    with zipfile.ZipFile(destination) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"Archive CRC check failed for {bad}")
    return destination
