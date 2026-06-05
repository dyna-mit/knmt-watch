"use strict";

const LS = {
  loc: "knmt.loc",      // {lat,lng,label}
  orsKey: "knmt.orsKey",
};

const state = {
  all: [],
  origin: load(LS.loc),          // {lat,lng,label} | null
  orsKey: localStorage.getItem(LS.orsKey) || "",
  driveMin: {},                  // slug -> minutes (for current origin)
};

const $ = (sel) => document.querySelector(sel);
function load(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } }

// ---------- data ----------
async function init() {
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    const data = await res.json();
    state.all = data.vacancies || [];
    $("#meta").textContent =
      `${data.count} vacatures · bijgewerkt ${fmtDate(data.generated_at)}`;
    fillSelect($("#f-area"), data.facets?.work_area, "Alle regio's");
    fillSelect($("#f-emp"), data.facets?.employment_type, "Alle dienstverbanden");
  } catch (e) {
    $("#list").innerHTML = `<p class="empty">Kon data.json niet laden.<br>${e}</p>`;
    return;
  }
  bindUI();
  refreshLocStatus();
  if (state.orsKey) $("#ors-key").value = state.orsKey;
  render();
}

function fillSelect(sel, values, allLabel) {
  if (!values) return;
  sel.innerHTML = `<option value="">${allLabel}</option>` +
    values.map((v) => `<option>${esc(v)}</option>`).join("");
}

// ---------- filtering / sorting ----------
function currentList() {
  const q = $("#search").value.trim().toLowerCase();
  const area = $("#f-area").value;
  const emp = $("#f-emp").value;
  const minH = parseInt($("#f-hours").value, 10) || 0;
  const sort = $("#sort").value;

  let list = state.all.filter((v) => {
    if (area && !(v.work_areas || []).includes(area)) return false;
    if (emp && v.employment_type !== emp) return false;
    if (minH && !(v.hours_max >= minH)) return false;
    if (q) {
      const hay = `${v.title} ${v.practice} ${v.city} ${v.description} ${v.requirements} ${v.what_we_offer}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  for (const v of list) v._dist = distanceKm(v);
  list.sort((a, b) => {
    if (sort === "title") return (a.title || "").localeCompare(b.title || "");
    if (sort === "distance") {
      const da = bestMetric(a), db = bestMetric(b);
      return (da ?? 1e9) - (db ?? 1e9);
    }
    return (b.date_posted || "").localeCompare(a.date_posted || "");
  });
  return list;
}

function bestMetric(v) {
  if (state.driveMin[v.slug] != null) return state.driveMin[v.slug];
  return v._dist;
}

function render() {
  const list = currentList();
  const main = $("#list");
  main.innerHTML = "";
  if (!list.length) { main.innerHTML = `<p class="empty">Geen vacatures gevonden.</p>`; }
  const tpl = $("#card-tpl");
  for (const v of list) main.appendChild(card(tpl, v));
  $("#count").textContent = `${list.length} van ${state.all.length} getoond`;
}

function card(tpl, v) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  el.querySelector(".title").textContent = v.title || v.slug;
  const subBits = [v.city, v.practice, v.date_posted ? `geplaatst ${v.date_posted}` : ""].filter(Boolean);
  el.querySelector(".sub").textContent = subBits.join(" · ");

  const tags = [v.work_area, v.employment_type, v.hours].filter(Boolean);
  el.querySelector(".tags").innerHTML = tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("");

  const distEl = el.querySelector(".dist");
  distEl.textContent = metricLabel(v);

  el.querySelector(".excerpt").textContent = trim(v.description, 220);

  fillBlock(el.querySelector(".offer"), "Wat wij bieden", v.what_we_offer);
  fillBlock(el.querySelector(".req"), "Functie-eisen", v.requirements);
  fillBlock(el.querySelector(".desc"), "Omschrijving", v.description);
  const c = [v.contact_name, v.contact_email && `<a href="mailto:${esc(v.contact_email)}">${esc(v.contact_email)}</a>`,
             v.contact_phone && `<a href="tel:${esc(v.contact_phone)}">${esc(v.contact_phone)}</a>`].filter(Boolean);
  el.querySelector(".contact").innerHTML = c.length ? "Contact: " + c.join(" · ") : "";

  el.querySelector(".open").href = v.url;
  const more = el.querySelector(".more"), btn = el.querySelector(".toggle");
  btn.addEventListener("click", () => {
    const open = more.hidden;
    more.hidden = !open;
    btn.textContent = open ? "Minder ▴" : "Meer ▾";
  });
  return el;
}

function fillBlock(node, label, text) {
  if (!text) { node.hidden = true; return; }
  node.innerHTML = `<h3>${label}</h3><div>${esc(text)}</div>`;
}

function metricLabel(v) {
  if (state.driveMin[v.slug] != null) return `🚗 ${Math.round(state.driveMin[v.slug])} min`;
  const d = distanceKm(v);
  return d == null ? "" : `📍 ${d.toFixed(d < 10 ? 1 : 0)} km`;
}

// ---------- distance / travel ----------
function distanceKm(v) {
  if (!state.origin || v.lat == null || v.lng == null) return null;
  const R = 6371, toRad = (x) => (x * Math.PI) / 180;
  const dLat = toRad(v.lat - state.origin.lat), dLng = toRad(v.lng - state.origin.lng);
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(state.origin.lat)) * Math.cos(toRad(v.lat)) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

async function geocodeUser(text) {
  const url = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" +
    encodeURIComponent(text);
  const r = await fetch(url, { headers: { "Accept-Language": "nl" } });
  const d = await r.json();
  if (!d.length) throw new Error("plaats niet gevonden");
  return { lat: +d[0].lat, lng: +d[0].lon, label: d[0].display_name.split(",").slice(0, 2).join(",") };
}

async function setLocation(text) {
  $("#loc-status").textContent = "Locatie opzoeken…";
  try {
    state.origin = await geocodeUser(text);
    localStorage.setItem(LS.loc, JSON.stringify(state.origin));
    state.driveMin = {};
    refreshLocStatus();
    render();
    if (state.orsKey) computeDriveTimes();
  } catch (e) {
    $("#loc-status").textContent = "Kon locatie niet vinden.";
  }
}

function refreshLocStatus() {
  const s = $("#loc-status");
  s.textContent = state.origin
    ? `Afstanden vanaf: ${state.origin.label}${state.orsKey ? " · rijtijd aan" : ""}`
    : "Geen locatie ingesteld — stel je plaats in voor afstand/reistijd.";
  if (state.origin) $("#loc-input").value = state.origin.label;
}

// OpenRouteService driving-time matrix (optional). Chunked, one origin → many dests.
async function computeDriveTimes() {
  if (!state.orsKey || !state.origin) return;
  const dests = state.all.filter((v) => v.lat != null && v.lng != null);
  $("#loc-status").textContent = "Rijtijden berekenen…";
  const CH = 48;
  try {
    for (let i = 0; i < dests.length; i += CH) {
      const batch = dests.slice(i, i + CH);
      const locations = [[state.origin.lng, state.origin.lat],
        ...batch.map((v) => [v.lng, v.lat])];
      const res = await fetch("https://api.openrouteservice.org/v2/matrix/driving-car", {
        method: "POST",
        headers: { "Authorization": state.orsKey, "Content-Type": "application/json" },
        body: JSON.stringify({
          locations, sources: [0],
          destinations: batch.map((_, k) => k + 1), metrics: ["duration"],
        }),
      });
      if (!res.ok) throw new Error("ORS " + res.status);
      const data = await res.json();
      const row = data.durations?.[0] || [];
      batch.forEach((v, k) => { if (row[k] != null) state.driveMin[v.slug] = row[k] / 60; });
      render();
    }
    refreshLocStatus();
  } catch (e) {
    $("#loc-status").textContent = "Rijtijd mislukt (" + e.message + ") — toon afstand.";
  }
}

// ---------- UI wiring ----------
function bindUI() {
  let t;
  const deb = () => { clearTimeout(t); t = setTimeout(render, 150); };
  $("#search").addEventListener("input", deb);
  for (const id of ["#f-area", "#f-emp", "#f-hours", "#sort"]) $(id).addEventListener("input", render);
  $("#loc-set").addEventListener("click", () => {
    const v = $("#loc-input").value.trim(); if (v) setLocation(v);
  });
  $("#loc-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#loc-set").click(); });
  $("#loc-clear").addEventListener("click", () => {
    state.origin = null; state.driveMin = {}; localStorage.removeItem(LS.loc);
    $("#loc-input").value = ""; refreshLocStatus(); render();
  });
  $("#ors-save").addEventListener("click", () => {
    state.orsKey = $("#ors-key").value.trim();
    localStorage.setItem(LS.orsKey, state.orsKey);
    refreshLocStatus();
    if (state.orsKey && state.origin) computeDriveTimes();
  });
}

// ---------- helpers ----------
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function trim(s, n) { s = s || ""; return s.length > n ? s.slice(0, n).trimEnd() + "…" : s; }
function fmtDate(iso) { try { return new Date(iso).toLocaleString("nl-NL", { dateStyle: "medium", timeStyle: "short" }); } catch { return iso; } }

init();
