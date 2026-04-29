/**
 * ui.js — WebSocket client, UI event wiring, game loop
 * Connects to the FastAPI backend and drives the canvas renderer.
 */

import { renderFrame, getCellInfo } from "./renderer.js";

let W = 160, H = 100, CELL = 6;
let PX_W = W * CELL, PX_H = H * CELL;

function syncMapDimensions() {
    if (!mapData) return;
    if (Number.isFinite(mapData.width) && mapData.width > 0) W = mapData.width | 0;
    if (Number.isFinite(mapData.height) && mapData.height > 0) H = mapData.height | 0;
    if (Number.isFinite(mapData.cell_size) && mapData.cell_size > 0) CELL = mapData.cell_size;
    PX_W = W * CELL;
    PX_H = H * CELL;
}

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
const cityPanel    = document.getElementById("city-panel");
const cityPanelBody = document.getElementById("city-panel-body");
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
let dragOrigin = { x: 0, y: 0 };
let dragMoved  = false;
let selectedId   = null;
let selectedCity = null;  // city object when a city cell is clicked
let hoveredCell  = -1;    // cell index under mouse, for live tooltip updates
let hoverScreenX = 0;
let hoverScreenY = 0;
let ws           = null;

function getGoodMeta() {
    return (mapData && mapData.good_meta && typeof mapData.good_meta === "object")
        ? mapData.good_meta
    : {};
}

function getGoodKeys() {
    if (mapData && Array.isArray(mapData.goods) && mapData.goods.length) {
        return mapData.goods;
    }
    return Object.keys(getGoodMeta());
}

function goodIcon(good) {
    return getGoodMeta()[good]?.icon || "•";
}

function goodLabel(good) {
    if (getGoodMeta()[good]?.label) return getGoodMeta()[good].label;
    return String(good).replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}

function getProfessionMeta() {
    return (mapData && mapData.profession_meta && typeof mapData.profession_meta === "object")
        ? mapData.profession_meta
        : {};
}

function getEffectiveProfessionCounts(city) {
    return { ...(city.professions || {}) };
}

function calcAverageConsumptionLevelForCounts(counts, levels) {
    const entries = Object.entries(counts || {}).filter(([, c]) => Number(c) > 0);
    if (!entries.length) return 0;
    let num = 0;
    let den = 0;
    for (const [prof, cntRaw] of entries) {
        const cnt = Number(cntRaw || 0);
        const lvl = Number((levels || {})[prof] || 0);
        num += cnt * lvl;
        den += cnt;
    }
    return den > 0 ? num / den : 0;
}

function calcCityAverageConsumptionLevel(city) {
    return calcAverageConsumptionLevelForCounts(
        getEffectiveProfessionCounts(city),
        city.consumption_levels || {},
    );
}

function calcNationalAverageConsumptionLevel(civ) {
    if (!civ || !Array.isArray(civ.cities) || !civ.cities.length) return 0;
    let num = 0;
    let den = 0;
    for (const city of civ.cities) {
        const counts = getEffectiveProfessionCounts(city);
        for (const [prof, cntRaw] of Object.entries(counts)) {
            const cnt = Number(cntRaw || 0);
            if (cnt <= 0) continue;
            const lvl = Number((city.consumption_levels || {})[prof] || 0);
            num += cnt * lvl;
            den += cnt;
        }
    }
    return den > 0 ? num / den : 0;
}

// Build a single-ring SVG pie with a colour-legend sibling. ``entries`` is
// already sorted; each entry is {key, count, color, label, icon}. Total is
// the headcount sum. A single-profession city draws a full circle to dodge
// the degenerate M/L/A path when sweep is exactly 2π.
function renderProfessionPie(entries, total, size) {
    if (!entries.length || total <= 0) {
        return `<div style="font-size:9px;color:#8b949e">No employees</div>`;
    }
    const r = size / 2 - 1;
    const cx = size / 2, cy = size / 2;
    let slices = "";
    if (entries.length === 1) {
        slices = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${entries[0].color}" stroke="#0b1016" stroke-width="0.5"/>`;
    } else {
        let a = -Math.PI / 2;
        for (const e of entries) {
            const sweep = (e.count / total) * Math.PI * 2;
            const a2 = a + sweep;
            const large = sweep > Math.PI ? 1 : 0;
            const x1 = cx + r * Math.cos(a), y1 = cy + r * Math.sin(a);
            const x2 = cx + r * Math.cos(a2), y2 = cy + r * Math.sin(a2);
            slices += `<path d="M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z" fill="${e.color}" stroke="#0b1016" stroke-width="0.5"/>`;
            a = a2;
        }
    }
    return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" style="flex-shrink:0">${slices}</svg>`;
}

function renderProfessionsSection(city, civ) {
    const profMeta = getProfessionMeta();
    const profs = getEffectiveProfessionCounts(city);
    const wages = city.profession_wages || {};
    const levels = city.consumption_levels || {};
    const shares = city.profession_income_shares || {};
    const entries = Object.entries(profs)
        .filter(([, v]) => v > 0)
        .map(([key, count]) => {
            const m = profMeta[key] || {};
            return {
                key,
                count,
                color: m.color || "#8b949e",
                icon:  m.icon  || "•",
                label: m.label || key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
                wage: Number(wages[key] || 0),
                level: Number(levels[key] || 0),
                share: Number(shares[key] || 0),
            };
        })
        .sort((a, b) => b.count - a.count);
    const total = entries.reduce((a, e) => a + e.count, 0);
    const cityAvgConsumption = calcCityAverageConsumptionLevel(city);
    const nationalAvgConsumption = city.is_capital ? calcNationalAverageConsumptionLevel(civ) : null;
    const avgWage = total > 0
        ? entries.reduce((s, e) => s + e.wage * e.count, 0) / total
        : 0;

    const legend = entries.map(e => {
        const pct = total > 0 ? (e.count / total * 100).toFixed(1) : "0.0";
        const sharePct = (e.share * 100).toFixed(1);
        return `<div style="display:flex;align-items:center;gap:4px;font-size:9px;margin-bottom:2px">
            <span style="width:8px;height:8px;background:${e.color};border-radius:2px;display:inline-block;flex-shrink:0"></span>
            <span>${e.icon} ${e.label}</span>
            <span style="margin-left:auto;color:#8b949e">${e.count} · ${pct}% · ₿${e.wage.toFixed(2)} wage · C${e.level.toFixed(2)} · ${sharePct}% share</span>
        </div>`;
    }).join("");
    return `
        <div class="city-panel-section">
            <div class="city-panel-section-label">Professions (${total})</div>
            <div style="font-size:9px;color:#8b949e;margin-bottom:5px">
                City avg consumption: <span style="color:#c9d1d9">C${cityAvgConsumption.toFixed(2)}</span>
                ${city.is_capital ? ` · National avg: <span style="color:#58a6ff">C${(nationalAvgConsumption || 0).toFixed(2)}</span>` : ""}
                · Avg wage: <span style="color:#c9d1d9">₿${avgWage.toFixed(2)}</span>
            </div>
            <div style="display:flex;gap:10px;align-items:flex-start">
                ${renderProfessionPie(entries, total, 90)}
                <div style="flex:1;min-width:0">${legend || `<div style="font-size:9px;color:#8b949e">No employees</div>`}</div>
            </div>
        </div>
    `;
}

function populateResourceGoodSelect() {
    const prev = selResGood.value;
    const goods = getGoodKeys();
    selResGood.innerHTML = goods.map(g => `<option value="${g}">${goodIcon(g)} ${goodLabel(g)}</option>`).join("");
    if (goods.includes(prev)) {
        selResGood.value = prev;
    }
}

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
            syncMapDimensions();
            populateResourceGoodSelect();
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
    syncMapDimensions();
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
        const econSize = (civ.cities || []).reduce((sum, city) => sum + (city.economic_output || 0), 0);
        const netInc = (civ.cities || []).reduce((sum, city) => sum + (city.income_total || 0), 0);
        const netLabel = netInc >= 0 ? `+${netInc.toFixed(1)}` : netInc.toFixed(1);
        const econLabel = `${econSize.toFixed(0)} · ${netLabel}`;
        return `
        <div class="civ-row${sel ? " selected" : ""}" data-id="${civ.id}">
            <div class="civ-row-top">
                <span class="dot" style="background:${civ.color};${atWar ? "box-shadow:0 0 3px #f85149" : ""}"></span>
                <span class="civ-name">${civ.name}</span>
                ${atWar ? '<span class="war-badge">WAR</span>' : ""}
                <span class="civ-size">${civ.territory.length}</span>
            </div>
            <div class="civ-sub">${civ.cities.length}c · ${civ.population|0}p · 📈${econLabel}/t</div>
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

    // Refresh selected city from latest state snapshot.
    if (selectedCity && selectedId !== null) {
        const civSel = gameState.civs.find(c => c.id === selectedId);
        if (civSel) {
            const fresh = civSel.cities.find(c => c.cell === selectedCity.cell);
            selectedCity = fresh || null;
        } else {
            selectedCity = null;
        }
    }

    const selectedCiv = selectedId !== null ? gameState.civs.find(c => c.id === selectedId) : null;
    renderSelectedCityPanel(selectedCity, selectedCiv || null);

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

function renderSelectedCityPanel(city, civ) {
    if (!city) {
        cityPanel.style.display = "none";
        cityPanelBody.innerHTML = "";
        return;
    }

    cityPanel.style.display = "flex";

    const tags = [];
    if (city.is_capital) tags.push("★ Capital");
    if (city.river_mouth) tags.push("🏞 River Mouth");
    else if (city.near_river) tags.push("〰 River");
    if (city.coastal) tags.push("⚓ Coast");

    const goods = getGoodKeys();

    const goodsRows = goods.map(g => {
        const s = city.supply[g] || 0;
        const d = city.demand[g] || 0;
        const p = city.prices[g] || 1.0;
        const c = p < 1.0 ? "#3fb950" : (p > 2.0 ? "#f85149" : "#f0c040");

        // Match tooltip trade summary: aggregate by good across all partners.
        let tradeLine = "";
        const trades = city.last_trades?.[g];
        if (trades && trades.length > 0) {
            let totalVol = 0;
            let priceSum = 0;
            for (const [vol, , tp] of trades) {
                totalVol += vol;
                priceSum += tp * Math.abs(vol);
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

        return `<div style="display:flex;justify-content:space-between;font-size:10px"><span>${goodIcon(g)} ${goodLabel(g).toUpperCase()}${tradeLine}</span><span>${s.toFixed(1)} / ${d.toFixed(1)} · <span style="color:${c}">₿${p.toFixed(2)}</span></span></div>`;
    }).join("");

    const bDetails = city.building_details || [];
    const buildingRows = bDetails.length
        ? bDetails.map(b => {
            const pColor = (b.profit || 0) >= 0 ? "#3fb950" : "#f85149";
            const inTxt = b.inputs ? Object.entries(b.inputs).map(([g, a]) => `${a} ${g}`).join(", ") : "-";
            const outTxt = b.outputs ? Object.entries(b.outputs).map(([g, a]) => `${a} ${g}`).join(", ") : "-";
            return `<div style="margin-bottom:4px">
                <div style="display:flex;justify-content:space-between"><span>🏭 ${b.name} Lv.${b.level} · 👥 ${b.staffed}/${b.level}</span><span style="color:${pColor}">₿${(b.profit || 0).toFixed(2)}/t</span></div>
                <div style="font-size:9px;color:#8b949e">in: ${inTxt || "-"} · out: ${outTxt || "-"}</div>
            </div>`;
        }).join("")
        : `<div style="font-size:9px;color:#8b949e">No city buildings</div>`;

    const producerMeta = mapData?.producer_buildings || {};
    const capacities = city.capacities || {};
    const sharedCaps = city.shared_capacities || {};
    const capKeys = Object.keys(capacities).filter(k => (capacities[k] || 0) > 0);
    const capacityRows = capKeys.length
        ? capKeys
            .sort((a, b) => (capacities[b] || 0) - (capacities[a] || 0))
            .map(k => {
                const meta = producerMeta[k] || {};
                const label = meta.label || k;
                const icon = meta.icon || "🏗";
                const used = city.buildings?.[k] || 0;
                const cap = capacities[k] || 0;
                const bonus = city.capacity_bonuses?.[k] || {};
                const slots = bonus.slots || 0;
                const mult = bonus.mult || 0;
                const bonusTxt = slots > 0 && mult > 0 ? ` · bonus ${slots} @ +${(mult * 100).toFixed(0)}%` : "";
                return `<div style="display:flex;justify-content:space-between;font-size:10px"><span>${icon} ${label}</span><span>${used}/${cap}${bonusTxt}</span></div>`;
            }).join("")
        : `<div style="font-size:9px;color:#8b949e">No producer capacity on current territory</div>`;

    const agriCap = sharedCaps.agri || 0;
    const agriUsed = (city.buildings?.farm || 0) + (city.buildings?.cotton_farm || 0);
    const sharedRows = agriCap > 0
        ? `<div style="font-size:10px;color:#8b949e">Shared agri pool: ${agriUsed}/${agriCap} (farm + cotton)</div>`
        : "";


    const workforce = city.workforce || 0;
    const employed = city.employed_pop || 0;
    const unemployed = city.unemployed_pop || 0;
    const net = city.income_total || 0;
    const netColor = net >= 0 ? "#3fb950" : "#f85149";
    const econOut = city.economic_output || 0;
    const pull = city.attractiveness ?? 1.0;
    const netMig = city.net_migration ?? 0.0;
    const pullColor = pull > 1.3 ? "#3fb950" : (pull < 0.7 ? "#f85149" : "#f0c040");
    const migColor = netMig > 0.05 ? "#3fb950" : (netMig < -0.05 ? "#f85149" : "#8b949e");
    const migArrow = netMig > 0.05 ? "↑" : (netMig < -0.05 ? "↓" : "·");
    const migLabel = netMig > 0.05 ? `+${netMig.toFixed(1)} incoming` : (netMig < -0.05 ? `${netMig.toFixed(1)} leaving` : "steady");

    const growthPct = (city.population_growth_rate || 0) * 100.0;
    const foodPct = (city.growth_food_contribution || 0) * 100.0;
    const consPct = (city.growth_consumption_penalty || 0) * 100.0;
    const unempPct = (city.growth_unemployment_penalty || 0) * 100.0;
    const growthColor = growthPct > 0.02 ? "#3fb950" : (growthPct < -0.02 ? "#f85149" : "#8b949e");
    const foodColor = foodPct >= 0 ? "#3fb950" : "#f85149";
    const consColor = consPct < 0 ? "#f85149" : "#8b949e";
    const unempColorPanel = unempPct < 0 ? "#f85149" : "#8b949e";
    const fmtPct = v => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
    const growthLine = `<div>📈 Growth <span style="color:${growthColor}">${fmtPct(growthPct)}</span> / tick · <span style="font-size:9px;color:#8b949e">food <span style="color:${foodColor}">${fmtPct(foodPct)}</span> · consumption <span style="color:${consColor}">${fmtPct(consPct)}</span> · unemployment <span style="color:${unempColorPanel}">${fmtPct(unempPct)}</span></span></div>`;

    let governmentSection = "";
    if (city.is_capital && civ && civ.government) {
        const gov = civ.government;
        const disposition = String(civ.disposition || "calm");
        const dispTicks = Number(civ.disposition_ticks || 0);
        const dispositionLabel = disposition.charAt(0).toUpperCase() + disposition.slice(1);
        const dispositionColor = disposition === "aggressive"
            ? "#f85149"
            : (disposition === "fortifying" ? "#f0c040" : "#3fb950");
        const taxRate = gov.tax_rate || 0;
        const treasury = gov.treasury || 0;
        const revenue = gov.last_tax_collected || 0;
        const buildSpend = gov.last_build_spending || 0;
        const fortSpend = gov.last_fort_spending || 0;
        const benefitSpend = gov.last_benefit_spending || 0;
        const flows = gov.last_flows || [];
        const netGov = revenue - buildSpend - fortSpend - benefitSpend;
        const netGovColor = netGov >= 0 ? "#3fb950" : "#f85149";

        const flowRows = flows.length
            ? flows.map(flow => {
                const amount = Number(flow.amount || 0);
                const isExpense = String(flow.category || "income") === "expense";
                const sign = isExpense ? "-" : "+";
                const color = isExpense ? "#f85149" : "#3fb950";
                const place = flow.city_name ? ` · ${flow.city_name}` : "";
                const note = flow.note ? ` · ${flow.note}` : "";
                return `<div style="display:flex;justify-content:space-between;gap:8px;margin-top:3px;font-size:9px">
                    <span>${flow.label || flow.kind || "Flow"}${place}${note}</span>
                    <span style="color:${color}">${sign}₿${amount.toFixed(2)}</span>
                </div>`;
            }).join("")
            : `<div style="font-size:9px;color:#8b949e">No treasury flows recorded this tick.</div>`;

        const queueRows = (gov.construction_queue || []).slice(0, 4).map(order => {
            const statusColor = order.status === "built" ? "#3fb950" : (order.status?.startsWith("blocked") ? "#f85149" : "#f0c040");
            return `<div style="margin-top:4px;padding:4px;border:1px solid #30363d;border-radius:4px;background:#0b1016">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <span>${order.asset_label || order.asset_key} near ${order.target_civ_name || "?"}</span>
                    <span style="color:${statusColor}">${order.status || "queued"}</span>
                </div>
                <div style="font-size:9px;color:#8b949e">prio ${Number(order.priority || 0).toFixed(1)} · host ${order.host_city_name || "?"} · rel ${Number(order.relation || 0).toFixed(2)}</div>
                <div style="font-size:9px;color:#8b949e">spend ₿${Number(order.estimated_spending || 0).toFixed(2)} · upkeep ₿${Number(order.estimated_upkeep || 0).toFixed(2)}</div>
                <div style="font-size:9px;color:#8b949e">${order.reason || ""}</div>
            </div>`;
        }).join("");

        const upkeepRows = Object.entries(gov.fort_upkeep_goods || {}).map(([good, qty]) => {
            return `<div style="display:flex;justify-content:space-between;margin-top:3px;font-size:9px">
                <span>${goodIcon(good)} ${goodLabel(good)}</span>
                <span>${qty.toFixed(1)} / fort / tick</span>
            </div>`;
        }).join("");

        const fortRows = (gov.forts || []).map(f => {
            const raw = gameState.impr?.[f.cell] || 0;
            const fType = raw & 31;
            const level = raw > 0 && fType === 7 ? ((raw >> 5) + 1) : 0;
            const statusColor = f.active ? "#3fb950" : "#f85149";
            return `<div style="margin-top:4px;padding:4px;border:1px solid #30363d;border-radius:4px;background:#0b1016">
                <div style="display:flex;justify-content:space-between;align-items:center">
                    <span>🏯 Fort @${f.cell} · Lv.${level || "?"}</span>
                    <span style="color:${statusColor}">${f.active ? "active" : "inactive"}</span>
                </div>
                <div style="font-size:9px;color:#8b949e">buffer ${f.buffer.toFixed(2)} · last upkeep ₿${(f.last_upkeep_value || 0).toFixed(2)}</div>
            </div>`;
        }).join("") || `<div style="font-size:9px;color:#8b949e">No forts under government control.</div>`;

        const ownedImprovementRows = Object.entries(gov.owned_assets?.improvements || {})
            .map(([assetKey, assets]) => {
                const profile = mapData?.government_profiles?.improvements?.[assetKey] || {};
                const label = profile.label || String(assetKey).replace(/_/g, " ");
                if (!Array.isArray(assets) || assets.length === 0) {
                    return `<div style="font-size:9px;color:#8b949e">${label}: none</div>`;
                }
                return `<div style="margin-top:4px">
                    <div style="font-size:9px;color:#8b949e">${label}</div>
                    ${assets.map(a => {
                        const statusColor = a.active ? "#3fb950" : "#f85149";
                        return `<div style="display:flex;justify-content:space-between;font-size:9px;padding-left:6px">
                            <span>cell ${a.cell}</span>
                            <span style="color:${statusColor}">${a.active ? "active" : "inactive"}</span>
                        </div>`;
                    }).join("")}
                </div>`;
            }).join("");

        const ownedBuildingRows = Object.entries(gov.owned_assets?.buildings || {})
            .map(([cityCell, holdings]) => {
                const entries = Object.entries(holdings || {});
                if (!entries.length) return "";
                return `<div style="font-size:9px;margin-top:3px">city ${cityCell}: ${entries.map(([k, v]) => `${k} Lv.${v}`).join(", ")}</div>`;
            }).join("");

        governmentSection = `
        <div class="city-panel-section">
            <div class="city-panel-section-label">Government (Capital)</div>
            <div>🏛 Treasury <span style="color:#58a6ff">₿${treasury.toFixed(2)}</span></div>
            <div>🧠 Disposition <span style="color:${dispositionColor}">${dispositionLabel}</span> · for ${dispTicks} ticks</div>
            <div>💸 Revenue ₿${revenue.toFixed(2)}/t · Build Spend ₿${buildSpend.toFixed(2)} · Fort Spend ₿${fortSpend.toFixed(2)}/t · Benefits ₿${benefitSpend.toFixed(2)}/t · Net <span style="color:${netGovColor}">₿${netGov.toFixed(2)}/t</span></div>
            <div style="margin-top:4px;font-size:9px;color:#8b949e">Policy snapshot: tax ${ (taxRate * 100).toFixed(1) }% · activation on ${ (gov.fort_buffer_on || 0).toFixed(2) } · off ${ (gov.fort_buffer_off || 0).toFixed(2) }</div>
            <div style="margin-top:6px;font-size:9px;color:#8b949e">All treasury flows</div>
            ${flowRows}
            <div style="margin-top:6px;font-size:9px;color:#8b949e">Government construction queue</div>
            ${queueRows || `<div style="font-size:9px;color:#8b949e">No construction queued.</div>`}
            <div style="margin-top:6px;font-size:9px;color:#8b949e">Fort upkeep basket (per funded fort/tick)</div>
            ${upkeepRows || `<div style="font-size:9px;color:#8b949e">No upkeep goods configured.</div>`}
            <div style="margin-top:6px;font-size:9px;color:#8b949e">Government-owned forts</div>
            ${fortRows}
            <div style="margin-top:6px;font-size:9px;color:#8b949e">Government-owned improvements</div>
            ${ownedImprovementRows || `<div style="font-size:9px;color:#8b949e">None</div>`}
            <div style="margin-top:6px;font-size:9px;color:#8b949e">Government-owned city buildings</div>
            ${ownedBuildingRows || `<div style="font-size:9px;color:#8b949e">None</div>`}
        </div>`;
    }

    cityPanelBody.innerHTML = `
        <div class="city-panel-title">🏘 ${city.name}</div>
        <div class="city-panel-sub">${tags.join(" · ") || "City"}</div>
        <div>👥 Pop ${city.population|0} · 💰 Gold ${city.gold|0}</div>
        <div>💼 Workforce ${workforce} · Employed ${employed} · Unemployed ${unemployed}</div>
        ${growthLine}
        <div>🏦 Economy Size ₿${econOut.toFixed(1)}/t · 📈 Net Income <span style="color:${netColor}">₿${net.toFixed(2)}/t</span> · Per Person ₿${(city.income_per_person || 0).toFixed(3)}</div>
        <div>🧭 Pull <span style="color:${pullColor}">${pull.toFixed(2)}</span> · <span style="color:${migColor}">${migArrow} ${migLabel}</span></div>

        <div class="city-panel-section">
            <div class="city-panel-section-label">Local Market (Supply / Demand · Price)</div>
            ${goodsRows}
        </div>

        <div class="city-panel-section">
            <div class="city-panel-section-label">Buildings</div>
            ${buildingRows}
            <div style="margin-top:6px;font-size:9px;color:#8b949e">🏛 Trading House Lv.${(city.buildings?.trading_house || 0)} · merchants ${(city.building_staffing?.trading_house || 0)}/${(city.buildings?.trading_house || 0)}</div>
            <div style="font-size:9px">capacity ${(city.trade_capacity_required || 0).toFixed(1)}/${(city.trade_capacity_provided || 0).toFixed(1)} · exports ${(city.trade_export_volume || 0).toFixed(1)} · <span style="color:${((city.building_profit?.trading_house || city.trade_export_income || 0) >= 0) ? '#3fb950' : '#f85149'}">₿${(city.building_profit?.trading_house || city.trade_export_income || 0).toFixed(2)}/t</span></div>
        </div>

        <div class="city-panel-section">
            <div class="city-panel-section-label">Producer Capacity</div>
            ${sharedRows}
            ${capacityRows}
        </div>

        ${renderProfessionsSection(city, civ)}
        ${governmentSection}
    `;
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
                <span>${c.population|0}p · 💰${c.gold|0} · 🌾${c.supply["grain"]|0}/${c.demand["grain"]|0}
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
            <div>💰 Gold: ${civ.gold|0} · 🌾 Grain Out: ${civ.farm_output|0}</div>
            <div>🔬 Tech: ${civ.tech.toFixed(1)} · 🎭 Culture: ${civ.culture.toFixed(1)}</div>
            <div>🛡 Integrity: <span style="color:${intColor}">${(civ.integrity*100)|0}%</span> · 💢 Aggressiveness: <span style="color:${aggrColor}">${(aggr*100)|0}%</span></div>
            <div>⚡ Power: ${civ.power|0}</div>
            <div>⛏ Copper Ore/Copper: ${civ.ore_output|0}/${civ.metal_output|0} · 🧱 Stone: ${civ.stone_output|0} · 🛤 Roads: ${civ.roads.length}</div>
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
    dragOrigin = { x: e.clientX, y: e.clientY };
    dragMoved = false;
});
canvas.addEventListener("mouseup",    () => isDragging = false);
canvas.addEventListener("mouseleave", () => { isDragging = false; tooltip.style.display = "none"; hoveredCell = -1; });

canvas.addEventListener("mousemove", e => {
    const rect = canvas.getBoundingClientRect();
    if (isDragging) {
        const moved = Math.hypot(e.clientX - dragOrigin.x, e.clientY - dragOrigin.y);
        if (moved >= 4) dragMoved = true;
        viewOffset.x += e.clientX - dragStart.x;
        viewOffset.y += e.clientY - dragStart.y;
        dragStart = { x: e.clientX, y: e.clientY };
        renderAll();
        return;
    }
    if (!mapData) return;
    syncMapDimensions();
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
    if (isDragging || dragMoved) {
        dragMoved = false;
        return;
    }
    const rect = canvas.getBoundingClientRect();
    syncMapDimensions();
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
    const producerMeta = mapData?.producer_buildings || {};
    const fmtCap = (caps, bonus) => {
        const keys = Object.keys(caps || {}).filter(k => (caps[k] || 0) > 0);
        if (!keys.length) return "";
        const rows = keys
            .sort((a, b) => (caps[b] || 0) - (caps[a] || 0))
            .slice(0, 7)
            .map(k => {
                const m = producerMeta[k] || {};
                const icon = m.icon || "🏗";
                const label = m.label || k;
                const b = (bonus || {})[k] || {};
                const slots = b.slots || 0;
                const mult = b.mult || 0;
                const bTxt = slots > 0 && mult > 0 ? ` · bonus ${slots} @ +${(mult * 100).toFixed(0)}%` : "";
                return `<div style="font-size:9px;display:flex;justify-content:space-between"><span>${icon} ${label}</span><span>${caps[k]}${bTxt}</span></div>`;
            })
            .join("");
        return `<div style="margin-top:4px;border-top:1px solid #30363d;padding-top:4px;font-size:10px;color:#8b949e">Tile Capacity Contribution</div>${rows}`;
    };

    let lines = [
        `<div class="tt-coord">(${info.x},${info.y}) ${info.terrain} ${info.alt}m</div>`,
        info.river   ? `<div style="color:#4aaef0">〰 River</div>` : "",
        info.coastal ? `<div style="color:#1a6a9e">⚓ Coastal</div>` : "",
        info.imp.name !== "—" ? `<div style="color:#c8a000">${info.imp.name} Lv.${info.imp.level}${info.imp.detail ? ` — ${info.imp.detail}` : ""}</div>` : "",
        info.imp.employees ? `<div style="color:#9a8fb8">${info.imp.employees}</div>` : "",
        info.imp.efficiency ? `<div style="color:#8dd9c7">${info.imp.efficiency}</div>` : "",
        fmtCap(info.tile_capacity, info.tile_capacity_bonus),
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
        lines.push(`<div style="font-size:10px">💼 Workforce ${workforce} · <span style="color:#3fb950">Employed ${employed}</span> · <span style="color:${unempColor}">Unemployed ${unemployed}</span></div>`);

        // ── Migration ─────────────────────────────────────────────────
        const pull = c.attractiveness ?? 1.0;
        const netMig = c.net_migration ?? 0.0;
        const pullColor = pull > 1.3 ? "#3fb950" : (pull < 0.7 ? "#f85149" : "#f0c040");
        const migColor = netMig > 0.05 ? "#3fb950" : (netMig < -0.05 ? "#f85149" : "#8b949e");
        const migArrow = netMig > 0.05 ? "↑" : (netMig < -0.05 ? "↓" : "·");
        const migLabel = netMig > 0.05 ? `+${netMig.toFixed(1)} incoming` : (netMig < -0.05 ? `${netMig.toFixed(1)} leaving` : "steady");
        const consLvl = c.avg_consumption_level || 0;
        const growthRate = (c.population_growth_rate || 0) * 100.0;
        const growthColor = growthRate > 0.02 ? "#3fb950" : (growthRate < -0.02 ? "#f85149" : "#8b949e");
        lines.push(`<div style="margin-top:4px; border-top:1px solid #30363d; padding-top:4px; font-size:10px; color:#8b949e">Migration</div>`);
        lines.push(`<div style="font-size:10px">🧭 Pull <span style="color:${pullColor}">${pull.toFixed(2)}</span> · <span style="color:${migColor}">${migArrow} ${migLabel}</span></div>`);
        lines.push(`<div style="font-size:10px">🛒 Avg Consumption Tier ${consLvl.toFixed(2)}</div>`);
        lines.push(`<div style="font-size:10px">📈 Growth <span style="color:${growthColor}">${growthRate >= 0 ? "+" : ""}${growthRate.toFixed(2)}%</span> / tick</div>`);
        
        // Growth breakdown for debugging
        const foodGrowth = ((c.growth_food_contribution || 0) * 100.0);
        const consumPenalty = ((c.growth_consumption_penalty || 0) * 100.0);
        const unempPenalty = ((c.growth_unemployment_penalty || 0) * 100.0);
        const foodColor = foodGrowth > 0 ? "#3fb950" : "#f85149";
        const penaltyColor = consumPenalty < 0 ? "#f85149" : "#8b949e";
        const unempPenaltyColor = unempPenalty < 0 ? "#f85149" : "#8b949e";
        lines.push(`<div style="font-size:9px; color:#8b949e">  • Food: <span style="color:${foodColor}">${foodGrowth >= 0 ? "+" : ""}${foodGrowth.toFixed(2)}%</span> | Consumption: <span style="color:${penaltyColor}">${consumPenalty >= 0 ? "+" : ""}${consumPenalty.toFixed(2)}%</span> | Unemployment: <span style="color:${unempPenaltyColor}">${unempPenalty >= 0 ? "+" : ""}${unempPenalty.toFixed(2)}%</span></div>`);

        // ── Income breakdown ──────────────────────────────────────────
        const incColor = c.income_total > 0 ? "#3fb950" : c.income_total < 0 ? "#f85149" : "#8b949e";
        const econSize = c.economic_output || 0;
        lines.push(`<div style="margin-top:4px; border-top:1px solid #30363d; padding-top:4px; font-size:10px; color:#8b949e">Economy (gold/tick)</div>`);
        lines.push(`<div style="font-size:10px">🏦 Economy Size ₿${econSize.toFixed(1)} <span style="color:#8b949e">(gross throughput)</span></div>`);
        lines.push(`<div style="font-size:10px">Net <span style="color:${incColor}">₿${c.income_total.toFixed(2)}</span> · Per person <span style="color:${incColor}">₿${c.income_per_person.toFixed(3)}</span></div>`);
        const goodsIn = getGoodKeys();
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
                <span>${goodIcon(g)} ${g}</span>
                <span>${parts.join(" · ")} = <span style="color:${nColor}">${net.toFixed(1)}</span></span>
            </div>`);
        }
        if (c.income_misc >= 0.05) {
            lines.push(`<div style="font-size:9px">✦ Gold resource +${c.income_misc.toFixed(1)}</div>`);
        }

        const goods = getGoodKeys();
        
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
                <span>${goodIcon(g)} ${g.toUpperCase()}${tradeLine}</span>
                <span><span style="color:#fff">${supply.toFixed(1)} / ${demand.toFixed(1)}</span> · <span style="color:${color}">₿${price.toFixed(2)}</span></span>
            </div>`);
        }

        if (s) {
            const focusMap   = {0: "Farming", 1: "Mining", 2: "Defense", 3: "Trade"};
            const focusEmoji = {0: "🌾",       1: "⛏",     2: "🛡",      3: "📦"};
            const focusColor = {0: "#c8a000", 1: "#6a737d", 2: "#d73a49", 3: "#3b8bd6"};
            const fValue = c.focus | 0;

            lines.push(`<div style="color:#8b949e">Focus: <span style="color:${focusColor[fValue]}">${focusEmoji[fValue]} ${focusMap[fValue]}</span></div>`);
            lines.push(`<div style="color:#8b949e">⛏ Copper Ore ${c.city_ore}/${c.city_ore_total} · 🧱 Stone ${c.city_stone}/${c.city_stone_total} · 🔶 Copper ${c.city_metal}/${c.city_metal_total}</div>`);

            lines.push(`<div style="color:#8b949e">📐 ${s.tileCount} tiles · 💎 ${s.resCount} resources</div>`);
            lines.push(`<div style="color:#8b949e">🏗 Producer capacity ${s.builtTotal || 0}/${s.capacityTotal || 0}</div>`);

            const caps = c.capacities || {};
            const keys = Object.keys(caps).filter(k => (caps[k] || 0) > 0);
            if (keys.length) {
                const capLines = keys
                    .sort((a, b) => (caps[b] || 0) - (caps[a] || 0))
                    .slice(0, 6)
                    .map(k => {
                        const m = producerMeta[k] || {};
                        const used = c.buildings?.[k] || 0;
                        const cap = caps[k] || 0;
                        return `${m.icon || "🏗"} ${m.label || k} ${used}/${cap}`;
                    });
                lines.push(`<div style="color:#8b949e">${capLines.join(" · ")}</div>`);
            }

            const agriCap2 = c.shared_capacities?.agri || 0;
            if (agriCap2 > 0) {
                const agriUsed2 = (c.buildings?.farm || 0) + (c.buildings?.cotton_farm || 0);
                lines.push(`<div style="color:#8b949e">🌾 Shared agri pool ${agriUsed2}/${agriCap2}</div>`);
            }
        }

        // ── City buildings ───────────────────────────────────────────
        const bLevels = c.buildings || {};
        const bStaff  = c.building_staffing || {};
        const bProfit = c.building_profit || {};
        const bDetails = c.building_details || [];
        const bKeys = Object.keys(bLevels).filter(k => (bLevels[k] || 0) > 0);
        if (bKeys.length) {
            lines.push(`<div style="margin-top:4px; border-top:1px solid #30363d; padding-top:4px; font-size:10px; color:#8b949e">City Buildings</div>`);
            for (const key of bKeys) {
                const d = bDetails.find(x => x.key === key) || null;
                const lvl = d ? (d.level || 0) : (bLevels[key] || 0);
                const staffed = d ? (d.staffed || 0) : Math.min(lvl, bStaff[key] || 0);
                const prof = d ? (d.profit || 0) : (bProfit[key] || 0);
                const pColor = prof >= 0 ? "#3fb950" : "#f85149";
                const name = d?.name || key;
                const inTxt = d && d.inputs
                    ? Object.entries(d.inputs).map(([g, a]) => `${a} ${g}`).join(", ")
                    : "-";
                const outTxt = d && d.outputs
                    ? Object.entries(d.outputs).map(([g, a]) => `${a} ${g}`).join(", ")
                    : "-";
                lines.push(`<div style="font-size:10px; display:flex; justify-content:space-between"><span>🏭 ${name} Lv.${lvl} · 👥 ${staffed}/${lvl}</span><span style="color:${pColor}">₿${prof.toFixed(2)}/t</span></div>`);
                lines.push(`<div style="font-size:9px; color:#8b949e">in: ${inTxt || "-"} · out: ${outTxt || "-"}</div>`);
            }

            const tradeLv = bLevels.trading_house || 0;
            const tradeStaff = bStaff.trading_house || 0;
            const tradeProfit = bProfit.trading_house || c.trade_export_income || 0;
            const tradeVol = c.trade_export_volume || 0;
            const tradeCapReq = c.trade_capacity_required || 0;
            const tradeCapProv = c.trade_capacity_provided || 0;
            if (tradeLv > 0 || tradeVol > 0 || tradeProfit > 0 || tradeCapReq > 0 || tradeCapProv > 0) {
                const tradeColor = tradeProfit >= 0 ? "#3fb950" : "#f85149";
                lines.push(`<div style="margin-top:6px;font-size:9px;color:#8b949e">🏛 Trading House Lv.${tradeLv} · merchants ${tradeStaff}/${tradeLv}</div>`);
                lines.push(`<div style="font-size:9px">capacity ${tradeCapReq.toFixed(1)}/${tradeCapProv.toFixed(1)} · exports ${tradeVol.toFixed(1)} · <span style="color:${tradeColor}">₿${tradeProfit.toFixed(2)}/t</span></div>`);
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
