# Funded Desk

An original creator desk with a large chart, presenter portrait, guest or replay seat, and status panel.
The composition uses the seat and status hierarchy of a televised poker table.

Funded Desk has light and dark variants with identical geometry.
The selected variant is saved as `template.json` in this copy.
Light uses the `Funded Desk` scene and `assets/background.png`.
Dark uses the `Funded Desk Dark` scene and `assets/background-dark.png`.
Both use the same transparent gold frame.
Only the selected background is copied.

The copy command defaults to light. Add `--theme dark` to select dark.
Your existing text and chart inputs keep their own colors.

This copy belongs to you. Edit `template.json`, the PNG files, or these prompts.
Future package updates do not change your copy.

The canvas is 1920 by 1080 pixels. Layers are ordered from bottom to top.
Borders sit inside their source rectangles.

## Source bindings

- `CHART`: Existing central chart or market-board input.
- `HOST`: Existing primary presenter input, shown in a square portrait area.
- `HOST_B`: Existing guest or replay input, shown in a 16:9 area.
- `STATUS`: Existing market context or status input.

The copy command writes your source names into the recipe.
It does not create capture inputs or connect to OBS.
Use distinct source names that already exist in OBS.
`STATUS` can display supplied P&L. Label its units and paper or live status within that source.
The template calculates no results.

| Binding | Position | Size |
| --- | --- | --- |
| `CHART` | 40, 180 | 1296 by 729 |
| `HOST` | 1416, 180 | 464 by 464 |
| `HOST_B` | 1416, 668 | 464 by 261 |
| `STATUS` | 40, 944 | 1296 by 96 |

Positions and sizes use canvas pixels. Inputs stretch to fill their rectangles.
Use matching source aspect ratios to avoid distortion.
Use a square input or source crop filter for the primary presenter portrait.

## Apply your copy

1. Open `template.json`.
2. Adjust the rectangles and colors.
3. Preview the recipe with `obs_preview_layout`.
4. Examine all source, artwork, and capability checks.
5. Apply the same recipe with `obs_apply_layout`.

Use `{ "recipe": YOUR_RECIPE }` for preview.
Use `{ "recipe": YOUR_RECIPE, "dry_run": false }` for an explicit apply.
If OBS has active outputs, `allow_live: true` is also required.
Read the project layout documentation for scene ownership rules.
If you move this directory, update its absolute paths in `assets`.

## Theme reference

[TradeFunded](https://tradefunded.com/) was visually reviewed on 2026-10-08.
Its observed palette uses warm ivory `#F5F1EB`, near-black `#111111`, and gold `#BD852E`, `#F0C970`, and `#C48E36`.
The observed type pairing uses Newsreader or Georgia with Inter or Arial.
Fonts are not bundled. Your existing inputs supply all text.

This template is an original interpretation, with no affiliation or endorsement.
No TradeFunded logo, slogan, website asset, or presenter likeness is copied.
The template supplies no market data, trading signals, probabilities, or performance claims.
