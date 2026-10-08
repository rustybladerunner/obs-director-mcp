"use strict";

// Fixed synthetic values in arbitrary units. This animation never dispatches events.
const stage = document.getElementById("stage");
const trace = document.getElementById("trace-path");
const area = document.getElementById("trace-area");
const scan = document.getElementById("scan-line");
const halo = document.getElementById("scan-halo");
const dot = document.getElementById("scan-dot");
const readout = document.getElementById("sample-readout");
const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
const points = Array.from({ length: 97 }, (_, index) => ({
  x: 32 + index * 11.5,
  y: 156 - 28 * Math.sin(index * 0.17) - 11 * Math.sin(index * 0.67)
    - 53 * Math.exp(-((index - 47) ** 2) / 50) + 8 * Math.cos(index * 0.39),
}));
const path = points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
trace.setAttribute("d", path);
area.setAttribute("d", `${path} L1136 240 L32 240 Z`);

function fit() {
  const scale = Math.min(window.innerWidth / 1280, window.innerHeight / 720);
  stage.style.transform = `translate(-50%, -50%) scale(${scale})`;
}

function draw(timestamp) {
  const sample = motion.matches ? 47 : ((timestamp % 6000) / 6000) * 96;
  const first = Math.floor(sample);
  const next = Math.min(first + 1, 96);
  const fraction = sample - first;
  const x = points[first].x + (points[next].x - points[first].x) * fraction;
  const y = points[first].y + (points[next].y - points[first].y) * fraction;
  scan.setAttribute("d", `M${x} 40 V240`);
  for (const marker of [halo, dot]) {
    marker.setAttribute("cx", String(x));
    marker.setAttribute("cy", String(y));
  }
  readout.textContent = `${motion.matches ? "REDUCED MOTION" : "LOCAL ANIMATION"} / SAMPLE ${String(first).padStart(2, "0")}`;
  requestAnimationFrame(draw);
}

window.addEventListener("resize", fit);
fit();
requestAnimationFrame(draw);
