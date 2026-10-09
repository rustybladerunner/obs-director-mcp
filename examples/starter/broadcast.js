/* Neutral local presentation. Shared data helpers are parity-checked against the tournament renderer. */
"use strict";

function parseUTC(value) {
  if (typeof value !== "string" || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?Z$/.test(value)) throw new Error("Invalid UTC timestamp");
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString().slice(0,19) !== value.slice(0,19)) throw new Error("Invalid calendar timestamp");
  return parsed;
}

function rankRows(snapshot, clockMs, maxAgeSeconds) {
  const snapshotTime = parseUTC(snapshot.as_of);
  if (snapshotTime - clockMs > 5000) throw new Error("Snapshot is ahead of the local clock");
  const staleSnapshot = clockMs - snapshotTime > maxAgeSeconds * 1000;
  const rows = snapshot.participants.map(participant => {
    const missing = participant.observed_at === null || [participant.realized_minor, participant.unrealized_minor, participant.fees_minor].some(value => value === null);
    const observed = participant.observed_at === null ? null : parseUTC(participant.observed_at);
    if (observed !== null && observed > snapshotTime) throw new Error("Participant time follows snapshot time");
    const stale = staleSnapshot || (!missing && clockMs - observed > maxAgeSeconds * 1000);
    return {...participant, eligibility: missing ? "missing" : stale ? "stale" : "ranked",
      net_minor: missing ? null : participant.realized_minor + participant.unrealized_minor - participant.fees_minor,
      rank: null, tied: false};
  });
  const ranked = rows.filter(row => row.eligibility === "ranked").sort((a, b) => b.net_minor - a.net_minor || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  let previous = null;
  let rank = 0;
  const counts = new Map();
  ranked.forEach((row, index) => {
    if (row.net_minor !== previous) rank = index + 1;
    row.rank = rank;
    previous = row.net_minor;
    counts.set(rank, (counts.get(rank) || 0) + 1);
  });
  ranked.forEach(row => { row.tied = counts.get(row.rank) > 1; });
  return {rows, standings: [...ranked, ...rows.filter(row => row.rank === null).sort((a,b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0)], rankedCount: ranked.length};
}

function formatMoney(value, signed = true) {
  if (value === null) return "—";
  return (signed && value > 0 ? "+" : "") + (value / 100).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
}


function buildCandleChart(snapshot, rows = snapshot.participants) {
  if (snapshot.mode !== "demo") return {available: false, reason: "Waiting for supplied OHLC data"};
  const validTrade = row => row.trade && ["long", "short", "flat"].includes(row.trade.direction) &&
    ["entry", "stop", "target"].every(key => typeof row.trade[key] === "number" && Number.isFinite(row.trade[key]) && Math.abs(row.trade[key]) <= 1e9);
  const featured = rows.find(row => row.eligibility === "ranked" && validTrade(row)) || rows.find(validTrade);
  if (!featured) return {available: false, reason: "No declared trade levels · OHLC data unavailable"};
  const trade = featured.trade;
  const span = Math.max(Math.abs(trade.target - trade.stop), Math.abs(trade.entry - trade.stop), Math.abs(trade.entry - trade.target), Math.abs(trade.entry) * .003, 1e-6);
  // Authored, dimensionless fixture: no timestamps, random prices or market feed.
  const closes = [-.18,-.12,-.08,-.10,-.03,.02,-.01,.05,.09,.04,0,-.08,-.13,-.05,.01,.07,
    .12,.10,.16,.20,.14,.08,.11,.18,.24,.19,.13,.09,.03,-.04,-.10,-.16,
    -.11,-.07,.02,.09,.14,.20,.16,.22,.27,.19,.15,.20,.30,.34,.29,.23,
    .26,.32,.38,.34,.31,.25,.19,.22,.30,.35,.39,.32,.28,.36,.41,.37];
  const candles = closes.map((close, index) => {
    const open = index ? closes[index - 1] + ((index % 3) - 1) * .008 : -.21;
    return {open: trade.entry + open * span, close: trade.entry + close * span,
      high: trade.entry + (Math.max(open, close) + .032 + (index % 4) * .009) * span,
      low: trade.entry + (Math.min(open, close) - .030 - (index % 5) * .007) * span};
  });
  const values = candles.flatMap(candle => [candle.low, candle.high]).concat([trade.entry, trade.stop, trade.target]);
  const lower = Math.min(...values), upper = Math.max(...values);
  const padding = Math.max((upper - lower) * .12, span * .06);
  const minimum = lower - padding, maximum = upper + padding;
  const plot = {left: 150, right: 1040, top: 24, bottom: 282};
  const y = value => plot.bottom - (value - minimum) / (maximum - minimum) * (plot.bottom - plot.top);
  const step = (plot.right - plot.left) / candles.length;
  const bars = candles.map((candle, index) => ({...candle, x: plot.left + (index + .5) * step,
    openY: y(candle.open), closeY: y(candle.close), highY: y(candle.high), lowY: y(candle.low), width: step * .62}));
  const levels = ["entry", "stop", "target"].map(kind => ({kind, value: trade[kind], y: y(trade[kind]), labelY: y(trade[kind])})).sort((a,b) => a.y - b.y);
  // Separate labels, not price lines. Leaders retain each line's exact price.
  levels.forEach((level, index) => { level.labelY = Math.max(level.labelY, index ? levels[index - 1].labelY + 26 : plot.top); });
  if (levels.at(-1).labelY > plot.bottom) {
    levels.at(-1).labelY = plot.bottom;
    for (let index = levels.length - 2; index >= 0; index--) levels[index].labelY = Math.min(levels[index].labelY, levels[index + 1].labelY - 26);
  }
  const ticks = Array.from({length: 5}, (_, index) => ({value: maximum - (maximum - minimum) * index / 4,
    y: plot.top + (plot.bottom - plot.top) * index / 4}));
  return {available: true, featured: {id: featured.id, name: featured.name, instrument: trade.instrument,
    direction: trade.direction, eligibility: featured.eligibility || "declared"}, plot, minimum, maximum, bars, levels, ticks};
}

if (typeof module !== "undefined" && module.exports) module.exports = {rankRows, formatMoney, parseUTC, buildCandleChart};

if (typeof document !== "undefined") {
  const bundle = JSON.parse(document.getElementById("tournament-data").textContent);
  const snapshot = bundle.snapshot;
  const stage = document.getElementById("stage");
  const view = document.body.dataset.view;
  const demonstration = snapshot.mode === "demo";
  const svgNS = "http://www.w3.org/2000/svg";
  let lastState = "";
  stage.dataset.view = view;

  function node(tag, className, text, parent) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = text;
    if (parent) parent.appendChild(element);
    return element;
  }
  function svgNode(tag, attributes, parent, text) {
    const element = document.createElementNS(svgNS, tag);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    if (text !== undefined) element.textContent = text;
    if (parent) parent.appendChild(element);
    return element;
  }
  function portrait(parent, large = false, participant = snapshot.participants[0]) {
    const box = node("div", large ? "intermission-portrait" : "portrait", null, parent);
    const initials = participant.name.split(/\s+/).map(word => word[0]).join("").slice(0,2).toUpperCase();
    const letters = node("div", "initials", large ? null : initials, box);
    if (large) node("span", "", initials, letters);
    return box;
  }
  function header(result) {
    const top = node("header", "topbar", null, stage);
    const brand = node("div", "brand", null, top);
    const titles = node("div", "brand-titles", null, brand);
    node("div", "eyebrow", "YOUR CHANNEL", titles);
    node("h1", "", "SESSION DESK", titles);
    const status = node("div", "top-status", null, top);
    node("div", "session-label", snapshot.label, status);
    node("div", "pill", demonstration ? "DEMO" : "PAPER", status);
    node("div", "clock", "", status).id = "clock";
    const line = node("div", "section-label", null, stage);
    node("strong", "", snapshot.stage.toUpperCase() + "  /  ROUND " + snapshot.round, line);
    node("span", "", "NET P&L · " + snapshot.currency + " · AFTER FEES", line);
    node("span", "field-count", result.rankedCount + " OF " + snapshot.participants.length + " RANKED", line);
    if (result.rankedCount < snapshot.participants.length) node("div", "stale-banner", "STALE / MISSING · UNRANKED", stage);
    const footer = node("footer", "footer", null, stage);
    node("span", "", demonstration ? "ILLUSTRATIVE VALUES · NO REAL TRADES" : "SUPPLIED PAPER VALUES · UNVERIFIED SOURCE", footer);
    node("span", "", "AS OF " + snapshot.as_of.replace("T", " ").replace("Z", " UTC"), footer);
  }
  function chart(result, replay = false) {
    const panel = node("section", "chart-panel" + (replay ? " replay-panel" : ""), null, stage);
    const heading = node("div", "chart-heading", null, panel);
    const data = buildCandleChart(snapshot, result.rows);
    const identity = data.available ? data.featured.name + " · " + data.featured.instrument + " · " + data.featured.direction.toUpperCase() +
      (["stale", "missing"].includes(data.featured.eligibility) ? " · " + data.featured.eligibility.toUpperCase() : "") : "THE SHARED BOARD";
    node("strong", "", identity, heading);
    node("span", "", data.available ? "DEMO OHLC · DECLARED LEVELS" : "CHART UNAVAILABLE", heading);
    if (!data.available) {
      node("div", "chart-empty", data.reason, panel);
      return;
    }
    const svg = svgNode("svg", {viewBox: "0 0 1400 338", class: "chart", role: "img",
      "aria-label": "64 illustrative synthetic OHLC candles with declared levels for " + data.featured.name + "; not market data"}, panel);
    const colors = {up: "var(--gold, #d9b56c)", down: "#929ba9", entry: "#eee6d5", stop: "#aa9297", target: "var(--gold, #d9b56c)"};
    const number = value => Math.abs(value) >= 1e7 || (value !== 0 && Math.abs(value) < .001) ? value.toExponential(5) : value.toLocaleString("en-US", {maximumSignificantDigits: 7});
    data.ticks.forEach(tick => {
      svgNode("line", {x1: data.plot.left, x2: data.plot.right, y1: tick.y, y2: tick.y, class: "chart-grid"}, svg);
      svgNode("text", {x: 132, y: tick.y + 5, class: "chart-axis", "text-anchor": "end"}, svg, number(tick.value));
    });
    [0,15,31,47,63].forEach(index => {
      const x = data.bars[index].x;
      svgNode("line", {x1: x, x2: x, y1: data.plot.top, y2: data.plot.bottom, class: "chart-grid"}, svg);
      svgNode("text", {x, y: 307, class: "chart-axis", "text-anchor": "middle"}, svg, String(index + 1));
    });
    data.bars.forEach(bar => {
      const color = bar.close >= bar.open ? colors.up : colors.down;
      svgNode("line", {x1: bar.x, x2: bar.x, y1: bar.highY, y2: bar.lowY, stroke: color, "stroke-width": 1.7}, svg);
      svgNode("rect", {x: bar.x - bar.width / 2, y: Math.min(bar.openY, bar.closeY), width: bar.width,
        height: Math.max(1.5, Math.abs(bar.closeY - bar.openY)), fill: color, "fill-opacity": bar.close >= bar.open ? .95 : .38,
        stroke: color, "stroke-width": 1}, svg);
    });
    data.levels.forEach(level => {
      const color = colors[level.kind];
      const label = level.kind.toUpperCase() + " " + String(level.value);
      svgNode("line", {x1: data.plot.left, x2: data.plot.right, y1: level.y, y2: level.y, stroke: color,
        "stroke-width": 1.4, "stroke-dasharray": level.kind === "entry" ? "7 4" : "3 5", "stroke-opacity": .88}, svg);
      svgNode("path", {d: "M " + data.plot.right + " " + level.y + " L 1072 " + level.y + " L 1090 " + level.labelY,
        fill: "none", stroke: color, "stroke-width": 1}, svg);
      svgNode("rect", {x: 1092, y: level.labelY - 11, width: 306, height: 22, rx: 2, fill: "#121212", stroke: color, "stroke-opacity": .35}, svg);
      svgNode("text", {x: 1101, y: level.labelY + 5, fill: color, "font-family": "Consolas,monospace", "font-size": Math.min(16, 288 / (label.length * .62))}, svg, label);
    });
    svgNode("text", {x: 0, y: 333, class: "chart-note"}, svg, "64 SYNTHETIC BARS · NOT MARKET DATA");
    svgNode("text", {x: 1398, y: 333, class: "chart-note", "text-anchor": "end"}, svg, "LEVELS IN SUPPLIED POINTS · DECLARED");
  }
  function rankLabel(participant) {
    return participant.rank === null ? "UNRANKED · " + participant.eligibility.toUpperCase() : (participant.tied ? "TIED · " : "") + "RANK " + participant.rank;
  }
  function card(participant, index, comparison = false) {
    const cardElement = node("section", "card " + (index === 0 ? "left" : "right") + (comparison ? " comparison-card" : ""), null, stage);
    portrait(cardElement, false, participant);
    const content = node("div", "card-content", null, cardElement);
    const head = node("div", "card-head", null, content);
    const seat = node("div", "seat", null, head);
    node("span", "", participant.rank === null ? "—" : (participant.tied ? "=" : "") + participant.rank, seat);
    const identity = node("div", "identity", null, head);
    node("div", "participant-name", participant.name, identity);
    node("div", "participant-status", rankLabel(participant) + " · " + participant.status.toUpperCase(), identity);
    const body = node("div", "pnl-block", null, content);
    node("div", "pnl-label", "SESSION NET · " + snapshot.currency, body);
    const text = participant.rank === null ? participant.eligibility.toUpperCase() : formatMoney(participant.net_minor);
    const amount = node("div", "money" + (participant.rank === null ? " unranked" : participant.net_minor < 0 ? " loss" : ""), text, body);
    if (participant.rank !== null) amount.style.fontSize = (text.length > 14 ? 50 : text.length > 11 ? 68 : 96) + "px";
    if (comparison) {
      const components = node("div", "components", null, cardElement);
      [["REALIZED", "realized_minor"], ["UNREALIZED", "unrealized_minor"], ["FEES", "fees_minor"]].forEach(([label, key]) => {
        const component = node("div", "", null, components);
        node("small", "", label, component);
        node("strong", "", participant.rank === null ? "—" : formatMoney(participant[key], key !== "fees_minor"), component);
      });
    }
    const trade = node("div", "trade-line", null, cardElement);
    if (participant.trade) {
      node("span", "trade-direction", participant.trade.direction.toUpperCase() + " · " + participant.trade.instrument, trade);
      node("span", "", "E " + participant.trade.entry + "  S " + participant.trade.stop + "  T " + participant.trade.target + " PTS · DECLARED", trade);
    } else {
      node("span", "trade-direction", "NO DECLARED POSITION", trade);
      node("span", "", "FEES " + formatMoney(participant.fees_minor, false) + " " + snapshot.currency, trade);
    }
  }
  function table(result) {
    chart(result);
    result.rows.slice(0,2).forEach((participant, index) => card(participant, index));
    const divider = node("div", "versus", "VS", stage);
    divider.setAttribute("aria-label", "Two participants in the same session");
  }
  function standings(result) {
    const title = node("section", "standings-head", null, stage);
    node("h2", "", "Leaderboard.", title);
    node("p", "", "SESSION NET · " + snapshot.currency + " · AFTER FEES · " + (demonstration ? "DEMO" : "PAPER"), title);
    if (result.rows.length === 2) {
      result.standings.forEach((participant, index) => card(participant, index, true));
      node("div", "standings-note", "NET = REALIZED + UNREALIZED − FEES · EQUAL RESULTS SHARE A RANK", stage);
      return;
    }
    const tableElement = node("table", "standings-table" + (result.rows.length > 5 ? " dense" : ""), null, stage);
    const headings = node("tr", "", null, node("thead", "", null, tableElement));
    ["RANK", "PARTICIPANT", "REALIZED", "UNREALIZED", "FEES", "NET / " + snapshot.currency].forEach(text => node("th", "", text, headings));
    const body = node("tbody", "", null, tableElement);
    result.standings.forEach(participant => {
      const row = node("tr", "", null, body);
      const place = node("td", "", participant.rank === null ? "—" : (participant.tied ? "=" : "") + participant.rank, row);
      if (participant.rank === 1) { place.style.color = "var(--gold, #d9b56c)"; place.style.fontWeight = "700"; }
      const identity = node("td", "row-name", null, row);
      node("span", "", participant.name, identity);
      node("small", "", participant.status.toUpperCase() + " · " + (participant.rank === null ? participant.eligibility.toUpperCase() : participant.tied ? "TIED RANK " + participant.rank : "RANK " + participant.rank), identity);
      node("small", "", participant.observed_at === null ? "OBSERVED UNKNOWN" : "OBSERVED " + participant.observed_at.replace("T", " ").replace("Z", " UTC"), identity);
      ["realized_minor", "unrealized_minor", "fees_minor"].forEach(key => node("td", "", participant.rank === null ? "—" : formatMoney(participant[key], key !== "fees_minor"), row));
      node("td", "net" + (participant.rank === null ? " unavailable" : participant.net_minor < 0 ? " negative" : ""), participant.rank === null ? participant.eligibility.toUpperCase() : formatMoney(participant.net_minor), row);
    });
    node("div", "standings-note", "NET = REALIZED + UNREALIZED − FEES · EQUAL RESULTS SHARE A RANK (1, 1, 3)", stage);
  }
  function replay(result) {
    const title = node("section", "replay-title", null, stage);
    node("h2", "", "The moment, revisited.", title);
    node("div", "replay-mark", "ILLUSTRATIVE REPLAY", title);
    chart(result, true);
    const caption = node("div", "replay-caption", null, stage);
    node("span", "", "NOT A RECORDED EXECUTION", caption);
    node("span", "", "REFERENCE " + snapshot.as_of.replace("T", " ").replace("Z", " UTC"), caption);
    const note = node("section", "replay-note", null, stage);
    portrait(note);
    const explanation = node("div", "", null, note);
    node("div", "eyebrow", "THE REVIEW", explanation);
    node("h2", "", "A closer look.", explanation);
    node("p", "", "The shared board. A different perspective.", explanation);
  }
  function intermission(result) {
    const section = node("section", "intermission", null, stage);
    const copy = node("div", "intermission-copy", null, section);
    node("div", "eyebrow", view === "starting-soon" ? "TAKE YOUR SEAT" : view === "break" ? "BACK AT THE TABLE SOON" : "UNTIL NEXT SESSION", copy);
    const title = view === "starting-soon" ? "The table\nis waiting." : view === "break" ? "A moment\naway." : "That's\nthe session.";
    node("h2", "", title, copy);
    node("p", "subhead", view === "starting-soon" ? "Starting soon. " + snapshot.participants.length + " seats. One shared board." : view === "break" ? "Taking a short break. Stay with us." : "Thanks for being part of the session.", copy);
    node("div", "intermission-rule", null, copy);
    if (view === "ending") {
      const resultBox = node("div", "ending-result", null, copy);
      const allComplete = result.rankedCount === result.rows.length && result.rows.every(row => row.status === "complete");
      node("small", "", allComplete ? "FINAL SUPPLIED SNAPSHOT · NET AFTER FEES" : "LATEST SNAPSHOT · NOT FINAL", resultBox);
      if (!result.rankedCount) node("span", "result-pending", "No fresh ranked result", resultBox);
      else {
        const first = result.standings[0];
        node("span", "result-leader", (first.tied ? "SHARED LEAD · " : "LEADING · ") + first.name, resultBox);
        const amount = node("strong", first.net_minor < 0 ? "loss" : "", formatMoney(first.net_minor) + " " + snapshot.currency, resultBox);
        if (amount.textContent.length > 19) amount.style.fontSize = "34px";
      }
    }
    const art = node("div", "intermission-art", null, section);
    node("div", "orbit orbit-outer", null, art);
    node("div", "orbit orbit-inner", null, art);
    node("div", "art-wordmark", "SESSION", art);
    portrait(art, true);
    node("div", "portrait-signature", snapshot.participants[0].name, art);
  }
  function resize() {
    const scale = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    stage.style.transform = "translate(-50%, -50%) scale(" + scale + ")";
  }
  function render() {
    let result;
    try { result = rankRows(snapshot, Date.now(), bundle.max_age_seconds); }
    catch {
      if (lastState !== "invalid") {
        stage.replaceChildren();
        const section = node("section", "intermission", null, stage);
        const copy = node("div", "intermission-copy", null, section);
        node("h2", "", "Snapshot unavailable.", copy);
        node("p", "subhead", "Results are unavailable while the source time cannot be confirmed.", copy);
        lastState = "invalid";
      }
      return;
    }
    const state = result.rows.map(row => row.eligibility).join("|");
    if (state !== lastState) {
      stage.replaceChildren();
      header(result);
      if (view === "table") table(result);
      else if (view === "standings") standings(result);
      else if (view === "replay") replay(result);
      else intermission(result);
      lastState = state;
    }
    document.getElementById("clock").textContent = new Date().toISOString().slice(11,19) + " UTC";
  }
  window.addEventListener("resize", resize);
  resize();
  render();
  setInterval(render, 1000);
}
