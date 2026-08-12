# NovelAI image metadata

- Upstream: https://github.com/NovelAI/novelai-image-metadata
- Commit: `3d9c7b7659e37a6ac90dd6494dbc862edb91a032`
- Vendored file: `nai_meta.py`
- License: MIT, see `LICENSE`

The application adapter calls the upstream `stealth_pngcomp` reader only after
cheap embedded-metadata checks. The upstream file is kept separate so local
NAI/Comfy policy changes do not get mixed into third-party code.
