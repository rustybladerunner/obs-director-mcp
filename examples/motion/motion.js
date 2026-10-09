/* Original decorative geometry. No market feed, prediction, score, or clock. */
(function (root) {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const TAU = Math.PI * 2;
  const DURATIONS = Object.freeze({ fibonacci: 18, market: 24, table: 20 });
  const GOLD = "#d4ad65", PALE = "#d7b777", SMOKE = "#171719";
  const clamp = (x) => Math.max(0, Math.min(1, x));
  const ease = (a, b, x) => { const q = clamp((x - a) / (b - a)); return q * q * (3 - 2 * q); };
  const pulse = (p, a, b, c, d) => ease(a, b, p) * (1 - ease(c, d, p));
  const phase = (seconds, duration) => ((seconds % duration) + duration) % duration / duration;
  const round = (x) => Math.round(x * 10000) / 10000;

  function options(search) {
    const query = new URLSearchParams(search);
    const raw = query.get("freeze");
    const value = raw !== null && raw.trim() !== "" ? Number(raw) : NaN;
    return { dark: query.get("background") === "dark",
      freeze: Number.isFinite(value) && value >= 0 && value <= 3600 ? value : null };
  }

  function fibonacciSquares() {
    // Adjacent Fibonacci squares and contiguous quarter-circle arcs, not a fitted spiral.
    const data = [[0, 0, 1], [1, 0, 1], [0, 1, 2], [-3, 0, 3],
      [-3, -5, 5], [2, -5, 8], [-3, 3, 13], [-24, -5, 21]];
    const ends = [[1, 0], [2, 1], [0, 3], [-3, 0], [2, -5], [10, 3], [-3, 16], [-24, -5]];
    const unit = 34;
    let start = [0, 1];
    return data.map(([x, y, size], i) => {
      const point = (p) => [round(382 + (p[0] + 24) * unit), round(183 + (p[1] + 5) * unit)];
      const a = point(start), b = point(ends[i]); start = ends[i];
      return { x: point([x, y])[0], y: point([x, y])[1], size: size * unit,
        arc: `M ${a[0]} ${a[1]} A ${size * unit} ${size * unit} 0 0 1 ${b[0]} ${b[1]}` };
    });
  }

  function frame(preset, seconds) {
    if (!Object.hasOwn(DURATIONS, preset) || !Number.isFinite(seconds)) throw new TypeError("Known loop and finite time required");
    const p = phase(seconds, DURATIONS[preset]);
    if (preset === "fibonacci") return {
      phase: p, opacity: pulse(p, 0, 0.09, 0.88, 1),
      x: round(5 * Math.sin(TAU * p)), y: round(4 * Math.cos(TAU * p)),
      squares: Array.from({ length: 8 }, (_, i) => ({
        line: pulse(p, 0.035 + i * 0.043, 0.17 + i * 0.043, 0.75 + i * 0.014, 0.9 + i * 0.012),
        arc: pulse(p, 0.19 + i * 0.04, 0.32 + i * 0.04, 0.80 + i * 0.012, 0.92 + i * 0.009)
      })),
      ratios: [0.236, 0.382, 0.5, 0.618, 0.786].map((ratio, i) => ({ ratio,
        draw: pulse(p, 0.32 + i * 0.025, 0.48 + i * 0.025, 0.78, 0.96) }))
    };
    if (preset === "market") return {
      phase: p,
      candles: Array.from({ length: 15 }, (_, i) => {
        const q = (p + i / 15) % 1;
        return { x: round(-180 + 2280 * q),
          y: round(260 + (i % 3) * 268 + 66 * Math.sin(TAU * q + i * 1.7)),
          angle: round(6 * Math.sin(TAU * q + i)), width: 28 + (i % 4) * 9,
          height: round(95 + (i % 5) * 29 + 16 * Math.sin(TAU * q)),
          opacity: pulse(q, 0, 0.09, 0.91, 1) * (0.48 + (i % 3) * 0.18) };
      })
    };
    return {
      phase: p, opacity: pulse(p, 0, 0.12, 0.87, 1),
      orbit: pulse(p, 0.04, 0.35, 0.76, 0.97),
      cards: [-1, 0, 1].map((side, i) => {
        const arrival = ease(0.10 + i * 0.045, 0.32 + i * 0.045, p);
        const departure = ease(0.73 + i * 0.035, 0.91 + i * 0.03, p);
        return { x: round(960 + side * 148 + (1 - arrival) * side * 160 + departure * side * 120),
          y: round(510 + Math.abs(side) * 26 + (1 - arrival) * 100 + departure * 70),
          angle: round(side * (14 + (1 - arrival) * 12) + 1.5 * Math.sin(TAU * p)),
          opacity: arrival * (1 - departure), draw: pulse(p, 0.10 + i * 0.045, 0.40 + i * 0.045, 0.80, 0.97) };
      }),
      chips: [{ x: 426, y: 728, r: 105 }, { x: 1510, y: 358, r: 82 }].map((v, i) => ({ ...v,
        y: round(v.y + 9 * Math.sin(TAU * p + i * Math.PI)),
        angle: round(6 * Math.sin(TAU * p + i)),
        opacity: pulse(p, 0.12 + i * 0.1, 0.32 + i * 0.1, 0.76 + i * 0.06, 0.95 + i * 0.04) }))
    };
  }

  function mount(doc, win) {
    const preset = doc.body.dataset.motionLoop;
    if (!Object.hasOwn(DURATIONS, preset)) return null;
    let stage = doc.getElementById("stage");
    if (!stage) {
      stage = doc.createElement("main"); stage.id = "stage";
      doc.body.dataset.motionStandalone = "";
      stage.className = "motion-standalone"; doc.body.appendChild(stage);
    }
    if (stage.querySelector(".motion-layer")) return null;
    const ambient = doc.body.dataset.motionTreatment === "ambient";
    const configuration = options(win.location.search);
    if (configuration.dark) doc.body.dataset.motionBackground = "dark";
    const node = (tag, attributes, parent) => {
      const element = doc.createElementNS(NS, tag);
      for (const [name, value] of Object.entries(attributes || {})) element.setAttribute(name, String(value));
      if (parent) parent.appendChild(element);
      return element;
    };
    const svg = node("svg", { class: "motion-layer", viewBox: "0 0 1920 1080", "aria-hidden": "true",
      focusable: "false", "data-preset": preset, "data-treatment": ambient ? "ambient" : "full" });
    const defs = node("defs", {}, svg);
    const prefix = "obs-motion-" + preset;
    const metal = node("linearGradient", { id: prefix + "-metal", x1: "0%", y1: "0%", x2: "100%", y2: "100%" }, defs);
    [["0%", "#09090b"], ["48%", "#242426"], ["75%", "#171719"], ["100%", "#09090b"]]
      .forEach(([offset, color]) => node("stop", { offset, "stop-color": color }, metal));
    const edge = node("linearGradient", { id: prefix + "-edge", x1: "0%", y1: "0%", x2: "100%", y2: "100%" }, defs);
    [["0%", "#79613a"], ["45%", PALE], ["62%", GOLD], ["100%", "#79613a"]]
      .forEach(([offset, color]) => node("stop", { offset, "stop-color": color }, edge));
    if (ambient) {
      const clip = node("clipPath", { id: prefix + "-perimeter" }, defs);
      node("rect", { x: 0, y: 244, width: 1920, height: 771 }, clip);
      const feather = node("filter", { id: prefix + "-feather", x: "-20%", y: "-20%", width: "140%", height: "140%" }, defs);
      node("feGaussianBlur", { stdDeviation: 24 }, feather);
      const mask = node("mask", { id: prefix + "-copy", maskUnits: "userSpaceOnUse", x: 0, y: 0,
        width: 1920, height: 1080, "mask-type": "luminance" }, defs);
      node("rect", { width: 1920, height: 1080, fill: "white" }, mask);
      // One static soft mask, then a fully protected copy core. Never blur text.
      node("rect", { x: -100, y: 140, width: 1650, height: 780, rx: 60,
        fill: "black", filter: `url(#${prefix}-feather)` }, mask);
      node("rect", { x: 0, y: 244, width: 1500, height: 616, fill: "black" }, mask);
    }
    const scene = node("g", { class: "motion-ornament", ...(ambient ? {
      "clip-path": `url(#${prefix}-perimeter)`, mask: `url(#${prefix}-copy)` } : {}) }, svg);
    const placement = node("g", ambient && preset !== "market" ? {
      transform: preset === "fibonacci" ? "translate(1370 330) scale(0.32)" : "translate(1290 315) scale(0.4)"
    } : {}, scene);
    const art = node("g", {}, placement);
    const stroke = `url(#${prefix}-edge)`, fill = `url(#${prefix}-metal)`;
    const lines = [], arcs = [], labels = [], objects = [];
    const draw = (element, progress) => element.setAttribute("stroke-dashoffset", round(1 - progress));
    const lineAttributes = { fill: "none", stroke, "stroke-width": 1.5, pathLength: 1,
      "stroke-dasharray": 1, "stroke-linecap": "round", "stroke-linejoin": "round" };
    if (preset === "fibonacci") {
      fibonacciSquares().forEach((square) => {
        node("rect", { x: square.x, y: square.y, width: square.size, height: square.size,
          fill: "none", stroke: GOLD, "stroke-width": 0.7, opacity: 0.13 }, art);
        lines.push(node("rect", { ...lineAttributes, x: square.x, y: square.y, width: square.size, height: square.size }, art));
        arcs.push(node("path", { ...lineAttributes, d: square.arc, "stroke-width": 3.2 }, art));
      });
      [0.236, 0.382, 0.5, 0.618, 0.786].forEach((ratio) => {
        const y = round(183 + 714 * ratio);
        const group = node("g", {}, art);
        // Axis-aligned paths have a zero-extent bounding box; use solid paint.
        const line = node("path", { ...lineAttributes, stroke: GOLD, d: `M310 ${y}H1608`, "stroke-width": 0.8 }, group);
        const label = node("text", { x: 1626, y: y + 4, class: "motion-ratio", fill: PALE }, group);
        label.textContent = ratio.toFixed(3);
        labels.push({ group, line });
      });
      node("circle", { cx: 1232, cy: 387, r: 5, fill: GOLD, opacity: 0.8 }, art);
    } else if (preset === "market") {
      for (let i = 0; i < 15; i += 1) {
        const group = node("g", {}, art);
        const shadow = node("rect", { rx: 5, fill: "#09090b", opacity: 0.32 }, group);
        const wick = node("path", { stroke: GOLD, "stroke-width": 2.2, fill: "none" }, group);
        const body = node("rect", { rx: 4, fill, stroke, "stroke-width": 2 }, group);
        const highlight = node("path", { stroke: PALE, "stroke-width": 1.2, opacity: 0.55 }, group);
        objects.push({ group, shadow, wick, body, highlight });
      }
    } else {
      [0, 1].forEach((i) => lines.push(node("ellipse", { ...lineAttributes, cx: 960, cy: 540,
        rx: 706 - i * 24, ry: 353 - i * 20, "stroke-width": i ? 0.7 : 1.6, opacity: i ? 0.22 : 0.48 }, art)));
      for (let i = 0; i < 3; i += 1) {
        const group = node("g", {}, art);
        node("rect", { x: -106, y: -155, width: 220, height: 322, rx: 16, fill: "#09090b", opacity: 0.4 }, group);
        node("rect", { x: -110, y: -164, width: 220, height: 322, rx: 16, fill, stroke, "stroke-width": 2 }, group);
        const outline = node("rect", { ...lineAttributes, x: -95, y: -149, width: 190, height: 292, rx: 9, "stroke-width": 0.8 }, group);
        node("path", { d: "M0 -66L47 -3L0 60L-47 -3Z M0 -44L31 -3L0 38L-31 -3Z", fill: "none", stroke, "stroke-width": 1.5 }, group);
        node("path", { d: "M-76 -113L-64 -101L-76 -89L-88 -101Z M76 107L64 119L76 131L88 119Z", fill: GOLD, opacity: 0.65 }, group);
        objects.push({ group, outline });
      }
      [105, 82].forEach((r) => {
        const group = node("g", {}, art);
        for (let layer = 2; layer >= 0; layer -= 1) {
          node("ellipse", { cx: 0, cy: layer * 12 + 8, rx: r, ry: r * 0.43, fill: SMOKE, stroke: "#79613a", "stroke-width": 2 }, group);
        }
        node("ellipse", { cx: 0, cy: 0, rx: r, ry: r * 0.43, fill, stroke, "stroke-width": 2 }, group);
        node("ellipse", { cx: 0, cy: 0, rx: r * 0.7, ry: r * 0.3, fill: "none", stroke: GOLD, "stroke-width": 1, opacity: 0.55 }, group);
        for (let j = 0; j < 12; j += 1) {
          const angle = TAU * j / 12;
          node("path", { d: `M${round(Math.cos(angle) * r * 0.83)} ${round(Math.sin(angle) * r * 0.357)}L${round(Math.cos(angle) * r)} ${round(Math.sin(angle) * r * 0.43)}`,
            stroke: PALE, "stroke-width": 7, opacity: 0.72 }, group);
        }
        objects.push({ group });
      });
    }

    function render(seconds) {
      const state = frame(preset, seconds);
      svg.setAttribute("data-time", round(seconds));
      if (preset === "fibonacci") {
        art.setAttribute("opacity", state.opacity);
        art.setAttribute("transform", `translate(${state.x} ${state.y})`);
        state.squares.forEach((v, i) => { draw(lines[i], v.line); draw(arcs[i], v.arc); });
        state.ratios.forEach((v, i) => { draw(labels[i].line, v.draw); labels[i].group.setAttribute("opacity", v.draw * 0.64); });
      } else if (preset === "market") {
        state.candles.forEach((v, i) => {
          const o = objects[i], half = v.height / 2;
          o.group.setAttribute("transform", `translate(${v.x} ${v.y}) rotate(${v.angle})`);
          o.group.setAttribute("opacity", v.opacity);
          for (const [element, offset] of [[o.body, 0], [o.shadow, 6]]) {
            for (const [key, value] of Object.entries({ x: -v.width / 2 + offset, y: -half + offset, width: v.width, height: v.height })) element.setAttribute(key, value);
          }
          o.wick.setAttribute("d", `M0 ${-half - 36}V${half + 36}`);
          o.highlight.setAttribute("d", `M${-v.width / 2 + 4} ${half - 8}V${-half + 8}`);
        });
      } else {
        art.setAttribute("opacity", state.opacity);
        lines.forEach((line) => draw(line, state.orbit));
        state.cards.forEach((v, i) => {
          objects[i].group.setAttribute("transform", `translate(${v.x} ${v.y}) rotate(${v.angle})`);
          objects[i].group.setAttribute("opacity", v.opacity); draw(objects[i].outline, v.draw);
        });
        state.chips.forEach((v, i) => {
          objects[i + 3].group.setAttribute("transform", `translate(${v.x} ${v.y}) rotate(${v.angle})`);
          objects[i + 3].group.setAttribute("opacity", v.opacity);
        });
      }
    }

    stage.insertBefore(svg, stage.firstChild);
    const observer = new win.MutationObserver(() => {
      // The show renderer may replace its children. Reuse this node and clock.
      if (svg.parentNode !== stage) stage.insertBefore(svg, stage.firstChild);
    });
    observer.observe(stage, { childList: true });
    const reduced = win.matchMedia("(prefers-reduced-motion: reduce)");
    const started = win.performance.now();
    let handle = null, lastPaint = -Infinity, stopped = false;
    function cancel() { if (handle !== null) win.cancelAnimationFrame(handle); handle = null; }
    function tick(now) {
      handle = null;
      if (stopped || doc.hidden || reduced.matches || configuration.freeze !== null) return;
      if (now - lastPaint >= 1000 / 30 - 0.5) { render((now - started) / 1000); lastPaint = now; }
      handle = win.requestAnimationFrame(tick);
    }
    function refresh() {
      cancel();
      if (stopped) return;
      if (configuration.freeze !== null) render(configuration.freeze);
      else if (reduced.matches) render(DURATIONS[preset] * 0.52);
      else if (!doc.hidden) { render((win.performance.now() - started) / 1000); handle = win.requestAnimationFrame(tick); }
    }
    reduced.addEventListener("change", refresh);
    doc.addEventListener("visibilitychange", refresh);
    refresh();
    return { render, stop() { stopped = true; cancel(); observer.disconnect();
      reduced.removeEventListener("change", refresh); doc.removeEventListener("visibilitychange", refresh); } };
  }

  const api = Object.freeze({ DURATIONS, frame, fibonacciSquares, options, mount });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ObsMotion = api;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => mount(document, root), { once: true });
    else mount(document, root);
  }
})(typeof window !== "undefined" ? window : globalThis);
