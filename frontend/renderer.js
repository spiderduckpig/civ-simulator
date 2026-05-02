/**
 * renderer.js — pure canvas drawing, no framework
 * Receives game state from the server and paints it.
 */

let W = 160, H = 100, CELL = 6;

// Keep frontend metadata generic; concrete names/icons come from backend map payload.
const FALLBACK_RESOURCE_ICON = "?";

function terrainName(mapData, terrainId) {
    const names = mapData?.terrain_names;
    if (names) {
        return names[String(terrainId)] ?? names[terrainId] ?? `Terrain ${terrainId}`;
    }
    return `Terrain ${terrainId}`;
}

function improvementName(mapData, impTypeId) {
    const names = mapData?.imp_names;
    if (names) {
        return names[String(impTypeId)] ?? names[impTypeId] ?? `Improvement ${impTypeId}`;
    }
    return `Improvement ${impTypeId}`;
}

function resourceIcon(mapData, resourceType) {
    const icons = mapData?.resource_icons;
    if (icons) {
        return icons[resourceType] ?? FALLBACK_RESOURCE_ICON;
    }
    return FALLBACK_RESOURCE_ICON;
}

// Colorblind-friendly per-resource hues (Okabe-Ito-inspired palette where possible).
const RESOURCE_MODE_COLORS = {
    grain: "#009E73",
    bread: "#E69F00",
    lumber: "#0072B2",
    copper_ore: "#CC79A7",
    iron_ore: "#4E79A7",
    stone: "#595959",
    copper: "#D55E00",
    iron: "#6C757D",
    fabric: "#56B4E9",
    clothes: "#3B4CC0",
    paper: "#6A994E",
    housing: "#4D908E",
    ships: "#264653",
    sapphires: "#1F4EAE",
};

function _hexToRgb(hex) {
    const m = /^#?([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(String(hex || ""));
    if (!m) return { r: 127, g: 127, b: 127 };
    return {
        r: parseInt(m[1], 16),
        g: parseInt(m[2], 16),
        b: parseInt(m[3], 16),
    };
}

function _mixTowardWhite(hex, t) {
    const clamped = Math.max(0, Math.min(1, t));
    const { r, g, b } = _hexToRgb(hex);
    const rr = (255 + (r - 255) * clamped) | 0;
    const gg = (255 + (g - 255) * clamped) | 0;
    const bb = (255 + (b - 255) * clamped) | 0;
    return `rgb(${rr},${gg},${bb})`;
}

// Bit-packed improvement encoding: low 5 bits = type (0-31), rest = level-1.
const IMP_TYPE_BITS = 5;
const IMP_TYPE_MASK = (1 << IMP_TYPE_BITS) - 1;
function impType(raw)  { return raw & IMP_TYPE_MASK; }
function impLevel(raw) { return (raw >> IMP_TYPE_BITS) + 1; }

function getStaffableImprovementTypes(mapData) {
    const raw = mapData?.staffable_imp_types;
    if (Array.isArray(raw)) {
        return new Set(raw.map((v) => Number(v)).filter((v) => Number.isFinite(v)));
    }
    return new Set();
}

function getEmployeesPerLevel(mapData) {
    const n = Number(mapData?.employee_per_level);
    return Number.isFinite(n) && n > 0 ? n : 1;
}

function getPrimaryGoodByImprovement(mapData) {
    const raw = mapData?.imp_primary_good;
    const out = {};
    if (!raw || typeof raw !== "object") return out;
    for (const [k, v] of Object.entries(raw)) {
        out[Number(k)] = v;
    }
    return out;
}

function _impInfo(raw, cell, rivers, ter, ownerCity, goodEff, mapData) {
        const staffableTypes = getStaffableImprovementTypes(mapData);
        const employeesPerLevel = getEmployeesPerLevel(mapData);
        const primaryGoodByImprovement = getPrimaryGoodByImprovement(mapData);

    if (!raw) return { name: "—", level: 0, detail: null, employees: null };
    const type = impType(raw);
    const lvl = impLevel(raw);
    const name = improvementName(mapData, type);
    const onRiver = rivers.cell_river.has(cell);
    const riv = onRiver ? 2.0 : 1.0;
    let isCoastal = false;
    for (const n of neighbors(cell)) {
        if (n >= 0 && n < W * H && ter[n] <= 2) { isCoastal = true; break; }
    }
    const coast = isCoastal ? 1.5 : 1.0;

    // Staffing lookup: ownerCity.staffing is { "cell_int": levels_filled }.
    let staffLvl = 0;
    if (ownerCity && ownerCity.staffing) {
        const v = ownerCity.staffing[String(cell)];
        if (typeof v === "number") staffLvl = Math.min(v, lvl);
    }
    const staffFrac = lvl > 0 ? staffLvl / lvl : 0;
    const maxEmp = lvl * employeesPerLevel;
    const curEmp = staffLvl * employeesPerLevel;
    let employees = null;
    if (staffableTypes.has(type)) {
        employees = `👥 Employees ${curEmp} / ${maxEmp} (${staffLvl}/${lvl} staffed)`;
    }

    const s = (x) => (Math.round(x * 10) / 10);

    let detail = null;
    if (type === 1) { // Farm
        const grainMax = (1.5 + lvl * 1.0) * riv * coast;
        const grain = s(grainMax * staffFrac);
        let upCost = lvl < 20 ? `(up ${15 * lvl * lvl}g)` : "(max)";
        detail = `🌾 ${grain} grain (max ${s(grainMax)}) ${upCost}`;
    } else if (type === 2) { // Mine
        const rType = mapData?.res?.[String(cell)] ?? mapData?.res?.[cell] ?? null;
        let upCost = lvl < 5 ? `(up ${15 * lvl * 1.5}g)` : "(max)";
        const extras = [];
        if (rType === "sapphires") extras.push("Sapphires");
        if (rType === "iron") extras.push("Iron Ore");
        detail = `⛏ Produces Copper Ore${extras.length ? ` + ${extras.join(" + ")}` : ""} @ ${Math.round(staffFrac * 100)}% ${upCost}`;
    } else if (type === 4) { // Quarry
        let upCost = lvl < 5 ? `(up ${15 * lvl * 1.5}g)` : "(max)";
        detail = `🧱 Produces Stone @ ${Math.round(staffFrac * 100)}% ${upCost}`;
    } else if (type === 6) { // Windmill
        let upCost = lvl < 5 ? `(up ${15 * lvl * 1.5}g)` : "(max)";
        // Linear scaling: effective bonus uses staffed level, not building level.
        detail = `🌾 Neighbor farms ×${(1.0 + staffLvl * 0.5).toFixed(2)} (max ×${(1.0 + lvl * 0.5).toFixed(2)}) ${upCost}`;
    } else if (type === 7) { // Fort
        let upCost = lvl < 5 ? `(up ${15 * lvl * 1.5}g)` : "(max)";
        detail = `🏰 Garrison (10 metal/tick upkeep) ${upCost}`;
    } else if (type === 8) { // Port
        let upCost = lvl < 5 ? `(up ${15 * lvl * 1.5}g)` : "(max)";
        detail = `🚢 +${s(lvl * 2.0 * staffFrac)} Trade (max +${s(lvl * 2.0)}) ${upCost}`;
    } else if (type === 9) { // Smithery
        let upCost = lvl < 5 ? `(up ${15 * lvl * 1.5}g)` : "(max)";
        detail = `🔶 Refines up to ${s(lvl * 2.0 * staffFrac)} Copper Ore → Copper (max ${s(lvl * 2.0)}) ${upCost}`;
    } else if (type === 10) { // Fishery
        const grainMax = (1.0 + lvl * 0.8) * coast;
        const grain = s(grainMax * staffFrac);
        let upCost = lvl < 5 ? "upgradable" : "(max)";
        detail = `🐟 ${grain} grain + ${s(lvl * 0.8 * staffFrac)} trade (max ${s(grainMax)}g) ${upCost}`;
    } else if (type === 11) { // Cotton Farm
        const fabricMax = (1.2 + lvl * 0.75) * riv * coast;
        const fabric = s(fabricMax * staffFrac);
        let upCost = lvl < 10 ? `(up ${15 * lvl * 1.8}g)` : "(max)";
        detail = `🧵 ${fabric} fabric (max ${s(fabricMax)}) ${upCost}`;
    }

    // Local regional efficiency for the improvement's primary good (per employee unit).
    let efficiency = null;
    if (type === 2 && goodEff) {
        const parts = [];
        const copperEff = goodEff.copper?.[cell];
        const sappEff = goodEff.sapphires?.[cell];
        const ironOreEff = goodEff.iron_ore?.[cell];
        if (typeof copperEff === "number") {
            parts.push(`copper ×${copperEff.toFixed(2)}`);
        }
        if (typeof sappEff === "number") {
            parts.push(`sapphires ×${sappEff.toFixed(2)}`);
        }
        if (typeof ironOreEff === "number") {
            parts.push(`iron_ore ×${ironOreEff.toFixed(2)}`);
        }
        if (parts.length) {
            efficiency = `🗺 Regional eff ${parts.join(" · ")}`;
        }
    } else {
        const primaryGood = primaryGoodByImprovement[type];
        if (primaryGood && goodEff && goodEff[primaryGood]) {
            const eff = goodEff[primaryGood][cell];
            if (typeof eff === "number") {
                const perEmp = maxEmp > 0 ? eff / maxEmp : 0;
                efficiency = `🗺 Regional ${primaryGood} eff ×${eff.toFixed(2)} (${perEmp.toFixed(3)}/employee)`;
            }
        }
    }
    return { name, level: lvl, detail, employees, efficiency };
}

// ── Colour helpers ────────────────────────────────────────────────────────────

function hexToRgb(hex) {
    return [
        parseInt(hex.slice(1, 3), 16),
        parseInt(hex.slice(3, 5), 16),
        parseInt(hex.slice(5, 7), 16),
    ];
}

function blendColor(hex1, hex2, ratio) {
    const [r1, g1, b1] = hexToRgb(hex1);
    const [r2, g2, b2] = hexToRgb(hex2);
    return `rgb(${(r1*(1-ratio)+r2*ratio)|0},${(g1*(1-ratio)+g2*ratio)|0},${(b1*(1-ratio)+b2*ratio)|0})`;
}

function _syncMapDimensions(mapData) {
    if (!mapData) return;
    if (Number.isFinite(mapData.width) && mapData.width > 0) W = mapData.width | 0;
    if (Number.isFinite(mapData.height) && mapData.height > 0) H = mapData.height | 0;
    if (Number.isFinite(mapData.cell_size) && mapData.cell_size > 0) CELL = mapData.cell_size;
}

// ── Neighbour lookup (cardinal) ───────────────────────────────────────────────

function neighbors(cell) {
    const x = cell % W, y = (cell / W) | 0, r = [];
    if (x > 0)     r.push(cell - 1);
    if (x < W - 1) r.push(cell + 1);
    if (y > 0)     r.push(cell - W);
    if (y < H - 1) r.push(cell + W);
    return r;
}

// ── Road drawing helpers ──────────────────────────────────────────────────────

function _cellPx(cell) {
    return [(cell % W) * CELL + CELL / 2, ((cell / W) | 0) * CELL + CELL / 2];
}

function _tracePath(ctx, cells) {
    const [sx, sy] = _cellPx(cells[0]);
    ctx.moveTo(sx, sy);
    for (let i = 1; i < cells.length; i++) {
        const [px, py] = _cellPx(cells[i]);
        ctx.lineTo(px, py);
    }
}

function _traceOffsetPath(ctx, cells, offset) {
    for (let i = 0; i < cells.length; i++) {
        const [cx, cy] = _cellPx(cells[i]);
        let dx = 0, dy = 0;
        if (i > 0) { const [px, py] = _cellPx(cells[i - 1]); dx += cx - px; dy += cy - py; }
        if (i < cells.length - 1) { const [nx, ny] = _cellPx(cells[i + 1]); dx += nx - cx; dy += ny - cy; }
        const len = Math.hypot(dx, dy) || 1;
        dx /= len; dy /= len;
        const ox = cx + dy * offset, oy = cy - dx * offset;
        if (i === 0) ctx.moveTo(ox, oy); else ctx.lineTo(ox, oy);
    }
}

function _drawRiverRoad(ctx, seg) {
    if (seg.length < 2) return;
    ctx.strokeStyle = "#c8a862";
    ctx.lineWidth = 1.5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.setLineDash([]);
    ctx.beginPath();
    _traceOffsetPath(ctx, seg, CELL * 0.38);
    ctx.stroke();
}

// ── Main render ───────────────────────────────────────────────────────────────

export function renderFrame(ctx, mapData, state, opts = {}) {
    _syncMapDimensions(mapData);
    const { showRes = true, mapMode = "terrain", resourceGood = "grain", tick = 0, zoom = 1, selectedCity = null } = opts;
    const { ter, res, rivers, terrain_colors, imp_colors, good_efficiency } = mapData;
    let { civs = [], wars = [], impr = [] } = state;

    // ── Pre-flight data checks ───────────────────────────────────────────────
    if (!civs || !Array.isArray(civs)) civs = [];
    if (!wars || !Array.isArray(wars)) wars = [];
    if (!impr || !Array.isArray(impr)) impr = [];

    const civMap = new Map(civs.map(c => [c.id, c]));

    // Build war pairs set for fast lookup
    const warPairs = new Set();
    for (const w of wars) {
        if (!w || w.att === undefined || w.def_id === undefined) continue;
        warPairs.add(`${w.att}|${w.def_id}`);
        warPairs.add(`${w.def_id}|${w.att}`);
    }

    // Build ownership map from civ territories
    const om = new Int32Array(W * H);
    for (const civ of civs) {
        if (!civ.alive || !civ.territory) continue;
        for (const cell of civ.territory) {
            om[cell] = civ.id;
        }
    }

    // ── Terrain fill ─────────────────────────────────────────────────────────
    const effField = (mapMode === "resource" && good_efficiency) ? good_efficiency[resourceGood] : null;
    let effScale = 1.0;
    if (effField && effField.length) {
        let mx = 0.0;
        for (let i = 0; i < effField.length; i++) {
            const v = effField[i] || 0.0;
            if (v > mx) mx = v;
        }
        effScale = Math.max(0.001, mx);
    }
    const resourceHue = RESOURCE_MODE_COLORS[resourceGood] || "#0072B2";
    for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
            const i   = y * W + x;
            const t   = ter[i];
            const oid = om[i];
            const civ = oid ? civMap.get(oid) : null;

            if (mapMode === "political") {
                ctx.fillStyle = civ ? civ.color : (t <= 2 ? terrain_colors[t] : "#2a2a2a");
            } else if (mapMode === "resource" && effField) {
                // Water stays terrain-colored; land fades from white to a
                // single per-resource hue by concentration.
                if (t <= 2) {
                    ctx.fillStyle = terrain_colors[t];
                } else {
                    const e = effField[i] || 0;
                    const n = Math.max(0, Math.min(1, e / effScale));
                    ctx.fillStyle = _mixTowardWhite(resourceHue, n);
                }
            } else {
                ctx.fillStyle = civ
                    ? blendColor(terrain_colors[t], civ.color, 0.55)
                    : terrain_colors[t];
            }
            ctx.fillRect(x * CELL, y * CELL, CELL, CELL);
        }
    }

    // ── Improvements (zoomed in) ──────────────────────────────────────────────
    if (zoom >= 1.3 && mapMode !== "political" && mapMode !== "resource") {
        for (let i = 0; i < W * H; i++) {
            const raw = impr[i];
            if (!raw) continue;
            const it = impType(raw);
            const lvl = impLevel(raw);
            const cx = i % W;
            const cy = (i / W) | 0;
            const x = cx * CELL, y = cy * CELL;

            if (it === 1) { // Farm
                // Better graphic for farms: clear green/gold plots
                const baseColor = "#d4c84a";
                ctx.fillStyle = blendColor(baseColor, "#b0f048", Math.min(1, lvl / 20));
                ctx.globalAlpha = 0.6 + Math.min(0.4, (lvl / 20));
                
                // Draw tiny sub-plots to represent level
                // We divide the 4x4 inner cell into small squares based on level
                const cols = Math.ceil(Math.sqrt(lvl));
                const rows = Math.ceil(lvl / cols);
                const w = (CELL - 2) / cols;
                const h = (CELL - 2) / rows;
                
                let plotCount = 0;
                for (let r = 0; r < rows; r++) {
                    for (let c = 0; c < cols; c++) {
                        if (plotCount >= lvl) break;
                        ctx.fillRect(x + 1 + c * w, y + 1 + r * h, Math.max(0.4, w * 0.8), Math.max(0.4, h * 0.8));
                        plotCount++;
                    }
                }
                
                // Draw a text shadow and number for higher level farms to be extremely clear
                if (opts.showLevels && zoom >= 2 && lvl >= 1) {
                    ctx.globalAlpha = 1;
                    ctx.fillStyle = "white";
                    ctx.font = `bold ${Math.max(8, CELL * 0.4)}px sans-serif`;
                    ctx.textAlign = "center";
                    ctx.textBaseline = "middle";
                    ctx.shadowColor = "black";
                    ctx.shadowBlur = 3;
                    ctx.fillText(lvl.toString(), x + CELL / 2, y + CELL / 2);
                    ctx.shadowBlur = 0;
                }
            } else if (it === 4 && lvl > 1) {
                // Quarries: stone gray
                const baseColor = "#999999";
                const plotSize = CELL - 2;
                const plots = lvl;  // 1-3 rows of plots
                const rowH = Math.max(1, (plotSize / 3) | 0);
                for (let p = 0; p < plots; p++) {
                    ctx.fillStyle = baseColor;
                    ctx.globalAlpha = 0.35 + lvl * 0.1;
                    ctx.fillRect(x + 1, y + 1 + p * rowH, plotSize, rowH - (plots > 1 ? 1 : 0));
                }
            } else if (it === 10) { // Fishery — wavy blue ripples
                ctx.fillStyle = "#4aaed8";
                ctx.globalAlpha = 0.45 + Math.min(0.4, lvl * 0.08);
                ctx.fillRect(x + 1, y + 1, CELL - 2, CELL - 2);
                // Net grid overlay so it reads as a fishery and not water
                ctx.strokeStyle = "rgba(255,255,255,.7)";
                ctx.lineWidth = 0.6;
                ctx.beginPath();
                ctx.moveTo(x + 1, y + CELL / 2); ctx.lineTo(x + CELL - 1, y + CELL / 2);
                ctx.moveTo(x + CELL / 2, y + 1); ctx.lineTo(x + CELL / 2, y + CELL - 1);
                ctx.stroke();
            } else if (it === 7) { // Fort — crenellated tower
                ctx.globalAlpha = 1.0;
                // Shadow base so the fort stands out on any terrain
                ctx.fillStyle = "rgba(0,0,0,0.55)";
                ctx.fillRect(x + 0.6, y + 1.4, CELL - 1.2, CELL - 1.8);

                // Body — dark stone
                const bodyColor = "#3a3230";
                ctx.fillStyle = bodyColor;
                const bx = x + 1.2;
                const by = y + 2.0;
                const bw = CELL - 2.4;
                const bh = CELL - 3.0;
                ctx.fillRect(bx, by, bw, bh);

                // Crenellations along the top
                ctx.fillStyle = bodyColor;
                const merlonW = Math.max(0.7, bw / 3.2);
                ctx.fillRect(bx,                       by - 1.1, merlonW, 1.1);
                ctx.fillRect(bx + bw / 2 - merlonW/2,  by - 1.1, merlonW, 1.1);
                ctx.fillRect(bx + bw - merlonW,        by - 1.1, merlonW, 1.1);

                // Mortar highlight — thin light rim
                ctx.strokeStyle = "rgba(255,230,180,0.55)";
                ctx.lineWidth = 0.4;
                ctx.strokeRect(bx + 0.2, by + 0.2, bw - 0.4, bh - 0.4);

                // Level marker: colored pennant on top for level >= 2
                if (lvl >= 2) {
                    const pennantColors = ["#d9b84a", "#e8a33a", "#e07d2a", "#d7432a", "#9a1a1a"];
                    ctx.fillStyle = pennantColors[Math.min(lvl - 1, 4)];
                    ctx.beginPath();
                    ctx.moveTo(bx + bw / 2, by - 1.1);
                    ctx.lineTo(bx + bw / 2 + 1.6, by - 0.3);
                    ctx.lineTo(bx + bw / 2, by + 0.5);
                    ctx.closePath();
                    ctx.fill();
                }

                // Higher-level forts get a second lower band to look bulkier
                if (lvl >= 3) {
                    ctx.fillStyle = "rgba(255,255,255,0.12)";
                    ctx.fillRect(bx, by + bh * 0.55, bw, 0.8);
                }

                if (opts.showLevels && zoom >= 2.2 && lvl >= 1) {
                    ctx.fillStyle = "#ffdc88";
                    ctx.font = `bold ${Math.max(7, CELL * 0.38)}px sans-serif`;
                    ctx.textAlign = "center";
                    ctx.textBaseline = "middle";
                    ctx.shadowColor = "black";
                    ctx.shadowBlur = 2;
                    ctx.fillText(lvl.toString(), x + CELL / 2, y + CELL / 2 + 0.5);
                    ctx.shadowBlur = 0;
                }
            } else if (it === 9) { // Smithery — anvil/forge appearance
                ctx.globalAlpha = 1.0;
                // Base platform
                ctx.fillStyle = "#4a4a4a";
                ctx.fillRect(x + 1, y + CELL - 2.5, CELL - 2, 1.5);
                // Anvil body
                ctx.fillStyle = "#2a2a2a";
                const ax_body = x + CELL/2 - 1.2;
                const ay_body = y + CELL - 3.5;
                ctx.fillRect(ax_body, ay_body, 2.4, 1.5);
                // Horn/Tail
                ctx.fillRect(ax_body - 0.8, ay_body, 0.8, 0.8);
                ctx.fillRect(ax_body + 2.4, ay_body, 0.6, 0.8);
                
                // Orange glow (forge)
                ctx.fillStyle = "#ff6a00";
                ctx.globalAlpha = 0.6 + Math.sin(tick * 0.2) * 0.3;
                ctx.beginPath();
                ctx.arc(x + CELL/2 + 1, y + CELL/2 - 0.5, 0.8, 0, Math.PI * 2);
                ctx.fill();
                ctx.globalAlpha = 1.0;
            } else {
                ctx.fillStyle = imp_colors[it] || imp_colors[raw];
                ctx.globalAlpha = 0.4;
                ctx.fillRect(x + 1, y + 1, CELL - 2, CELL - 2);
            }
        }
        ctx.globalAlpha = 1;
    }

    // ── Rivers ────────────────────────────────────────────────────────────────
    ctx.strokeStyle = "#4aaef0";
    ctx.lineWidth   = zoom > 1.5 ? 2 : 1.3;
    ctx.lineCap     = "butt";
    ctx.lineJoin    = "round";
    for (const path of rivers.paths) {
        if (path.length < 2) continue;
        ctx.beginPath();
        let started = false;
        let prevPx = 0, prevPy = 0;
        for (let i = 0; i < path.length; i++) {
            let px = (path[i] % W) * CELL + CELL / 2;
            let py = ((path[i] / W) | 0) * CELL + CELL / 2;
            let isWater = ter[path[i]] <= 2;
            
            if (started && isWater) {
                // Stop exactly at the border between the river mouth (land) and the sea
                px = (prevPx + px) / 2;
                py = (prevPy + py) / 2;
            }
            
            if (!started) { 
                ctx.moveTo(px, py); 
                started = true; 
            } else {
                ctx.lineTo(px, py);
            }
            
            if (isWater) break;
            
            prevPx = px;
            prevPy = py;
        }
        if (started) ctx.stroke();
    }

    // ── Roads (with river interaction) ───────────────────────────────────────
    if (zoom >= 0.7) {
        const riverSet = rivers.cell_river;
        const roadLw = zoom > 1.5 ? 1.8 : 1.1;
        const riverLw = zoom > 1.5 ? 2 : 1.3;

        for (const civ of civs) {
             if (!civ.alive || !civ.road_paths) continue;
             for (const path of civ.road_paths) {
                 if (!path || path.length < 2) continue;

                 // Split path into segments: on-river vs off-river
                 let seg = [path[0]];
                 let segOnRiver = riverSet.has(path[0]) && riverSet.has(path[1]);

                 for (let i = 1; i < path.length; i++) {
                    const bothOnR = riverSet.has(path[i]) && riverSet.has(path[i - 1]);

                    if (bothOnR !== segOnRiver) {
                        if (segOnRiver) {
                            _drawRiverRoad(ctx, seg, riverLw);
                        } else {
                            // Normal road: solid line
                            ctx.strokeStyle = "#c8a862";
                            ctx.lineWidth = roadLw;
                            ctx.lineCap = "butt";
                            ctx.lineJoin = "round";
                            ctx.setLineDash([]);
                            ctx.beginPath();
                            _tracePath(ctx, seg);
                            ctx.stroke();
                        }
                        seg = [path[i - 1]];  // overlap by one for continuity
                        segOnRiver = bothOnR;
                    }
                    seg.push(path[i]);
                }
                // Draw final segment
                if (segOnRiver) {
                    _drawRiverRoad(ctx, seg, riverLw);
                } else {
                    ctx.strokeStyle = "#c8a862";
                    ctx.lineWidth = roadLw;
                    ctx.lineCap = "butt";
                    ctx.lineJoin = "round";
                    ctx.setLineDash([]);
                    ctx.beginPath();
                    _tracePath(ctx, seg);
                    ctx.stroke();
                }

                // Bridges: perpendicular planks + railings based on road direction
                for (let i = 1; i < path.length - 1; i++) {
                    if (riverSet.has(path[i]) && !riverSet.has(path[i - 1]) && !riverSet.has(path[i + 1])) {
                        const [bx, by] = _cellPx(path[i]);
                        const isH = ((path[i - 1] / W) | 0) === ((path[i] / W) | 0);
                        const half = CELL * 0.5;
                        ctx.setLineDash([]);
                        ctx.strokeStyle = "#7a5c14";
                        ctx.lineWidth = 1.3;
                        ctx.lineCap = "butt";
                        for (let p = -1; p <= 1; p++) {
                            const off = p * half * 0.45;
                            ctx.beginPath();
                            if (isH) { ctx.moveTo(bx + off, by - half * 0.7); ctx.lineTo(bx + off, by + half * 0.7); }
                            else      { ctx.moveTo(bx - half * 0.7, by + off); ctx.lineTo(bx + half * 0.7, by + off); }
                            ctx.stroke();
                        }
                        ctx.strokeStyle = "#5a3c0a";
                        ctx.lineWidth = 0.8;
                        ctx.beginPath();
                        if (isH) {
                            ctx.moveTo(bx - half * 0.5, by - half * 0.72); ctx.lineTo(bx + half * 0.5, by - half * 0.72);
                            ctx.moveTo(bx - half * 0.5, by + half * 0.72); ctx.lineTo(bx + half * 0.5, by + half * 0.72);
                        } else {
                            ctx.moveTo(bx - half * 0.72, by - half * 0.5); ctx.lineTo(bx - half * 0.72, by + half * 0.5);
                            ctx.moveTo(bx + half * 0.72, by - half * 0.5); ctx.lineTo(bx + half * 0.72, by + half * 0.5);
                        }
                        ctx.stroke();
                    }
                }
            }
        }
        ctx.setLineDash([]);
    }

    // ── Borders ───────────────────────────────────────────────────────────────
    for (const civ of civs) {
        if (!civ.alive || !civ.territory) continue;
        const terSet = new Set(civ.territory);
        for (const cell of civ.territory) {
            const x = cell % W, y = (cell / W) | 0;
            for (const n of neighbors(cell)) {
                if (terSet.has(n)) continue;
                const nOid = (n >= 0 && n < W * H) ? om[n] : 0;
                const isWar = nOid && warPairs.has(`${civ.id}|${nOid}`);
                if (isWar) {
                    ctx.strokeStyle = Math.sin(tick * 0.6 + cell * 0.15) > 0
                        ? "rgba(255,50,20,.9)" : "rgba(255,140,0,.85)";
                    ctx.lineWidth = 2;
                    ctx.setLineDash([3, 2]);
                } else if (nOid) {
                    // Border between two nations — white
                    ctx.strokeStyle = "rgba(255,255,255,.55)";
                    ctx.lineWidth   = 1.4;
                    ctx.setLineDash([]);
                } else {
                    // Border between nation and unclaimed — use civ's own color, bright
                    const [cr, cg, cb] = hexToRgb(civ.color);
                    ctx.strokeStyle = `rgba(${Math.min(cr+60,255)},${Math.min(cg+60,255)},${Math.min(cb+60,255)},.7)`;
                    ctx.lineWidth   = 1.2;
                    ctx.setLineDash([]);
                }
                const nx = n % W, ny = (n / W) | 0;
                ctx.beginPath();
                if      (nx === x - 1) { ctx.moveTo(x*CELL,      y*CELL);      ctx.lineTo(x*CELL,      y*CELL+CELL); }
                else if (nx === x + 1) { ctx.moveTo(x*CELL+CELL, y*CELL);      ctx.lineTo(x*CELL+CELL, y*CELL+CELL); }
                else if (ny === y - 1) { ctx.moveTo(x*CELL,      y*CELL);      ctx.lineTo(x*CELL+CELL, y*CELL);      }
                else if (ny === y + 1) { ctx.moveTo(x*CELL,      y*CELL+CELL); ctx.lineTo(x*CELL+CELL, y*CELL+CELL); }
                ctx.stroke();
            }
        }
    }
    ctx.setLineDash([]);

    // ── Resources ─────────────────────────────────────────────────────────────
    if (showRes && zoom >= 1.3) {
        ctx.font          = `${Math.max(5, CELL - 1)}px serif`;
        ctx.textAlign     = "center";
        ctx.textBaseline  = "middle";
        for (const [idxStr, type] of Object.entries(res)) {
            const i = parseInt(idxStr);
            ctx.fillText(resourceIcon(mapData, type), (i % W) * CELL + CELL / 2, ((i / W) | 0) * CELL + CELL / 2);
        }
    }

    // ── Selected city tile overlay ───────────────────────────────────────────
    if (selectedCity) {
        const sc = selectedCity;
        const allTiles = sc.tiles || [];
        const farmSet = new Set(sc.farm_tiles || []);
        const tileSet = new Set(allTiles);

        // Shade all city tiles
        for (const cell of allTiles) {
            ctx.fillStyle = farmSet.has(cell) ? "rgba(220,180,30,.25)" : "rgba(255,255,255,.12)";
            ctx.fillRect((cell % W) * CELL, ((cell / W) | 0) * CELL, CELL, CELL);
        }

        // Solid border around outer edge of the tile region (no lines between adjacent owned tiles)
        ctx.strokeStyle = "rgba(20,20,20,.7)";
        ctx.lineWidth = 1.2;
        ctx.setLineDash([]);
        ctx.beginPath();
        for (const cell of allTiles) {
            const x = cell % W, y = (cell / W) | 0;
            // Left edge
            if (!tileSet.has(cell - 1) || x === 0)
                { ctx.moveTo(x*CELL, y*CELL); ctx.lineTo(x*CELL, y*CELL+CELL); }
            // Right edge
            if (!tileSet.has(cell + 1) || x === W - 1)
                { ctx.moveTo(x*CELL+CELL, y*CELL); ctx.lineTo(x*CELL+CELL, y*CELL+CELL); }
            // Top edge
            if (!tileSet.has(cell - W) || y === 0)
                { ctx.moveTo(x*CELL, y*CELL); ctx.lineTo(x*CELL+CELL, y*CELL); }
            // Bottom edge
            if (!tileSet.has(cell + W) || y === H - 1)
                { ctx.moveTo(x*CELL, y*CELL+CELL); ctx.lineTo(x*CELL+CELL, y*CELL+CELL); }
        }
        ctx.stroke();
    }

    // ── Cities ────────────────────────────────────────────────────────────────
    // sqrt(pop) scaling: pop 25 -> sz 2, pop 100 -> sz 3.2, pop 1000 -> sz 6.3, pop 10000 -> sz 10
    for (const civ of civs) {
        if (!civ.alive || !civ.cities) continue;
        for (const city of civ.cities) {
            const cx = city.cell % W, cy = (city.cell / W) | 0;
            const px = cx * CELL + CELL / 2, py = cy * CELL + CELL / 2;
            const rawSz = Math.sqrt(city.population / 2) * 0.055;
            const sz = Math.max(1.2, Math.min(7.5, rawSz));
            const showLabel = city.is_capital || (zoom >= 1.5 && city.population > 60) || zoom >= 2.5;

            if (city.is_capital) {
                // Geometric star disc: civ-colored ring, white base,
                // gold 5-point star polygon on top. Crisper than a unicode
                // glyph and scales cleanly with zoom.
                const R = sz + 0.6;
                // dark halo so the icon stays legible over any terrain
                ctx.fillStyle = "rgba(0,0,0,0.55)";
                ctx.beginPath(); ctx.arc(px, py, R + 1.1, 0, Math.PI * 2); ctx.fill();
                // civ-colored ring
                ctx.fillStyle = civ.color;
                ctx.beginPath(); ctx.arc(px, py, R + 0.4, 0, Math.PI * 2); ctx.fill();
                // white inset disc (keeps star legible on dark civ colors)
                ctx.fillStyle = "#fff";
                ctx.beginPath(); ctx.arc(px, py, R - 0.5, 0, Math.PI * 2); ctx.fill();
                // 5-point gold star polygon
                const sOuter = R * 0.95;
                const sInner = sOuter * 0.42;
                ctx.beginPath();
                for (let i = 0; i < 10; i++) {
                    const ang = -Math.PI / 2 + i * Math.PI / 5;
                    const r = (i % 2 === 0) ? sOuter : sInner;
                    const xx = px + Math.cos(ang) * r;
                    const yy = py + Math.sin(ang) * r;
                    if (i === 0) ctx.moveTo(xx, yy);
                    else ctx.lineTo(xx, yy);
                }
                ctx.closePath();
                ctx.fillStyle = "#ffcc2a";
                ctx.fill();
                ctx.strokeStyle = "#6b4400";
                ctx.lineWidth = 0.7;
                ctx.stroke();
            } else if (zoom >= 0.7) {
                ctx.fillStyle   = "#fff";
                ctx.strokeStyle = civ.color;
                ctx.lineWidth   = 1;
                ctx.beginPath(); ctx.arc(px, py, sz, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
                ctx.fillStyle = civ.color;
                ctx.beginPath(); ctx.arc(px, py, Math.max(0.1, sz - 1), 0, Math.PI * 2); ctx.fill();
            }

            if (showLabel) {
                const base = city.is_capital ? 7.5 : 6;
                // Shrink gently with zoom so labels don't balloon; sqrt keeps
                // them readable at all zoom levels.
                const ls = Math.max(4, base / Math.sqrt(zoom));
                const lw = Math.max(0.4, 1.8 / zoom);
                const label = city.name;
                ctx.font         = `700 ${ls}px 'Cinzel', serif`;
                ctx.textAlign    = "center";
                ctx.textBaseline = "bottom";
                ctx.strokeStyle  = "rgba(0,0,0,.7)";
                ctx.lineWidth    = lw;
                ctx.strokeText(label, px, py - sz - 2);
                ctx.fillStyle    = city.is_capital ? "#ffd700" : "#eee";
                ctx.fillText(label, px, py - sz - 2);
            }
        }
    }

    // ── Nation name labels ────────────────────────────────────────────────────
    for (const civ of civs) {
        if (!civ.alive || !civ.territory || civ.territory.length < 10) continue;

        let minX = W, maxX = 0, minY = H, maxY = 0;
        for (const c of civ.territory) {
            const x = c % W, y = (c / W) | 0;
            if (x < minX) minX = x; if (x > maxX) maxX = x;
            if (y < minY) minY = y; if (y > maxY) maxY = y;
        }
        const bw = (maxX - minX) * CELL;
        let fs = Math.max(7, Math.min(22, Math.sqrt(civ.territory.length) * 1.2));
        const nw = civ.name.length * fs * 0.55;
        if (nw > bw * 0.85) fs = Math.max(5, (bw * 0.85) / (civ.name.length * 0.55));
        if (fs * zoom < 4.5) continue;

        let sx = 0, sy = 0;
        for (const c of civ.territory) { sx += c % W; sy += (c / W) | 0; }
        const cx2 = sx / civ.territory.length, cy2 = sy / civ.territory.length;
        const px = cx2 * CELL + CELL / 2, py = cy2 * CELL + CELL / 2;

        // Fade country names out as you zoom in — at close range they're
        // irrelevant and obscure the map.
        const civNameAlpha = Math.max(0, Math.min(1, (3.5 - zoom) / 1.5));
        if (civNameAlpha <= 0) continue;

        const atWar = wars.some(w => w.att === civ.id || w.def_id === civ.id);
        ctx.font         = `900 ${fs}px 'Cinzel', serif`;
        ctx.textAlign    = "center";
        ctx.textBaseline = "middle";
        ctx.strokeStyle  = `rgba(0,0,0,${0.6 * civNameAlpha})`;
        ctx.lineWidth    = 3;
        ctx.strokeText(civ.name.toUpperCase(), px, py);
        const { r, g, b } = _hexToRgb(civ.color);
        ctx.fillStyle    = `rgba(${r},${g},${b},${civNameAlpha})`;
        ctx.shadowColor  = atWar ? `rgba(255,51,51,${civNameAlpha})` : `rgba(${r},${g},${b},${civNameAlpha})`;
        ctx.shadowBlur   = atWar ? 6 : 3;
        ctx.fillText(civ.name.toUpperCase(), px, py);
        ctx.shadowBlur   = 0;
    }

    // ── Army icons ────────────────────────────────────────────────────────────
    // In "armies" map mode, also draw an objective line from each army to its
    // current target so the player can see what every force is doing.
    const armyMode = mapMode === "armies";

    // Build a cell -> list of armies index so multiple armies on the same
    // cell render fanned out instead of stacking on top of each other.
    const armiesByCell = new Map();
    for (const w of wars) {
        if (!w || w.att === undefined || w.def_id === undefined) continue;
        const cA = civMap.get(w.att), cB = civMap.get(w.def_id);
        if (!cA || !cB) continue;
        for (const a of (w.armies_a || [])) {
            if (a.strength <= 0) continue;
            const list = armiesByCell.get(a.cell) || [];
            list.push({ a, color: cA.color });
            armiesByCell.set(a.cell, list);
        }
        for (const a of (w.armies_d || [])) {
            if (a.strength <= 0) continue;
            const list = armiesByCell.get(a.cell) || [];
            list.push({ a, color: cB.color });
            armiesByCell.set(a.cell, list);
        }
    }

    for (const w of wars) {
        if (!w || w.att === undefined || w.def_id === undefined) continue;
        const cA = civMap.get(w.att), cB = civMap.get(w.def_id);
        if (!cA || !cB) continue;

        const allArmies = [
            ...(w.armies_a || []).map(a => ({ ...a, color: cA.color })),
            ...(w.armies_d || []).map(a => ({ ...a, color: cB.color })),
        ];

        // Objective lines (drawn first, beneath the icons)
        if (armyMode) {
            for (const army of allArmies) {
                const obj = army.objective;
                if (!obj || obj.target_cell == null) continue;
                if (obj.target_cell === army.cell) continue;
                const [ax, ay] = _cellPx(army.cell);
                const [tx, ty] = _cellPx(obj.target_cell);
                ctx.strokeStyle = army.color;
                ctx.globalAlpha = 0.7;
                ctx.lineWidth   = 1.4;
                if (obj.type === "city") {
                    ctx.setLineDash([3, 2]);
                } else if (obj.type === "army") {
                    ctx.setLineDash([1.5, 1.5]);
                } else if (obj.type === "relieve") {
                    ctx.setLineDash([4, 1, 1, 1]);
                } else {
                    ctx.setLineDash([0.5, 2.5]);
                }
                ctx.beginPath();
                ctx.moveTo(ax, ay);
                ctx.lineTo(tx, ty);
                ctx.stroke();
                // Arrowhead
                const ang = Math.atan2(ty - ay, tx - ax);
                const ah = 4;
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.moveTo(tx, ty);
                ctx.lineTo(tx - ah * Math.cos(ang - 0.5), ty - ah * Math.sin(ang - 0.5));
                ctx.lineTo(tx - ah * Math.cos(ang + 0.5), ty - ah * Math.sin(ang + 0.5));
                ctx.closePath();
                ctx.fill();
                ctx.globalAlpha = 1;
            }
            ctx.setLineDash([]);
        }

        for (const army of allArmies) {
            if (army.strength <= 0) continue;
            const px = (army.cell % W) * CELL + CELL / 2;
            const py = ((army.cell / W) | 0) * CELL + CELL / 2;

            // Background banner — colored shield/circle scaled to army strength
            const sFrac = Math.max(0, Math.min(1, army.strength / Math.max(1, army.max_strength)));
            const baseR = 1.5 + Math.sqrt(army.max_strength) * 0.09;

            // Outer ring (civ color)
            ctx.fillStyle = army.color;
            ctx.beginPath();
            ctx.arc(px, py, baseR, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = "rgba(0,0,0,.85)";
            ctx.lineWidth = 0.6;
            ctx.stroke();

            // Inner sword icon
            const fs = Math.max(5, baseR * 1.5);
            ctx.font         = `bold ${fs}px sans-serif`;
            ctx.textAlign    = "center";
            ctx.textBaseline = "middle";
            ctx.fillStyle    = "rgba(255,255,255,.95)";
            ctx.fillText("⚔", px, py + 0.5);

            // Health bar above the icon
            const bw = Math.max(6, baseR * 2.2);
            const bh = 1.0;
            const bx = px - bw / 2;
            const by = py - baseR - bh - 1;
            ctx.fillStyle = "rgba(0,0,0,.7)";
            ctx.fillRect(bx - 0.5, by - 0.5, bw + 1, bh + 1);
            ctx.fillStyle = sFrac > 0.5 ? "#3fb950" : sFrac > 0.25 ? "#f0c040" : "#f85149";
            ctx.fillRect(bx, by, bw * sFrac, bh);

            // Organization sub-bar (thinner, below)
            const oFrac = Math.max(0, Math.min(1, army.organization / 100));
            ctx.fillStyle = "rgba(0,0,0,.7)";
            ctx.fillRect(bx - 0.5, by + bh + 0.2, bw + 1, 0.9);
            ctx.fillStyle = "#58a6ff";
            ctx.fillRect(bx, by + bh + 0.2 + 0.05, bw * oFrac, 0.8);
        }
    }

    // ── City HP bars (only when damaged) ──────────────────────────────────────
    // Drawn BELOW the city symbol so it never overlaps the army HP bar that
    // hovers above any besieging army icon. Wider and walled to read as a
    // city wall meter, not just another army bar.
    for (const civ of civs) {
         if (!civ.alive || !civ.cities) continue;
        for (const city of civ.cities) {
            if (city.max_hp <= 0) continue;
            if (city.hp >= city.max_hp - 0.5) continue;  // not damaged
            const cx = city.cell % W, cy = (city.cell / W) | 0;
            const px = cx * CELL + CELL / 2, py = cy * CELL + CELL / 2;
            const frac = Math.max(0, Math.min(1, city.hp / city.max_hp));
            const bw = 16, bh = 2.2;
            const bx = px - bw / 2;
            const by = py + 5;  // BELOW the city tile (army bars sit above)
            // Outer dark frame (the "wall")
            ctx.fillStyle = "rgba(0,0,0,.85)";
            ctx.fillRect(bx - 1, by - 1, bw + 2, bh + 2);
            // Empty-bar background
            ctx.fillStyle = "#2a1a1a";
            ctx.fillRect(bx, by, bw, bh);
            // HP fill
            ctx.fillStyle = frac > 0.5 ? "#3fb950" : frac > 0.25 ? "#f0c040" : "#f85149";
            ctx.fillRect(bx, by, bw * frac, bh);
            // Crenellated top edge — three pips above the bar so it reads as a wall
            ctx.fillStyle = "rgba(220,220,220,.9)";
            const pipW = 2, pipH = 1;
            ctx.fillRect(bx + 1,            by - pipH - 0.5, pipW, pipH);
            ctx.fillRect(bx + bw / 2 - 1,   by - pipH - 0.5, pipW, pipH);
            ctx.fillRect(bx + bw - pipW - 1, by - pipH - 0.5, pipW, pipH);
        }
    }
}

// ── Tooltip hit-test ──────────────────────────────────────────────────────────

export function getCellInfo(mapData, state, cellIndex) {
    _syncMapDimensions(mapData);
    const { ter, res, rivers, hm, good_efficiency } = mapData;
    const { civs = [], impr = [], wars = [] } = state;

    const om = new Int32Array(W * H);
    for (const civ of civs) {
         if (!civ.alive || !civ.territory) continue;
         for (const cell of civ.territory) om[cell] = civ.id;
    }

    const oid  = om[cellIndex];
    const civ  = oid ? civs.find(c => c.id === oid) : null;
    // Match exact city cell
    let city = civ ? civ.cities.find(c => c.cell === cellIndex) : null;

    // Also figure out which city (if any) WORKS this tile — used to look up
    // staffing for the improvement on this cell, which lives on the owning
    // city's `staffing` dict (keyed by cell).
    let workingCity = city;
    if (!workingCity && civ) {
        for (const c2 of civ.cities) {
            const tiles = c2.tiles || [];
            if (tiles.includes(cellIndex)) { workingCity = c2; break; }
        }
    }

    // Check coastal: any neighbour is ocean/coast/deep
    let coastal = false;
    for (const n of neighbors(cellIndex)) {
        if (n >= 0 && n < W * H && ter[n] <= 2) { coastal = true; break; }
    }

    // Capacity/usage summary for this city's tiles
    let cityStats = null;
    if (city) {
        const tiles = city.tiles || [];
        let resCount = 0;
        for (const t of tiles) {
            if (res[t]) resCount++;
        }
        const capacities = city.capacities || {};
        const buildings = city.buildings || {};
        const capacityTotal = Object.values(capacities).reduce((a, v) => a + (Number(v) || 0), 0);
        const builtTotal = Object.keys(capacities).reduce((a, k) => a + (Number(buildings[k]) || 0), 0);
        cityStats = {
            resCount,
            tileCount: tiles.length,
            capacityTotal,
            builtTotal,
        };
    }

    const tileCapacityCity = workingCity || city;
    const tileCaps = tileCapacityCity?.tile_capacities?.[String(cellIndex)] || {};
    const tileBonus = tileCapacityCity?.tile_capacity_bonuses?.[String(cellIndex)] || {};

    // Collect any armies sitting on this cell across all wars
    const armiesHere = [];
    for (const w of wars) {
        for (const a of (w.armies_a || [])) {
            if (a.cell === cellIndex) armiesHere.push(a);
        }
        for (const a of (w.armies_d || [])) {
            if (a.cell === cellIndex) armiesHere.push(a);
        }
    }
    const armyInfos = armiesHere.map(a => {
        const owner = civs.find(c => c.id === a.civ_id);
        let targetCell = null, targetKind = null, targetName = null;
        if (a.objective) {
            targetCell = a.objective.target_cell;
            targetKind = a.objective.type;
            if (targetKind === "city") {
                outer: for (const c2 of civs) {
                    for (const ct of c2.cities) {
                        if (ct.cell === targetCell) { targetName = ct.name; break outer; }
                    }
                }
                if (!targetName) targetName = "enemy city";
            } else if (targetKind === "army") {
                // Locate the targeted army's owner via target_id across all wars
                const tid = a.objective.target_id;
                outer2: for (const w2 of wars) {
                    for (const arr of [w2.armies_a || [], w2.armies_d || []]) {
                        for (const ea of arr) {
                            if (ea.id === tid) {
                                const tc = civs.find(c => c.id === ea.civ_id);
                                targetName = tc ? `${tc.name} army` : "enemy army";
                                break outer2;
                            }
                        }
                    }
                }
                if (!targetName) targetName = "enemy army";
            } else if (targetKind === "defend") {
                targetName = "home fort";
            } else if (targetKind === "relieve") {
                outer3: for (const c2 of civs) {
                    for (const ct of c2.cities) {
                        if (ct.cell === targetCell) { targetName = `relieve ${ct.name}`; break outer3; }
                    }
                }
                if (!targetName) targetName = "relieve city";
            }
        }
        return {
            id: a.id,
            owner_name:   owner ? owner.name : "?",
            owner_color:  owner ? owner.color : "#888",
            commander:    a.commander ? a.commander.name : "?",
            skill:        a.commander ? a.commander.skill : 1.0,
            strength:     a.strength,
            max_strength: a.max_strength,
            organization: a.organization,
            supply:       a.supply,
            behavior:     a.behavior,
            fort_level:   a.fort_level,
            target_kind:  targetKind,
            target_name:  targetName,
            target_cell:  targetCell,
        };
    });

    return {
        x:       cellIndex % W,
        y:       (cellIndex / W) | 0,
        terrain: terrainName(mapData, ter[cellIndex]),
        alt:     (hm[cellIndex] * 100) | 0,
        res:     res[cellIndex],
        civ:     civ ? { name: civ.name, color: civ.color } : null,
        armies:  armyInfos,
        city:    city ? {
            name: city.name,
            pop: city.population | 0,
            gold: city.gold | 0,
            supply: city.supply || {},
            demand: city.demand || {},
            prices: city.prices || {},
            last_trades: city.last_trades || {},
            is_capital: city.is_capital,
            near_river: city.near_river,
            coastal: city.coastal,
            river_mouth: city.river_mouth || false,
            cell: city.cell,
            founded: city.founded,
            focus: city.focus || 1,
            stats: cityStats,
            workforce: city.workforce || 0,
            employed_pop: city.employed_pop || 0,
            unemployed_pop: city.unemployed_pop || 0,
            income_domestic: city.income_domestic || {},
            income_export: city.income_export || {},
            income_import: city.income_import || {},
            income_misc: city.income_misc || 0,
            income_total: city.income_total || 0,
            income_per_person: city.income_per_person || 0,
            economic_output: city.economic_output || 0,
            trade_export_volume: city.trade_export_volume || 0,
            trade_export_income: city.trade_export_income || 0,
            trade_capacity_required: city.trade_capacity_required || 0,
            trade_capacity_provided: city.trade_capacity_provided || 0,
            avg_consumption_level: city.avg_consumption_level || 0,
            population_growth_rate: city.population_growth_rate || 0,
            growth_food_contribution: city.growth_food_contribution !== undefined ? city.growth_food_contribution : 0,
            growth_consumption_penalty: city.growth_consumption_penalty !== undefined ? city.growth_consumption_penalty : 0,
            growth_unemployment_penalty: city.growth_unemployment_penalty !== undefined ? city.growth_unemployment_penalty : 0,
            buildings: city.buildings || {},
            building_staffing: city.building_staffing || {},
            building_profit: city.building_profit || {},
            building_details: city.building_details || [],
            capacities: city.capacities || {},
            shared_capacities: city.shared_capacities || {},
            capacity_bonuses: city.capacity_bonuses || {},
            tile_capacities: city.tile_capacities || {},
            tile_capacity_bonuses: city.tile_capacity_bonuses || {},
            attractiveness: city.attractiveness ?? 1.0,
            net_migration: city.net_migration ?? 0,
        } : null,
        imp:     _impInfo(impr[cellIndex] || 0, cellIndex, rivers, ter, workingCity, good_efficiency, mapData),
        tile_capacity: tileCaps,
        tile_capacity_bonus: tileBonus,
        river:   rivers.cell_river.has(cellIndex),
        coastal,
    };
}
