# Pack Break Closeup

The hit reveal scene. The close-up camera fills the whole frame.
A small table inset sits at the top right, and a hit name card sits at the lower left on a dark plate.
The canvas is 1920 by 1080 pixels and must match your OBS base canvas.
The scene is named `Pack Break Closeup`.

## Source bindings

- `CLOSEUP_CAM`: close-up camera aimed at the card, 16:9.
- `TABLE_CAM`: wide table camera, shown small. Use the same input as the table scene.
- `HIT_CARD`: text input for the card name.

| Binding | Position | Size |
| --- | --- | --- |
| `CLOSEUP_CAM` | 0, 0 | 1920 by 1080 |
| `TABLE_CAM` | 1456, 40 | 424 by 238 |
| `HIT_CARD` | 64, 884 | 1072 by 140 |

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
