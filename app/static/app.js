/* MatchItUp front end: renders the live stream (stage 4: Render). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const params = new URLSearchParams(location.search);
  const form = $("#controls");
  const state = {
    es: null, meta: null, teams: {}, players: {}, colours: {}, home: null, away: null,
    t: 0, speed: 30, profile: {}, momentum: [], feedCount: 0, queues: {}, timers: {},
  };

  if (params.get("embed") === "1") document.body.classList.add("embed");

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ------------------------------------------------------------------ setup
  async function init() {
    const res = await fetch("/api/match");
    const data = await res.json();
    applyMeta(data.meta);
    const clubSel = form.favourite_club, playerSel = form.favourite_player;
    for (const team of data.meta.teams) {
      clubSel.add(new Option(team.name, team.team_id));
      const group = document.createElement("optgroup");
      group.label = team.name;
      for (const p of team.players) group.appendChild(new Option(`${p.name} (#${p.number}, ${p.position})`, p.player_id));
      playerSel.appendChild(group);
    }
    for (const key of ["language", "favourite_club", "favourite_player", "focus_metric", "speed"]) {
      if (params.get(key)) form[key].value = params.get(key);
    }
    if (params.get("mode")) {
      const r = form.querySelector(`input[name=mode][value="${params.get("mode")}"]`);
      if (r) r.checked = true;
    }
    health();
    if (params.get("autostart") === "1") start();
  }

  async function health() {
    try {
      const h = await (await fetch("/api/health")).json();
      const el = $("#aiStatus");
      if (h.llm.live) {
        el.textContent = `AI live · ${h.llm.provider} · ${h.llm.model}`;
        el.classList.add("live");
      } else {
        el.textContent = "Template mode (no AI model connected)";
        el.classList.remove("live");
      }
    } catch (_) { /* ignore */ }
  }

  function applyMeta(meta) {
    state.meta = meta;
    [state.home, state.away] = meta.teams;
    for (const t of meta.teams) {
      state.teams[t.team_id] = t;
      state.colours[t.team_id] = t.colour;
      for (const p of t.players) state.players[p.player_id] = p;
    }
    const root = document.documentElement.style;
    root.setProperty("--home", state.home.colour);
    root.setProperty("--away", state.away.colour);
    $("#sbHome").textContent = state.home.short;
    $("#sbAway").textContent = state.away.short;
    $("#lgHome").textContent = state.home.name;
    $("#lgAway").textContent = state.away.name;
    $("#stHome").textContent = state.home.short;
    $("#stAway").textContent = state.away.short;
  }

  function currentProfile() {
    const fd = new FormData(form);
    return {
      mode: fd.get("mode"), language: fd.get("language"), favourite_club: fd.get("favourite_club"),
      favourite_player: fd.get("favourite_player"), focus_metric: fd.get("focus_metric"),
      speed: fd.get("speed"),
    };
  }

  // ------------------------------------------------------------------ streaming
  function start() {
    if (state.es) state.es.close();
    resetUI();
    const p = currentProfile();
    if (p.mode === "player_focus" && !p.favourite_player) {
      // Pick the favourite club's number 10, or the home number 10, so the mode is never empty.
      const team = state.teams[p.favourite_club] || state.home;
      const ten = team.players.find((x) => x.number === 10) || team.players[9];
      form.favourite_player.value = ten.player_id;
      p.favourite_player = ten.player_id;
    }
    state.profile = p;
    state.speed = Number(p.speed);
    document.documentElement.lang = p.language;
    const q = new URLSearchParams({ mode: p.mode, language: p.language, speed: p.speed });
    for (const k of ["favourite_club", "favourite_player", "focus_metric"]) if (p[k]) q.set(k, p[k]);
    if (params.get("start_minute")) q.set("start_minute", params.get("start_minute"));
    if (params.get("seed")) q.set("seed", params.get("seed"));
    state.es = new EventSource(`/api/stream?${q}`);
    state.es.onmessage = (e) => handle(JSON.parse(e.data));
    state.es.onerror = () => { /* the browser retries automatically; stop at the end */ };
    $("#splash").classList.add("gone");
    $("#startBtn").textContent = "↻ Restart";
  }

  function handle(msg) {
    switch (msg.k) {
      case "hello": applyMeta(msg.meta); break;
      case "ev": onEvent(msg); break;
      case "stats": onStats(msg); break;
      case "insight": onInsight(msg); break;
      case "recap": onRecap(msg); break;
      case "end": state.es.close(); $("#startBtn").textContent = "▶ Replay"; break;
    }
  }

  // ------------------------------------------------------------------ pitch
  const svgNS = "http://www.w3.org/2000/svg";
  function trail(points, colour, cls) {
    const g = $("#trails");
    const path = document.createElementNS(svgNS, "polyline");
    path.setAttribute("points", points.map((p) => p.join(",")).join(" "));
    path.setAttribute("class", `trail ${cls || ""}`);
    path.style.stroke = colour;
    g.appendChild(path);
    while (g.childNodes.length > 14) g.removeChild(g.firstChild);
  }

  function moveBall(pos) {
    $("#ballGroup").setAttribute("transform", `translate(${pos[0]} ${pos[1]})`);
    // Keep the name tag inside the pitch.
    const bg = $("#tagBg");
    const w = Number(bg.getAttribute("width")) || 18;
    const flip = pos[0] + w > 103;
    $("#tag").setAttribute("transform", `translate(${flip ? -(w + 1.8) : 1.8} ${pos[1] < 6 ? 4.5 : -2.4})`);
  }

  function setTag(text, colour) {
    const t = $("#tagText");
    t.textContent = text;
    let w = 0;
    try { w = t.getBBox().width + 1.6; } catch (_) { w = text.length * 1.1 + 1.6; }
    $("#tagBg").setAttribute("width", Math.max(w, 6));
    $("#tag").style.setProperty("--tag-colour", colour);
  }

  function onEvent(ev) {
    state.t = ev.t;
    const colour = state.colours[ev.team] || "#fff";
    $("#sbClock").textContent = `${ev.m}'`;
    if (ev.type === "pass" && ev.s && ev.e) {
      moveBall(ev.s);
      trail([ev.s, ev.e], colour, ev.o === "complete" ? "" : "miss");
      let label = ev.p || "";
      if (state.profile.mode === "analyst" && ev.d) label += ` · ${ev.dist}m · diff ${ev.d}`;
      else if (ev.spd >= 85) label += ` · ⚡ ${Math.round(ev.spd)} km/h`;
      setTag(label, colour);
      setTimeout(() => moveBall(ev.e), 120);
    } else if (ev.type === "shot" && ev.s) {
      moveBall(ev.s);
      const goalX = ev.s[0] > 52.5 ? 105 : 0;
      trail([ev.s, [goalX, 34]], colour, "shot");
      setTag(`${ev.p} · SHOT ${Math.round(ev.spd)} km/h${state.profile.mode === "analyst" ? ` · xG ${ev.xg}` : ""}`, colour);
      setTimeout(() => moveBall([goalX, 34 + (ev.o === "goal" ? 0 : 5)]), 150);
    } else if (ev.s) {
      moveBall(ev.s);
      if (ev.p && ["tackle", "interception"].includes(ev.type)) setTag(`${ev.p} · ${ev.type}`, colour);
    } else if (ev.type === "kickoff") {
      moveBall([52.5, 34]);
    }
  }

  // ------------------------------------------------------------------ stats
  const STAT_ROWS = [
    ["possession_pct", "Possession %", true], ["xg", "Chance quality (xG)", false],
    ["shots", "Shots", false], ["shots_on_target", "On target", false],
    ["pass_accuracy_pct", "Pass accuracy %", true], ["avg_pass_difficulty", "Avg pass difficulty", false],
    ["final_third_entries", "Final-third entries", false], ["pressures", "Pressures", false],
    ["high_regains", "High ball wins", false], ["max_shot_speed_kmh", "Fastest shot km/h", false],
    ["corners", "Corners", false], ["yellow_cards", "Yellow cards", false],
  ];

  function onStats(s) {
    if (!s.home) return;
    $("#sbScore").textContent = `${s.home.goals}–${s.away.goals}`;
    $("#sbClock").textContent = s.status === "half_time" ? "HT" : s.status === "full_time" ? "FT" : `${s.minute}'`;
    const chip = $("#rhythmChip");
    const labels = { chaos: "⚡ Chaos", control: "◼ Control", balanced: "Balanced" };
    chip.textContent = `${labels[s.rhythm.label]} · ${s.rhythm.turnovers_per_min} turnovers/min`;
    chip.className = `rhythm-chip ${s.rhythm.label}`;
    $("#statRows").innerHTML = STAT_ROWS.map(([key, label]) => {
      const h = Number(s.home[key]) || 0, a = Number(s.away[key]) || 0;
      const total = h + a || 1;
      return `<div class="srow"><div class="lbl"><span>${esc(s.home[key])}</span><span>${esc(label)}</span>` +
        `<span>${esc(s.away[key])}</span></div><div class="bar"><i style="width:${(100 * h) / total}%"></i>` +
        `<i style="width:${(100 * a) / total}%"></i></div></div>`;
    }).join("");
    $("#topPlayers").innerHTML = (s.top_players || []).map((p) =>
      `<li>${esc(p.name)} <span>${esc(state.teams[p.team_id]?.short || "")} · ${esc(p.position)} · ` +
      `impact ${esc(p.impact)} · ${esc(p.passes_completed)} passes · ${esc(p.distance_km)} km</span></li>`).join("");
    state.momentum = s.momentum_history || [];
    drawMomentum();
  }

  function drawMomentum() {
    const c = $("#momentum");
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    c.width = w * dpr; c.height = h * dpr;
    const ctx = c.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    const mid = h / 2;
    ctx.strokeStyle = "rgba(255,255,255,.15)";
    ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(w, mid); ctx.stroke();
    const total = 95, bw = w / total;
    const cs = getComputedStyle(document.documentElement);
    const home = cs.getPropertyValue("--home"), away = cs.getPropertyValue("--away");
    for (const [minute, v] of state.momentum) {
      const x = minute * bw, bh = (Math.abs(v) / 100) * (mid - 4);
      ctx.fillStyle = v >= 0 ? home : away;
      ctx.fillRect(x, v >= 0 ? mid - bh : mid, Math.max(bw - 1, 1), Math.max(bh, v === 0 ? 0 : 1.5));
    }
    ctx.fillStyle = "rgba(255,255,255,.35)";
    ctx.font = "10px Inter, sans-serif";
    for (const m of [15, 30, 45, 60, 75, 90]) ctx.fillText(`${m}'`, m * bw - 6, h - 2);
  }
  window.addEventListener("resize", drawMomentum);

  // ------------------------------------------------------------------ insights + overlays
  function onInsight(card) {
    const colour = state.colours[card.team_id] || "var(--accent)";
    addCard(card, colour);
    showOverlay(card, colour);
  }

  function receiptHTML(r, card) {
    const grounded = r.grounded
      ? `<span class="pill ok">✓ grounded · ${r.numbers_checked} numbers checked</span>`
      : `<span class="pill warn">⚠ check numbers</span>`;
    const src = r.source === "model" ? `AI · ${esc(r.model)}` : "template";
    const tokens = r.input_tokens ? `<span class="pill">${r.input_tokens}+${r.output_tokens} tokens</span>` : "";
    const steps = r.steps.map((s) => {
      const extra = [s.provider && s.agent !== "Router" ? s.model : "", s.cache || "", s.fallback_reason || "",
        s.rejected || "", s.attempt > 1 ? `retry ${s.attempt}` : "",
        s.grounded === false ? "ungrounded" : s.grounded ? "grounded" : ""].filter(Boolean).join(" · ");
      return `<li><span>${esc(s.agent)}${extra ? ` <em>(${esc(extra)})</em>` : ""}</span><span>${s.ms ?? 0} ms</span></li>`;
    }).join("");
    const facts = card.facts.map((f) => `<span>${esc(f.id)} ${esc(f.label)}</span><span>${esc(f.value)}${esc(f.unit && f.unit !== "" ? " " + f.unit : "")}</span>`).join("");
    return `<details class="receipt"><summary>${grounded}<span class="pill">${src}</span>${tokens}` +
      `<span class="pill">${r.total_ms} ms</span>${card.cache_hit ? '<span class="pill">cached</span>' : ""}</summary>` +
      `<ul class="steps">${steps}</ul><div class="facts">${facts}</div>` +
      `<div>Evidence: ${card.evidence.map(esc).join(", ") || "—"}</div></details>`;
  }

  function addCard(card, colour) {
    const feed = $("#feed");
    feed.querySelector(".empty")?.remove();
    const el = document.createElement("article");
    el.className = `card p${card.priority}`;
    el.style.setProperty("--card-colour", colour);
    el.innerHTML = `<div class="card-top"><span class="theme">${esc(card.theme_label)}</span><span>${card.minute}'</span></div>` +
      `<h4>${esc(card.headline)}</h4>${card.body ? `<p>${esc(card.body)}</p>` : ""}` +
      `${card.why ? `<p class="why">${esc(card.why)}</p>` : ""}${receiptHTML(card.receipt, card)}`;
    feed.prepend(el);
    while (feed.children.length > 60) feed.lastChild.remove();
    $("#feedCount").textContent = ++state.feedCount;
  }

  const SLOT_EL = { lower_third: "#ovLower", top_right: "#ovTop", player_tag: "#ovPlayer", full_banner: "#ovBanner" };

  function showOverlay(card, colour) {
    const slot = SLOT_EL[card.overlay.slot] ? card.overlay.slot : "lower_third";
    if (slot === "full_banner") {
      // A full banner (goal, half-time, full-time) takes the screen: clear the other graphics.
      for (const other of ["lower_third", "top_right", "player_tag"]) {
        clearTimeout(state.timers[other]); state.timers[other] = null; state.queues[other] = [];
        $(SLOT_EL[other]).hidden = true;
      }
    }
    const q = (state.queues[slot] ||= []);
    if (!state.timers[slot]) return render(slot, card, colour);
    if (card.priority === 1) { clearTimeout(state.timers[slot]); return render(slot, card, colour); }
    q.push([card, colour]);
    if (q.length > 2) q.shift();
  }

  function render(slot, card, colour) {
    const el = $(SLOT_EL[slot]);
    el.style.setProperty("--ov-colour", colour);
    el.innerHTML = `<div class="ov-theme">${esc(card.theme_label)} · ${card.minute}'</div>` +
      `<div class="ov-head">${esc(card.headline)}</div>` +
      (card.body ? `<div class="ov-body">${esc(card.body)}</div>` : "") +
      (card.why ? `<div class="ov-why">${esc(card.why)}</div>` : "");
    el.hidden = false;
    el.style.animation = "none"; void el.offsetWidth; el.style.animation = "";
    const ms = Math.min(card.overlay.duration_s, slot === "full_banner" ? 4.5 : 5.5) * 1000;
    state.timers[slot] = setTimeout(() => {
      el.hidden = true;
      state.timers[slot] = null;
      let next = state.queues[slot]?.shift();
      // Drop graphics that are more than 3 match minutes old: live overlays must stay live.
      while (next && state.t - next[0].t > 180) next = state.queues[slot].shift();
      if (next) setTimeout(() => render(slot, ...next), 250);
    }, ms);
  }

  // ------------------------------------------------------------------ recap
  function onRecap(r) {
    const el = $("#recap");
    const block = `<div class="recap-block"><div class="label">${esc(r.label)} recap · ${r.source === "model" ? "AI" : "template"} · ` +
      `${r.verification?.grounded ? "✓ grounded" : "⚠ check"}</div><h3>${esc(r.title)}</h3><p>${esc(r.summary)}</p>` +
      `<ul>${(r.key_moments || []).map((k) => `<li>${esc(k)}</li>`).join("")}</ul>` +
      (r.player_of_the_match ? `<div class="potm">⭐ <b>${esc(r.player_of_the_match)}</b> — ${esc(r.player_reason)}</div>` : "") +
      `</div><hr style="border-color:var(--line);margin:14px 0">`;
    el.querySelector(".empty")?.remove();
    el.insertAdjacentHTML("afterbegin", block);
    const tab = document.querySelector('.tab[data-tab="recap"]');
    tab.textContent = "Recap •";
  }

  // ------------------------------------------------------------------ ask
  $("#askForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#askInput");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    const item = document.createElement("div");
    item.className = "qa";
    item.innerHTML = `<div class="q">${esc(question)}</div><div class="a">Thinking…</div>`;
    $("#askLog").prepend(item);
    try {
      const p = currentProfile();
      const res = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, t: state.t || 5400, mode: p.mode, language: p.language,
          seed: Number(params.get("seed") || 8) }),
      });
      const r = await res.json();
      const tools = r.steps.filter((s) => s.agent.startsWith("tool:")).map((s) => s.agent.slice(5));
      const meta = [`minute ${r.minute}`, r.source === "model" ? "AI" : "template",
        tools.length ? `tools: ${tools.join(", ")}` : (r.tools_used || []).join(", "),
        r.verification ? (r.verification.grounded ? "✓ grounded" : "⚠ check") : "", `${r.total_ms} ms`]
        .filter(Boolean).join(" · ");
      item.querySelector(".a").innerHTML = `${esc(r.answer)}<div class="hint" style="margin-top:6px">${esc(meta)}</div>`;
    } catch (err) {
      item.querySelector(".a").textContent = "Sorry, something went wrong.";
    }
  });
  $("#askChips").addEventListener("click", (e) => {
    if (e.target.matches(".chip")) { $("#askInput").value = e.target.textContent; $("#askForm").requestSubmit(); }
  });

  // ------------------------------------------------------------------ misc UI
  document.querySelectorAll(".tab").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${btn.dataset.tab}`));
    if (btn.dataset.tab === "recap") btn.textContent = "Recap";
  }));

  function resetUI() {
    $("#feed").innerHTML = '<p class="empty">Waiting for the first key moment…</p>';
    $("#recap").innerHTML = '<p class="empty">An automated recap is written at half-time and full-time.</p>';
    $("#trails").innerHTML = "";
    $("#sbScore").textContent = "0–0";
    state.feedCount = 0; $("#feedCount").textContent = "0";
    state.momentum = []; drawMomentum();
    for (const slot of Object.keys(SLOT_EL)) {
      clearTimeout(state.timers[slot]); state.timers[slot] = null; state.queues[slot] = [];
      $(SLOT_EL[slot]).hidden = true;
    }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); start(); });
  $("#splashStart").addEventListener("click", start);
  init();
})();
