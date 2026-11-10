# Pack Break Vertical

A portrait version of the pack-opening show for vertical platforms.
From top to bottom: title strip, table camera, close-up camera, then the face camera
beside the now-opening and hit cards.
The canvas is 1080 by 1920 pixels. Your OBS base canvas must be 1080 by 1920 for this scene,
so use a separate OBS profile or scene collection from the 1920 by 1080 layouts.
The bottom 280 pixels stay empty because phone apps cover them with their own controls.
The scene is named `Pack Break Vertical`.

## Source bindings

- `TABLE_CAM`: wide table camera, 16:9.
- `CLOSEUP_CAM`: close-up camera, 16:9.
- `FACE_CAM`: host camera, 16:9.
- `TITLE`: text input for the title strip.
- `NOW_OPENING`: text input for the now-opening card.
- `HIT_CARD`: text input for the hit name card.

| Binding | Position | Size |
| --- | --- | --- |
| `TITLE` | 40, 84 | 1000 by 80 |
| `TABLE_CAM` | 40, 216 | 1000 by 562 |
| `CLOSEUP_CAM` | 40, 802 | 1000 by 562 |
| `FACE_CAM` | 40, 1388 | 448 by 252 |
| `NOW_OPENING` | 528, 1400 | 496 by 96 |
| `HIT_CARD` | 528, 1532 | 496 by 96 |

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
