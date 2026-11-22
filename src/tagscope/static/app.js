/* TagScope dashboard. Plain JS, no build step. */
(() => {
  "use strict";

  const PALETTE = ["#38d9b0", "#6aa8ff", "#f5b13d", "#c792ea", "#ff7a90", "#7fdbff", "#b5e48c", "#ffb86b"];
  const SPARK_SECONDS = 300;
  const MAX_CARDS = 12;
  const $ = (sel, el = document) => el.querySelector(sel);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("tagscope." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("tagscope." + k, JSON.stringify(v)); } catch { /* private mode */ } },
  };

  const S = {
    tags: new Map(),      // name -> meta
    values: {},           // name -> {value, ts, quality, state}
    servers: [],
    selected: [],         // ordered tag names on the trend
    colors: new Map(),    // name -> colour
    minutes: store.get("minutes", 15),
    live: true,
    sparks: new Map(),    // name -> {t: [], v: []}
    events: [],
    eventFilter: "",
    samples: { n: 0, at: 0, rate: 0 },
  };

  // ---------------------------------------------------------------- utils
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isBool = (name) => { const v = S.values[name]; return S.tags.get(name)?.unit === "" && v && (v.value === 0 || v.value === 1) && /running|on|open|alarm|fault|enabled|active/i.test(name); };
  function fmt(v, unit = "") {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    const a = Math.abs(v);
    const d = a >= 1000 ? 0 : a >= 100 ? 1 : a >= 10 ? 2 : 2;
    return v.toLocaleString(undefined, { minimumFractionDigits: unit === "pcs" ? 0 : Math.min(d, 1), maximumFractionDigits: unit === "pcs" ? 0 : d });
  }
  const hhmmss = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
  function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 1800); }
  const groupOf = (name) => (name.includes(".") ? name.split(".")[0] : name.includes("/") ? name.split("/")[0] : "");
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  async function api(path) { const r = await fetch(path); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText); return r.json(); }

  // ---------------------------------------------------------------- theme
  function applyTheme(t) { document.documentElement.dataset.theme = t; store.set("theme", t); if (chart) rebuildChart(); drawAllSparks(); }
  const prefersLight = window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches;
  document.documentElement.dataset.theme = store.get("theme", prefersLight ? "light" : "dark");
  $("#theme").onclick = () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");

  // ---------------------------------------------------------------- servers + stats
  function renderServers() {
    $("#servers").innerHTML = S.servers.map((s) =>
      `<span class="server" title="${esc(s.endpoint)}${s.error ? "\n" + esc(s.error) : ""}"><i class="dot ${esc(s.status)}"></i><b>${esc(s.name)}</b>${esc(s.status)}</span>`).join("");
  }
  setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString([], { hour12: false }); }, 1000);
  async function pollStats() {
    try {
      const o = await api("/api/overview");
      const n = o.stats.samples_written, now = performance.now();
      if (S.samples.at) S.samples.rate = ((n - S.samples.n) / ((now - S.samples.at) / 1000));
      S.samples = { n, at: now, rate: S.samples.rate };
      $("#stat-samples").textContent = n.toLocaleString();
      $("#stat-rate").textContent = Math.max(0, S.samples.rate).toFixed(1);
    } catch { /* offline */ }
  }

  // ---------------------------------------------------------------- tag list
  function colorFor(name) {
    if (!S.colors.has(name)) {
      const used = new Set(S.colors.values());
      S.colors.set(name, PALETTE.find((c) => !used.has(c)) || PALETTE[S.colors.size % PALETTE.length]);
    }
    return S.colors.get(name);
  }
  function renderTagList() {
    const q = $("#search").value.trim().toLowerCase();
    let html = "", lastGroup = null;
    for (const t of S.tags.values()) {
      if (q && !(t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q) || t.node.toLowerCase().includes(q))) continue;
      const g = groupOf(t.name);
      if (g !== lastGroup) { html += g ? `<div class="group">${esc(g)}</div>` : ""; lastGroup = g; }
      const sel = S.selected.includes(t.name);
      html += `<button class="tag" role="listitem" data-tag="${esc(t.name)}" title="${esc(t.description || t.node)}">
        <span class="swatch" style="${sel ? `background:${colorFor(t.name)}` : ""}"></span>
        <span class="tag-name">${esc(g ? t.name.slice(g.length + 1) : t.name)}</span>
        <span class="tag-value"></span></button>`;
    }
    $("#taglist").innerHTML = html || `<p class="hint">No tags match “${esc(q)}”.</p>`;
    for (const name of S.tags.keys()) paintTag(name);
  }
  function paintTag(name, flash) {
    const el = $(`.tag[data-tag="${CSS.escape(name)}"]`);
    if (!el) return;
    const v = S.values[name], meta = S.tags.get(name);
    el.classList.toggle("selected", S.selected.includes(name));
    el.classList.remove("high", "low", "bad");
    if (v) {
      if (v.state === "high" || v.state === "low") el.classList.add(v.state);
      if (v.quality === 2) el.classList.add("bad");
    }
    const ve = el.lastElementChild;
    ve.textContent = !v ? "–" : isBool(name) ? (v.value ? "ON" : "OFF") : `${fmt(v.value, meta.unit)} ${meta.unit}`.trim();
    if (flash) { ve.classList.add("flash"); clearTimeout(ve._t); ve._t = setTimeout(() => ve.classList.remove("flash"), 350); }
  }
  $("#taglist").addEventListener("click", (e) => {
    const b = e.target.closest(".tag"); if (!b) return;
    toggleSelected(b.dataset.tag);
  });
  $("#search").addEventListener("input", renderTagList);
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== $("#search")) { e.preventDefault(); $("#search").focus(); }
    if (e.key === "Escape" && document.activeElement === $("#search")) { $("#search").value = ""; renderTagList(); $("#search").blur(); }
  });
  function toggleSelected(name) {
    const i = S.selected.indexOf(name);
    if (i >= 0) { S.selected.splice(i, 1); S.colors.delete(name); }
    else { if (S.selected.length >= 8) { toast("Up to 8 tags on one trend"); return; } S.selected.push(name); colorFor(name); }
    store.set("selected", S.selected);
    renderTagList();
    loadTrend();
  }

  // ---------------------------------------------------------------- cards
  function cardTags() {
    const all = [...S.tags.values()];
    const withLimits = all.filter((t) => t.low !== null || t.high !== null);
    const rest = all.filter((t) => !withLimits.includes(t));
    return [...withLimits, ...rest].slice(0, MAX_CARDS).map((t) => t.name);
  }
  function renderCards() {
    $("#cards").innerHTML = cardTags().map((name) => {
      const t = S.tags.get(name);
      return `<article class="card" data-tag="${esc(name)}" title="${esc(t.description || t.node)}">
        <div class="card-top"><span class="card-name">${esc(name)}</span><span class="state hidden"></span></div>
        <div class="card-value">–</div>
        <canvas class="spark"></canvas>
        ${t.low !== null || t.high !== null ? `<div class="limits"><div class="band"></div><div class="pos"></div></div>` : ""}
      </article>`;
    }).join("");
    for (const name of cardTags()) paintCard(name);
  }
  $("#cards").addEventListener("click", (e) => { const c = e.target.closest(".card"); if (c) toggleSelected(c.dataset.tag); });

  function paintCard(name) {
    const el = $(`.card[data-tag="${CSS.escape(name)}"]`);
    if (!el) return;
    const t = S.tags.get(name), v = S.values[name];
    el.classList.remove("high", "low", "bad");
    const st = $(".state", el);
    st.className = "state hidden";
    if (!v) return;
    if (v.state === "high" || v.state === "low") { el.classList.add(v.state); st.className = `state ${v.state}`; st.textContent = v.state; }
    if (v.quality === 2) el.classList.add("bad");
    const valueEl = $(".card-value", el);
    if (isBool(name)) {
      valueEl.innerHTML = v.value ? "Running" : "Stopped";
      st.className = `state ${v.value ? "on" : "off"}`; st.textContent = v.value ? "on" : "off";
    } else {
      valueEl.innerHTML = `${fmt(v.value, t.unit)}<small>${esc(t.unit)}</small>`;
    }
    const lim = $(".limits", el);
    if (lim && v.value !== null) {
      const sp = S.sparks.get(name);
      const lo = t.low ?? Math.min(...(sp?.v.length ? sp.v : [0]));
      const hi = t.high ?? Math.max(...(sp?.v.length ? sp.v : [1]));
      const pad = (hi - lo) * 0.25 || 1;
      const min = lo - pad, max = hi + pad, pct = (x) => Math.min(100, Math.max(0, ((x - min) / (max - min)) * 100));
      const band = $(".band", lim);
      band.style.left = `${t.low !== null ? pct(t.low) : 0}%`;
      band.style.right = `${100 - (t.high !== null ? pct(t.high) : 100)}%`;
      $(".pos", lim).style.left = `calc(${pct(v.value)}% - 1px)`;
    }
    drawSpark(name);
  }

  function pushSpark(name, ts, value) {
    if (value === null || value === undefined) return;
    let sp = S.sparks.get(name);
    if (!sp) S.sparks.set(name, (sp = { t: [], v: [] }));
    sp.t.push(ts); sp.v.push(value);
    const cutoff = ts - SPARK_SECONDS;
    while (sp.t.length && sp.t[0] < cutoff) { sp.t.shift(); sp.v.shift(); }
  }
  function drawSpark(name) {
    const el = $(`.card[data-tag="${CSS.escape(name)}"] canvas`);
    const sp = S.sparks.get(name);
    if (!el || !sp || sp.v.length < 2) return;
    const dpr = window.devicePixelRatio || 1, w = el.clientWidth, h = el.clientHeight;
    if (el.width !== w * dpr) { el.width = w * dpr; el.height = h * dpr; }
    const ctx = el.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const now = Date.now() / 1000, t0 = now - SPARK_SECONDS;
    let lo = Math.min(...sp.v), hi = Math.max(...sp.v);
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const x = (t) => ((t - t0) / SPARK_SECONDS) * w, y = (v) => h - 3 - ((v - lo) / (hi - lo)) * (h - 6);
    const v = S.values[name], warn = v && (v.state === "high" || v.state === "low");
    const color = warn ? css("--warn") : css("--accent");
    const bool = isBool(name);
    ctx.beginPath();
    sp.t.forEach((t, i) => {
      if (i === 0) ctx.moveTo(x(t), y(sp.v[i]));
      else if (bool) { ctx.lineTo(x(t), y(sp.v[i - 1])); ctx.lineTo(x(t), y(sp.v[i])); }
      else ctx.lineTo(x(t), y(sp.v[i]));
    });
    ctx.lineTo(w, y(sp.v[sp.v.length - 1]));
    ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.lineJoin = "round"; ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(x(sp.t[0]), h); ctx.closePath();
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, color + "40"); g.addColorStop(1, color + "00");
    ctx.fillStyle = g; ctx.fill();
  }
  function drawAllSparks() { for (const n of S.sparks.keys()) drawSpark(n); }

  // ---------------------------------------------------------------- trend chart
  let chart = null, chartData = null, zoomed = false;
  function axisFor(scale, side, color) {
    return {
      scale, side, stroke: css("--muted"), size: 58,
      grid: { show: side === 3, stroke: css("--line"), width: 1 },
      ticks: { show: false },
      values: (u, vals) => vals.map((v) => (v === null ? "" : Math.abs(v) >= 1000 ? v.toLocaleString() : String(+v.toFixed(2)))),
      labelGap: 0, font: `11px ${css("--mono")}`, ...(color ? { stroke: color } : {}),
    };
  }
  function buildOpts(names) {
    const scales = { x: { time: true } }, units = [], series = [{}];
    names.forEach((name) => {
      const t = S.tags.get(name);
      const key = isBool(name) ? "__bool" : (t.unit || name);
      if (!units.includes(key)) units.push(key);
      if (key === "__bool") scales[key] = { range: [-0.1, 1.15] };
      else scales[key] = scales[key] || { auto: true };
      series.push({
        label: name, scale: key, stroke: colorFor(name), width: 1.8, spanGaps: true, points: { show: false },
        paths: key === "__bool" ? uPlot.paths.stepped({ align: 1 }) : undefined,
        value: (u, v) => (v === null || v === undefined ? "–" : `${fmt(v, t.unit)} ${t.unit}`.trim()),
        fill: names.length === 1 ? colorFor(name) + "18" : undefined,
      });
    });
    const axes = [{
      stroke: css("--muted"), grid: { show: true, stroke: css("--line"), width: 1 }, ticks: { show: false }, font: `11px ${css("--sans")}`,
      values: (u, vals) => vals.map((v) => {
        const d = new Date(v * 1000);
        const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(S.minutes <= 10 ? { second: "2-digit" } : {}), hour12: false });
        return S.minutes > 720 && d.getHours() === 0 && d.getMinutes() === 0 ? d.toLocaleDateString([], { day: "numeric", month: "short" }) : hm;
      }),
    }];
    units.slice(0, 2).forEach((u, i) => {
      const firstName = names.find((n) => (isBool(n) ? "__bool" : (S.tags.get(n).unit || n)) === u);
      axes.push(axisFor(u, i === 0 ? 3 : 1, units.length > 1 ? colorFor(firstName) : null));
    });
    const el = $("#chart");
    return {
      width: el.clientWidth, height: el.clientHeight, scales, series, axes,
      legend: { show: false },
      cursor: { drag: { x: true, y: false }, points: { size: 7 } },
      hooks: {
        setCursor: [(u) => updateLegend(u, u.cursor.idx)],
        setSelect: [() => { zoomed = true; setLive(false); }],
      },
    };
  }
  function updateLegend(u, idx) {
    $("#legend").innerHTML = S.selected.map((name, i) => {
      let v = null;
      if (u && idx !== null && idx !== undefined) {
        const col = u.data[i + 1];
        for (let j = idx; j >= 0; j--) if (col[j] !== null && col[j] !== undefined) { v = col[j]; break; }
      } else v = S.values[name]?.value ?? null;
      const unit = S.tags.get(name).unit;
      const shown = isBool(name) && v !== null ? (v ? "ON" : "OFF") : `${fmt(v, unit)} ${unit}`.trim();
      return `<span><i style="background:${colorFor(name)}"></i>${esc(name)} <b>${shown}</b></span>`;
    }).join("");
  }
  function rebuildChart() {
    if (chart) { chart.destroy(); chart = null; }
    const el = $("#chart");
    el.innerHTML = "";
    if (!S.selected.length) { el.innerHTML = `<div class="empty">Select a tag on the left to plot it</div>`; $("#legend").innerHTML = ""; return; }
    if (!chartData) return;
    chart = new uPlot(buildOpts(S.selected), chartData, el);
    updateLegend(null, null);
  }
  let trendReq = 0;
  async function loadTrend() {
    const req = ++trendReq;
    $("#trend-sub").textContent = S.selected.length ? `· ${S.selected.length} tag${S.selected.length > 1 ? "s" : ""} · last ${label(S.minutes)}` : "";
    if (!S.selected.length) { chartData = null; rebuildChart(); return; }
    const end = Date.now() / 1000;
    const results = await Promise.all(S.selected.map((name) =>
      api(`/api/history?tag=${encodeURIComponent(name)}&minutes=${S.minutes}&end=${end}&points=800`).catch(() => ({ t: [], avg: [] }))));
    if (req !== trendReq) return;
    const tables = results.map((r) => [r.t, r.avg]);
    chartData = tables.length === 1 ? tables[0] : uPlot.join(tables);
    zoomed = false;
    rebuildChart();
  }
  const label = (m) => (m >= 60 ? `${m / 60} h` : `${m} min`);

  let redrawQueued = false;
  function appendLive(name, ts, value) {
    if (!S.live || !chartData || zoomed) return;
    const i = S.selected.indexOf(name);
    if (i < 0) return;
    const row = new Array(S.selected.length + 1).fill(null);
    row[0] = ts; row[i + 1] = value;
    const x = chartData[0];
    if (x.length && ts < x[x.length - 1]) return; // ignore late arrivals
    chartData.forEach((col, k) => col.push(row[k]));
    const cutoff = Date.now() / 1000 - S.minutes * 60;
    let drop = 0;
    while (drop < x.length && x[drop] < cutoff) drop++;
    if (drop) chartData.forEach((col) => col.splice(0, drop));
    if (!redrawQueued) {
      redrawQueued = true;
      setTimeout(() => {
        redrawQueued = false;
        if (chart && !zoomed) { chart.setData(chartData); updateLegend(null, null); }
      }, 250);
    }
  }
  function setLive(on) {
    S.live = on;
    $("#live").classList.toggle("on", on);
    if (on && zoomed) loadTrend();
  }
  $("#live").onclick = () => setLive(!S.live);
  $("#ranges").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    for (const x of $("#ranges").children) x.classList.toggle("on", x === b);
    S.minutes = Number(b.dataset.min); store.set("minutes", S.minutes);
    setLive(true); loadTrend();
  });
  $("#chart").addEventListener("dblclick", () => { if (zoomed) { setLive(true); loadTrend(); } });
  $("#export").onclick = () => {
    const tags = S.selected.length ? S.selected : [...S.tags.keys()];
    location.href = `/api/export.csv?tags=${encodeURIComponent(tags.join(","))}&minutes=${S.minutes}`;
    toast(`Exporting ${tags.length} tag${tags.length > 1 ? "s" : ""} · last ${label(S.minutes)}`);
  };
  new ResizeObserver(() => { if (chart) chart.setSize({ width: $("#chart").clientWidth, height: $("#chart").clientHeight }); drawAllSparks(); }).observe($("#chart"));

  // ---------------------------------------------------------------- events
  const SYSTEM = new Set(["info", "warning", "error"]);
  function renderEvents() {
    const list = S.events.filter((e) => !S.eventFilter || (S.eventFilter === "alarm" ? !SYSTEM.has(e.kind) : SYSTEM.has(e.kind))).slice(0, 150);
    $("#events").innerHTML = list.length
      ? list.map((e) => `<li class="${e._new ? "new" : ""}"><time>${hhmmss(e.ts)}</time><span class="kind ${esc(e.kind)}">${esc(e.kind)}</span><span>${esc(e.message)}</span></li>`).join("")
      : `<li class="empty">No events yet. Alarms appear here when a value crosses its limits.</li>`;
    S.events.forEach((e) => delete e._new);
    const active = Object.values(S.values).filter((v) => v.state === "high" || v.state === "low").length;
    const badge = $("#alarm-count");
    badge.textContent = active; badge.classList.toggle("hidden", !active);
    badge.title = `${active} tag(s) outside limits`;
  }
  $("#event-filter").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    for (const x of $("#event-filter").children) x.classList.toggle("on", x === b);
    S.eventFilter = b.dataset.kind; renderEvents();
  });

  // ---------------------------------------------------------------- live feed
  let ws, retry = 500;
  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { retry = 500; };
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "snapshot") {
        S.values = msg.values; S.servers = msg.servers; renderServers();
        for (const n of S.tags.keys()) { paintTag(n); paintCard(n); }
        renderEvents();
      } else if (msg.type === "batch") msg.messages.forEach(handle);
    };
    ws.onclose = () => {
      S.servers = S.servers.map((s) => ({ ...s, status: "disconnected", error: "dashboard lost connection to TagScope" }));
      renderServers();
      setTimeout(connect, (retry = Math.min(retry * 2, 10000)));
    };
  }
  let alarmsDirty = false;
  function handle(msg) {
    if (msg.type === "value") {
      const { tag, type, ...v } = msg;
      const prev = S.values[tag];
      S.values[tag] = v;
      if (prev && prev.state !== v.state) alarmsDirty = true;
      pushSpark(tag, v.ts, v.value);
      paintTag(tag, true); paintCard(tag);
      appendLive(tag, v.ts, v.value);
    } else if (msg.type === "server") {
      const i = S.servers.findIndex((s) => s.name === msg.server.name);
      if (i >= 0) S.servers[i] = msg.server; else S.servers.push(msg.server);
      renderServers();
    } else if (msg.type === "event") {
      S.events.unshift({ ...msg.event, _new: true });
      S.events.length = Math.min(S.events.length, 500);
      alarmsDirty = true;
    }
    if (alarmsDirty) { alarmsDirty = false; renderEvents(); }
  }

  // ---------------------------------------------------------------- address space browser
  const dlg = $("#browser");
  $("#open-browser").onclick = async () => {
    const server = S.servers[0]?.name;
    if (!server) return toast("No servers configured");
    $("#browser-server").textContent = `· ${server}`;
    dlg.dataset.server = server;
    $("#tree").innerHTML = `<li class="hint">Loading…</li>`;
    dlg.showModal();
    try { $("#tree").innerHTML = renderNodes(await api(`/api/browse?server=${encodeURIComponent(server)}`)); }
    catch (e) { $("#tree").innerHTML = `<li class="hint">${esc(e.message)}</li>`; }
  };
  $("#close-browser").onclick = () => dlg.close();
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  function renderNodes(items) {
    if (!items.length) return `<li class="hint">Empty</li>`;
    return items.map((n) => {
      const folder = n.class === "Object";
      const val = n.value !== null && n.value !== undefined ? `<span class="val">${esc(typeof n.value === "number" ? fmt(n.value) : n.value)}</span>` : "<span></span>";
      return `<li><div class="node ${folder ? "folder" : ""}" data-node="${esc(n.node)}">
        <span class="caret">${folder ? "▸" : n.class === "Variable" ? "•" : ""}</span>
        <span>${esc(n.name)} <code>${esc(n.node)}</code></span>${val}
        ${n.class === "Variable" ? `<button class="copy" data-copy="${esc(n.node)}">copy</button>` : "<span></span>"}
      </div></li>`;
    }).join("");
  }
  $("#tree").addEventListener("click", async (e) => {
    const copy = e.target.closest(".copy");
    if (copy) { navigator.clipboard?.writeText(copy.dataset.copy); toast(`Copied ${copy.dataset.copy}`); return; }
    const node = e.target.closest(".node.folder"); if (!node) return;
    const li = node.parentElement;
    if (node.classList.toggle("open")) {
      const ul = document.createElement("ul"); ul.innerHTML = `<li class="hint">Loading…</li>`; li.appendChild(ul);
      try { ul.innerHTML = renderNodes(await api(`/api/browse?server=${encodeURIComponent(dlg.dataset.server)}&node=${encodeURIComponent(node.dataset.node)}`)); }
      catch (err) { ul.innerHTML = `<li class="hint">${esc(err.message)}</li>`; }
    } else li.querySelector("ul")?.remove();
  });

  // ---------------------------------------------------------------- boot
  async function boot() {
    const o = await api("/api/overview");
    o.tags.forEach((t) => S.tags.set(t.name, t));
    S.values = o.values; S.servers = o.servers;
    const saved = store.get("selected", null);
    S.selected = (saved || ["Tank1.Level", "Pump1.Speed"]).filter((n) => S.tags.has(n));
    if (!S.selected.length) S.selected = [...S.tags.keys()].slice(0, 2);
    S.selected.forEach(colorFor);
    for (const b of $("#ranges").children) b.classList.toggle("on", Number(b.dataset.min) === S.minutes);
    renderServers(); renderTagList(); renderCards();
    S.events = await api("/api/events?limit=200").catch(() => []);
    renderEvents();
    connect();
    loadTrend();
    pollStats(); setInterval(pollStats, 3000);
    // seed sparklines with the last few minutes of history
    const end = Date.now() / 1000;
    await Promise.all(cardTags().map(async (name) => {
      const h = await api(`/api/history?tag=${encodeURIComponent(name)}&minutes=${SPARK_SECONDS / 60}&end=${end}&points=150`).catch(() => null);
      if (!h) return;
      const sp = S.sparks.get(name) || { t: [], v: [] };
      const live = sp.t.length ? sp : null;
      S.sparks.set(name, { t: [...h.t, ...(live ? live.t : [])], v: [...h.avg, ...(live ? live.v : [])] });
      paintCard(name);
    }));
    setInterval(drawAllSparks, 1000);
  }
  boot().catch((e) => { document.body.insertAdjacentHTML("beforeend", `<p class="hint" style="padding:16px">Could not load TagScope: ${esc(e.message)}</p>`); });
})();
