"use strict";

const LS = {
  loc: "knmt.loc",      // {lat,lng,label}
  orsKey: "knmt.orsKey",
};

// Pre-filled OpenRouteService key (car travel-time) so you don't have to enter it.
// Free tier, low-stakes; rotate at openrouteservice.org if ever needed.
const DEFAULT_ORS_KEY =
  "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjZlMjk3MDFiYTY5MTQ0MzQ5YzBmYTk0NTg5ZmY3N2RlIiwiaCI6Im11cm11cjY0In0=";

const state = {
  all: [],
  origin: load(LS.loc),          // {lat,lng,label} | null
  orsKey: localStorage.getItem(LS.orsKey) || DEFAULT_ORS_KEY,
  driveMin: {},                  // slug -> minutes (for current origin)
  days: new Set(),               // selected weekday codes
};

const DAY_ORDER = ["ma", "di", "wo", "do", "vr", "za", "zo"];
const DAY_LABEL = { ma: "Ma", di: "Di", wo: "Wo", do: "Do", vr: "Vr", za: "Za", zo: "Zo" };

const $ = (sel) => document.querySelector(sel);
function load(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } }

// ---------- data ----------
async function init() {
  try {
    // Use the dataset the cache-busting loader already fetched, if present.
    const data = window.__KNMT_DATA__
      || await (await fetch("data.json", { cache: "no-store" })).json();
    state.all = data.vacancies || [];
    $("#meta").textContent =
      `${data.count} vacatures · bijgewerkt ${fmtDate(data.generated_at)}`;
    fillSelect($("#f-area"), data.facets?.work_area, "Alle regio's");
    fillSelect($("#f-emp"), data.facets?.employment_type, "Alle dienstverbanden");
    state.practiceCount = {};
    for (const v of state.all) {
      const k = pKey(v);
      state.practiceCount[k] = (state.practiceCount[k] || 0) + 1;
    }
    populateSince();
  } catch (e) {
    $("#list").innerHTML = `<p class="empty">Kon data.json niet laden.<br>${e}</p>`;
    return;
  }
  bindUI();
  refreshLocStatus();
  if (state.orsKey) $("#ors-key").value = state.orsKey;
  // Filters expanded on desktop, collapsed on phones (where they'd eat the screen).
  $("#filters-panel").open = window.innerWidth >= 760;
  render();
}

// Count of active narrowing filters, shown on the collapsed Filters summary.
function updateFilterSummary() {
  let n = 0;
  if ($("#search").value.trim()) n++;
  if ($("#f-area").value) n++;
  if ($("#f-emp").value) n++;
  if (parseInt($("#f-hours").value, 10) > 0) n++;
  if ($("#f-since").value) n++;
  if ($("#f-reviews").checked) n++;
  if ($("#f-direct").checked) n++;
  if (state.days.size) n += state.days.size;
  $("#filters-summary").innerHTML =
    "⚙ Filters &amp; sortering" + (n ? ` <span class="fcount">${n} actief</span>` : "");
}

function fillSelect(sel, values, allLabel) {
  if (!values) return;
  sel.innerHTML = `<option value="">${allLabel}</option>` +
    values.map((v) => `<option>${esc(v)}</option>`).join("");
}

const MONTHS_NL = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
  "augustus", "september", "oktober", "november", "december"];

// Build the "Geplaatst vanaf" dropdown from the actual posting months in the data.
function populateSince() {
  const months = [...new Set(state.all.map((v) => (v.date_posted || "").slice(0, 7))
    .filter(Boolean))].sort().reverse();
  const opts = months.map((ym) => {
    const [y, m] = ym.split("-");
    return `<option value="${ym}">${MONTHS_NL[+m - 1]} ${y}</option>`;
  }).join("");
  $("#f-since").innerHTML = `<option value="">alle datums</option>` + opts;
}

// ---------- filtering / sorting ----------
function currentList() {
  const q = $("#search").value.trim().toLowerCase();
  const area = $("#f-area").value;
  const emp = $("#f-emp").value;
  const minH = parseInt($("#f-hours").value, 10) || 0;
  const sort = $("#sort").value;

  const includeNegot = $("#f-negot").checked;
  const onlyReviews = $("#f-reviews").checked;
  const onlyDirect = $("#f-direct").checked;
  const since = $("#f-since").value;   // "YYYY-MM" cutoff, or ""
  let list = state.all.filter((v) => {
    if (area && !(v.work_areas || []).includes(area)) return false;
    if (emp && v.employment_type !== emp) return false;
    if (minH && !(v.hours_max >= minH)) return false;
    if (onlyReviews && !(v.enrichment && v.enrichment.rating)) return false;
    if (onlyDirect && v.start_sort !== "0000-00-00") return false;
    if (since && (v.date_posted || "") < since) return false;
    if (state.days.size) {
      const vdays = v.days || [];
      const explicit = vdays.some((d) => state.days.has(d));
      const flexible = includeNegot && (v.days_negotiable || vdays.length === 0);
      if (!explicit && !flexible) return false;
    }
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
    if (sort === "rating") {
      const ra = (a.enrichment && a.enrichment.rating) || -1;
      const rb = (b.enrichment && b.enrichment.rating) || -1;
      return rb - ra;
    }
    if (sort === "start") {
      return (a.start_sort || "9999-99-99").localeCompare(b.start_sort || "9999-99-99");
    }
    return (b.date_posted || "").localeCompare(a.date_posted || "");
  });
  return list;
}

function bestMetric(v) {
  if (state.driveMin[v.slug] != null) return state.driveMin[v.slug];
  return v._dist;
}

function pKey(v) { return `${(v.practice || "?").toLowerCase()}|${(v.city || "").toLowerCase()}`; }

function render() {
  updateFilterSummary();
  const list = currentList();
  const main = $("#list");
  main.innerHTML = "";
  if (!list.length) {
    main.innerHTML = `<p class="empty">Geen vacatures gevonden.</p>`;
    $("#count").textContent = `0 van ${state.all.length} getoond`;
    return;
  }
  if ($("#f-group").checked) {
    const groups = new Map();
    for (const v of list) {
      const k = pKey(v);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(v);
    }
    for (const items of groups.values()) main.appendChild(groupCard(items));
    $("#count").textContent = `${groups.size} praktijken · ${list.length} vacatures`;
  } else {
    const tpl = $("#card-tpl");
    for (const v of list) main.appendChild(card(tpl, v));
    $("#count").textContent = `${list.length} van ${state.all.length} getoond`;
  }
}

// Tags shown on a card/row. `withRating` adds the practice rating (skipped inside groups).
function tagsHtml(v, withRating = true) {
  const out = [v.work_area, v.employment_type, v.hours].filter(Boolean)
    .map((t) => `<span class="tag">${esc(t)}</span>`);
  if (v.start_label) {
    const cls = v.start_sort === "0000-00-00" ? "tag start direct" : "tag start";
    out.push(`<span class="${cls}">📅 ${esc(v.start_label)}${v.end_label ? "–" + esc(v.end_label) : ""}</span>`);
  } else if (v.temporary) {
    out.push(`<span class="tag start">tijdelijk</span>`);
  }
  if (v.days) out.push(...v.days.map((d) => `<span class="tag day">${DAY_LABEL[d] || d}</span>`));
  if (v.days_negotiable) out.push(`<span class="tag negot">in overleg</span>`);
  if (withRating && v.enrichment && v.enrichment.rating) {
    const e = v.enrichment;
    out.push(`<span class="tag rating">★ ${e.rating}${e.reviews ? " · " + e.reviews : ""}</span>`);
  }
  return out.join("");
}

// Fills the expandable details (offer/req/desc/contact), open link and toggle button.
function wireDetails(el, v, label) {
  fillBlock(el.querySelector(".offer"), "Wat wij bieden", v.what_we_offer);
  fillBlock(el.querySelector(".req"), "Functie-eisen", v.requirements);
  fillBlock(el.querySelector(".desc"), "Omschrijving", v.description);
  const c = [v.contact_name,
    v.contact_email && `<a href="mailto:${esc(v.contact_email)}">${esc(v.contact_email)}</a>`,
    v.contact_phone && `<a href="tel:${esc(v.contact_phone)}">${esc(v.contact_phone)}</a>`].filter(Boolean);
  el.querySelector(".contact").innerHTML = c.length ? "Contact: " + c.join(" · ") : "";
  el.querySelector(".open").href = v.url;
  const more = el.querySelector(".more"), btn = el.querySelector(".toggle");
  btn.addEventListener("click", () => {
    more.hidden = !more.hidden;
    btn.textContent = more.hidden ? `${label} ▾` : `Minder ▴`;
  });
}

function card(tpl, v) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  el.querySelector(".practice").textContent = v.practice || v.title || v.slug;
  const n = state.practiceCount[pKey(v)] || 1;
  const locBits = [v.city, v.date_posted ? `geplaatst ${v.date_posted}` : "",
    n > 1 ? `${n} vacatures bij deze praktijk` : ""].filter(Boolean);
  el.querySelector(".loc-line").innerHTML = locBits
    .map((b, i) => i === 2 ? `<span class="multi">${esc(b)}</span>` : esc(b)).join(" · ");
  el.querySelector(".vac-title").textContent = v.title || "";
  el.querySelector(".tags").innerHTML = tagsHtml(v);
  el.querySelector(".dist").textContent = metricLabel(v);
  el.querySelector(".excerpt").textContent = trim(v.description, 220);
  renderEnrichment(el.querySelector(".practice-info"), v.enrichment);
  wireDetails(el, v, "Meer");
  return el;
}

function groupCard(items) {
  const v0 = items[0];
  const el = $("#group-tpl").content.firstElementChild.cloneNode(true);
  el.querySelector(".practice").textContent = v0.practice || "Onbekende praktijk";
  el.querySelector(".loc-line").innerHTML =
    `${esc(v0.city || "")} · <span class="multi">${items.length} vacature${items.length > 1 ? "s" : ""}</span>`;
  el.querySelector(".dist").textContent = metricLabel(v0);
  const e = v0.enrichment;
  el.querySelector(".gtags").innerHTML = e && e.rating
    ? `<span class="tag rating">★ ${e.rating}${e.reviews ? " · " + e.reviews : ""}</span>` : "";
  renderEnrichment(el.querySelector(".practice-info"), v0.enrichment);

  const tabsEl = el.querySelector(".gtabs");
  const contentEl = el.querySelector(".gtabcontent");
  const rt = $("#vacrow-tpl");

  function show(i) {
    contentEl.innerHTML = "";
    contentEl.appendChild(vacRow(rt, items[i]));
    [...tabsEl.children].forEach((b, j) => b.classList.toggle("active", j === i));
  }

  if (items.length === 1) {
    tabsEl.hidden = true;
    contentEl.appendChild(vacRow(rt, items[0]));
  } else {
    items.forEach((v, i) => {
      const b = document.createElement("button");
      b.className = "gtab";
      b.type = "button";
      b.setAttribute("role", "tab");
      const label = trim((v.title || "").trim(), 22) || `Vacature ${i + 1}`;
      b.innerHTML = `<span class="gtab-n">${i + 1}</span> ${esc(label)}`;
      b.addEventListener("click", () => show(i));
      tabsEl.appendChild(b);
    });
    show(0);
  }
  return el;
}

function vacRow(tpl, v) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  el.querySelector(".vac-title").textContent =
    (v.date_posted ? `${v.date_posted} · ` : "") + (v.title || v.slug);
  el.querySelector(".tags").innerHTML = tagsHtml(v, false);
  el.querySelector(".excerpt").textContent = trim(v.description, 160);
  wireDetails(el, v, "Details");
  return el;
}

function fillBlock(node, label, text) {
  if (!text) { node.hidden = true; return; }
  node.innerHTML = `<h3>${label}</h3><div>${esc(text)}</div>`;
}

function renderEnrichment(node, enr) {
  if (!enr) { node.hidden = true; return; }
  const rows = [];
  if (enr.website) {
    const host = enr.website.replace(/^https?:\/\/(www\.)?/, "");
    rows.push(`🌐 <a href="${esc(enr.website)}" target="_blank" rel="noopener">${esc(host)}</a>`);
  }
  if (enr.rating) {
    const link = enr.zorgkaart_url
      ? `<a href="${esc(enr.zorgkaart_url)}" target="_blank" rel="noopener">${enr.reviews || "?"} reviews</a>`
      : `${enr.reviews || "?"} reviews`;
    rows.push(`⭐ <b>${enr.rating}/10</b> · ${link} <span class="src">(Zorgkaart)</span>`);
  }
  if (enr.kvk) {
    rows.push(`🏢 KvK ${esc(enr.kvk)}${enr.kvk_url ? ` · <a href="${esc(enr.kvk_url)}" target="_blank" rel="noopener">kvk.nl</a>` : ""}`);
  }
  if (enr.emails && enr.emails.length) {
    rows.push(`✉️ ${enr.emails.slice(0, 2).map((e) => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join(", ")}`);
  }

  // Team grid (photos + names + confirmed BIG). Names are best-effort from the site.
  let teamHtml = "";
  const team = enr.team || [];
  if (team.length) {
    const bigByName = {};
    (enr.big_checks || []).forEach((c) => { bigByName[c.name] = c; });
    const cards = team.map((p) => {
      const v = bigByName[p.name];
      const reg = v && v.status === "registered";
      const photo = p.photo
        ? `<img class="pphoto" src="${esc(p.photo)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'pphoto ph',textContent:'👤'}))">`
        : `<span class="pphoto ph">👤</span>`;
      const big = reg
        ? `<span class="big-badge big-ok" title="${esc(v.big_number || "")}">✓ BIG</span>` : "";
      return `<div class="person">${photo}<div class="pmeta">` +
        `<div class="pname">${esc(p.name)} ${big}</div>` +
        `<div class="ptitle">${esc(p.title || "")}</div></div></div>`;
    }).join("");
    teamHtml = `<h3>Team <span class="src">(namen best-effort van de site)</span></h3>` +
      `<div class="team-grid">${cards}</div>` +
      `<div class="big-warn-row">✓ BIG = automatisch bevestigd in het BIG-register. ` +
      `Geen vinkje ≠ niet geregistreerd — ` +
      `<a href="https://www.bigregister.nl/zoek-zorgverlener" target="_blank" rel="noopener">zelf checken</a>.</div>`;
  }

  const photoHtml = enr.practice_photo
    ? `<img class="practice-photo" src="${esc(enr.practice_photo)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">`
    : "";
  node.hidden = false;
  node.innerHTML = `<h3>Over de praktijk</h3>${photoHtml}` +
    `<div class="enr-rows">${rows.join("<br>")}</div>` + teamHtml;
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
function buildDayToggles() {
  const box = $("#day-toggles");
  box.innerHTML = "";
  for (const d of DAY_ORDER) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "day-toggle";
    b.textContent = DAY_LABEL[d];
    b.addEventListener("click", () => {
      if (state.days.has(d)) { state.days.delete(d); b.classList.remove("on"); }
      else { state.days.add(d); b.classList.add("on"); }
      render();
    });
    box.appendChild(b);
  }
}

function bindUI() {
  let t;
  const deb = () => { clearTimeout(t); t = setTimeout(render, 150); };
  $("#search").addEventListener("input", deb);
  for (const id of ["#f-area", "#f-emp", "#f-hours", "#sort", "#f-since"]) $(id).addEventListener("input", render);
  for (const id of ["#f-negot", "#f-reviews", "#f-direct", "#f-group"]) $(id).addEventListener("change", render);
  buildDayToggles();
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
