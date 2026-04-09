/**
 * renderer.js — pure canvas drawing, no framework
 * Receives game state from the server and paints it.
 */

const W = 160, H = 100, CELL = 6;

// Terrain names for the tooltip
const TERRAIN_NAMES = {
    0:"Deep Ocean", 1:"Ocean", 2:"Coast", 3:"Beach", 4:"Plains",
    5:"Grassland",  6:"Forest", 7:"Dense Forest", 8:"Hills",
    9:"Mountains",  10:"Snow Peak", 11:"Desert", 12:"Tundra",
    13:"Jungle",    14:"Swamp",
};

const RESOURCE_ICONS = {
    iron:"⛏", gold:"✦", horses:"🐎", wheat:"🌾", fish:"🐟",
    gems:"💎", wood:"🪵", stone:"🪨", spices:"🌶", ivory:"🦷",
};

const IMP_NAMES = { 0:"—", 1:"Farm", 2:"Mine", 3:"Lumber", 4:"Quarry", 5:"Pasture" };

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

// ── Neighbour lookup (cardinal) ───────────────────────────────────────────────

function neighbors(cell) {
    const x = cell % W, y = (cell / W) | 0, r = [];
    if (x > 0)     r.push(cell - 1);
    if (x < W - 1) r.push(cell + 1);
    if (y > 0)     r.push(cell - W);
    if (y < H - 1) r.push(cell + W);
    return r;
}

// ── Road segment drawing helper ───────────────────────────────────────────────

function _drawRoadSeg(ctx, seg, onRiver, riverSet, offset, lw) {
    if (seg.length < 2) return;

    if (onRiver) {
        // Road runs along river: draw two thin parallel lines on each side
        ctx.strokeStyle = "rgba(210,185,130,.7)";
        ctx.lineWidth = lw * 0.6;
        ctx.setLineDash([3, 3]);
        for (const side of [-1, 1]) {
            ctx.beginPath();
            for (let i = 0; i < seg.length; i++) {
                const cx = (seg[i] % W) * CELL + CELL / 2;
                const cy = ((seg[i] / W) | 0) * CELL + CELL / 2;
                let dx = 0, dy = 0;
                if (i < seg.length - 1) {
                    dx = (seg[i + 1] % W) - (seg[i] % W);
                    dy = ((seg[i + 1] / W) | 0) - ((seg[i] / W) | 0);
                } else {
                    dx = (seg[i] % W) - (seg[i - 1] % W);
                    dy = ((seg[i] / W) | 0) - ((seg[i - 1] / W) | 0);
                }
                const len = Math.sqrt(dx * dx + dy * dy) || 1;
                const px = cx + side * (-dy / len) * offset;
                const py = cy + side * (dx / len) * offset;
                if (i === 0) ctx.moveTo(px, py);
                else ctx.lineTo(px, py);
            }
            ctx.stroke();
        }
    } else {
        ctx.strokeStyle = "rgba(210,185,130,.8)";
        ctx.lineWidth = lw;
        ctx.setLineDash([4, 2]);
        ctx.beginPath();
        ctx.moveTo((seg[0] % W) * CELL + CELL / 2, ((seg[0] / W) | 0) * CELL + CELL / 2);
        for (let i = 1; i < seg.length; i++) {
            ctx.lineTo((seg[i] % W) * CELL + CELL / 2, ((seg[i] / W) | 0) * CELL + CELL / 2);
        }
        ctx.stroke();
    }
}

// ── Main render ───────────────────────────────────────────────────────────────

export function renderFrame(ctx, mapData, state, opts = {}) {
    const { showRes = true, mapMode = "terrain", tick = 0, zoom = 1, selectedCity = null } = opts;
    const { ter, res, rivers, terrain_colors, imp_colors } = mapData;
    const { civs = [], wars = [], impr = [] } = state;

    const civMap = new Map(civs.map(c => [c.id, c]));

    // Build war pairs set for fast lookup
    const warPairs = new Set();
    for (const w of wars) {
        warPairs.add(`${w.a_id}|${w.d_id}`);
        warPairs.add(`${w.d_id}|${w.a_id}`);
    }

    // Build ownership map from civ territories
    const om = new Int32Array(W * H);
    for (const civ of civs) {
        if (!civ.alive) continue;
        for (const cell of civ.territory) {
            om[cell] = civ.id;
        }
    }

    // ── Terrain fill ─────────────────────────────────────────────────────────
    for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
            const i   = y * W + x;
            const t   = ter[i];
            const oid = om[i];
            const civ = oid ? civMap.get(oid) : null;

            if (mapMode === "political") {
                ctx.fillStyle = civ ? civ.color : (t <= 2 ? terrain_colors[t] : "#2a2a2a");
            } else {
                ctx.fillStyle = civ
                    ? blendColor(terrain_colors[t], civ.color, 0.55)
                    : terrain_colors[t];
            }
            ctx.fillRect(x * CELL, y * CELL, CELL, CELL);
        }
    }

    // ── Improvements (zoomed in) ──────────────────────────────────────────────
    if (zoom >= 1.3 && mapMode !== "political") {
        for (let i = 0; i < W * H; i++) {
            const imp = impr[i];
            if (!imp) continue;
            ctx.fillStyle = imp_colors[imp];
            ctx.globalAlpha = 0.3;
            ctx.fillRect((i % W) * CELL + 1, ((i / W) | 0) * CELL + 1, CELL - 2, CELL - 2);
        }
        ctx.globalAlpha = 1;
    }

    // ── Rivers ────────────────────────────────────────────────────────────────
    ctx.strokeStyle = "#4aaef0";
    ctx.lineWidth   = zoom > 1.5 ? 2 : 1.3;
    ctx.lineCap     = "round";
    ctx.lineJoin    = "round";
    for (const path of rivers.paths) {
        if (path.length < 2) continue;
        ctx.beginPath();
        ctx.moveTo((path[0] % W) * CELL + CELL / 2, ((path[0] / W) | 0) * CELL + CELL / 2);
        for (let i = 1; i < path.length; i++) {
            ctx.lineTo((path[i] % W) * CELL + CELL / 2, ((path[i] / W) | 0) * CELL + CELL / 2);
        }
        ctx.stroke();
    }

    // ── Roads (with river interaction) ───────────────────────────────────────
    if (zoom >= 0.7) {
        const riverSet = rivers.cell_river;
        const roadLw = zoom > 1.5 ? 2.0 : 1.2;
        const offset = roadLw + 1;  // pixel offset for parallel lines

        for (const civ of civs) {
            if (!civ.alive) continue;
            for (const path of civ.road_paths) {
                if (path.length < 2) continue;

                // Split path into segments: on-river vs off-river
                let seg = [path[0]];
                let segOnRiver = riverSet.has(path[0]) && riverSet.has(path[1]);

                for (let i = 1; i < path.length; i++) {
                    const cellOnR = riverSet.has(path[i]);
                    const prevOnR = riverSet.has(path[i - 1]);
                    const bothOnR = cellOnR && prevOnR;

                    if (bothOnR !== segOnRiver) {
                        // Draw the accumulated segment
                        _drawRoadSeg(ctx, seg, segOnRiver, riverSet, offset, roadLw);
                        seg = [path[i - 1]];  // overlap by one for continuity
                        segOnRiver = bothOnR;
                    }
                    seg.push(path[i]);
                }
                _drawRoadSeg(ctx, seg, segOnRiver, riverSet, offset, roadLw);

                // Draw bridges where road crosses river (single cell on river between non-river)
                for (let i = 1; i < path.length - 1; i++) {
                    if (riverSet.has(path[i]) && !riverSet.has(path[i - 1]) && !riverSet.has(path[i + 1])) {
                        const px = (path[i] % W) * CELL + CELL / 2;
                        const py = ((path[i] / W) | 0) * CELL + CELL / 2;
                        ctx.fillStyle = "#6b4c2a";
                        ctx.fillRect(px - 2, py - 2, 4, 4);
                        ctx.strokeStyle = "#3d2a14";
                        ctx.lineWidth = 0.8;
                        ctx.setLineDash([]);
                        ctx.strokeRect(px - 2, py - 2, 4, 4);
                    }
                }
            }
        }
        ctx.setLineDash([]);
    }

    // ── Borders ───────────────────────────────────────────────────────────────
    for (const civ of civs) {
        if (!civ.alive) continue;
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
            ctx.fillText(RESOURCE_ICONS[type], (i % W) * CELL + CELL / 2, ((i / W) | 0) * CELL + CELL / 2);
        }
    }

    // ── Selected city farm tile overlay ──────────────────────────────────────
    if (selectedCity) {
        const sc = selectedCity;
        // Draw tile boundary for selected city's farm tiles
        if (sc.farm_tiles && sc.farm_tiles.length > 0) {
            ctx.fillStyle = "rgba(200,160,0,.15)";
            for (const cell of sc.farm_tiles) {
                ctx.fillRect((cell % W) * CELL, ((cell / W) | 0) * CELL, CELL, CELL);
            }
            // Border around farm tile region
            const farmSet = new Set(sc.farm_tiles);
            ctx.strokeStyle = "rgba(200,160,0,.6)";
            ctx.lineWidth = 1;
            ctx.setLineDash([2, 1]);
            for (const cell of sc.farm_tiles) {
                const x = cell % W, y = (cell / W) | 0;
                for (const n of neighbors(cell)) {
                    if (!farmSet.has(n)) {
                        const nx2 = n % W, ny2 = (n / W) | 0;
                        ctx.beginPath();
                        if      (nx2 === x - 1) { ctx.moveTo(x*CELL,      y*CELL);      ctx.lineTo(x*CELL,      y*CELL+CELL); }
                        else if (nx2 === x + 1) { ctx.moveTo(x*CELL+CELL, y*CELL);      ctx.lineTo(x*CELL+CELL, y*CELL+CELL); }
                        else if (ny2 === y - 1) { ctx.moveTo(x*CELL,      y*CELL);      ctx.lineTo(x*CELL+CELL, y*CELL);      }
                        else if (ny2 === y + 1) { ctx.moveTo(x*CELL,      y*CELL+CELL); ctx.lineTo(x*CELL+CELL, y*CELL+CELL); }
                        ctx.stroke();
                    }
                }
            }
            ctx.setLineDash([]);
        }
    }

    // ── Cities ────────────────────────────────────────────────────────────────
    // sqrt(pop) scaling: pop 25 -> sz 2, pop 100 -> sz 3.2, pop 1000 -> sz 6.3, pop 10000 -> sz 10
    for (const civ of civs) {
        if (!civ.alive) continue;
        for (const city of civ.cities) {
            const cx = city.cell % W, cy = (city.cell / W) | 0;
            const px = cx * CELL + CELL / 2, py = cy * CELL + CELL / 2;
            const rawSz = Math.sqrt(city.population) * 0.1;
            const sz = city.is_capital ? Math.max(5, rawSz + 1) : Math.max(2, Math.min(12, rawSz));
            const showLabel = city.is_capital || (zoom >= 1.5 && city.population > 60) || zoom >= 2.5;

            if (city.is_capital) {
                ctx.fillStyle   = "#fff";
                ctx.strokeStyle = civ.color;
                ctx.lineWidth   = 2;
                ctx.beginPath(); ctx.arc(px, py, sz + 1, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
                ctx.fillStyle    = civ.color;
                ctx.font         = `bold ${CELL + 2}px sans-serif`;
                ctx.textAlign    = "center";
                ctx.textBaseline = "middle";
                ctx.fillText("★", px, py + 1);
            } else if (zoom >= 0.7) {
                ctx.fillStyle   = "#fff";
                ctx.strokeStyle = civ.color;
                ctx.lineWidth   = 1;
                ctx.beginPath(); ctx.arc(px, py, sz, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
                ctx.fillStyle = civ.color;
                ctx.beginPath(); ctx.arc(px, py, sz - 1, 0, Math.PI * 2); ctx.fill();
            }

            if (showLabel) {
                const ls = city.is_capital ? 7 : 5.5;
                ctx.font         = `bold ${ls}px sans-serif`;
                ctx.textAlign    = "center";
                ctx.textBaseline = "top";
                ctx.strokeStyle  = "rgba(0,0,0,.7)";
                ctx.lineWidth    = 2;
                ctx.strokeText(city.name, px, py + sz + 2);
                ctx.fillStyle    = "#eee";
                ctx.fillText(city.name, px, py + sz + 2);
            }
        }
    }

    // ── Nation name labels ────────────────────────────────────────────────────
    for (const civ of civs) {
        if (!civ.alive || civ.territory.length < 10) continue;

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

        const atWar = wars.some(w => w.a_id === civ.id || w.d_id === civ.id);
        ctx.font         = `900 ${fs}px Georgia,serif`;
        ctx.textAlign    = "center";
        ctx.textBaseline = "middle";
        ctx.strokeStyle  = "rgba(0,0,0,.55)";
        ctx.lineWidth    = 2.5;
        ctx.strokeText(civ.name.toUpperCase(), px, py);
        ctx.fillStyle    = civ.color;
        ctx.shadowColor  = atWar ? "#f33" : civ.color;
        ctx.shadowBlur   = atWar ? 5 : 2;
        ctx.fillText(civ.name.toUpperCase(), px, py);
        ctx.shadowBlur   = 0;
    }

    // ── War icons ─────────────────────────────────────────────────────────────
    const drawnWars = new Set();
    for (const w of wars) {
        const k = `${Math.min(w.a_id, w.d_id)}|${Math.max(w.a_id, w.d_id)}`;
        if (drawnWars.has(k)) continue;
        drawnWars.add(k);
        const cA = civMap.get(w.a_id), cB = civMap.get(w.d_id);
        if (!cA || !cB) continue;
        const terB = new Set(cB.territory);
        let bx = 0, by = 0, bc = 0;
        outer: for (const c of cA.territory) {
            for (const n of neighbors(c)) {
                if (terB.has(n)) { bx += c % W; by += (c / W) | 0; bc++; if (bc > 4) break outer; }
            }
        }
        if (bc) {
            const px = (bx / bc) * CELL + CELL / 2, py = (by / bc) * CELL + CELL / 2;
            const fs = (12 + Math.sin(tick * 0.4) * 2) | 0;
            ctx.font         = `bold ${fs}px sans-serif`;
            ctx.textAlign    = "center";
            ctx.textBaseline = "middle";
            ctx.strokeStyle  = "rgba(0,0,0,.5)";
            ctx.lineWidth    = 2;
            ctx.strokeText("⚔", px, py);
            ctx.fillStyle   = "#f44";
            ctx.shadowColor = "#f00";
            ctx.shadowBlur  = 4;
            ctx.fillText("⚔", px, py);
            ctx.shadowBlur  = 0;
        }
    }
}

// ── Tooltip hit-test ──────────────────────────────────────────────────────────

export function getCellInfo(mapData, state, cellIndex) {
    const { ter, res, rivers, hm } = mapData;
    const { civs = [], impr = [] } = state;

    const om = new Int32Array(W * H);
    for (const civ of civs) {
        if (!civ.alive) continue;
        for (const cell of civ.territory) om[cell] = civ.id;
    }

    const oid  = om[cellIndex];
    const civ  = oid ? civs.find(c => c.id === oid) : null;
    const city = civ ? civ.cities.find(c => c.cell === cellIndex) : null;

    // Check coastal: any neighbour is ocean/coast/deep
    let coastal = false;
    for (const n of neighbors(cellIndex)) {
        if (n >= 0 && n < W * H && ter[n] <= 2) { coastal = true; break; }
    }

    return {
        x:       cellIndex % W,
        y:       (cellIndex / W) | 0,
        terrain: TERRAIN_NAMES[ter[cellIndex]],
        alt:     (hm[cellIndex] * 100) | 0,
        res:     res[cellIndex],
        civ:     civ ? { name: civ.name, color: civ.color } : null,
        city:    city ? {
            name: city.name,
            pop: city.population | 0,
            trade: city.trade | 0,
            food: city.food_production | 0,
            cap: city.carrying_cap | 0,
            farm_tiles: city.farm_tiles || [],
            is_capital: city.is_capital,
            near_river: city.near_river,
            coastal: city.coastal,
            wealth: city.wealth | 0,
            cell: city.cell,
        } : null,
        imp:     IMP_NAMES[impr[cellIndex] || 0],
        river:   rivers.cell_river.has(cellIndex),
        coastal,
    };
}
