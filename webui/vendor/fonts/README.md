# Vendored WebUI fonts

Rubik and Roboto Mono are vendored from the official
[`google/fonts`](https://github.com/google/fonts) repository at commit
`7ff85c87f93ea6cca5f41c69f2e4edcb90240f26`.

| Local artifact | Upstream artifact | License |
| --- | --- | --- |
| `rubik-variable.ttf` | `ofl/rubik/Rubik[wght].ttf` | `rubik-OFL.txt` |
| `rubik-italic-variable.ttf` | `ofl/rubik/Rubik-Italic[wght].ttf` | `rubik-OFL.txt` |
| `roboto-mono-variable.ttf` | `ofl/robotomono/RobotoMono[wght].ttf` | `roboto-mono-OFL.txt` |
| `roboto-mono-italic-variable.ttf` | `ofl/robotomono/RobotoMono-Italic[wght].ttf` | `roboto-mono-OFL.txt` |

Each TTF has a derived `*.woff2` sibling (same basename) produced with
`fontTools` 4.67.0:

```bash
python - <<'PY'
from fontTools.ttLib import TTFont
f = TTFont("rubik-variable.ttf"); f.flavor = "woff2"; f.save("rubik-variable.woff2")
PY
```

`fonts.css` lists the WOFF2 first with the TTF as fallback; the TTF is only
downloaded by browsers that cannot decode WOFF2.

`fonts.css` exposes the upstream variable weight ranges through local
`@font-face` declarations. The WebUI must not fall back to a remote font host.
