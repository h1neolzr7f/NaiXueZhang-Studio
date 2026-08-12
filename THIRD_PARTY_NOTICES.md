# Third-party notices

## NovelAI image metadata

`third_party/novelai_image_metadata/` vendors code from
`NovelAI/novelai-image-metadata` at commit
`3d9c7b7659e37a6ac90dd6494dbc862edb91a032`.

Copyright © 2023 NovelAI. Licensed under the MIT License; the complete license
is retained at `third_party/novelai_image_metadata/LICENSE`.

Pixiv and NovelAI names are used only to describe interoperability. This
project is not affiliated with or endorsed by Pixiv Inc. or NovelAI.

## Bundled starter tag indexes

The JSON files declared by `data/seed_manifest.json` are deterministic starter
indexes assembled from the source snapshots named inside those files, including
the deepghs Danbooru tag mirror and the nichind Danbooru-Tags mirror. They contain
tag names and aggregate counts, not downloaded artwork. Names, character names,
and trademarks remain subject to their respective owners' rights and are not
relicensed by the project's MIT License. The release manifest records the exact
SHA-256 and byte size of every shipped snapshot.

## CPython portable runtime

The optional Windows one-click release bundles a 64-bit CPython runtime and
the pinned packages listed in `requirements.lock.txt`. The exact Python
version is recorded in `runtime/runtime_manifest.json`, and the Python license
is retained at `runtime/LICENSE.txt`. The lightweight source/Core release does
not contain this runtime.

## Release profiles

The public `core` release includes the metadata component above and excludes
the optional Live2D/Hiyori assets. The `full` profile preserves the existing
complete bundle; assets carrying separate license files remain governed by
those files and are not relicensed under the project's MIT License.
