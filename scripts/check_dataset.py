from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from pathlib import Path

import requests

from orcha_eval_dataset.manifest import check_manifest, manifest_sha256


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate dataset files and manifest.")
    parser.add_argument("--root", type=Path, default=Path(__file__).parent.parent)
    parser.add_argument(
        "--fetch-remote",
        action="store_true",
        help="Download remote documents and verify pinned sizes and SHA-256 hashes.",
    )
    args = parser.parse_args()

    manifest = check_manifest(args.root)
    if args.fetch_remote:
        for record_id, record in manifest["records"].items():
            document = record["document"]
            if document["source"] != "remote":
                continue
            print(f"Checking {record_id}: {document['url']}", flush=True)
            digest = hashlib.sha256()
            size = 0
            try:
                with requests.get(
                    document["url"], stream=True, timeout=120
                ) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        digest.update(chunk)
                        size += len(chunk)
            except requests.RequestException as exc:
                raise SystemExit(f"{record_id}: remote download failed: {exc}") from exc
            if size != document["size"] or digest.hexdigest() != document["sha256"]:
                raise SystemExit(
                    f"{record_id}: remote document mismatch; "
                    f"expected size={document['size']}, sha256={document['sha256']}; "
                    f"got size={size}, sha256={digest.hexdigest()}"
                )
            print(f"Verified {record_id}: size={size}, sha256={digest.hexdigest()}")

    categories = Counter(record["category"] for record in manifest["records"].values())
    print(f"Validated {len(manifest['records'])} records")
    for category, count in sorted(categories.items()):
        print(f"  {category}: {count}")
    print(f"Manifest sha256: {manifest_sha256(manifest)}")


if __name__ == "__main__":
    main()
