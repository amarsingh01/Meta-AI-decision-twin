// Decision Twin demo UI. Builds the DOM with textContent only (no innerHTML of data).
"use strict";

const state = { runId: null, since: 0, principles: [], timer: null, cards: new Map() };
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "title") node.title = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const time = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

// ------------------------------------------------------------------ principles panel

function renderPrinciples(principles, hardRules, settings) {
  state.principles = principles;
  const list = $("principles");
  list.replaceChildren();
  const max = Math.max(...principles.map((p) => p.weight));
  for (const p of principles) {
    const fill = el("span");
    fill.style.width = `${(p.weight / max) * 100}%`;
    list.append(
      el("li", { class: "principle" },
        el("div", { class: "row" },
          el("span", {}, p.name, p.veto_below != null ? el("span", { class: "veto-tag" }, `veto < ${p.veto_below}`) : null),
          el("b", {}, pct(p.weight))),
        el("div", { class: "bar", title: `weight ${pct(p.weight)}` }, fill),
        el("div", { class: "desc" }, p.description)));
  }
  $("hard-rules").replaceChildren(...hardRules.map((r) => el("li", {}, r)));
  if (settings) {
    const a = settings.blend_principles_vs_model;
    $("blend-text").textContent = `${pct(a)} principles + ${pct(1 - a)} model`;
  }
}

// ------------------------------------------------------------------ evaluation

function renderEval(data) {
  const box = $("eval");
  const rows = Object.entries(data || {}).filter(([, v]) => v && v.n);
  if (!rows.length) { box.replaceChildren(el("p", { class: "hint" }, "Not evaluated yet.")); return; }
  box.replaceChildren(...rows.map(([key, v]) => {
    const fill = el("span");
    fill.style.width = `${(v.agreement || 0) * 100}%`;
    return el("div", { class: "eval-row" },
      el("div", { class: "row" }, el("span", {}, key === "glimmer" ? "Muse Glimmer (open)" : "Muse Spark (API)"),
        el("b", {}, `${Math.round((v.agreement || 0) * v.n)}/${v.n} · ${pct(v.agreement)}`)),
      el("div", { class: "bar" }, fill),
      el("div", { class: "desc" },
        `Average confidence in the recorded choice: ${pct(v.mean_p_truth)}` +
        (v.mean_model_p_truth != null ? ` · model's own: ${pct(v.mean_model_p_truth)}` : "") +
        (v.flagged_for_human ? ` · ${v.flagged_for_human} flagged as close calls` : "")));
  }));
}

// ------------------------------------------------------------------ cards

function scoreClass(s) { return s >= 7 ? "s-hi" : s >= 4 ? "s-mid" : "s-lo"; }

function decisionCard(ev) {
  const d = ev.decision;
  const principles = state.principles.length ? state.principles : Object.keys(d.options[0].scores).map((id) => ({ id, name: id, weight: 0 }));
  const hasModel = d.model_probabilities_available;
  const head = el("tr", {}, el("th", {}, "Option"),
    principles.map((p) => el("th", { title: p.description || "" }, p.name.split(" ")[0], el("small", {}, pct(p.weight)))),
    el("th", { title: "Σ weight × score ÷ 10" }, "Weighted", el("small", {}, "0–1")),
    el("th", { title: "Softmax of the weighted values, vetoed options at 0%" }, "Principles", el("small", {}, "probability")),
    hasModel ? el("th", { title: "The open-weight model's own probability for this option letter" }, "Model", el("small", {}, "probability")) : null,
    el("th", { class: "final-col" }, "Final", el("small", {}, "confidence")));
  const rows = d.options.map((o) => {
    const fill = el("div", { class: "fill" });
    fill.style.width = `${Math.round((o.p_final || 0) * 100)}%`;
    return el("tr", { class: [o.id === d.chosen ? "chosen" : "", o.vetoed ? "vetoed" : ""].join(" ") },
      el("td", { class: "opt" },
        el("div", { class: "lbl" }, `${o.id}) ${o.label}`),
        o.rationale ? el("div", { class: "why" }, o.rationale) : null,
        o.vetoed ? el("div", { class: "veto-reason" }, `Vetoed: ${o.veto_reason}`) : null),
      principles.map((p) => el("td", { class: `score ${scoreClass(o.scores[p.id] ?? 0)}` }, o.scores[p.id] ?? "–")),
      el("td", { class: "num" }, o.weighted.toFixed(2)),
      el("td", { class: "num" }, pct(o.p_principles)),
      hasModel ? el("td", { class: "num model" }, pct(o.p_model)) : null,
      el("td", { class: "final-col" }, el("div", { class: "final" }, el("div", { class: "track" }, fill), el("b", {}, pct(o.p_final)))));
  });

  const chosen = d.options.find((o) => o.id === d.chosen);
  const verdict = el("div", { class: `verdict ${d.needs_human ? "human" : ""}` },
    chosen ? el("b", {}, `Chose ${chosen.id} with ${pct(d.confidence)} confidence. `) : el("b", {}, "No acceptable option. "),
    d.needs_human ? `Flagged as a close call: ${d.escalation_reason}.` : "Clear enough to proceed without asking a human.");

  return el("li", { class: "card decision" },
    el("div", { class: "head" },
      el("span", { class: "pill twin" }, "Twin decides"),
      el("span", { class: "pill" }, d.brain),
      !d.model_probabilities_available ? el("span", { class: "pill warn", title: "Hosted reasoning APIs do not return token probabilities; the open-weight brain does" }, "principles only · no token probabilities") : el("span", { class: "pill good" }, "principles + model token probabilities"),
      el("span", { class: "when" }, `turn ${ev.turn} · ${(d.latency_ms / 1000).toFixed(1)} s · ${time(ev.ts)}`)),
    el("div", { class: "q" }, d.question),
    el("div", { class: "matrix-wrap" }, el("table", { class: "matrix" }, el("thead", {}, head), el("tbody", {}, rows))),
    verdict,
    el("div", { class: "reply" }, `→ to builder: ${d.reply_to_worker}`));
}

function workerCard(ev) {
  const text = (ev.text || "").replace(/<<<DECISION_REQUEST>>>[\s\S]*?<<<END_DECISION_REQUEST>>>/g, "[asks the twin a question ↓]")
    .replace(/<<<DONE>>>[\s\S]*?<<<END_DONE>>>/g, "[reports the build is finished]").trim();
  return el("li", { class: "card worker" },
    el("div", { class: "head" },
      el("span", { class: "pill worker" }, "Builder works"),
      ev.ok ? null : el("span", { class: "pill bad" }, "turn failed"),
      el("span", { class: "when" }, `turn ${ev.turn} · ${((ev.duration_ms || 0) / 1000).toFixed(0)} s · ${time(ev.ts)}`)),
    el("div", {}, text ? text.slice(-700) : (ev.error || "(no message)")),
    ev.files_changed ? el("details", {}, el("summary", {}, "Files changed"), el("pre", {}, ev.files_changed)) : null);
}

function verifyCard(ev) {
  return el("li", { class: `card verify ${ev.passed ? "pass" : "fail"}` },
    el("div", { class: "head" },
      el("span", { class: `pill ${ev.passed ? "good" : "bad"}` }, ev.passed ? "Tests pass" : "Tests fail"),
      el("span", {}, "Checked independently of the builder's own claim"),
      el("span", { class: "when" }, time(ev.ts))),
    el("details", {}, el("summary", {}, `Output of: ${ev.command || "tests"}`), el("pre", {}, ev.output || "")));
}

function simpleCard(ev, cls, label) {
  return el("li", { class: `card ${cls}` },
    el("div", { class: "head" }, el("span", { class: `pill ${cls === "error" ? "bad" : "warn"}` }, label), el("span", { class: "when" }, time(ev.ts))),
    el("div", {}, ev.message || ""), ev.summary ? el("div", { class: "ctx" }, ev.summary) : null);
}

function finishedCard(ev) {
  return el("li", { class: `card ${ev.finished ? "verify pass" : "error"}` },
    el("div", { class: "head" }, el("span", { class: `pill ${ev.finished ? "good" : "bad"}` }, ev.finished ? "Build accepted" : `Stopped: ${ev.outcome}`),
      el("span", { class: "when" }, time(ev.ts))),
    el("div", {}, `${ev.decisions ?? 0} decisions made by the twin.`));
}

// ------------------------------------------------------------------ stats

function renderStats(usage, decisions, startedTs, lastTs) {
  const p = usage?.prompt_tokens || 0, c = usage?.completion_tokens || 0;
  const cost = (p * 1.25 + c * 4.25) / 1e6; // Muse Spark 1.3 Standard list price per million tokens
  const mins = startedTs && lastTs ? Math.round((lastTs - startedTs) / 60) : 0;
  const stat = (k, v, t) => el("div", { class: "stat", title: t || "" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v));
  $("stats").replaceChildren(
    stat("Decisions", decisions),
    stat("Builder tokens", `${((p + c) / 1000).toFixed(0)}k`, "Tokens the builder used through the model gateway"),
    stat("Builder cost", `$${cost.toFixed(2)}`, "At Muse Spark 1.3 Standard list prices"),
    stat("Elapsed", `${mins} min`));
}

// ------------------------------------------------------------------ polling

let meta = { decisions: 0, usage: null, started: null, last: null, finished: false };

function applyEvent(ev) {
  const tl = $("timeline");
  meta.last = ev.ts;
  switch (ev.type) {
    case "run_started":
      meta.started = ev.ts;
      $("twin-brain").textContent = ev.brain;
      $("worker-brain").textContent = ev.worker;
      renderPrinciples(ev.principles, ev.hard_rules, ev.settings);
      $("task-box").replaceChildren(el("b", {}, "Task given to the twin: "), ev.task);
      $("task-box").classList.remove("hidden");
      break;
    case "worker_turn": meta.usage = ev.usage; tl.append(workerCard(ev)); break;
    case "decision": meta.decisions += 1; tl.append(decisionCard(ev)); break;
    case "verification": tl.append(verifyCard(ev)); break;
    case "note": tl.append(simpleCard(ev, "note", "Note")); break;
    case "error": tl.append(simpleCard(ev, "error", "Problem")); break;
    case "run_finished": meta.finished = true; meta.usage = ev.usage || meta.usage; tl.append(finishedCard(ev)); break;
  }
}

async function poll() {
  if (!state.runId) return;
  try {
    const events = await getJSON(`/api/runs/${encodeURIComponent(state.runId)}/events?since=${state.since}`);
    const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
    for (const ev of events) { applyEvent(ev); state.since = ev.seq + 1; }
    renderStats(meta.usage, meta.decisions, meta.started, meta.last);
    $("status").textContent = meta.finished ? "Run finished." : "Running… the builder and the twin are taking turns.";
    if (events.length && atBottom && !meta.finished) window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  } catch (e) {
    $("status").textContent = `Could not load events (${e.message}). Retrying…`;
  }
  if (!meta.finished) state.timer = setTimeout(poll, 3000);
}

function openRun(id) {
  clearTimeout(state.timer);
  state.runId = id; state.since = 0;
  meta = { decisions: 0, usage: null, started: null, last: null, finished: false };
  $("timeline").replaceChildren();
  const url = new URL(location.href); url.searchParams.set("run", id); history.replaceState(null, "", url);
  poll();
}

async function loadRuns(selectId) {
  const runs = await getJSON("/api/runs");
  const sel = $("run-select");
  sel.replaceChildren(...runs.map((r) => el("option", { value: r.id }, `${r.id.slice(0, 15)} · ${r.brain} · ${r.status}`)));
  const want = selectId || new URL(location.href).searchParams.get("run") || runs[0]?.id;
  if (want) { sel.value = want; openRun(want); } else { $("status").textContent = "No runs yet."; }
}

async function init() {
  try {
    const { profile } = await getJSON("/api/profile");
    renderPrinciples(profile.principles, profile.hard_rules, profile.decision_settings);
  } catch (e) { /* the run_started event carries the same data */ }
  getJSON("/api/eval").then(renderEval).catch(() => renderEval(null));
  $("run-select").addEventListener("change", (e) => openRun(e.target.value));
  $("task").value = "Build me a small website that shows near-real-time data and news for an NSE-listed stock and gives an explained Buy / Hold / Sell signal. Budget is close to zero. It is a demo: it must never place trades and must not present itself as financial advice.";
  $("start-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("start-msg").textContent = "Starting…";
    try {
      const { id } = await getJSON("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Access-Token": $("token").value },
        body: JSON.stringify({ task: $("task").value, brain: $("brain").value }),
      });
      $("start-msg").textContent = "Started.";
      await loadRuns(id);
    } catch (err) { $("start-msg").textContent = `Could not start: ${err.message}`; }
  });
  await loadRuns();
}

document.addEventListener("DOMContentLoaded", init);
