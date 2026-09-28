# Example Catalog

TODO(setup): replace this file. It is the front door to the published catalog,
the page people land on at the public base URL. It is not the same document as
the README in the repository root, which is never published.

## What is here

TODO(setup): the datasets, in one paragraph. Say what each collection covers,
the area and the time range, and what a reader can do with it.

## License

TODO(setup): the SPDX identifier, or `other` with a link to the terms. Say who
holds the rights and what a reuser has to attribute.

## Provenance

TODO(setup): where the data came from. Say whether this catalog is the official
publication from the producing organization, or a mirror of someone else's data.
If it is a mirror, link the upstream and say how often it syncs.

## Access

TODO(setup): one query someone can run without downloading anything, against a
real published file. Run it before you paste it.

```sql
-- Example shape. Replace the URL and the columns with your own.
INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;
SELECT count(*) FROM 'https://example.invalid/prefix/collection/data.parquet';
```
