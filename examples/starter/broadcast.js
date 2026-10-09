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

if (typeof module !== "undefined" && module.exports) module.exports = {rankRows, formatMoney, parseUTC};

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
  function chart(replay = false) {
    const panel = node("section", "chart-panel" + (replay ? " replay-panel" : ""), null, stage);
    const heading = node("div", "chart-heading", null, panel);
    node("strong", "", "THE SHARED BOARD", heading);
    node("span", "", demonstration ? "ILLUSTRATIVE · POINTS" : "CHART UNAVAILABLE", heading);
    if (!demonstration) {
      node("div", "chart-empty", "Waiting for a supplied chart", panel);
      return;
    }
    const svg = svgNode("svg", {viewBox: "0 0 1400 338", class: "chart", role: "img", "aria-label": "Illustrative synthetic chart; not market data"}, panel);
    const defs = svgNode("defs", {}, svg);
    const gradient = svgNode("linearGradient", {id: "chart-fill", x1: "0", y1: "0", x2: "0", y2: "1"}, defs);
    svgNode("stop", {offset: "0%", "stop-color": "#bdc5d0", "stop-opacity": ".24"}, gradient);
    svgNode("stop", {offset: "100%", "stop-color": "#bdc5d0", "stop-opacity": "0"}, gradient);
    [32,96,160,224,288].forEach((y, index) => {
      svgNode("line", {x1: 0, x2: 1308, y1: y, y2: y, class: "chart-grid"}, svg);
      svgNode("text", {x: 1324, y: y + 5, class: "chart-axis"}, svg, (5148 - index * 8).toFixed(2));
    });
    [0,216,432,648,864,1080,1296].forEach(x => svgNode("line", {x1: x, x2: x, y1: 22, y2: 288, class: "chart-grid"}, svg));
    const values = [238,229,243,208,212,221,195,206,172,180,191,153,156,161,124,138,112,147,124,103,124,97,107,74,91,68,80,42,66,61,88,69,83,65,79,54];
    const points = values.map((y, index) => [16 + index * 36.4, y]);
    const path = "M " + points.map(([x,y]) => x.toFixed(1) + " " + y).join(" L ");
    svgNode("path", {d: path + " L 1290 288 L 16 288 Z", class: "chart-area"}, svg);
    svgNode("path", {d: path, class: "chart-line"}, svg);
    svgNode("circle", {cx: points.at(-1)[0], cy: points.at(-1)[1], r: 6, fill: "#d6dce4"}, svg);
    svgNode("text", {x: 0, y: 328, class: "chart-note"}, svg, "STATIC ILLUSTRATION");
    svgNode("text", {x: 1136, y: 328, class: "chart-note"}, svg, "NOT MARKET DATA");
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
    chart();
    result.rows.slice(0,2).forEach((participant, index) => card(participant, index));
    const divider = node("div", "versus", "VS", stage);
    divider.setAttribute("aria-label", "Two participants in the same session");
  }
  function standings(result) {
    const title = node("section", "standings-head", null, stage);
    node("h2", "", result.rows.length === 2 ? "Head to head." : "The standings.", title);
    node("p", "", "SESSION RESULTS", title);
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
      node("td", "", participant.rank === null ? "—" : (participant.tied ? "=" : "") + participant.rank, row);
      const identity = node("td", "row-name", null, row);
      node("span", "", participant.name, identity);
      node("small", "", participant.rank === null ? participant.eligibility.toUpperCase() : participant.tied ? "TIED RANK " + participant.rank : participant.status.toUpperCase(), identity);
      ["realized_minor", "unrealized_minor", "fees_minor"].forEach(key => node("td", "", participant.rank === null ? "—" : formatMoney(participant[key], key !== "fees_minor"), row));
      node("td", "net" + (participant.rank === null ? " unavailable" : participant.net_minor < 0 ? " negative" : ""), participant.rank === null ? participant.eligibility.toUpperCase() : formatMoney(participant.net_minor), row);
    });
    node("div", "standings-note", "NET = REALIZED + UNREALIZED − FEES · EQUAL RESULTS SHARE A RANK (1, 1, 3)", stage);
  }
  function replay() {
    const title = node("section", "replay-title", null, stage);
    node("h2", "", "The moment, revisited.", title);
    node("div", "replay-mark", "ILLUSTRATIVE REPLAY", title);
    chart(true);
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
    node("p", "subhead", view === "starting-soon" ? "Starting soon. Two seats. One shared board." : view === "break" ? "Taking a short break. Stay with us." : "Thanks for being part of the session.", copy);
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
      else if (view === "replay") replay();
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
