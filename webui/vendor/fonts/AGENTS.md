# WebUI Font Vendor DOX

## Purpose

- Own the locally served Rubik and Roboto Mono WebUI font bundle.
- Keep core typography available without internet access.

## Ownership

- `fonts.css` owns the local `@font-face` declarations and supported variable weight ranges.
- `*.ttf` files are clean upstream font artifacts.
- `*.woff2` files are derived artifacts converted from the paired TTFs with `fontTools` (flavor `woff2`); they are not upstream downloads.
- `*-OFL.txt` files own the corresponding upstream licenses.
- `README.md` records upstream provenance and artifact mappings.

## Local Contracts

- Font sources in `fonts.css` must remain same-origin relative URLs.
- Preserve normal and italic Rubik weights 300–900 and Roboto Mono weights 100–700.
- Keep each font artifact paired with its upstream license and pinned provenance.
- `@font-face` sources must list the WOFF2 file first and the TTF second so modern browsers download only the smaller artifact while the TTF stays as a fallback.
- Regenerate `.woff2` from the matching `.ttf` whenever a font artifact is replaced; never hand-edit the binary.

## Work Guidance

- TTF/OTF fallbacks are gzip-compressed by the server (`helpers/ui_server._COMPRESSIBLE_CONTENT_TYPES`); WOFF2 is served as-is because it is already compressed.
- Replace fonts from a pinned official upstream revision rather than modifying binaries.
- Update `fonts.css`, licenses, and provenance together when changing the bundle.

## Verification

- Run `pytest tests/test_webui_offline_assets.py`.
- Smoke-test the main and login pages without external network access.

## Child DOX Index

No child DOX files.
