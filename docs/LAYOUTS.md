# Layout recipes

`obs_preview_layout` reads OBS and returns a plan. It makes no changes.
`obs_apply_layout` also returns a plan unless `dry_run` is false.
Both tools accept a `recipe` object with schema `obs.layout.v1`.

The recipe declares a scene name, the OBS base canvas, and ordered layers.
Layers run from bottom to top. Each layer has a unique `id`, `type`, and
`rect` with integer `x`, `y`, `width`, and `height` values. A rectangle must
fit inside the canvas. The planner never changes the OBS video settings.

| Type | Required value | Behavior |
| --- | --- | --- |
| `existing` | `source_name` | Add or reuse one item for an explicitly named input. |
| `image` | `asset` | Load the matching path from the recipe's `assets` map. |
| `color` | `color` | Create a solid rectangle from `#RRGGBBAA`. |

An optional `visible` boolean defaults to true. An optional `border` object
contains a pixel `width` and `#RRGGBBAA` `color`. Each border uses four color
items. Layers and border items together cannot exceed 32 items.
The planner supports at most 128 items in the target scene.

Image paths must be absolute local paths. PNG, JPEG, and WebP images must be
single frames, at most 10 MiB and 16,777,216 pixels. Each dimension must be
at most 8192 pixels. Image bytes are checked again during application.
The template copy command resolves the bundled artwork into local paths.

## Repeated application

The planner reuses matching generated inputs and existing scene items.
A second apply of an unchanged recipe requires zero changes.
It preserves unrelated items and their relative order, then places the
recipe's items above them. It does not delete inputs or select a scene.

Generated names use `Layout SCENE/LAYER`. A name already used for different
input settings is a collision. The planner refuses to overwrite it because
another scene can use that input. To change generated colors, native sizes,
or image paths, use a new scene name or a new layer ID. Remove obsolete
items manually after inspecting the new layout.

Geometry stretches an input into its rectangle and resets its crop,
rotation, and scale. Match the source aspect ratio to the rectangle.
Use a crop filter on the source when necessary.

## Execution and failure

The planner checks the complete recipe, assets, capabilities, source names,
canvas, profile, collection, and current item state before writing.
It checks state again before each change and verifies each OBS readback.
An active recording or stream requires `allow_live: true`.

Application is not atomic. If a command fails or state changes, completed
changes remain in OBS. The receipt records completed changes and uncertain
acknowledgements. There is no automatic retry or rollback. Inspect OBS before
repeating a failed application. Execution has a 30-second request budget.

A verified receipt confirms OBS state. It does not confirm rendered pixels.
Inspect the scene before selecting it for a real program output.
Layouts cannot start a stream or recording.
