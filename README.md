# Orcha evaluation dataset

This repository contains the fixed evaluation dataset for
[Orcha's](https://github.com/inveniosoftware/orcha)
bibliographic metadata extraction pipeline.
It pairs 181 scholarly documents with manually curated bibliographic metadata
in JSON, including titles, authors, affiliations, and publication details.

The dataset covers 179 PDFs and two PNG posters.
Of these, 160 documents are stored in Git LFS;
the other 21 are retrieved from publicly accessible URLs.
All metadata and the complete `manifest.json` are stored in regular Git files.

## Layout

```text
files/<category>/*.{pdf,png}
metadata/<record_id>.json
manifest.json
```

Each metadata file identifies one PDF or PNG through `file_paths`,
including documents retrieved remotely.
Paths are case-sensitive and start with `files/`.
The manifest records each file's path, byte size, and SHA-256 hash.

Generated extraction text, experiment results, and intake files are excluded.

## Document sources

Each manifest document has one of two sources:

- `dataset` (local): the document is stored under `files/` in Git LFS
  and included in the Zenodo ZIP.
- `remote`: the document is downloaded from the HTTP(S) `url` in its descriptor.
  Its bytes are not distributed in this repository or the Zenodo ZIP.
  The `path` specifies its destination when a consumer assembles the dataset.

Both sources pin the expected byte size and SHA-256 hash.
Remote URLs currently point to exact arXiv versions,
but the format also supports other HTTP(S) download URLs.
Consumers should verify downloaded bytes against the manifest
and report a mismatch rather than substitute a different version.

## Local setup

Install Git LFS before cloning or committing documents:

```bash
brew install git-lfs
git lfs install
uv sync
```

Rebuild and check the manifest after changing local documents or metadata:

```bash
uv run python3 scripts/build_manifest.py
uv run python3 scripts/check_dataset.py
git lfs fsck
```

Commit `manifest.json` with the dataset changes.
Rebuilding preserves existing remote document URLs, hashes, and sizes unchanged;
it recalculates metadata and local-document hashes from local files.
Neither rebuilding nor the default check downloads remote documents
or requires local copies of them.
Keep the existing manifest: it is the source of truth for remote descriptors,
which cannot be reconstructed from metadata alone.

To also download every remote document and check its size and SHA-256 hash:

```bash
uv run python3 scripts/check_dataset.py --fetch-remote
```

This check requires network access and fails on download errors or mismatched bytes.
It does not save downloaded documents or change the manifest.

## Zenodo publishing

The publishing script creates a new Zenodo record or a new version
with two downloadable files:

- `orcha-eval-dataset-<version>.zip`, a deterministic archive containing
  the complete `manifest.json`, all 181 metadata files,
  and the 160 local documents under `files/`.
  Remote document bytes are omitted.
- `manifest.json`, the same complete manifest available outside the ZIP
  so consumers can inspect every record without downloading the archive.

Test against Zenodo Sandbox first:

```bash
ZENODO_SANDBOX_TOKEN=... \
  uv run python3 scripts/publish_zenodo.py --sandbox --draft
```

Publish to Zenodo:

```bash
ZENODO_TOKEN=... uv run python3 scripts/publish_zenodo.py
```

The token needs `deposit:write` and `deposit:actions` scopes.
The first production publish writes `.zenodo-record.json`.
Commit that file so later runs create versions under the same concept DOI.
Unchanged manifests are skipped unless `--force` is passed.

> [!NOTE]
> Publishing is handled by this script, not the Zenodo GitHub integration.

## Retrieval model

The manifest describes the complete dataset;
the Zenodo ZIP supplies its metadata and local document bytes.
Consumers obtain remote documents from their manifest URLs.
See [Orcha's evaluation code](https://github.com/inveniosoftware/orcha/tree/main/app/evals)
for its synchronization, caching, and evaluation implementation.

## Redistribution rights

Documents are distributed unchanged and remain under their respective licenses.
Author credits, copyright notices, and license information supplied within each
document are retained.

The dataset contains third-party documents.
Their source licenses must permit redistribution before the production Zenodo
record and GitHub repository are made public.
A repository license does not replace the rights attached to each PDF.

For documents redistributed under Creative Commons attribution licenses,
retain the supplied attribution and notices, credit the creators,
link to the source and applicable license, and identify any modifications.
See the [CC BY 4.0 attribution requirements](https://creativecommons.org/licenses/by/4.0/)
and check the specific license and version attached to each document.
