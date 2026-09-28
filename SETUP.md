# Setup

Work through this list, then delete this file. `tests/test_setup.py` fails
while the repository is half-edited, so run the gates as you go.

Every placeholder in this repository is marked `TODO(setup)`. To find what is
left:

```bash
grep -rn "TODO(setup)" . --exclude-dir=.git --exclude=SETUP.md
```

## 1. Point it at your storage

Edit `catalog.publish.yaml`. Replace all three sentinel values.

- `write_prefix` — the `s3://bucket/prefix` uploads go to. Copy it from your
  storage provider rather than assembling it by hand.
- `public_base` — the public URL that serves `write_prefix`. Every href in the
  catalog is built from this, so it is what ends up in the STAC JSON.
- `region` — your bucket's region.

`tools/publish.py` refuses to upload while any sentinel survives, and it checks
before making any AWS call, so you can test this without credentials.

## 2. Name the catalog

Edit `catalog/catalog.json`. Replace `id`, `title`, and the `description`.

The `id` becomes part of URLs and other people's scripts, so treat it as a
contract: short, lowercase, no tooling artifacts. The `title` does the
describing.

## 3. Write the published documentation

`catalog/README.md` is the page people land on at your public base URL.
`catalog/AGENTS.md` is what an agent reads before querying. Both publish. Both
ship as skeletons with `TODO(setup)` bodies.

Keep the rule at the top of `catalog/AGENTS.md`: every claim in it is quoted
from a source or measured from the data.

## 4. Rewrite the repository README

The root `README.md` is the GitHub front door and is never published. It
currently describes the template. Replace it with a description of your
catalog.

## 5. Decide official or mirror

If you produce the data, your catalog is official. If you are republishing
someone else's, it is a mirror, and each collection needs a `rel:via` link to
the original source. Portolan derives this from your `providers`, so get the
`producer` and `host` roles right.

## 6. Add a logo, optionally

Put the file in `catalog/_assets/`, then add a link to `catalog/catalog.json`:

```json
{
  "rel": "icon",
  "href": "./_assets/logo.svg",
  "type": "image/svg+xml",
  "title": "Your Organization"
}
```

The `type` has to be a displayable image media type, and the href stays
relative.

## 7. Point the catalog back at this repository

`catalog/catalog.json` ships a `vcs` link and an `issues` link. Each one holds
a `TODO(setup)` href. Replace both. The `vcs` link names your repository. The
`issues` link names its issue tracker.

Use an absolute URL in both hrefs. The repository sits outside the published
catalog. A relative href resolves against your `public_base`, so it points at
a bucket path that holds nothing.

The Portolan spec recommends these two links for a git-backed catalog. See
[git-backed catalogs](https://github.com/portolan-sdi/portolan-spec/blob/main/specs/best-practices/git-backed-catalogs.md).

## 8. Add your first collection

A catalog with no collections is valid, which is why this repository is green
before you start. Add collections under `catalog/`, each with a `child` link
from `catalog/catalog.json`, and run the gates after each one.

## 9. Finish

```bash
grep -rn "TODO(setup)" . --exclude-dir=.git --exclude=SETUP.md
# the line above should print nothing
python3 tests/run_all.py
python3 tools/publish.py                       # dry run
```

Then delete `tests/test_setup.py`, remove it from the `TESTS` list in
`tests/run_all.py`, and delete this file.

## 10. If you are outside the portolan-sdi organization

Delete `.github/workflows/repo-checks.yml`, and delete the `ops-sync` block
from `AGENTS.md` and `CLAUDE.md`. Those enforce the portolan-sdi contribution
contract, which is governance for that organization and not for your catalog.
Keep everything below the `ops-sync:end` marker in `AGENTS.md`.
