# Portolan Catalog Template

A starting point for a [Portolan](https://www.portolan-sdi.org/) catalog whose
metadata lives in git. Click **Use this template**, work through
[SETUP.md](SETUP.md), and you have a repository whose CI validates every change
before it publishes.

**`catalog/` is the published catalog.** Everything in it is published.
Everything outside it never is. That boundary is the whole publish contract,
and `tools/publish.py` has no flag or config key that widens it.

## Three kinds of file

| Kind | Where | Example |
|---|---|---|
| Tracked and published | inside `catalog/` | STAC JSON, `README.md`, `AGENTS.md`, thumbnails, logos |
| Tracked, never published | outside `catalog/` | `tools/`, `tests/`, `docs/`, this README, `catalog.publish.yaml` |
| Neither | gitignored | GeoParquet, COGs, PMTiles, credentials |

The data lives in object storage next to the published metadata. The
repository references it by URL and never stores it.

## Layout

| Path | What it is |
|---|---|
| `catalog/` | The published tree, synced 1:1 to object storage |
| `catalog.publish.yaml` | Where it publishes, and under what public URL |
| `tools/publish.py` | The sync. Dry run by default |
| `tools/upload_data.py` | The data upload. Dry run by default |
| `tests/` | The gates CI runs on every pull request |
| `docs/conformance.md` | Any validator finding this catalog accepts, and why |
| `SETUP.md` | The checklist. Delete it when you are done |

## Publish

```bash
python3 tools/publish.py            # dry run: what would change
python3 tools/publish.py --confirm  # upload; needs AWS credentials
```

It never deletes. Removing a file from `catalog/` does not unpublish it, so
delete the object yourself if that is what you meant.

## Upload the data

The data is too large for git, so it lives outside `catalog/`.
`tools/upload_data.py` carries it to the same bucket prefix. Set `data_dir` in
`catalog.publish.yaml` to the directory that holds it.

```bash
python3 tools/upload_data.py            # dry run: what would change
python3 tools/upload_data.py --confirm  # upload; needs AWS credentials
```

Both scripts share one set of rules. `upload_data.py` imports the sentinel
guard, the content types, the change detection, and the upload pool from
`publish.py`. It changes one thing, the directory it walks. Only the suffixes
in its allow-list upload, so staged scratch files stay out of the bucket.

## Test

```bash
python3 tests/run_all.py
```

| Gate | What it checks |
|---|---|
| `test_setup.py` | Template placeholders are all edited, or all untouched |
| `test_links.py` | Every relative link and asset href resolves |
| `test_publish.py` | Nothing outside `catalog/` can be uploaded |
| `test_upload_data.py` | Only staged files with an allowed suffix upload |
| `test_stac_valid.py` | Valid STAC 1.1.0, via `stac-check` |
| `test_conformance.py` | Portolan conformance, via `rashid` |

The two validator gates skip when their tools are absent, so a clean checkout
runs with no setup. CI installs both and enforces them.

## What this template does not decide

How a published catalog points back at the repository that maintains it. Three
encodings are in use across real catalogs and none is standardized, so this
template ships none of them rather than freezing one in by default. The
tradeoffs are in
[portolan-spec#145](https://github.com/portolan-sdi/portolan-spec/issues/145)
and in the
[git-backed catalogs guidance](https://github.com/portolan-sdi/portolan-spec/blob/main/specs/best-practices/git-backed-catalogs.md).

## License

Apache-2.0, covering the tooling in this repository. The data you catalog
carries its own license, which belongs in `catalog/README.md`.
