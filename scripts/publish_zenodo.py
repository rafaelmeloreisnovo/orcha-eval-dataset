"""Publish the dataset to Zenodo as a new record or version."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import date
from pathlib import Path

import requests

from orcha_eval_dataset.archive import build_archive
from orcha_eval_dataset.manifest import check_manifest, manifest_sha256

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
REPO_URL = "https://github.com/zenodo/orcha-eval-dataset"
TITLE = "Orcha metadata extraction evaluation dataset"

CERN_ROR_ID = "01ggx4157"
CREATORS = (
    ("Julie", "Skoven Hinge", "0009-0002-7876-8602", CERN_ROR_ID),
    ("Dinah", "Chander Madan", None, CERN_ROR_ID),
    ("Katherine", "Bettis", "0009-0001-9971-6564", CERN_ROR_ID),
    ("Alex", "Ioannidis", "0000-0002-5082-6404", CERN_ROR_ID),
)


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fp:
        while chunk := fp.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def build_description(manifest: dict) -> str:
    categories = Counter(record["category"] for record in manifest["records"].values())
    inventory = ", ".join(
        f"{category}: {count}" for category, count in sorted(categories.items())
    )
    return (
        "<p>Evaluation dataset for bibliographic metadata extraction from "
        "scholarly PDFs and PNG posters. "
        "Each document is paired with manually curated JSON ground "
        "truth for title, description, creators, affiliations, ORCIDs, DOI, "
        "and publication date.</p>"
        f"<p>This version contains {len(manifest['records'])} records. "
        f"Category counts: {inventory}.</p>"
        "<p>The ZIP contains all ground-truth <code>metadata/</code>, hosted "
        "<code>files/</code> documents, and the complete <code>manifest.json</code>. "
        "Documents marked <code>source: remote</code> are omitted from the ZIP "
        "and downloaded from their original HTTP(S) URLs in the manifest. "
        "Both the archived and separately uploaded manifests list every record "
        "with pinned SHA-256 hashes and byte sizes for validation and "
        "incremental retrieval. The latest "
        f'dataset and tooling are maintained at <a href="{REPO_URL}">'
        f"{REPO_URL}</a>.</p>"
    )


def build_record(version: str, manifest: dict) -> dict:
    return {
        "access": {"record": "public", "files": "public"},
        "files": {"enabled": True},
        "metadata": {
            "resource_type": {"id": "dataset"},
            "title": TITLE,
            "publisher": "Zenodo",
            "publication_date": date.today().isoformat(),
            "version": version,
            "creators": [
                {
                    "person_or_org": {
                        "type": "personal",
                        "given_name": given,
                        "family_name": family,
                        "identifiers": [{"scheme": "orcid", "identifier": orcid}]
                        if orcid
                        else [],
                    },
                    "affiliations": [{"id": affiliation}],
                }
                for given, family, orcid, affiliation in CREATORS
            ],
            "description": build_description(manifest),
            "related_identifiers": [
                {
                    "identifier": REPO_URL,
                    "scheme": "url",
                    "relation_type": {"id": "isdocumentedby"},
                }
            ],
        },
    }


class Zenodo:
    def __init__(self, base_url: str, token: str):
        self.api = base_url.rstrip("/") + "/api"
        self.http = requests.Session()
        self.http.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.inveniordm.v1+json",
            }
        )

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = path if path.startswith("http") else self.api + path
        kwargs.setdefault("timeout", 120)
        response = self.http.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise SystemExit(
                f"{method} {url} failed with {response.status_code}:\n"
                f"{response.text[:2000]}"
            )
        return response


def unchanged(api: Zenodo, record: dict, manifest_path: Path) -> bool:
    entries = api.request("GET", f"/records/{record['id']}/files").json()["entries"]
    remote = {entry["key"]: entry["checksum"] for entry in entries}
    return remote.get("manifest.json") == f"md5:{md5(manifest_path)}"


def upload(api: Zenodo, draft_id: str, files: list[Path]) -> None:
    entries = api.request("GET", f"/records/{draft_id}/draft/files").json()["entries"]
    for entry in entries:
        api.request("DELETE", f"/records/{draft_id}/draft/files/{entry['key']}")

    api.request(
        "POST",
        f"/records/{draft_id}/draft/files",
        json=[{"key": path.name} for path in files],
    )
    for path in files:
        print(f"Uploading {path.name} ({path.stat().st_size:,} bytes)...")
        with path.open("rb") as fp:
            api.request(
                "PUT",
                f"/records/{draft_id}/draft/files/{path.name}/content",
                data=fp,
                headers={"Content-Type": "application/octet-stream"},
                timeout=3600,
            )
        api.request("POST", f"/records/{draft_id}/draft/files/{path.name}/commit")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=date.today().isoformat())
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sandbox", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.version):
        raise SystemExit("Version may contain only letters, digits, '.', '_', and '-'.")

    token_name = "ZENODO_SANDBOX_TOKEN" if args.sandbox else "ZENODO_TOKEN"
    token = os.environ.get(token_name)
    if not token:
        raise SystemExit(f"Set {token_name} to a Zenodo personal access token.")

    state_path = ROOT / (
        ".zenodo-record.sandbox.json" if args.sandbox else ".zenodo-record.json"
    )
    manifest = check_manifest(ROOT)
    manifest_hash = manifest_sha256(manifest)
    api = Zenodo(
        "https://sandbox.zenodo.org" if args.sandbox else "https://zenodo.org",
        token,
    )

    latest = None
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        latest = api.request("GET", f"/records/{state['concept_recid']}").json()
        if not args.force and unchanged(api, latest, ROOT / "manifest.json"):
            latest_url = latest["links"]["self_html"]
            print(
                f"Manifest matches the latest version ({latest_url}); nothing to "
                "publish."
            )
            return

    archive = build_archive(
        ROOT,
        manifest,
        DIST / f"orcha-eval-dataset-{args.version}.zip",
    )
    files = [ROOT / "manifest.json", archive]

    if latest:
        draft = api.request("POST", f"/records/{latest['id']}/versions").json()
        draft = api.request(
            "PUT",
            f"/records/{draft['id']}/draft",
            json=build_record(args.version, manifest),
        ).json()
    else:
        draft = api.request(
            "POST", "/records", json=build_record(args.version, manifest)
        ).json()

    upload(api, draft["id"], files)
    if args.draft:
        print(f"Draft ready for review: {draft['links']['self_html']}")
        if not state_path.exists():
            print(
                "This is the first version. Publish it in the browser, then create "
                f"{state_path.name} from the published record before running this "
                "script again."
            )
        return

    record = api.request("POST", f"/records/{draft['id']}/draft/actions/publish").json()
    state = {
        "archive": archive.name,
        "concept_doi": record["links"]["parent_doi"].removeprefix("https://doi.org/"),
        "concept_recid": record["parent"]["id"],
        "latest_doi": record["pids"]["doi"]["identifier"],
        "latest_recid": record["id"],
        "manifest_sha256": manifest_hash,
        "version": args.version,
    }
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(f"Published {record['links']['self_html']}")
    print(f"Wrote {state_path.name}; commit it with the new manifest.")


if __name__ == "__main__":
    main()
