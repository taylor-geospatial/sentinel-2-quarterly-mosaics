# AGENTS.md — Example Catalog

Guidance for AI agents and automated clients working with this catalog.

**One rule survives every edit to this file.** Every claim here is either quoted
from a source or measured from the data. If you cannot point at where a fact
came from, it does not belong in this file. An agent acting on an invented join
key or an invented column name produces a confident wrong answer, and nothing
downstream catches it.

## What this catalog holds

TODO(setup): the collections, and what each one covers. Name the public root
URL so a client that arrived here by another route can orient itself.

## How to read it

TODO(setup): one worked query per format you publish, each one run against the
published files before it was written down.

## Join keys

TODO(setup): the columns that join collections to each other, and their types.
State which side is unique. If no two collections join, say that instead.

## Quirks that produce silently wrong answers

TODO(setup): the traps. A projection that is not WGS84, so lengths come out in
feet. A column whose name differs from the one the source documentation uses. A
category field with inconsistent casing. A geometry column that GDAL names
`geometry_bbox` where the query you copied says `bbox`. These are the entries
that earn this file, so write them as you find them.

## Structure

Assets and structural links resolve relative to the object that carries them.
Catalogs here carry no `self` link, so a client tracks its own location.
