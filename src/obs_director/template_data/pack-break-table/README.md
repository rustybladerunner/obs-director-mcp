# Pack Break Table

The main scene of a trading-card pack-opening show. The wide table camera is the hero.
A small face camera sits at the top right. A title strip runs along the top.
A now-opening card sits along the bottom for the pack being opened.
The canvas is 1920 by 1080 pixels and must match your OBS base canvas.
The scene is named `Pack Break Table`.

## Source bindings

- `TABLE_CAM`: wide overhead camera that shows the table, 16:9.
- `FACE_CAM`: host camera, 16:9.
- `TITLE`: text input for the title strip.
- `NOW_OPENING`: text input for the now-opening card.

| Binding | Position | Size |
| --- | --- | --- |
| `TITLE` | 40, 20 | 1840 by 64 |
| `TABLE_CAM` | 40, 132 | 1296 by 729 |
| `FACE_CAM` | 1416, 132 | 464 by 261 |
| `NOW_OPENING` | 40, 885 | 1840 by 150 |

The empty panel under the face camera is a flat color. Put your own overlay there.

The copy command writes your source names into the recipe.
It does not create capture inputs or connect to OBS.
Use distinct source names that already exist in OBS.
Text inputs stretch to their rectangles, so size the text inside the source.
Inputs stretch to fill their rectangles. Use matching source aspect ratios to avoid distortion.

This copy belongs to you. Edit `template.json` or this file.
Future package updates do not change your copy.
The layout uses only flat colors, so it ships no artwork.
It has one dark theme. Layers are ordered from bottom to top.
Borders sit inside their source rectangles.

## Apply your copy

1. Open `template.json` and adjust rectangles and colors.
2. Preview the recipe with `obs_preview_layout`.
3. Apply it with `obs_apply_layout` and `dry_run=false`.
   If OBS has an active output, `allow_live: true` is also required.

Applying a layout never selects the program scene and never starts an output.
