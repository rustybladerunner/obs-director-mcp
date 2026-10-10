/* Original choreography and geometry; no feeds, external assets, or franchise material. */
(function (root) {
  "use strict";
  const DURATION = 24, WIDTH = 1920, HEIGHT = 1080, TAU = Math.PI * 2;
  const GOLD = "#d4ad65", PALE = "#d7b777", BRONZE = "#79613a", BLACK = "#09090b";
  const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
  const ease = (a, b, t) => { const p = clamp((t - a) / (b - a)); return p * p * (3 - 2 * p); };
  const windowed = (a, b, c, d, t) => ease(a, b, t) * (1 - ease(c, d, t));
  function options(search) {
    const q = new URLSearchParams(search), raw = q.get("freeze");
    const value = raw !== null && raw.trim() !== "" ? Number(raw) : NaN;
    return Object.freeze({ freeze: Number.isFinite(value) && value >= 0 && value <= DURATION ? value : null,
      obs: q.get("obs") === "1", silent: q.get("silent") === "1" });
  }
  function stateAt(seconds) {
    if (!Number.isFinite(seconds)) throw new TypeError("Finite intro time required");
    const t = clamp(seconds, 0, DURATION);
    return { time: t, shot: t < 4.8 ? "iris" : t < 9.6 ? "corridor" : t < 14.4 ? "reveal" : t < 18 ? "gather" : "title",
      iris: 1 - ease(4.1, 4.8, t), aperture: 42 + 1340 * ease(0.35, 4.8, t),
      corridor: windowed(0, 1.1, 12.8, 14.6, t), wire: windowed(9.6, 10.7, 13.6, 14.4, t),
      code: windowed(9.6, 10.4, 13.4, 14.4, t), gather: windowed(14.1, 14.6, 17.3, 18, t),
      emblem: ease(15.5, 17.5, t), title: ease(17.1, 18, t), done: t >= DURATION };
  }

  // The media clock is authoritative only while playback demonstrably advances.
  // Pending play promises, rejected autoplay and decoder stalls cannot freeze the picture.
  function createClock(start = 0) {
    let anchor = start, value = 0, mediaLast = -1, progressedAt = start, engaged = false, seekTarget = null;
    return {
      get seekTarget() { return seekTarget; },
      reset(now) { anchor = now; value = 0; mediaLast = -1; progressedAt = now; engaged = false; seekTarget = null; },
      sample(now, media) {
        if (!Number.isFinite(now)) throw new TypeError("Finite clock time required");
        seekTarget = null;
        const fallback = clamp(value + Math.max(0, now - anchor), 0, DURATION);
        const current = media && media.currentTime;
        const usable = media && media.paused === false && media.ended === false && Number.isFinite(current) && current >= 0;
        const moved = usable && current > 0 && Math.abs(current - mediaLast) > 0.0001;
        if (moved) { engaged = true; progressedAt = now; mediaLast = current; }
        if (usable && engaged && now - progressedAt <= 0.75) {
          if (current < value - 0.05) { value = fallback; if (moved) seekTarget = value; }
          else value = clamp(Math.max(value, current), 0, DURATION);
        } else value = fallback;
        anchor = now;
        return value;
      }
    };
  }

  function worldState(seconds) {
    const t = stateAt(seconds).time, travel = ease(0.7, 14.4, t), collapse = ease(14.1, 16.65, t);
    return { travel, collapse, wire: ease(9.6, 11.15, t), visibility: windowed(0, 0.6, 15.75, 16.8, t),
      camera: [Math.sin(travel * Math.PI) * 0.42, 3.2 + Math.sin(travel * Math.PI) * 0.18, 20 - travel * 24],
      aperture: 0.16 + 15 * ease(0.35, 4.8, t), iris: 1 - ease(4.1, 4.8, t) };
  }

  function createWorld(THREE, doc) {
    if (!THREE || typeof THREE.WebGLRenderer !== "function") return null;
    const canvas = doc.createElement("canvas"), gpu = new THREE.WebGLRenderer({ canvas, antialias: true,
      alpha: false, preserveDrawingBuffer: true, powerPreference: "default" });
    gpu.setPixelRatio(1); gpu.setSize(WIDTH, HEIGHT, false);
    gpu.outputColorSpace = THREE.SRGBColorSpace; gpu.toneMapping = THREE.ACESFilmicToneMapping; gpu.toneMappingExposure = 1.25;
    const scene = new THREE.Scene(); scene.background = new THREE.Color(BLACK); scene.fog = new THREE.FogExp2(BLACK, 0.012);
    const camera = new THREE.PerspectiveCamera(46, WIDTH / HEIGHT, 0.08, 180);
    const geometries = new Set(), materials = new Set(), solids = [], travellers = [], irisBlades = [];
    const geometry = value => { geometries.add(value); return value; };
    const material = value => { materials.add(value); return value; };
    const physical = (color, metalness, roughness) => material(new THREE.MeshStandardMaterial({ color, metalness, roughness }));
    const gold = physical(0xb49457, 0.78, 0.24), bronze = physical(0x77562b, 0.68, 0.29), dark = physical(0x252225, 0.40, 0.30);
    const edge = material(new THREE.LineBasicMaterial({ color: 0xd7b777, transparent: true, opacity: 0 }));
    // Original studio-light reflection map, generated locally. No downloaded texture or raster asset.
    const reflection = doc.createElement("canvas"); reflection.width = 512; reflection.height = 256;
    const light = reflection.getContext("2d");
    const shade = light.createLinearGradient(0, 0, 0, 256); shade.addColorStop(0, "#171719"); shade.addColorStop(0.52, "#393127"); shade.addColorStop(1, "#09090b");
    light.fillStyle = shade; light.fillRect(0, 0, 512, 256);
    for (const [x, y, w, h, color] of [[64, 26, 86, 140, "#d7b777"], [314, 16, 22, 182, "#d4ad65"], [410, 76, 76, 34, "#79613a"]]) {
      light.fillStyle = color; light.fillRect(x, y, w, h);
    }
    const environment = new THREE.CanvasTexture(reflection); environment.mapping = THREE.EquirectangularReflectionMapping;
    environment.colorSpace = THREE.SRGBColorSpace; scene.environment = environment; scene.environmentIntensity = 1.1;
    scene.add(new THREE.HemisphereLight(0xd7b777, 0x09090b, 0.42));
    const key = new THREE.DirectionalLight(0xffdf9a, 3.4); key.position.set(-5, 10, 8); scene.add(key);
    const rim = new THREE.DirectionalLight(0xd7b777, 1.4); rim.position.set(8, 3, -16); scene.add(rim);
    const movingLight = new THREE.PointLight(0xd4ad65, 95, 28, 2); scene.add(movingLight);
    const floorMaterial = material(new THREE.MeshPhysicalMaterial({ color: 0x09090b,
      metalness: 0, roughness: 1, envMapIntensity: 0.04, specularIntensity: 0.08 }));
    const floor = new THREE.Mesh(geometry(new THREE.PlaneGeometry(80, 150)), floorMaterial); floor.rotation.x = -Math.PI / 2; floor.position.set(0, -0.35, -40); scene.add(floor);
    const grid = new THREE.GridHelper(120, 40, 0xd4ad65, 0x79613a); grid.position.set(0, -0.32, -35);
    geometry(grid.geometry); material(grid.material); grid.material.transparent = true; grid.material.opacity = 0; scene.add(grid);

    function body(shape, skin, parent) {
      const mesh = new THREE.Mesh(geometry(shape), skin), wire = new THREE.LineSegments(geometry(new THREE.EdgesGeometry(shape, 28)), edge);
      const group = new THREE.Group(); group.add(mesh, wire); parent.add(group); solids.push(mesh); return group;
    }
    function travel(group) { scene.add(group); travellers.push({ group, base: group.position.clone(), angle: group.rotation.clone() }); }
    const cardShape = new THREE.Shape();
    cardShape.moveTo(-0.61, -1.0); cardShape.lineTo(0.61, -1.0); cardShape.quadraticCurveTo(0.69, -1.0, 0.69, -0.92);
    cardShape.lineTo(0.69, 0.92); cardShape.quadraticCurveTo(0.69, 1.0, 0.61, 1.0); cardShape.lineTo(-0.61, 1.0);
    cardShape.quadraticCurveTo(-0.69, 1.0, -0.69, 0.92); cardShape.lineTo(-0.69, -0.92); cardShape.quadraticCurveTo(-0.69, -1.0, -0.61, -1.0);
    const cardGeometry = geometry(new THREE.ExtrudeGeometry(cardShape, { depth: 0.05, bevelEnabled: true, bevelThickness: 0.018, bevelSize: 0.016, bevelSegments: 2, steps: 1, curveSegments: 4 }));
    for (let i = 0; i < 9; i++) {
      const z = -i * 6.6, height = 2.0 + (i % 4) * 0.56;
      for (const sign of [-1, 1]) {
        const group = new THREE.Group(); group.position.set(sign * (4.8 + (i % 3) * 0.65), 1.55 + (i % 2) * 0.44, z);
        body(new THREE.BoxGeometry(0.64, height, 0.63), (i + sign) % 3 === 0 ? gold : bronze, group);
        body(new THREE.CylinderGeometry(0.022, 0.022, height + 1.05, 8), gold, group);
        travel(group);
      }
      if (i % 2 === 0) {
        const group = new THREE.Group(); group.position.set(i % 4 ? -3.0 : 3.0, 4.65 + i * 0.06, z - 3.0);
        group.rotation.set(0.08, i % 4 ? 0.30 : -0.30, i % 4 ? -0.22 : 0.22);
        body(cardGeometry, dark, group);
        const diamond = new THREE.Shape(); diamond.moveTo(0, 0.36); diamond.lineTo(0.23, 0); diamond.lineTo(0, -0.36); diamond.lineTo(-0.23, 0); diamond.closePath();
        const inset = body(new THREE.ExtrudeGeometry(diamond, { depth: 0.018, bevelEnabled: true, bevelSize: 0.008, bevelThickness: 0.006, bevelSegments: 1, steps: 1 }), gold, group);
        inset.position.z = 0.07; travel(group);
      }
      if (i % 3 !== 2) {
        const group = new THREE.Group(); group.position.set(i % 2 ? -2.2 : 2.25, 0.8 + (i % 3) * 0.35, z - 2.8); group.rotation.set(0.34, 0, i % 2 ? 0.12 : -0.12);
        body(new THREE.CylinderGeometry(0.64, 0.64, 0.18, 48), bronze, group);
        const face = body(new THREE.CylinderGeometry(0.54, 0.54, 0.025, 48), dark, group); face.position.y = 0.105;
        for (let k = 0; k < 12; k++) { const a = k * TAU / 12;
          const mark = body(new THREE.BoxGeometry(0.11, 0.012, 0.10), gold, group); mark.position.set(Math.cos(a) * 0.568, 0.112, Math.sin(a) * 0.568); mark.rotation.y = -a;
        }
        travel(group);
      }
    }
    // A luminous seed defines the travelling axis and remains alive through the collapse.
    const seedMaterial = material(new THREE.MeshBasicMaterial({ color: 0xffdfa0 }));
    const seed = new THREE.Mesh(geometry(new THREE.OctahedronGeometry(0.12)), seedMaterial); seed.position.set(0, 3.2, -36); scene.add(seed);
    const seedLight = new THREE.PointLight(0xd4ad65, 65, 16, 2); seedLight.position.copy(seed.position); scene.add(seedLight);
    const irisMaterial = physical(0x5b452a, 0.75, 0.27), irisGroup = new THREE.Group(); irisGroup.position.set(0, 3.2, 11.2); scene.add(irisGroup);
    for (let i = 0; i < 12; i++) {
      const a = i * TAU / 12, b = a + TAU / 12 + 0.03, point = (r, q) => [Math.cos(q) * r, Math.sin(q) * r];
      const p = [point(0.16, a), point(0.16, b), point(32, b + 0.27), point(32, a + 0.27)];
      const shape = new THREE.Shape(); shape.moveTo(...p[0]); p.slice(1).forEach(v => shape.lineTo(...v)); shape.closePath();
      const blade = new THREE.Mesh(geometry(new THREE.ExtrudeGeometry(shape, { depth: 0.065, bevelEnabled: true, bevelSize: 0.018, bevelThickness: 0.012, bevelSegments: 1, steps: 1 })), irisMaterial);
      blade.position.z = i * 0.004; irisGroup.add(blade); irisBlades.push({ blade, angle: a + TAU / 24 });
    }
    let disposed = false, last = worldState(0);
    return { canvas, render(seconds) {
      if (disposed) throw new Error("World renderer disposed");
      if (typeof gpu.getContext === "function" && gpu.getContext().isContextLost()) throw new Error("World graphics context unavailable");
      const w = worldState(seconds); last = w;
      camera.position.set(...w.camera); camera.lookAt(0, 3.2, -36);
      movingLight.position.set(-2.3, 4.6, w.camera[2] - 7.5);
      movingLight.intensity = 95 * (1 - w.wire * 0.9);
      key.intensity = 3.4 * (1 - w.wire * 0.9); rim.intensity = 1.4 * (1 - w.wire * 0.8);
      for (const skin of [gold, bronze, dark]) { skin.transparent = w.wire > 0; skin.opacity = 1 - w.wire; skin.depthWrite = w.wire < 0.01; }
      for (const mesh of solids) mesh.visible = w.wire < 0.999;
      edge.opacity = w.wire * (0.75 - w.collapse * 0.4);
      floorMaterial.transparent = true; floorMaterial.opacity = 1 - w.wire; floor.visible = w.wire < 0.999;
      grid.material.opacity = w.wire * 0.38 * (1 - w.collapse);
      const target = new THREE.Vector3(0, 5.6, -36);
      travellers.forEach(({ group, base, angle }, i) => {
        group.position.lerpVectors(base, target, w.collapse);
        const arc = Math.sin(w.collapse * Math.PI) * 0.48;
        group.position.x += Math.sin(i * 2.4) * arc; group.position.y += Math.cos(i * 2.4) * arc;
        group.rotation.set(angle.x, angle.y + w.collapse * 0.45, angle.z + w.collapse * 0.85);
        group.scale.setScalar(1 - w.collapse * 0.99);
      });
      seed.position.y = 3.2 + w.collapse * 2.4; seed.scale.setScalar(1 + Math.sin(w.collapse * Math.PI) * 1.4); seedLight.position.copy(seed.position);
      irisGroup.visible = w.iris > 0.001; irisMaterial.transparent = w.iris < 1; irisMaterial.opacity = w.iris;
      irisBlades.forEach(({ blade, angle }) => { const opening = w.aperture - 0.16;
        blade.position.x = Math.cos(angle) * opening; blade.position.y = Math.sin(angle) * opening; });
      gpu.render(scene, camera); return canvas;
    }, inspect() { return { camera: [...last.camera], collapse: last.collapse, materialOpacity: gold.opacity,
      edgeOpacity: edge.opacity, gridOpacity: grid.material.opacity, travellers: travellers.length,
      positions: travellers.map(v => v.group.position.toArray()), meshes: solids.length, disposed }; },
    dispose() { if (disposed) return; disposed = true; geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose());
      environment.dispose(); gpu.dispose(); if (typeof gpu.forceContextLoss === "function") gpu.forceContextLoss(); } };
  }

  function renderer(ctx, emblem, world = null, fallback = () => {}) {
    function stroke(points, color = GOLD, width = 1, close = false) {
      ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
      if (close) ctx.closePath(); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke();
    }
    function polygon(points, fill, edge = null, width = 1) {
      ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
      ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
      if (edge) { ctx.strokeStyle = edge; ctx.lineWidth = width; ctx.stroke(); }
    }
    function circle(x, y, r, color, width = 1) {
      ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke();
    }
    function glow(x, y, radius, alpha) {
      const g = ctx.createRadialGradient(x, y, 1, x, y, radius);
      g.addColorStop(0, `rgba(212,173,101,${alpha})`); g.addColorStop(0.38, `rgba(121,97,58,${alpha * 0.36})`); g.addColorStop(1, "rgba(9,9,11,0)");
      ctx.fillStyle = g; ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
    }
    function tracking(text, x, y, size, spacing, color = PALE, weight = 400) {
      ctx.font = `${weight} ${size}px Arial, Helvetica, sans-serif`;
      const widths = Array.from(text, c => ctx.measureText(c).width), total = widths.reduce((a, b) => a + b, 0) + spacing * (text.length - 1);
      let left = x - total / 2; ctx.fillStyle = color;
      Array.from(text).forEach((c, i) => { ctx.fillText(c, left, y); left += widths[i] + spacing; });
    }
    function project(x, y, z) { const s = 900 / (z + 680); return [960 + x * s, 442 + y * s, s]; }
    function corridor(t, amount, wire) {
      if (amount <= 0) return;
      ctx.save(); ctx.globalAlpha = amount;
      const drive = ease(1.2, 13.8, t), turn = Math.sin(clamp((t - 4.8) / 9.6) * Math.PI) * 0.022;
      ctx.translate(960, 540); ctx.rotate(turn); ctx.translate(-960, -540);
      glow(960, 426, 570, 0.17);
      ctx.globalAlpha = amount * 0.70;
      stroke([[960, 419], [960, 449]], PALE, 1.4);
      ctx.fillStyle = GOLD; ctx.fillRect(956, 429, 8, 13);
      // Perspective rails provide the unbroken forward move through all three opening shots.
      ctx.globalAlpha = amount * (0.24 + wire * 0.4);
      for (let i = -6; i <= 6; i++) stroke([project(i * 200, 330, 2800), project(i * 200, 330, -260)], BRONZE, i === 0 ? 1.2 : 0.7);
      for (let i = 0; i < 13; i++) { const z = 180 + i * i * 18 - drive * 200;
        stroke([project(-1800, 330, z), project(1800, 330, z)], GOLD, 0.7); }
      // Far objects first. The sweep is finite, not a repeating corridor conveyor.
      for (let i = 10; i >= 0; i--) {
        const z = 120 + i * 270 - drive * 390, depth = clamp(1 - i / 14), side = i % 2 ? -1 : 1;
        for (const sign of [-1, 1]) {
          const p = project(sign * (510 + (i % 3) * 80), 70 + Math.sin(i * 2) * 60, z);
          const w = (38 + (i % 3) * 14) * p[2], h = (145 + (i % 4) * 50) * p[2];
          ctx.globalAlpha = amount * (0.28 + depth * 0.62);
          const body = ctx.createLinearGradient(p[0] - w, p[1], p[0] + w, p[1]);
          body.addColorStop(0, "#09090b"); body.addColorStop(0.52, "#302a20"); body.addColorStop(1, "#171719");
          stroke([[p[0], p[1] - h / 2 - 42 * p[2]], [p[0], p[1] + h / 2 + 42 * p[2]]], GOLD, 1.4 * p[2]);
          const corners = [[p[0] - w / 2, p[1] - h / 2], [p[0] + w / 2, p[1] - h / 2], [p[0] + w / 2, p[1] + h / 2], [p[0] - w / 2, p[1] + h / 2]];
          // The material falls away during the reveal; the same objects become a mesh.
          const objectAlpha = ctx.globalAlpha;
          ctx.globalAlpha = objectAlpha * (1 - wire * 0.92);
          polygon(corners, body);
          ctx.globalAlpha = objectAlpha;
          stroke(corners, GOLD, 1.1 * p[2], true);
          stroke([[p[0] + w / 2, p[1] - h / 2], [p[0] + w / 2 + 12 * p[2], p[1] - h / 2 - 11 * p[2]], [p[0] + w / 2 + 12 * p[2], p[1] + h / 2 - 11 * p[2]], [p[0] + w / 2, p[1] + h / 2]], BRONZE, p[2]);
        }
        if (i % 2 === 0) {
          const p = project(side * (250 + i * 18), -130 - (i % 3) * 50, z + 150);
          ctx.save(); ctx.translate(p[0], p[1]); ctx.rotate(side * (0.24 + drive * 0.12)); ctx.scale(p[2], p[2]);
          ctx.globalAlpha = amount * (0.18 + depth * 0.48);
          polygon([[-68, -100], [68, -100], [68, 100], [-68, 100]], "#111113", BRONZE, 1.5);
          stroke([[-58, -90], [58, -90], [58, 90], [-58, 90]], GOLD, 0.7, true);
          stroke([[0, -29], [20, 0], [0, 29], [-20, 0]], GOLD, 1.1, true); ctx.restore();
        }
        if (i % 3 === 1) {
          const p = project(-side * 350, 180 + (i % 2) * 30, z);
          ctx.save(); ctx.translate(p[0], p[1]); ctx.rotate(-0.18 + drive * 0.12); ctx.scale(p[2], p[2] * 0.4);
          ctx.globalAlpha = amount * 0.64; ctx.fillStyle = "#171719";
          ctx.beginPath(); ctx.arc(0, 0, 78, 0, TAU); ctx.fill(); circle(0, 0, 78, GOLD, 3); circle(0, 0, 55, BRONZE, 2);
          for (let k = 0; k < 12; k++) { const a = k * TAU / 12; stroke([[Math.cos(a) * 66, Math.sin(a) * 66], [Math.cos(a) * 77, Math.sin(a) * 77]], PALE, 4); }
          ctx.restore();
        }
      }
      ctx.restore();
    }
    function iris(t, opacity, aperture) {
      if (opacity <= 0) return;
      ctx.save(); ctx.globalAlpha = opacity; ctx.translate(960, 540);
      const angle = -0.24 + ease(0.4, 4.8, t) * 0.34;
      ctx.rotate(angle);
      for (let i = 0; i < 12; i++) {
        const a = i * TAU / 12, b = a + TAU / 12 + 0.025;
        const p = (r, q) => [Math.cos(q) * r, Math.sin(q) * r];
        const blade = ctx.createLinearGradient(...p(aperture, a), ...p(1600, b));
        blade.addColorStop(0, "#30291f"); blade.addColorStop(0.22, "#171719"); blade.addColorStop(1, "#09090b");
        polygon([p(aperture, a), p(aperture, b), p(1650, b + 0.30), p(1650, a + 0.30)], blade, "#3b3224", 1.5);
        stroke([p(aperture, a), p(aperture + 82, a + 0.018), p(1400, a + 0.285)], i % 3 === 0 ? GOLD : BRONZE, i % 3 === 0 ? 2 : 0.8);
      }
      circle(0, 0, aperture + 2, GOLD, 2.4);
      ctx.globalAlpha *= 0.28; circle(0, 0, aperture + 13, PALE, 0.8); ctx.restore();
      // The market seed lives inside the opening, never as a full-screen lens effect.
      ctx.save(); ctx.globalAlpha = opacity * (1 - ease(1.5, 3.4, t));
      glow(960, 540, 190, 0.45); stroke([[960, 483], [960, 597]], PALE, 2);
      ctx.fillStyle = GOLD; ctx.fillRect(950, 515, 20, 50); ctx.restore();
    }
    function reveal(t, amount) {
      if (amount <= 0) return;
      ctx.save(); ctx.globalAlpha = amount * 0.5;
      for (let i = 0; i < 8; i++) {
        const r = 230 + i * 99, squash = 0.48 + i * 0.02;
        ctx.beginPath(); ctx.ellipse(960, 520, r, r * squash, -0.07, 0, TAU); ctx.strokeStyle = BRONZE; ctx.lineWidth = 0.7; ctx.stroke();
      }
      const fragments = ["MATERIAL  /  MESH", "WORLD :: SYNTHETIC", "HORIZON.REBUILD", "SEAT[01] :: READY", "TABLE / REVEAL", "LIGHT -> GEOMETRY"];
      fragments.forEach((text, i) => {
        const enter = ease(9.7 + i * 0.17, 10.2 + i * 0.17, t), x = 265 + (i % 3) * 560, y = 260 + Math.floor(i / 3) * 490;
        ctx.globalAlpha = amount * enter * (0.36 + 0.12 * Math.sin(t * 2.3 + i));
        ctx.fillStyle = GOLD; ctx.font = "17px monospace"; ctx.fillText(text, x, y);
        stroke([[x, y + 17], [x + 185 * enter, y + 17]], BRONZE, 0.8);
        for (let k = 0; k < 6; k++) { const h = 5 + ((i * 3 + k * 7) % 19); ctx.fillRect(x + k * 9, y + 35, 3, h); }
      });
      const scan = windowed(10.65, 10.85, 11.15, 11.35, t) + windowed(12.45, 12.65, 12.85, 13.05, t);
      ctx.globalAlpha = amount * scan * 0.18;
      stroke([[140, 540 + Math.sin(t * 2) * 250], [1780, 540 + Math.sin(t * 2) * 250]], PALE, 1.5);
      ctx.restore();
    }
    function gather(t, amount) {
      if (amount <= 0) return;
      ctx.save(); ctx.translate(960, 435);
      const contraction = 1 - ease(14.4, 17.6, t), rotation = -0.24 + ease(14.4, 17.6, t) * 0.42;
      ctx.rotate(rotation);
      // Contiguous quarter circles on adjacent Fibonacci squares, contracting to the host.
      const arcs = [[1, 1, 1, 2], [1, 1, 1, 3], [0, 1, 2, 0], [0, 0, 3, 1],
        [2, 0, 5, 2], [2, 3, 8, 3], [-3, 3, 13, 0], [-3, -5, 21, 1]];
      const unit = 30 * (0.25 + contraction * 1.15);
      for (let i = 0; i < arcs.length; i++) {
        const [cx, cy, radius, quadrant] = arcs[i];
        const progress = ease(14.25 + i * 0.08, 15.4 + i * 0.10, t);
        ctx.globalAlpha = amount * (0.20 + i * 0.055);
        ctx.beginPath(); ctx.arc((cx - 1) * unit, (cy - 1) * unit, radius * unit,
          quadrant * Math.PI / 2, (quadrant + progress) * Math.PI / 2);
        ctx.strokeStyle = i % 3 === 0 ? PALE : GOLD; ctx.lineWidth = i % 3 === 0 ? 2.5 : 1.0; ctx.stroke();
        const a = i * 2.39996 + t * 0.12, distance = radius * unit + contraction * 220;
        ctx.beginPath(); ctx.arc(Math.cos(a) * distance, Math.sin(a) * distance, 2 + i * 0.2, 0, TAU); ctx.fillStyle = PALE; ctx.fill();
      }
      ctx.restore();
      // Connect the last world-space seed to the emblem; no dissolve-through-black gap.
      const bridge = windowed(14.05, 14.3, 16.2, 17.05, t);
      ctx.save(); ctx.globalAlpha = bridge * 0.85;
      glow(960, 435, 160, bridge * 0.34);
      stroke([[960, 420], [960, 450]], PALE, 2.2); circle(960, 435, 5, GOLD, 1.4);
      ctx.restore();
      glow(960, 435, 490, windowed(15.75, 16.5, 16.7, 17.6, t) * 0.24);
    }
    function finale(t, emblemAmount, titleAmount) {
      if (emblemAmount <= 0 && titleAmount <= 0) return;
      ctx.save(); ctx.globalAlpha = emblemAmount;
      glow(960, 393, 480, 0.10);
      const settle = ease(15.5, 18, t), size = 398 + (1 - settle) * 100, cy = 420 - settle * 27;
      circle(960, cy, 236 + (1 - settle) * 40, BRONZE, 1);
      circle(960, cy, 250 + (1 - settle) * 48, "#3b3224", 0.8);
      if (emblem && emblem.complete && emblem.naturalWidth > 0) ctx.drawImage(emblem, 960 - size / 2, cy - size / 2, size, size);
      else { circle(960, cy, 114, GOLD, 2); tracking("P", 960, cy + 38, 106, 0, PALE, 600); }
      for (let i = 0; i < 32; i++) { const a = TAU * i / 32;
        stroke([[960 + Math.cos(a) * 245, cy + Math.sin(a) * 245], [960 + Math.cos(a) * (i % 4 ? 250 : 260), cy + Math.sin(a) * (i % 4 ? 250 : 260)]], BRONZE, 1); }
      ctx.globalAlpha = titleAmount;
      tracking("PIPHOUND", 960, 752, 100, 19, PALE, 500);
      tracking("THE TRADING TABLE", 960, 833, 26, 10, GOLD, 400);
      stroke([[726, 881], [1194, 881]], BRONZE, 0.8);
      tracking("SYNTHETIC TOURNAMENT", 960, 929, 15, 5, "#b69a65", 400);
      ctx.restore();
    }
    return function render(seconds) {
      const s = stateAt(seconds), t = Math.min(s.time, 18);
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1; ctx.fillStyle = BLACK; ctx.fillRect(0, 0, WIDTH, HEIGHT);
      let hasWorld = Boolean(world);
      if (world && t < 16.8) {
        try { const frame = world.render(t); ctx.globalAlpha = worldState(t).visibility; ctx.drawImage(frame, 0, 0, WIDTH, HEIGHT); ctx.globalAlpha = 1; }
        catch (_) { world.dispose(); world = null; hasWorld = false; fallback(); }
      }
      if (!hasWorld) corridor(t, s.corridor, s.wire);
      reveal(t, s.code);
      if (!hasWorld) iris(t, s.iris, s.aperture);
      gather(t, s.gather);
      finale(t, s.emblem, s.title);
      // Edge falloff is a lighting boundary, not letterbox bars or a texture overlay.
      const edge = ctx.createRadialGradient(960, 515, 340, 960, 515, 1190);
      edge.addColorStop(0, "rgba(9,9,11,0)"); edge.addColorStop(1, "rgba(9,9,11,0.62)");
      ctx.fillStyle = edge; ctx.fillRect(0, 0, WIDTH, HEIGHT);
      return s;
    };
  }

  function mount(doc, win) {
    const canvas = doc.getElementById("picture");
    if (!canvas || canvas.dataset.mounted === "true") return null;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return null;
    canvas.dataset.mounted = "true";
    const configuration = options(win.location.search), audio = doc.getElementById("score"), controls = doc.getElementById("controls");
    const replayButton = doc.getElementById("replay"), soundButton = doc.getElementById("sound"), status = doc.getElementById("status");
    const reduced = win.matchMedia("(prefers-reduced-motion: reduce)");
    controls.hidden = configuration.obs;
    const emblem = new win.Image();
    let world = null;
    const fallback = () => { canvas.dataset.backend = "canvas-fallback"; status.textContent = "Basic preview · 3D unavailable"; };
    try { world = createWorld(win.THREE, doc); } catch (_) { fallback(); }
    canvas.dataset.backend = world ? "three-webgl" : "canvas-fallback";
    const render = renderer(ctx, emblem, world, fallback), clock = createClock(win.performance.now() / 1000);
    let handle = null, stopped = false, completed = false, sound = !configuration.silent, lastPaint = -Infinity, lastTime = 0, generation = 0, syncFailed = false;
    audio.volume = 0.65;
    const setStatus = text => { const message = canvas.dataset.backend === "canvas-fallback" ? text + " · basic preview" : text;
      if (status.textContent !== message) status.textContent = message; };
    function cancel() { if (handle !== null) win.cancelAnimationFrame(handle); handle = null; }
    function paint(t) { lastTime = clamp(t, 0, DURATION); const s = render(lastTime); canvas.dataset.time = lastTime.toFixed(3); canvas.dataset.shot = s.shot; return s; }
    function syncSoundButton() { soundButton.setAttribute("aria-pressed", String(sound)); soundButton.textContent = sound ? "Sound on" : "Sound off"; }
    function tick(now) {
      handle = null;
      if (stopped || completed || reduced.matches || configuration.freeze !== null) return;
      const seconds = clock.sample(now / 1000, sound && !syncFailed ? audio : null);
      if (clock.seekTarget !== null) {
        try {
          audio.currentTime = clock.seekTarget;
          if (!Number.isFinite(audio.currentTime) || Math.abs(audio.currentTime - clock.seekTarget) > 0.15) throw new Error("Unconfirmed score seek");
        } catch (_) { syncFailed = true; audio.pause(); setStatus("Silent playback · score unavailable"); }
      }
      if (now - lastPaint >= 1000 / 30 - 0.5 || seconds >= DURATION) { paint(seconds); lastPaint = now; }
      if (seconds >= DURATION) { completed = true; audio.pause(); setStatus("Intro complete"); return; }
      handle = win.requestAnimationFrame(tick);
    }
    function attemptAudio(token) {
      if (!sound || syncFailed || reduced.matches || configuration.freeze !== null || completed || stopped) return;
      try {
        const play = audio.play();
        if (play && typeof play.then === "function") play.then(() => {
          if (reduced.matches || completed || stopped || !sound || syncFailed) { audio.pause(); return; }
          if (token !== generation) return;
          setStatus("Playing with score");
        }).catch(() => { if (token === generation) setStatus("Silent playback · replay for sound"); });
      } catch (_) { setStatus("Silent playback · replay for sound"); }
    }
    function replay() {
      if (stopped) return;
      generation += 1; cancel(); audio.pause();
      try { audio.currentTime = 0; } catch (_) { /* A missing score has no seekable timeline. */ }
      clock.reset(win.performance.now() / 1000); completed = false; lastPaint = -Infinity; syncFailed = false;
      if (configuration.freeze !== null) { paint(configuration.freeze); setStatus("Frozen inspection frame"); return; }
      if (reduced.matches) { paint(DURATION); setStatus("Reduced motion · title held"); return; }
      paint(0); setStatus(sound ? "Starting score" : "Silent playback");
      handle = win.requestAnimationFrame(tick); attemptAudio(generation);
    }
    function toggleSound() {
      if (stopped) return;
      sound = !sound; syncSoundButton();
      if (!sound) { audio.pause(); setStatus(completed ? "Intro complete" : "Silent playback"); }
      else if (!completed && configuration.freeze === null && !reduced.matches) {
        syncFailed = false;
        try { audio.currentTime = lastTime; } catch (_) { /* Silent fallback remains active. */ }
        attemptAudio(generation);
      }
    }
    function preferenceChanged() { replay(); }
    replayButton.addEventListener("click", replay); soundButton.addEventListener("click", toggleSound);
    reduced.addEventListener("change", preferenceChanged);
    emblem.addEventListener("load", () => { if (!stopped) paint(lastTime); });
    emblem.addEventListener("error", () => { canvas.dataset.emblem = "unavailable"; });
    emblem.src = "../tournament/piphound-emblem.png";
    syncSoundButton(); replay();
    return { render(t) { if (!Number.isFinite(t) || t < 0 || t > DURATION) throw new RangeError("Inspection time must be 0..24 seconds"); return paint(t); },
      replay, stop() { stopped = true; generation += 1; cancel(); audio.pause(); if (world) world.dispose(); reduced.removeEventListener("change", preferenceChanged);
        replayButton.removeEventListener("click", replay); soundButton.removeEventListener("click", toggleSound); } };
  }
  const api = Object.freeze({ DURATION, WIDTH, HEIGHT, options, stateAt, worldState, createWorld, createClock, renderer, mount });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PipHoundIntro = api;
  if (typeof document !== "undefined") {
    const start = () => { root.pipHoundIntro = mount(document, root); };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true }); else start();
  }
})(typeof window !== "undefined" ? window : globalThis);
