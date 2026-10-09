from __future__ import annotations

import argparse
from pathlib import Path

from orcha_eval_dataset.manifest import (
    build_manifest,
    manifest_bytes,
    manifest_sha256,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild manifest.json.")
    parser.add_argument("--root", type=Path, default=Path(__file__).parent.parent)
    args = parser.parse_args()

    manifest = build_manifest(args.root)
    (args.root / "manifest.json").write_bytes(manifest_bytes(manifest))
    print(
        f"Wrote {len(manifest['records'])} records; sha256={manifest_sha256(manifest)}"
    )


if __name__ == "__main__":
    main()
