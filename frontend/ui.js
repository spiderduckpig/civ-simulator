/**
 * ui.js — WebSocket client, UI event wiring, game loop
 * Connects to the FastAPI backend and drives the canvas renderer.
 */

import { renderFrame, getCellInfo } from "./renderer.js";

const W = 160, H = 100, CELL = 6;
const PX_W = W * CELL, PX_H = H * CELL;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const canvas       = document.getElementById("map");
const ctx          = canvas.getContext("2d");
const btnPlay      = document.getElementById("btn-play");
const btnReset     = document.getElementById("btn-reset");
const selSpeed     = document.getElementById("sel-speed");
const btnMapMode   = document.getElementById("btn-mapmode");
const selResGood   = document.getElementById("sel-resource-good");
const chkRes       = document.getElementById("chk-res");
const chkLevels    = document.getElementById("chk-levels");
const zoomIn       = document.getElementById("btn-zoom-in");
const zoomOut      = document.getElementById("btn-zoom-out");
const zoomReset    = document.getElementById("btn-zoom-reset");
const lblYear      = document.getElementById("lbl-year");
const lblNations   = document.getElementById("lbl-nations");
const lblWars      = document.getElementById("lbl-wars");
const lblZoom      = document.getElementById("lbl-zoom");
const tooltip      = document.getElementById("tooltip");
const nationList   = document.getElementById("nation-list");
const fallenList   = document.getElementById("fallen-list");
const civDetail    = document.getElementById("civ-detail");
const eventLog     = document.getElementById("event-log");
const settingsPanel = document.getElementById("settings-panel");
const btnSettings  = document.getElementById("btn-settings");
const btnDiplo     = document.getElementById("btn-diplo");
const diploModal   = document.getElementById("diplo-modal");
const diploBody    = document.getElementById("diplo-body");
const diploClose   = document.getElementById("diplo-close");

// ── Client state ──────────────────────────────────────────────────────────────
let mapData    = null;
let gameState  = { civs: [], wars: [], impr: [], tick: 0, log: [] };
let playing    = false;
let mapMode    = "terrain";
let zoom       = 1;
let viewOffset = { x: 0, y: 0 };
let isDragging = false;
let dragStart  = { x: 0, y: 0 };
let selectedId   = null;
let selectedCity = null;  // city object when a city cell is clicked
let hoveredCell  = -1;    // cell index under mouse, for live tooltip updates
let hoverScreenX = 0;
let hoverScreenY = 0;
let ws           = null;

// Size canvas to fill its container, re-run on resize
const mapWrap = document.getElementById("map-wrap");
function resizeCanvas() {
    canvas.width  = mapWrap.clientWidth;
    canvas.height = mapWrap.clientHeight;
    renderAll();
}
resizeCanvas();
new ResizeObserver(resizeCanvas).observe(mapWrap);

// ── WebSocket ─────────────────────────────────────────────────────────────────

const connStatus = document.getElementById("conn-status");

function connect() {
    connStatus.textContent = "Connecting...";
    connStatus.style.color = "#f0c040";
    ws = new WebSocket(`ws://${location.host}/ws`);

    ws.onopen = () => {
        connStatus.textContent = "Generating map...";
        connStatus.style.color = "#58a6ff";
    };

    ws.onerror = (e) => {
        connStatus.textContent = "WebSocket error";
        connStatus.style.color = "#f85149";
        console.error("WebSocket error:", e);
    };

    ws.onmessage = ({ data }) => {
        const msg = JSON.parse(data);
        if (msg.type === "map") {
            mapData = msg;
            mapData.rivers.cell_river = new Set(msg.rivers.cell_river);
            connStatus.textContent = "Map received";
            connStatus.style.color = "#3fb950";
        } else if (msg.type === "state") {
            gameState = msg;
            connStatus.style.display = "none"; // hide once running
            // Convert territory arrays to plain arrays (already serialised as arrays from backend)
            renderAll();
            updateUI();
        }
    };

    ws.onclose = () => {
        setTimeout(connect, 1000); // auto-reconnect
    };
}

// ── Render ────────────────────────────────────────────────────────────────────

function renderAll() {
    if (!mapData) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.translate(viewOffset.x, viewOffset.y);
    ctx.scale(zoom, zoom);
    try {
        renderFrame(ctx, mapData, gameState, {
            showRes: chkRes.checked,
            showLevels: chkLevels.checked,
            mapMode,
            resourceGood: selResGood.value,
            tick: gameState.tick,
            zoom,
            selectedCity,
        });
    } finally {
        ctx.restore();
    }

    // Live-refresh tooltip while hovering
    if (hoveredCell >= 0) {
        const info = getCellInfo(mapData, gameState, hoveredCell);
        showTooltip(hoverScreenX, hoverScreenY, info);
    }
}

// ── UI updates ────────────────────────────────────────────────────────────────

function updateUI() {
    const alive = gameState.civs.filter(c => c.alive);
    const wars  = gameState.wars;

    lblYear.textContent    = `Year ${gameState.tick}`;
    lblNations.textContent = `${alive.length} nations`;
    lblWars.textContent    = wars.length > 0 ? `⚔${wars.length}` : "";
    lblZoom.textContent    = `${zoom.toFixed(1)}×`;

    // Nation list
    const sorted = [...alive].sort((a, b) => b.territory.length - a.territory.length);
    nationList.innerHTML = sorted.map(civ => {
        const atWar = wars.some(w => w.att === civ.id || w.def_id === civ.id);
        const sel   = civ.id === selectedId;
        return `
        <div class="civ-row${sel ? " selected" : ""}" data-id="${civ.id}">
            <div class="civ-row-top">
                <span class="dot" style="background:${civ.color};${atWar ? "box-shadow:0 0 3px #f85149" : ""}"></span>
                <span class="civ-name">${civ.name}</span>
                ${atWar ? '<span class="war-badge">WAR</span>' : ""}
                <span class="civ-size">${civ.territory.length}</span>
            </div>
            <div class="civ-sub">${civ.cities.length}c · ${civ.population|0}p · 💰${civ.gold|0}</div>
        </div>`;
    }).join("");

    // Fallen
    const fallen = gameState.civs.filter(c => !c.alive).slice(-5);
    document.getElementById("fallen-header").style.display = fallen.length ? "" : "none";
    fallenList.innerHTML = fallen.map(c =>
        `<div class="civ-row fallen" data-id="${c.id}"><span style="color:${c.color}">†</span> ${c.name} (${c.age}yr)</div>`
    ).join("");

    // Event log
    const logLines = [...gameState.log].reverse().slice(0, 10);
    eventLog.innerHTML = logLines.map(e => `<div class="log-line">${e}</div>`).join("");

    // Civ detail panel
    if (selectedId !== null) {
        const civ = gameState.civs.find(c => c.id === selectedId);
        renderCivDetail(civ, wars);
    }

    // Live-refresh diplomacy modal if open
    if (diploModal && diploModal.style.display === "block") {
        renderDiploScreen();
    }

    // Attach click handlers to civ rows
    document.querySelectorAll(".civ-row[data-id]").forEach(el => {
        el.addEventListener("click", () => {
            selectedId = parseInt(el.dataset.id);
            selectedCity = null;
            updateUI();
            renderAll();
        });
    });

    // Attach click handlers to city rows in detail panel
    document.querySelectorAll(".detail-city[data-city-cell]").forEach(el => {
        el.addEventListener("click", (e) => {
            e.stopPropagation();
            const cellId = parseInt(el.dataset.cityCell);
            const civ2 = gameState.civs.find(c => c.id === selectedId);
            if (civ2) {
                const city = civ2.cities.find(c => c.cell === cellId);
                selectedCity = (selectedCity && selectedCity.cell === cellId) ? null : city;
                updateUI();
                renderAll();
            }
        });
    });
}

function renderCivDetail(civ, wars) {
    if (!civ) { civDetail.innerHTML = ""; return; }
    const myWars = wars.filter(w => w.att === civ.id || w.def_id === civ.id);
    const warInfo = myWars.length
        ? myWars.map(w => {
            const eid = w.att === civ.id ? w.def_id : w.att;
            const en  = gameState.civs.find(c => c.id === eid);
            const dur = gameState.tick - w.start;
            return `<div class="detail-war">${en?.name ?? "?"} (${w.att === civ.id ? "aggr" : "def"} ${dur}yr)</div>`;
          }).join("")
        : `<div class="detail-peace">🕊 Peace</div>`;

    const cities = [...civ.cities]
        .sort((a, b) => b.population - a.population)
        .map(c => {
            const isSel = selectedCity && selectedCity.cell === c.cell;
            return `<div class="detail-city${isSel ? " city-selected" : ""}" data-city-cell="${c.cell}">
                ${c.is_capital ? "★" : "•"} <b>${c.name}</b>
                <span>${c.population|0}p · 💰${c.gold|0} · 🍞${c.supply["food"]|0}/${c.demand["food"]|0}
                ${c.near_river ? "〰" : ""}${c.coastal ? "⚓" : ""}</span>
            </div>`;
        })
        .join("");

    const history = civ.events.slice(-4)
        .map(e => `<div class="detail-event">• ${e}</div>`)
        .join("");

    const aggr = civ.aggressiveness ?? 0.5;
    const aggrColor = aggr > 0.65 ? "#f85149" : aggr > 0.4 ? "#f0c040" : "#3fb950";
    const intColor = civ.integrity > 0.6 ? "#3fb950" : "#f85149";
    const allyNames = (civ.allies || [])
        .map(aid => gameState.civs.find(c => c.id === aid))
        .filter(Boolean)
        .map(a => `<span style="color:${a.color}">${a.name}</span>`)
        .join(", ");

    civDetail.innerHTML = `
        <div class="detail-header">
            <span class="dot" style="background:${civ.color}"></span>
            <span class="detail-name">${civ.name}</span>
            ${!civ.alive ? '<span class="fallen-badge">FALLEN</span>' : ""}
        </div>
        ${civ.parent_name ? `<div class="detail-sub">From ${civ.parent_name}</div>` : ""}
        <div class="detail-stats">
            <div>👑 Leader: ${civ.leader}</div>
            <div>👥 Pop: ${civ.population|0} · ⚔ Military: ${civ.military|0} · 📐 Land: ${civ.territory.length}</div>
            <div>💰 Gold: ${civ.gold|0} · 🍞 Food Out: ${civ.farm_output|0}</div>
            <div>🔬 Tech: ${civ.tech.toFixed(1)} · 🎭 Culture: ${civ.culture.toFixed(1)}</div>
            <div>🛡 Integrity: <span style="color:${intColor}">${(civ.integrity*100)|0}%</span> · 💢 Aggressiveness: <span style="color:${aggrColor}">${(aggr*100)|0}%</span></div>
            <div>⚡ Power: ${civ.power|0}</div>
            <div>⛏ Ore/Metal: ${civ.ore_output|0}/${civ.metal_output|0} · 🧱 Stone: ${civ.stone_output|0} · 🛤 Roads: ${civ.roads.length}</div>
            ${allyNames ? `<div>🤝 Allies: ${allyNames}</div>` : ""}
        </div>
        ${cities ? `<div class="detail-section-label">CITIES (${civ.cities.length})</div>${cities}` : ""}
        ${myWars.length ? `<div class="detail-section-label war-label">⚔ WARS</div>${warInfo}` : warInfo}
        ${history ? `<div class="detail-section-label">HISTORY</div>${history}` : ""}
    `;
}

// ── Diplomacy modal ───────────────────────────────────────────────────────────

function relColor(r) {
    // -1 (deep red) → 0 (neutral gray) → +1 (deep green)
    if (r >= 0) {
        const t = Math.min(1, r);
        const g = (80 + t * 120) | 0;
        return `rgb(40, ${g}, 40)`;
    } else {
        const t = Math.min(1, -r);
        const rc = (80 + t * 130) | 0;
        return `rgb(${rc}, 40, 40)`;
    }
}

function renderDiploScreen() {
    const alive = gameState.civs.filter(c => c.alive);
    if (!alive.length) {
        diploBody.innerHTML = `<div style="padding:20px;color:#8b949e">No nations yet.</div>`;
        return;
    }

    const wars = gameState.wars || [];
    const warKeyOf = (a, b) => {
        const lo = Math.min(a, b), hi = Math.max(a, b);
        return `${lo}|${hi}`;
    };
    const warMap = new Map();
    for (const w of wars) warMap.set(warKeyOf(w.att, w.def_id), w);

    // Matrix of relations (symmetric — use the row civ's stored value)
    const headRow = `<tr><th class="row-head"></th>${alive.map(c =>
        `<th style="color:${c.color}">${c.name}</th>`
    ).join("")}</tr>`;

    const rows = alive.map(a => {
        const cells = alive.map(b => {
            if (a.id === b.id) {
                return `<td class="rel-cell rel-self">—</td>`;
            }
            const wkey = warKeyOf(a.id, b.id);
            const atWar = warMap.has(wkey);
            const allied = (a.allies || []).includes(b.id);
            const rel = (a.relations && a.relations[String(b.id)]) ?? 0;
            let marker = "";
            if (atWar) marker = `<div style="color:#ff6b6b;font-weight:700">⚔</div>`;
            else if (allied) marker = `<div style="color:#58a6ff">🤝</div>`;
            const bg = relColor(rel);
            return `<td class="rel-cell" style="background:${bg};color:#fff">
                ${(rel * 100).toFixed(0)}${marker}
            </td>`;
        }).join("");
        return `<tr><th class="row-head" style="color:${a.color}">${a.name}</th>${cells}</tr>`;
    }).join("");

    const matrix = `<table>${headRow}${rows}</table>`;

    // Power ranking
    const byPower = [...alive].sort((a, b) => (b.power || 0) - (a.power || 0));
    const maxPow = Math.max(1, byPower[0].power || 0);
    const powerList = byPower.map(c => {
        const w = Math.max(4, ((c.power || 0) / maxPow * 140) | 0);
        return `<div class="power-row">
            <span class="dot" style="background:${c.color}"></span>
            <span class="pname">${c.name}</span>
            <span class="pbar" style="width:${w}px"><span style="width:100%;background:${c.color}"></span></span>
            <span style="color:#8b949e">${(c.power||0)|0}</span>
        </div>`;
    }).join("");

    // Active wars
    const warCards = wars.length ? wars.map(w => {
        const att = alive.find(c => c.id === w.att);
        const dfn = alive.find(c => c.id === w.def_id);
        if (!att || !dfn) return "";
        const dur = gameState.tick - w.start;
        const confA = w.confidence_a ?? 0.5;
        const confD = w.confidence_d ?? 0.5;
        const exhA  = w.exhaustion_a ?? 0;
        const exhD  = w.exhaustion_d ?? 0;
        const moraleBar = (conf, exh, col) => {
            const cWidth = Math.max(0, Math.min(100, conf * 100)) | 0;
            const eWidth = Math.max(0, Math.min(100, exh  * 100)) | 0;
            return `
              <div class="morale-bar" title="Confidence ${(conf*100)|0}%"><span style="width:${cWidth}%;background:${col}"></span></div>
              <div class="morale-bar" title="Exhaustion ${(exh*100)|0}%"><span style="width:${eWidth}%;background:#d73a49"></span></div>
            `;
        };
        return `<div class="war-card">
            <div class="war-head">
                <span class="war-name" style="color:${att.color}">${att.name}</span>
                <span class="war-role agg">Aggressor</span>
                <span style="color:#8b949e">vs</span>
                <span class="war-name" style="color:${dfn.color}">${dfn.name}</span>
                <span class="war-role def">Defender</span>
                <span style="color:#6e7681;font-size:9px">· ${dur}yr</span>
            </div>
            <div style="display:grid;grid-template-columns:90px 1fr;gap:2px 8px;font-size:9px;color:#8b949e">
                <div>${att.name}</div>
                <div>Conf ${(confA*100)|0}% · Exh ${(exhA*100)|0}% ${moraleBar(confA, exhA, att.color)}</div>
                <div>${dfn.name}</div>
                <div>Conf ${(confD*100)|0}% · Exh ${(exhD*100)|0}% ${moraleBar(confD, exhD, dfn.color)}</div>
            </div>
        </div>`;
    }).join("") : `<div style="color:#3fb950">🕊 No active wars.</div>`;

    diploBody.innerHTML = `
        <div class="diplo-section">Relation Matrix <span style="font-weight:400;color:#6e7681;text-transform:none">(row's view of column · ⚔ war · 🤝 ally)</span></div>
        ${matrix}
        <div class="diplo-section">Active Wars</div>
        <div class="war-list">${warCards}</div>
        <div class="diplo-section">Power Ranking</div>
        <div class="power-list">${powerList}</div>
    `;
}

// ── Canvas interaction ────────────────────────────────────────────────────────

canvas.addEventListener("wheel", e => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const d  = e.deltaY > 0 ? 0.88 : 1.14;
    const nz = Math.max(0.3, Math.min(6, zoom * d));
    const s  = nz / zoom;
    viewOffset.x = mx - (mx - viewOffset.x) * s;
    viewOffset.y = my - (my - viewOffset.y) * s;
    zoom = nz;
    lblZoom.textContent = `${zoom.toFixed(1)}×`;
    renderAll();
}, { passive: false });

canvas.addEventListener("mousedown", e => {
    isDragging = true;
    dragStart  = { x: e.clientX, y: e.clientY };
});
canvas.addEventListener("mouseup",    () => isDragging = false);
canvas.addEventListener("mouseleave", () => { isDragging = false; tooltip.style.display = "none"; hoveredCell = -1; });

canvas.addEventListener("mousemove", e => {
    const rect = canvas.getBoundingClientRect();
    if (isDragging) {
        viewOffset.x += e.clientX - dragStart.x;
        viewOffset.y += e.clientY - dragStart.y;
        dragStart = { x: e.clientX, y: e.clientY };
        renderAll();
        return;
    }
    if (!mapData) return;
    const mx  = (e.clientX - rect.left - viewOffset.x) / zoom;
    const my  = (e.clientY - rect.top  - viewOffset.y) / zoom;
    const gx  = (mx / CELL) | 0;
    const gy  = (my / CELL) | 0;
    if (gx < 0 || gx >= W || gy < 0 || gy >= H) { tooltip.style.display = "none"; hoveredCell = -1; return; }
    hoveredCell = gy * W + gx;
    hoverScreenX = e.clientX - rect.left;
    hoverScreenY = e.clientY - rect.top;
    const info = getCellInfo(mapData, gameState, hoveredCell);
    showTooltip(hoverScreenX, hoverScreenY, info);
});

canvas.addEventListener("click", e => {
    if (isDragging) return;
    const rect = canvas.getBoundingClientRect();
    const mx  = (e.clientX - rect.left - viewOffset.x) / zoom;
    const my  = (e.clientY - rect.top  - viewOffset.y) / zoom;
    const gx  = (mx / CELL) | 0;
    const gy  = (my / CELL) | 0;
    if (gx < 0 || gx >= W || gy < 0 || gy >= H || !gameState) return;
    const cell = gy * W + gx;
    const om = new Int32Array(W * H);
    for (const civ of gameState.civs) {
        if (!civ.alive) continue;
        for (const c of civ.territory) om[c] = civ.id;
    }
    selectedId = om[cell] || null;

    // Check if clicked on a city cell
    selectedCity = null;
    if (selectedId) {
        const civ = gameState.civs.find(c => c.id === selectedId);
        if (civ) {
            const city = civ.cities.find(c => c.cell === cell);
            if (city) selectedCity = city;
        }
    }
    updateUI();
    renderAll();
});

function showTooltip(x, y, info) {
    let lines = [
        `<div class="tt-coord">(${info.x},${info.y}) ${info.terrain} ${info.alt}m</div>`,
        info.river   ? `<div style="color:#4aaef0">〰 River</div>` : "",
        info.coastal ? `<div style="color:#1a6a9e">⚓ Coastal</div>` : "",
        info.imp.name !== "—" ? `<div style="color:#c8a000">${info.imp.name} Lv.${info.imp.level}${info.imp.detail ? ` — ${info.imp.detail}` : ""}</div>` : "",
        info.imp.employees ? `<div style="color:#9a8fb8">${info.imp.employees}</div>` : "",
        info.imp.efficiency ? `<div style="color:#8dd9c7">${info.imp.efficiency}</div>` : "",
        info.res   ? `<div>${info.res}</div>` : "",
        info.civ   ? `<div style="color:${info.civ.color};font-weight:600">${info.civ.name}</div>` : "",
    ];
    if (info.city) {
        const c = info.city;
        const s = c.stats;
        const tags = [];
        if (c.is_capital) tags.push("★ Capital");
        if (c.river_mouth) tags.push("🏞 River Mouth");
        else if (c.near_river) tags.push("〰 River");
        if (c.coastal) tags.push("⚓ Coast");

        lines.push(`<div style="font-weight:600;margin-top:2px">🏘 ${c.name}</div>`);
        if (tags.length) lines.push(`<div style="color:#8b949e;font-size:9px">${tags.join(" · ")}</div>`);
        lines.push(`<div>👥 Pop ${c.pop}${c.founded ? ` · Est. yr ${c.founded}` : ""} · 💰 Gold ${c.gold}</div>`);

        // ── Employment ────────────────────────────────────────────────
        const workforce = c.workforce | 0;
        const employed = c.employed_pop | 0;
        const unemployed = c.unemployed_pop | 0;
        const unempColor = unemployed === 0 ? "#3fb950" : (unemployed > employed ? "#f85149" : "#f0c040");
        lines.push(`<div style="margin-top:4px; border-top:1px solid #30363d; padding-top:4px; font-size:10px; color:#8b949e">Employment</div>`);
        lines.push(`<div style="font-size:10px">💼 Workforce ${workforce * 20} · <span style="color:#3fb950">Employed ${employed}</span> · <span style="color:${unempColor}">Unemployed ${unemployed}</span></div>`);

        // ── Migration ─────────────────────────────────────────────────
        const pull = c.attractiveness ?? 1.0;
        const netMig = c.net_migration ?? 0.0;
        const pullColor = pull > 1.3 ? "#3fb950" : (pull < 0.7 ? "#f85149" : "#f0c040");
        const migColor = netMig > 0.05 ? "#3fb950" : (netMig < -0.05 ? "#f85149" : "#8b949e");
        const migArrow = netMig > 0.05 ? "↑" : (netMig < -0.05 ? "↓" : "·");
        const migLabel = netMig > 0.05 ? `+${netMig.toFixed(1)} incoming` : (netMig < -0.05 ? `${netMig.toFixed(1)} leaving` : "steady");
        lines.push(`<div style="margin-top:4px; border-top:1px solid #30363d; padding-top:4px; font-size:10px; color:#8b949e">Migration</div>`);
        lines.push(`<div style="font-size:10px">🧭 Pull <span style="color:${pullColor}">${pull.toFixed(2)}</span> · <span style="color:${migColor}">${migArrow} ${migLabel}</span></div>`);

        // ── Income breakdown ──────────────────────────────────────────
        const incColor = c.income_total > 0 ? "#3fb950" : c.income_total < 0 ? "#f85149" : "#8b949e";
        lines.push(`<div style="margin-top:4px; border-top:1px solid #30363d; padding-top:4px; font-size:10px; color:#8b949e">Income (gold/tick)</div>`);
        lines.push(`<div style="font-size:10px">Net <span style="color:${incColor}">₿${c.income_total.toFixed(2)}</span> · Per person <span style="color:${incColor}">₿${c.income_per_person.toFixed(3)}</span></div>`);
        const goodsIn = ["food", "lumber", "ore", "stone", "metal"];
        const icons2 = {food:"🍞", lumber:"🪵", ore:"⚙", stone:"🧱", metal:"🗡"};
        for (const g of goodsIn) {
            const dom = c.income_domestic[g] || 0;
            const exp = c.income_export[g] || 0;
            const imp = c.income_import[g] || 0;
            if (dom < 0.05 && exp < 0.05 && imp < 0.05) continue;
            const net = dom + exp - imp;
            const nColor = net >= 0 ? "#3fb950" : "#f85149";
            const parts = [];
            if (dom >= 0.05) parts.push(`<span style="color:#c9d1d9">prod ${dom.toFixed(1)}</span>`);
            if (exp >= 0.05) parts.push(`<span style="color:#d299ff">exp +${exp.toFixed(1)}</span>`);
            if (imp >= 0.05) parts.push(`<span style="color:#58a6ff">imp -${imp.toFixed(1)}</span>`);
            lines.push(`<div style="font-size:9px; display:flex; justify-content:space-between">
                <span>${icons2[g]} ${g}</span>
                <span>${parts.join(" · ")} = <span style="color:${nColor}">${net.toFixed(1)}</span></span>
            </div>`);
        }
        if (c.income_misc >= 0.05) {
            lines.push(`<div style="font-size:9px">✦ Gold resource +${c.income_misc.toFixed(1)}</div>`);
        }

        const goods = ["food", "lumber", "ore", "stone", "metal"];
        const icons = {food:"🍞", lumber:"🪵", ore:"⚙", stone:"🧱", metal:"🗡"};
        
        lines.push(`<div style="margin-top:4px; border-top:1px solid #30363d; padding-top:4px; font-size:10px; color:#8b949e">Local Market (Supply / Demand · Price)</div>`);

        for (const g of goods) {
            const supply = c.supply[g] || 0;
            const demand = c.demand[g] || 0;
            const price = c.prices[g] || 1.0;
            const color = price < 1.0 ? "#3fb950" : (price > 2.0 ? "#f85149" : "#f0c040");

            // Trade summary for this good — sum across all partners this tick
            // so a city trading with 3 neighbours doesn't flicker between them.
            let tradeLine = "";
            const trades = c.last_trades[g];
            if (trades && trades.length > 0) {
                let totalVol = 0;
                let priceSum = 0;
                for (const [vol, , p] of trades) {
                    totalVol += vol;
                    priceSum += p * Math.abs(vol);
                }
                const absVol = Math.abs(totalVol);
                if (absVol >= 0.05) {
                    const avgP = priceSum / Math.max(0.001, trades.reduce((a, [v]) => a + Math.abs(v), 0));
                    const type = totalVol > 0 ? "Import" : "Export";
                    const tColor = totalVol > 0 ? "#58a6ff" : "#d299ff";
                    const partners = trades.length > 1 ? `×${trades.length}` : "";
                    tradeLine = `<span style="color:${tColor}; margin-left:4px; font-size:9px">[${type}${partners} ${absVol.toFixed(1)} @ ₿${avgP.toFixed(2)}]</span>`;
                }
            }

            lines.push(`<div style="display:flex; justify-content:space-between; font-size:10px">
                <span>${icons[g]} ${g.toUpperCase()}${tradeLine}</span>
                <span><span style="color:#fff">${supply.toFixed(1)} / ${demand.toFixed(1)}</span> · <span style="color:${color}">₿${price.toFixed(2)}</span></span>
            </div>`);
        }

        if (s) {
            const imps = [];
            if (s.farms)      imps.push(`🌾 ${s.farms} farms${s.avgFarmLvl > 1 ? ` (avg Lv.${s.avgFarmLvl})` : ""}`);
            if (s.mines)      imps.push(`⛏ ${s.mines} mines${s.avgMineLvl > 1 ? ` (avg Lv.${s.avgMineLvl})` : ""}`);
            if (s.lumber)     imps.push(`🌲 ${s.lumber} lumber`);
            if (s.pastures)   imps.push(`🐄 ${s.pastures} pastures`);
            if (s.quarries)   imps.push(`🪨 ${s.quarries} quarries`);
            if (s.windmills)  imps.push(`🌀 ${s.windmills} windmills`);
            if (s.ports)      imps.push(`⚓ ${s.ports} ports`);
            if (s.fisheries)  imps.push(`🐟 ${s.fisheries} fisheries`);
            if (s.smitheries) imps.push(`🔨 ${s.smitheries} smitheries`);
            if (s.forts)      imps.push(`🛡 ${s.forts} forts`);

            const focusMap   = {0: "Farming", 1: "Mining", 2: "Defense", 3: "Trade"};
            const focusEmoji = {0: "🌾",       1: "⛏",     2: "🛡",      3: "📦"};
            const focusColor = {0: "#c8a000", 1: "#6a737d", 2: "#d73a49", 3: "#3b8bd6"};
            const fValue = c.focus | 0;

            lines.push(`<div style="color:#8b949e">Focus: <span style="color:${focusColor[fValue]}">${focusEmoji[fValue]} ${focusMap[fValue]}</span></div>`);
            lines.push(`<div style="color:#8b949e">⚙ Ore ${c.city_ore}/${c.city_ore_total} · 🧱 Stone ${c.city_stone}/${c.city_stone_total} · 🗡 Metal ${c.city_metal}/${c.city_metal_total}</div>`);

            lines.push(`<div style="color:#8b949e">📐 ${s.tileCount} tiles · 💎 ${s.resCount} resources</div>`);
            if (imps.length) {
                lines.push(`<div style="color:#8b949e">${imps.slice(0, 3).join(" · ")}</div>`);
                if (imps.length > 3) lines.push(`<div style="color:#8b949e">${imps.slice(3, 6).join(" · ")}</div>`);
                if (imps.length > 6) lines.push(`<div style="color:#8b949e">${imps.slice(6).join(" · ")}</div>`);
            }
        }
    }
    // Stacked army tooltip(s) — one block per army on this cell
    if (info.armies && info.armies.length) {
        const behaviorLabel = {
            defend_fort:      "🛡 Defending Fort",
            defend_territory: "🗺 Defending Frontier",
            attack_army:      "⚔ Hunting Army",
            attack_city:      "🏰 Assaulting City",
            relieve_city:     "🏳 Relieving City",
            retreating:       "↩ Retreating",
        };
        const behaviorColor = {
            defend_fort:      "#3fb950",
            defend_territory: "#56b870",
            attack_army:      "#f85149",
            attack_city:      "#d73a49",
            relieve_city:     "#58a6ff",
            retreating:       "#8b949e",
        };
        for (const a of info.armies) {
            const sFrac = a.max_strength > 0 ? a.strength / a.max_strength : 0;
            const sColor = sFrac > 0.5 ? "#3fb950" : sFrac > 0.25 ? "#f0c040" : "#f85149";
            const oColor = a.organization > 50 ? "#58a6ff" : a.organization > 25 ? "#f0c040" : "#f85149";
            const supColor = a.supply > 50 ? "#3fb950" : a.supply > 20 ? "#f0c040" : "#f85149";
            const bLabel = behaviorLabel[a.behavior] || a.behavior;
            const bColor = behaviorColor[a.behavior] || "#8b949e";

            const fort = a.fortification || 0;
            const fortPct = (fort * 100) | 0;
            const fortColor = fort > 0.4 ? "#f0c040" : fort > 0.15 ? "#e0b020" : fort > 0.05 ? "#a68a30" : "#6a6a6a";

            lines.push(`<div style="margin-top:6px;border-top:1px solid #30363d;padding-top:4px">`);
            lines.push(`<div style="font-weight:600">⚔ ${a.commander}</div>`);
            lines.push(`<div style="color:${a.owner_color};font-size:9px">${a.owner_name} · Fort Lv.${a.fort_level}</div>`);
            lines.push(`<div style="color:${bColor};font-size:10px">${bLabel}</div>`);
            if (a.target_name) {
                lines.push(`<div style="color:#8b949e;font-size:9px">→ ${a.target_name}</div>`);
            }
            lines.push(`<div style="color:#8b949e">💪 Strength: <span style="color:${sColor}">${a.strength.toFixed(0)}/${a.max_strength.toFixed(0)}</span></div>`);
            lines.push(`<div style="color:#8b949e">🎯 Organization: <span style="color:${oColor}">${a.organization.toFixed(0)}%</span> · 🍖 Supply: <span style="color:${supColor}">${a.supply.toFixed(0)}%</span></div>`);
            if (fortPct > 0) {
                lines.push(`<div style="color:#8b949e">🏯 Fortification: <span style="color:${fortColor}">+${fortPct}%</span> <span style="font-size:9px">(${a.fort_source || "open field"})</span></div>`);
            } else {
                lines.push(`<div style="color:#8b949e;font-size:9px">🏯 Open field (no fortification)</div>`);
            }
            lines.push(`<div style="color:#8b949e;font-size:9px">⭐ Skill ×${a.skill.toFixed(2)}</div>`);
            lines.push(`</div>`);
        }
    }
    tooltip.innerHTML = lines.filter(Boolean).join("");
    tooltip.style.display = "block";
    tooltip.style.left = `${x + 10}px`;
    tooltip.style.top  = `${y + 10}px`;
}

// ── Zoom buttons ──────────────────────────────────────────────────────────────

function applyZoom(factor) {
    const cx = canvas.width  / 2;
    const cy = canvas.height / 2;
    const nz = Math.max(0.3, Math.min(6, zoom * factor));
    const s  = nz / zoom;
    viewOffset.x = cx - (cx - viewOffset.x) * s;
    viewOffset.y = cy - (cy - viewOffset.y) * s;
    zoom = nz;
    lblZoom.textContent = `${zoom.toFixed(1)}×`;
    renderAll();
}

zoomIn.addEventListener("click",    () => applyZoom(1.3));
zoomOut.addEventListener("click",   () => applyZoom(1 / 1.3));
zoomReset.addEventListener("click", () => { zoom = 1; viewOffset = { x: 0, y: 0 }; renderAll(); });

// ── Controls ──────────────────────────────────────────────────────────────────

btnPlay.addEventListener("click", () => {
    playing = !playing;
    btnPlay.textContent = playing ? "⏸" : "▶";
    btnPlay.style.background = playing ? "#da3633" : "#238636";
    ws.send(JSON.stringify({ action: playing ? "play" : "pause" }));
});

btnReset.addEventListener("click", () => {
    playing = false;
    btnPlay.textContent   = "▶";
    btnPlay.style.background = "#238636";
    ws.send(JSON.stringify({ action: "reset" }));
});

selSpeed.addEventListener("change", () => {
    ws.send(JSON.stringify({ action: "speed", value: parseFloat(selSpeed.value) }));
});

const MAP_MODE_CYCLE = ["terrain", "political", "armies", "resource"];
const MAP_MODE_LABEL = { terrain: "🌍 Ter", political: "🗺 Pol", armies: "⚔ Arm", resource: "📊 Res" };
const MAP_MODE_BG    = { terrain: "#30363d", political: "#6c5ce7", armies: "#da3633", resource: "#1f6feb" };
btnMapMode.addEventListener("click", () => {
    const idx = MAP_MODE_CYCLE.indexOf(mapMode);
    mapMode = MAP_MODE_CYCLE[(idx + 1) % MAP_MODE_CYCLE.length];
    btnMapMode.textContent = MAP_MODE_LABEL[mapMode];
    btnMapMode.style.background = MAP_MODE_BG[mapMode];
    selResGood.style.display = mapMode === "resource" ? "" : "none";
    renderAll();
});

selResGood.addEventListener("change", renderAll);

chkRes.addEventListener("change", renderAll);
chkLevels.addEventListener("change", renderAll);

btnSettings.addEventListener("click", () => {
    settingsPanel.style.display = settingsPanel.style.display === "none" ? "flex" : "none";
    btnSettings.style.background = settingsPanel.style.display !== "none" ? "#58a6ff" : "#30363d";
});

btnDiplo.addEventListener("click", () => {
    const showing = diploModal.style.display === "block";
    diploModal.style.display = showing ? "none" : "block";
    btnDiplo.style.background = !showing ? "#a371f7" : "";
    if (!showing) renderDiploScreen();
});

diploClose.addEventListener("click", () => {
    diploModal.style.display = "none";
    btnDiplo.style.background = "";
});

diploModal.addEventListener("click", (e) => {
    if (e.target === diploModal) {
        diploModal.style.display = "none";
        btnDiplo.style.background = "";
    }
});

// Settings sliders
document.querySelectorAll("[data-param]").forEach(el => {
    const label = el.nextElementSibling;
    el.addEventListener("input", () => {
        if (label) label.textContent = el.value;
        ws.send(JSON.stringify({ action: "params", values: { [el.dataset.param]: parseFloat(el.value) } }));
    });
});

// ── Boot ──────────────────────────────────────────────────────────────────────
connect();
