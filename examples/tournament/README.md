# Funded Desk tournament scenes

These original pages combine tournament presentation with a bounded trading competition snapshot.
They use local system fonts and the cream/charcoal/gold Funded Desk palette.
The two example participants are synthetic PIPHOUND and HOUSEBOT.

From the repository root, generate a new bundle:

```console
python tools/render_tournament.py --demo --template piphound --output runtime/tournament-demo
```

The `runtime` parent must exist. The command refuses an existing output directory.
It copies the original PipHound artwork from `../funded-desk/presenter.png` into the generated bundle.
No new image generation, provider connection, server, or OBS process starts.

Open the generated `table.html`, `standings.html`, `replay.html`, `starting-soon.html`, `break.html`, or `ending.html`.
These pages fit a 1920x1080 composition inside the available viewport without cropping.
At 1280x720, the same layout uses a two-thirds scale.

The source HTML files contain a data marker and require the renderer before use.
The renderer copies editable HTML, CSS, JavaScript, snapshot JSON, and a scene manifest.
Editing `snapshot.json` after rendering does not change the embedded page data. Generate a new bundle to update the snapshot.

`synthetic-snapshot.json` is a fixed schema example. Its old timestamp intentionally becomes stale.
The explicit `--demo` option creates synthetic values with their actual creation time.
The browser marks values unranked after 120 seconds. It does not invent new observations.

See [the snapshot contract](../../docs/TOURNAMENT.md) and [the inspected tournament references](../../docs/TOURNAMENT-REFERENCES.md).
See [the artwork provenance](../../docs/DEMO-ART.md) for the original presenter image.
