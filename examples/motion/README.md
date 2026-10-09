# Original motion ornaments

Three silent, transparent Browser Source loops, drawn locally with SVG. Warm gold
and bronze sit against neutral charcoal objects. There is no green felt, external
font, image download, library, connection, market feed, probability or score.

| Page / `data-motion-loop` | Cycle | Treatment |
| --- | ---: | --- |
| `fibonacci.html` / `fibonacci` | 18 seconds | Adjacent Fibonacci squares construct a continuous quarter-circle spiral; mathematical ratio lines emerge and recede. |
| `market.html` / `market` | 24 seconds | Stylized candlestick objects drift at different depths. These are decoration, with no axes, numbers, prices or time labels. |
| `table.html` / `table` | 20 seconds | Charcoal cards assemble into a fan above etched table arcs and stacked metallic chips, then withdraw. |

Use a page as a local OBS Browser Source at 1920 × 1080. Other sizes preserve the
composition's aspect ratio. The canvas is transparent; add `?background=dark`
for a neutral preview. Add `&freeze=9` to inspect one deterministic instant.
`freeze` accepts finite seconds from 0 through 3600; empty, negative, nonnumeric
or larger values are ignored. The explicit freeze overrides reduced motion.

For a host page, load `motion.css` and `motion.js` from local files and set
`document.body.dataset.motionLoop` before the script runs. The script inserts one
`svg.motion-layer` as the first child of `#stage`. If `#stage` is absent it creates
a standalone stage. It does not replace or edit the host's text, snapshot or data.
An existing stage must establish a positioning context. Keep foreground content
above the ornament with the host's normal layer order.

Set `document.body.dataset.motionTreatment = 'ambient'` for background use.
Opacity drops to 22%. Ornament stays within y=244..1015, leaving header and footer
clear. A soft mask protects the left and center, with a fully excluded copy core
x=0..1500, y=244..860. Fibonacci and table motifs become smaller constructions at
the right perimeter. The mask affects only the decoration, never foreground text.
This protects the intended copy area, not arbitrary foreground layouts: inspect
actual text and portraits before using the effect on a show.
The standalone pages intentionally showcase the full composition.

`#stage.replaceChildren(...)` is supported: one child-list observer reattaches the
same SVG node without creating another animation loop or resetting its clock.
There are no iframes, inline scripts, HTML-string injection or runtime requests.
Existing `script-src 'self'` and `style-src 'self'` policies are sufficient.

Motion uses deterministic elapsed-time geometry, painted at up to 30 fps. Cycles
join at transparent construction endpoints, or move objects through invisible
edge intervals; no visible element snaps back. A hidden page cancels its animation
request. Resuming uses the elapsed clock. Reduced-motion preference, including a
preference change while running, stops animation at a complete midcycle drawing.
The observer remains available for host rebuilds. A frozen frame never starts a
requestAnimationFrame chain. `ObsMotion.mount(document, window)` returns a
controller with `render(seconds)` and `stop()`; stop disconnects observation and
listeners. Repeated mount calls do not duplicate a layer.

All artwork and motion code are original project work under the repository's MIT
license. Geometry and source tests establish deterministic contracts and bounded
assets. They do not establish actual OBS rendering, moving readability, visual
preference or compositor performance. Watch at least one full cycle in the
intended show and check a frozen frame before claiming that acceptance.
