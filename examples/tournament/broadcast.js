/* Original local renderer. Snapshot values are text, never HTML or scripts. */
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
    if (bundle.portrait && participant.id === "piphound") {
      const image = node("img", "", null, box);
      image.src = "presenter.png";
      image.alt = "PipHound, original illustrated presenter";
    } else {
      const initials = participant.name.split(/\s+/).map(word => word[0]).join("").slice(0,2).toUpperCase();
      const letters = node("div", "initials", large ? null : initials, box);
      if (large) node("span", "", initials, letters);
    }
    return box;
  }
  function header(result) {
    const top = node("header", "topbar", null, stage);
    const brand = node("div", "brand", null, top);
    node("div", "monogram", "FD", brand);
    const titles = node("div", "", null, brand);
    node("div", "eyebrow", "FUNDED DESK / TOURNAMENT", titles);
    node("h1", "", snapshot.label, titles);
    const status = node("div", "top-status", null, top);
    node("div", "pill", demonstration ? "SYNTHETIC DEMO" : "PAPER SNAPSHOT", status);
    node("div", "clock", "", status).id = "clock";
    const line = node("div", "section-label", null, stage);
    node("strong", "", snapshot.stage.toUpperCase() + " / ROUND " + snapshot.round, line);
    node("span", "", snapshot.currency + " • NET P&L AFTER FEES", line);
    node("span", "", result.rankedCount + " / " + snapshot.participants.length + " RANKED", line);
    if (result.rankedCount < snapshot.participants.length) node("div", "stale-banner", "MISSING / STALE VALUES ARE UNRANKED", stage);
    const footer = node("footer", "footer", null, stage);
    node("span", "", demonstration ? "SYNTHETIC VALUES • NO REAL TRADES OR RESULTS" : "CALLER-SUPPLIED PAPER VALUES • ORIGIN NOT AUTHENTICATED", footer);
    node("span", "", "SNAPSHOT " + snapshot.as_of + " • FRESHNESS " + bundle.max_age_seconds + "s", footer);
  }
  function chart(replay = false) {
    const panel = node("section", "chart-panel" + (replay ? " replay-panel" : ""), null, stage);
    const heading = node("div", "chart-heading", null, panel);
    node("strong", "", demonstration ? "SHARED BOARD / DEMO POINTS" : "SHARED BOARD / NO SERIES PROVIDED", heading);
    node("span", "", "ILLUSTRATIVE PATH • NOT EXECUTIONS", heading);
    if (!demonstration) {
      node("div", "chart-empty", "No chart series supplied", panel);
      return;
    }
    const svg = svgNode("svg", {viewBox: "0 0 1400 338", class: "chart", role: "img", "aria-label": "Illustrative synthetic chart; not market data"}, panel);
    const defs = svgNode("defs", {}, svg);
    const gradient = svgNode("linearGradient", {id: "chart-fill", x1: "0", y1: "0", x2: "0", y2: "1"}, defs);
    svgNode("stop", {offset: "0%", "stop-color": "#bd852e", "stop-opacity": ".2"}, gradient);
    svgNode("stop", {offset: "100%", "stop-color": "#bd852e", "stop-opacity": "0"}, gradient);
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
    svgNode("circle", {cx: points.at(-1)[0], cy: points.at(-1)[1], r: 6, fill: "#d4ad65"}, svg);
    svgNode("text", {x: 0, y: 328, class: "chart-note"}, svg, "SYNTHETIC PATH / STATIC REFERENCE");
    svgNode("text", {x: 1056, y: 328, class: "chart-note"}, svg, "POINTS • NOT CURRENCY");
  }
  function card(participant, index) {
    const cardElement = node("section", "card " + (index === 0 ? "left" : "right"), null, stage);
    const head = node("div", "card-head", null, cardElement);
    const seat = node("div", "seat", null, head);
    node("span", "", participant.rank === null ? "—" : (participant.tied ? "=" : "") + participant.rank, seat);
    const identity = node("div", "", null, head);
    node("div", "participant-name", participant.name, identity);
    node("div", "participant-status", (participant.rank === null ? "UNRANKED / " + participant.eligibility : participant.tied ? "TIED RANK " + participant.rank : "RANK " + participant.rank) + " · " + participant.status.toUpperCase() + (participant.trade ? " · " + participant.trade.instrument : ""), identity);
    const pnl = node("div", "pnl-row", null, cardElement);
    portrait(pnl, false, participant);
    const body = node("div", "pnl-block", null, pnl);
    node("div", "pnl-label", "SESSION NET P&L / " + snapshot.currency, body);
    const text = participant.rank === null ? participant.eligibility.toUpperCase() : formatMoney(participant.net_minor);
    const amount = node("div", "money" + (participant.rank === null ? " unranked" : participant.net_minor < 0 ? " loss" : ""), text, body);
    if (participant.rank !== null) amount.style.fontSize = (text.length > 14 ? 56 : text.length > 11 ? 76 : 104) + "px";
    const trade = node("div", "trade-line", null, cardElement);
    if (participant.trade) {
      node("span", "trade-direction", participant.trade.direction.toUpperCase() + " / DECLARED", trade);
      node("span", "", "E " + participant.trade.entry + " · S " + participant.trade.stop + " · T " + participant.trade.target + " PTS", trade);
    } else {
      node("span", "trade-direction", "NO TRADE CARD", trade);
      node("span", "", "Realized " + formatMoney(participant.realized_minor) + " / fees " + formatMoney(participant.fees_minor, false), trade);
    }
  }
  function table(result) {
    chart();
    result.rows.slice(0,2).forEach(card);
    const summary = node("section", "center-summary", null, stage);
    const diamond = node("div", "diamond", null, summary);
    node("span", "", "VS", diamond);
    node("h2", "", "SAME SESSION", summary);
    node("p", "", "NET = REALIZED + UNREALIZED − FEES", summary);
    node("h2", "", "RISK / POINTS", summary);
    node("p", "", "LEVELS ARE DECLARED", summary);
  }
  function standings(result) {
    const title = node("section", "standings-head", null, stage);
    node("h2", "", "Session standings", title);
    node("p", "", "Same session. Same currency. Net P&L after fees.", title);
    portrait(node("aside", "standings-portrait", null, stage));
    const tableElement = node("table", "standings-table" + (result.rows.length > 5 ? " dense" : ""), null, stage);
    const headings = node("tr", "", null, node("thead", "", null, tableElement));
    ["RANK", "PARTICIPANT", "REALIZED", "UNREALIZED", "FEES", "NET / " + snapshot.currency].forEach(text => node("th", "", text, headings));
    const body = node("tbody", "", null, tableElement);
    result.standings.forEach(participant => {
      const row = node("tr", "", null, body);
      node("td", "", participant.rank === null ? "—" : (participant.tied ? "=" : "") + participant.rank, row);
      node("td", "row-name", participant.name, row);
      ["realized_minor", "unrealized_minor", "fees_minor"].forEach(key => node("td", "", participant.rank === null ? "—" : formatMoney(participant[key], key !== "fees_minor"), row));
      node("td", "net" + (participant.rank === null ? " unavailable" : participant.net_minor < 0 ? " negative" : ""), participant.rank === null ? participant.eligibility.toUpperCase() : formatMoney(participant.net_minor), row);
    });
    node("div", "standings-note", "TIES SHARE A RANK (1, 1, 3). PARTICIPANT ID ORDERS TIED ROWS; IT DOES NOT BREAK THE TIE.", stage);
  }
  function replay() {
    node("div", "replay-mark", "ILLUSTRATIVE REPLAY", stage);
    chart(true);
    const caption = node("div", "replay-caption", null, stage);
    node("span", "", "SNAPSHOT " + snapshot.as_of, caption);
    node("span", "", "NOT A RECORDED EXECUTION", caption);
    const note = node("section", "replay-note", null, stage);
    portrait(note);
    const explanation = node("div", "", null, note);
    node("h2", "", "The decision, in focus.", explanation);
    node("p", "", "A static illustrative chart for scene design. Replace this scene with a verified local recording for actual replay review.", explanation);
  }
  function intermission(result) {
    const section = node("section", "intermission", null, stage);
    const copy = node("div", "intermission-copy", null, section);
    node("div", "eyebrow", "THE TOURNAMENT DESK", copy);
    const title = view === "starting-soon" ? "Starting soon." : view === "break" ? "Taking a short break." : "Thanks for watching.";
    node("h2", "", title, copy);
    node("p", "subhead", view === "starting-soon" ? "Two seats. One shared board. Every result in view." : view === "break" ? "The desk will return. This screen does not predict a return time." : "The session snapshot remains below. No winner is inferred from an unfinished or stale session.", copy);
    node("div", "intermission-rule", null, copy);
    if (view === "ending") {
      const resultBox = node("div", "ending-result", null, copy);
      const allComplete = result.rankedCount === result.rows.length && result.rows.every(row => row.status === "complete");
      node("small", "", allComplete ? "FINAL SUPPLIED SNAPSHOT / NET AFTER FEES" : "LATEST SUPPLIED SNAPSHOT / NOT A FINAL RESULT", resultBox);
      if (!result.rankedCount) node("span", "", "No fresh ranked result", resultBox);
      else {
        const first = result.standings[0];
        node("span", "", (first.tied ? "SHARED LEAD / " : "LEADING / ") + first.name + " " + formatMoney(first.net_minor) + " " + snapshot.currency, resultBox);
      }
    }
    portrait(section, true);
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
        node("p", "subhead", "The timestamp or local clock is invalid. Generate a new validated bundle.", copy);
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
