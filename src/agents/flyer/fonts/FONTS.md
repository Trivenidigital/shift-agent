# Vendored Fonts — Fix C Premium Renderer

All fonts are licensed under the **SIL Open Font License 1.1 (OFL 1.1)**.

## Files and Sources

| File | Source URL | License | Notes |
|------|-----------|---------|-------|
| `PlayfairDisplay-Bold.ttf` | https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf | SIL OFL 1.1 | **Substitution**: static Bold/Black instances unavailable; vendored the variable-weight TTF (`wght` axis 400–900). Pillow opens it at any size correctly. Serves both the `masthead` (Bold) role. |
| `PlayfairDisplay-Black.ttf` | https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf | SIL OFL 1.1 | **Substitution**: same variable TTF file as above (identical bytes). Serves `title` and `offer_price` (Black weight) roles. Static Black instance was not available in the repository. |
| `CormorantGaramond-SemiBold.ttf` | https://github.com/google/fonts/raw/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf | SIL OFL 1.1 | **Substitution**: static SemiBold instance unavailable; vendored the variable-weight TTF (`wght` axis 300–700). Serves `menu` role. |
| `Montserrat-SemiBold.ttf` | https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf | SIL OFL 1.1 | **Substitution**: static instances unavailable; vendored the variable-weight TTF (`wght` axis 100–900). Serves `footer` role. |
| `Montserrat-Bold.ttf` | https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf | SIL OFL 1.1 | **Substitution**: same variable TTF as above (identical bytes). Serves `kicker` role. |
| `Montserrat-ExtraBold.ttf` | https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf | SIL OFL 1.1 | **Substitution**: same variable TTF as above (identical bytes). Vendored for future use in high-contrast headings. |
| `Pacifico-Regular.ttf` | https://github.com/google/fonts/raw/main/ofl/pacifico/Pacifico-Regular.ttf | SIL OFL 1.1 | Brush-script hand-lettered display face (Workstream B). Serves the `script` role (`_premium_font`) and the `festive-vernacular` register headline (`premium_poster_v1._headline_font`). **STATIC single-weight** font — no `wght` axis, so it is intentionally NOT in `_ROLE_WEIGHT`. Full licence text vendored alongside as `Pacifico-OFL.txt`. sha256 `5b6c0d5334a7bf77dea52b975c5a0c408878c0f7115ed5b6fb151f634b7bf701`. |

## License Notes

The SIL OFL 1.1 permits bundling with software, modification, and redistribution provided:
- The Original Font Name is not used in derivative works sold standalone.
- The license and copyright notices are preserved.
- Derivative fonts are not sold by themselves.

The full license text is available at: https://scripts.sil.org/OFL

## Why Variable Fonts

Google Fonts removed static instances from these families in their repository; only variable-font TTFs (`[wght]` range) are published. Pillow ≥ 9.0 opens variable TTFs; the default instance is ~400 (Regular), which would render every role at the same light weight.

`premium_overlay._premium_font` therefore applies the per-role target weight to the `wght` axis via `ImageFont.set_variation_by_axes([weight])` after loading. This is why a single Playfair file serves both `masthead` (700/Bold) and `title`/`offer_price` (900/Black) with visibly distinct, genuinely heavy weights — verified by `test_variable_font_weight_axis_differentiates` (Black glyphs are measurably wider than Bold at the same pixel size). The call is wrapped in try/except so static fonts or older Pillow builds degrade to the default weight without error.

Available named instances per file (for reference): Regular, Medium, SemiBold, Bold, ExtraBold, Black. Role→weight mapping: masthead=700, kicker=700, title=900, offer_price=900, menu=600, footer=600. The `script` role (`Pacifico-Regular.ttf`) is a static single-weight brush-script face and carries no `wght` axis, so it takes no `_ROLE_WEIGHT` entry.
