# Copyable templates

Templates are local files that you own after copying.
Edit the recipe, artwork, and prompts without changing the package.
Package updates do not overwrite your copy.

The initial catalog contains one template: **Funded Desk**, with light and dark themes.
Its 1920 by 1080 canvas has a large chart, square presenter portrait, guest or replay seat, and status panel.
Light uses warm ivory and near-black with restrained gold accents.
Dark uses a near-black architectural background with the same gold frame and geometry.

The chart occupies 1296 by 729 pixels.
The main presenter occupies a 464-pixel square beside it.
A 464 by 261 guest or replay area sits below the presenter.
The status strip sits below the chart.
Inputs stretch to fill their rectangles. Use matching source aspect ratios to avoid distortion.

## Copy a template

List the catalog:

```sh
obs-director-template list
```

Create the parent directory first, if needed.
Select four distinct inputs that already exist in your OBS collection.
Substitute their exact names in this command:

```sh
obs-director-template add funded-desk --output ./my-funded-desk --bind "CHART=My Chart" --bind "HOST=My Camera" --bind "HOST_B=Guest Camera" --bind "STATUS=Session Status"
```

Light is the default. To copy the dark variant, add `--theme dark` and select a new output directory:

```sh
obs-director-template add funded-desk --theme dark --output ./my-funded-desk-dark --bind "CHART=My Chart" --bind "HOST=My Camera" --bind "HOST_B=Guest Camera" --bind "STATUS=Session Status"
```

The dark recipe targets `Funded Desk Dark`; the light recipe targets `Funded Desk`.
Separate scene names prevent their generated artwork inputs from colliding.
Both variants use your existing content inputs. Their text and chart colors remain under your control.
`STATUS` can display supplied P&L. Label its units and paper or live status within that source.
The template calculates no results.

The output directory must not exist.
The command refuses symbolic links, directory traversal, invalid images, and invalid geometry.
All files are validated before the first output write.
A failed copy removes only files and directories created by that invocation.

The copy contains:

- `template.json`: your recipe with explicit source names and absolute artwork paths.
- `assets/background.png` or `assets/background-dark.png`: the selected opaque background.
- `assets/frame.png`: the transparent edge decoration.
- `README.md`: composition notes and source bindings.
- `prompts.md`: original artwork prompts for manual adaptation.

The bundled recipe deliberately has empty source names.
It is invalid until the copy command supplies your explicit bindings.
The command does not connect to OBS, create capture sources, or call an image service.
It cannot confirm that your named inputs currently exist.

## Edit and preview

1. Open your copied `template.json`.
2. Adjust the rectangles and colors.
3. Keep every rectangle inside the declared canvas.
4. Preview the complete recipe with `obs_preview_layout`.

The preview tool accepts `{ "recipe": YOUR_RECIPE }`.
Preview checks current OBS sources, image files, canvas dimensions, and required capabilities.
Review its plan before an explicit apply.

The apply tool accepts `{ "recipe": YOUR_RECIPE, "dry_run": false }`.
Its default remains a dry run.
If outputs are active, also supply `allow_live: true` for an authorized layout change.
Read [Layout recipes](LAYOUTS.md) for collision and repeated-application rules.

No template starts a stream or recording.
All text, charts, and presenter content come from your named inputs.
No market data, trading signals, probabilities, or performance claims are generated.

If you move the copied directory, update its absolute artwork paths.
Use the copied prompts only when you want to generate replacement artwork manually.

## Theme provenance

The [TradeFunded site](https://tradefunded.com/) was visually reviewed on 2026-10-08.
The observed palette uses ivory `#F5F1EB`, near-black `#111111`, and gold `#BD852E`, `#F0C970`, and `#C48E36`.
The observed type pairing uses Newsreader or Georgia with Inter or Arial.
Fonts are not bundled; source inputs control typography.

Funded Desk is an original interpretation of this palette and a televised poker table composition.
It has no affiliation with or endorsement from TradeFunded.
No TradeFunded logo, slogan, website asset, or presenter likeness is copied.

## Python API

```python
from obs_director.templates import add_template, get_template, list_templates

catalog = list_templates()
metadata = get_template("funded-desk")
dark_metadata = get_template("funded-desk", theme="dark")
receipt = add_template("funded-desk", "my-funded-desk", {
    "CHART": "My Chart",
    "HOST": "My Camera",
    "HOST_B": "Guest Camera",
    "STATUS": "Session Status",
}, theme="light")
```

Catalog metadata uses portable identifiers and public reference links.
`get_template` also returns an unbound recipe with relative artwork references.
`get_template` and `add_template` default to `theme="light"`; both also accept `theme="dark"`.
Invalid themes are refused before output writes. The catalog lists both themes under one template.
Copy and bind it before applying; the unbound recipe is deliberately invalid for OBS.
Resource loading supports installed package resources, including ZIP-backed resources.
Each asset is limited to 8 MiB; each template copy is limited to 32 MiB.
Copied artwork must be a single PNG frame, with at most 16,777,216 pixels.
Its width and height cannot exceed 8192 pixels.
