import { useState, useEffect, useRef, useCallback } from "react";

const W = 160, H = 100, CELL = 6, PXW = W * CELL, PXH = H * CELL, N = W * H;
const T = { DEEP:0, OCEAN:1, COAST:2, BEACH:3, PLAINS:4, GRASS:5, FOREST:6, DFOREST:7, HILLS:8, MTN:9, SNOW:10, DESERT:11, TUNDRA:12, SWAMP:13, JUNGLE:14 };
const TC = {[T.DEEP]:"#0a1628",[T.OCEAN]:"#0f2847",[T.COAST]:"#1a4a6e",[T.BEACH]:"#d4c078",[T.PLAINS]:"#7ab648",[T.GRASS]:"#5a9e3a",[T.FOREST]:"#2d7a2d",[T.DFOREST]:"#1a5c1a",[T.HILLS]:"#8a7a50",[T.MTN]:"#6e6e6e",[T.SNOW]:"#e8e8f0",[T.DESERT]:"#d4a84b",[T.TUNDRA]:"#a8b8b0",[T.JUNGLE]:"#1a6830",[T.SWAMP]:"#3a5a3a"};
const TN = {[T.DEEP]:"Deep Ocean",[T.OCEAN]:"Ocean",[T.COAST]:"Coast",[T.BEACH]:"Beach",[T.PLAINS]:"Plains",[T.GRASS]:"Grassland",[T.FOREST]:"Forest",[T.DFOREST]:"Dense Forest",[T.HILLS]:"Hills",[T.MTN]:"Mountains",[T.SNOW]:"Snow Peak",[T.DESERT]:"Desert",[T.TUNDRA]:"Tundra",[T.JUNGLE]:"Jungle",[T.SWAMP]:"Swamp"};
const RICON = {iron:"⛏",gold:"✦",horses:"🐎",wheat:"🌾",fish:"🐟",gems:"💎",wood:"🪵",stone:"🪨",spices:"🌶",ivory:"🦷"};
const IMP = {NONE:0,FARM:1,MINE:2,LUMBER:3,QUARRY:4,PASTURE:5};
const IMP_COLORS = {[IMP.FARM]:"#c8a000",[IMP.MINE]:"#888",[IMP.LUMBER]:"#4a2",[IMP.QUARRY]:"#999",[IMP.PASTURE]:"#7b5"};
const IMP_NAMES = {[IMP.NONE]:"—",[IMP.FARM]:"Farm",[IMP.MINE]:"Mine",[IMP.LUMBER]:"Lumber",[IMP.QUARRY]:"Quarry",[IMP.PASTURE]:"Pasture"};
const CAN_FARM = new Set([T.PLAINS, T.GRASS, T.JUNGLE, T.SWAMP]);
const RES_LIST = ["iron","gold","horses","wheat","fish","gems","wood","stone","spices","ivory"];

const PRE=["Ar","Bal","Cor","Dra","El","Fal","Gor","Ha","Ith","Jar","Kel","Lor","Mar","Nor","Or","Par","Qar","Ren","Sol","Tar","Ul","Val","Wor","Xen","Yr","Zan","Ak","Bri","Cael","Dur","Esh","Fen","Gil","Hel","Iro","Jul","Kha","Lun","Myr","Niv","Osh","Pyr","Rha","Syr","Thal","Ur","Ves","Wyn","Xar","Yth","Zul"];
const MID_S=["an","eth","in","on","ul","ash","ith","or","en","al","os","ur","ak","em","id","ar","el","ok","un","is"];
const SUF_S=["ia","os","um","is","ar","en","oth","ax","ium","ica","esh","and","or","heim","gard","rok","ven","dale","mere","hold","stan","land","rea","nia","tia"];
const CPRE=["New ","Fort ","Port ","Saint ","North ","South ","East ","West ","Old ","Great ","","","","","","","","","",""];
const CSUF=["ton","burg","ville","haven","ford","field","gate","bridge","keep","watch","holm","stead","crest","fall","shore","wood","vale","moor","peak","port","bay","well","dale"];
const LF=["Arak","Belen","Cyra","Dorn","Eska","Fenn","Gael","Hira","Ivak","Jael","Kira","Lorn","Mira","Nael","Orik","Pala","Rath","Sela","Tarn","Ula","Vorn","Wyra","Xael","Yara","Zorn","Alys","Bram","Cassia","Theron","Lysa","Magnus","Freya"];
const LL=["the Bold","the Wise","Ironhand","Stormborn","Goldeneye","the Just","the Cruel","the Great","the Conqueror","Peacemaker","the Silent","Sunbringer","the Unyielding","the Cunning","the Mad","the Young"];
const pk = a => a[(Math.random() * a.length) | 0];
const gCN = () => pk(PRE) + (Math.random() > .35 ? pk(MID_S) : "") + pk(SUF_S);
const gCiN = () => pk(CPRE) + pk(PRE).toLowerCase() + pk(CSUF);
const gLN = () => pk(LF) + " " + pk(LL);
const PAL = ["#e74c3c","#3498db","#f39c12","#2ecc71","#9b59b6","#e67e22","#1abc9c","#c0392b","#2980b9","#27ae60","#8e44ad","#d35400","#16a085","#f1c40f","#e84393","#00b894","#6c5ce7","#fd79a8","#00cec9","#d63031","#0984e3","#00b4d8","#a29bfe","#636e72","#b2bec3"];
let cIdx = 0, civId = 1;
const nxtC = () => { const c = PAL[cIdx % PAL.length]; cIdx++; return c; };

// ── Noise ──────────────────────────────────────────────────────────
function mkNoise(seed) {
  const perm = new Uint8Array(512); let s = seed | 0;
  const nx = () => { s = (s * 16807) % 2147483647; return s; };
  const p = new Uint8Array(256); for (let i = 0; i < 256; i++) p[i] = i;
  for (let i = 255; i > 0; i--) { const j = nx() % (i + 1); [p[i], p[j]] = [p[j], p[i]]; }
  for (let i = 0; i < 512; i++) perm[i] = p[i & 255];
  const fd = t => t * t * t * (t * (t * 6 - 15) + 10);
  const lr = (a, b, t) => a + t * (b - a);
  const gr = (h, x, y) => { const v = h & 3; return v === 0 ? x + y : v === 1 ? -x + y : v === 2 ? x - y : -x - y; };
  return (x, y) => {
    const X = Math.floor(x) & 255, Y = Math.floor(y) & 255;
    const xf = x - Math.floor(x), yf = y - Math.floor(y);
    const u = fd(xf), v = fd(yf);
    return lr(lr(gr(perm[perm[X] + Y], xf, yf), gr(perm[perm[X + 1] + Y], xf - 1, yf), u),
      lr(gr(perm[perm[X] + Y + 1], xf, yf - 1), gr(perm[perm[X + 1] + Y + 1], xf - 1, yf - 1), u), v);
  };
}
function fbm(n, x, y, o = 6) {
  let v = 0, a = 1, f = 1, m = 0;
  for (let i = 0; i < o; i++) { v += n(x * f, y * f) * a; m += a; a *= .5; f *= 2; }
  return v / m;
}

// ── Rivers: strict downhill, no loops, no splits, discard if no ocean ──
function genRivers(hm, ter, seed) {
  const rng = mkNoise(seed + 7777);
  const numAttempts = 18;
  const allPaths = [];
  const globalUsed = new Set(); // cells already used by a river — prevents merging too

  for (let r = 0; r < numAttempts; r++) {
    // Find a high-altitude start
    let startCell = -1, startH = 0;
    for (let att = 0; att < 80; att++) {
      const x = 5 + (((rng(r * 3.1 + att * 7.3, r * 1.7 + att * 2.9) + 1) / 2) * (W - 10)) | 0;
      const y = 5 + (((rng(r * 2.3 + att * 5.1, r * 4.1 + att * 1.3) + 1) / 2) * (H - 10)) | 0;
      const i = y * W + x;
      if (hm[i] > 0.6 && hm[i] < 0.93 && ter[i] >= T.PLAINS && !globalUsed.has(i) && hm[i] > startH) {
        startH = hm[i]; startCell = i;
      }
    }
    if (startCell === -1) continue;

    // Flow strictly downhill — always pick the lowest neighbor
    const path = [startCell];
    const visited = new Set([startCell]);
    let cur = startCell;
    let reachedOcean = false;

    for (let step = 0; step < 300; step++) {
      const cx = cur % W, cy = (cur / W) | 0;
      const neighbors = [];
      if (cx > 0) neighbors.push(cur - 1);
      if (cx < W - 1) neighbors.push(cur + 1);
      if (cy > 0) neighbors.push(cur - W);
      if (cy < H - 1) neighbors.push(cur + W);

      // Sort by height ascending — pick the strictly lowest
      let bestN = -1, bestH = hm[cur]; // must go lower than current
      for (const n of neighbors) {
        if (visited.has(n)) continue;
        if (globalUsed.has(n)) continue; // don't merge into other rivers
        if (hm[n] < bestH) { bestH = hm[n]; bestN = n; }
      }

      if (bestN === -1) break; // stuck — no lower neighbor

      visited.add(bestN);
      path.push(bestN);

      if (ter[bestN] <= T.COAST) { reachedOcean = true; break; }
      cur = bestN;
    }

    // Only keep rivers that actually reached the ocean and are long enough
    if (reachedOcean && path.length >= 8) {
      allPaths.push(path);
      for (const c of path) globalUsed.add(c);
    }
  }

  // Build cell lookup
  const cellRiver = new Set();
  for (const p of allPaths) for (const c of p) cellRiver.add(c);
  return { paths: allPaths, cellRiver };
}

function cellOnRiver(cell, rivers) { return rivers.cellRiver.has(cell); }
function cellCoastal(cell, ter) {
  for (const n of nb(cell)) {
    if (n >= 0 && n < N && (ter[n] === T.OCEAN || ter[n] === T.COAST || ter[n] === T.DEEP)) return true;
  }
  return false;
}
function cellRiverMouth(cell, ter, rivers) {
  if (!rivers.cellRiver.has(cell)) return false;
  if (ter[cell] <= T.COAST) return false;
  for (const n of nb(cell)) {
    if (n >= 0 && n < N && (ter[n] === T.OCEAN || ter[n] === T.COAST || ter[n] === T.DEEP)) return true;
  }
  return false;
}

function settleScore(cell, ter, rivers, res, allCityCells, params) {
  const t = ter[cell];
  if (t === T.MTN || t === T.SNOW || t === T.DESERT || t <= T.COAST) return null;
  let score = 0;
  if (allCityCells.length) {
    let minD = Infinity;
    for (const oc of allCityCells) { const d = dist(cell, oc); if (d < minD) minD = d; }
    if (minD <= 2) score -= 500;
    else if (minD <= 4) score -= 80 / minD;
    else if (minD <= 7) score -= 30 / minD;
    score += Math.min(minD * 0.3, 5);
  }
  if (cellRiverMouth(cell, ter, rivers)) score += 60;
  else if (cellOnRiver(cell, rivers)) score += (params.riverPref || 10) * 1.5;
  if (cellCoastal(cell, ter)) score += (params.coastPref || 5) * 1.5;
  if (CAN_FARM.has(t)) score += 2;
  if (res.has(cell)) score += 3;
  return score;
}

function evalSettleCandidate(civ, cell, ter, rivers, res, allCityCells, params) {
  const sc = settleScore(cell, ter, rivers, res, allCityCells, params);
  if (sc !== null && sc > (civ._settleScore ?? -Infinity)) {
    civ._settleCandidate = cell;
    civ._settleScore = sc;
  }
}

// ── Map Gen ────────────────────────────────────────────────────────
function genMap(seed) {
  const n1 = mkNoise(seed), n2 = mkNoise(seed + 1e3), n3 = mkNoise(seed + 2e3), n4 = mkNoise(seed + 3e3);
  const hm = new Float32Array(N), mm = new Float32Array(N), tm = new Float32Array(N), ter = new Uint8Array(N);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const i = y * W + x, nx2 = x / W, ny = y / H;
    let h = fbm(n1, nx2 * 4, ny * 4, 6) + fbm(n2, nx2 * 8, ny * 8, 4) * .3;
    const dx = (nx2 - .5) * 2, dy = (ny - .5) * 2;
    h = h * .6 + (1 - Math.sqrt(dx * dx * .6 + dy * dy)) * .4 + fbm(n4, nx2 * 2.5, ny * 2.5, 3) * .25;
    hm[i] = h; mm[i] = (fbm(n3, nx2 * 5, ny * 5, 4) + 1) / 2; tm[i] = 1 - Math.abs(ny - .5) * 2 + fbm(n2, nx2 * 3, ny * 3, 3) * .2;
  }
  let mn = Infinity, mx = -Infinity;
  for (let i = 0; i < N; i++) { if (hm[i] < mn) mn = hm[i]; if (hm[i] > mx) mx = hm[i]; }
  for (let i = 0; i < N; i++) hm[i] = (hm[i] - mn) / (mx - mn);

  for (let i = 0; i < N; i++) {
    const h = hm[i], m = mm[i], t = tm[i];
    if (h < .28) ter[i] = T.DEEP; else if (h < .35) ter[i] = T.OCEAN; else if (h < .4) ter[i] = T.COAST;
    else if (h < .42) ter[i] = T.BEACH;
    else if (h < .75) {
      if (t < .3) ter[i] = m > .5 ? T.TUNDRA : T.SNOW;
      else if (t > .7 && m < .3) ter[i] = T.DESERT;
      else if (t > .65 && m > .6) ter[i] = T.JUNGLE;
      else if (m > .65 && h < .55) ter[i] = T.SWAMP;
      else if (m > .55) ter[i] = h > .6 ? T.DFOREST : T.FOREST;
      else if (m > .35) ter[i] = T.GRASS; else ter[i] = T.PLAINS;
    } else if (h < .85) ter[i] = T.HILLS; else if (h < .93) ter[i] = T.MTN; else ter[i] = T.SNOW;
  }

  const res = new Map(), rng2 = mkNoise(seed + 5e3);
  for (let y = 2; y < H - 2; y += 3) for (let x = 2; x < W - 2; x += 3) {
    const i = y * W + x, t = ter[i]; if (t <= T.COAST) continue;
    const rl = (rng2(x * .7, y * .7) + 1) / 2;
    if (rl < .12) {
      let tp;
      if (t === T.MTN || t === T.HILLS) tp = ["iron","gold","stone","gems"][(rl * 100 | 0) % 4];
      else if (t === T.FOREST || t === T.DFOREST || t === T.JUNGLE) tp = ["wood","spices","ivory"][(rl * 100 | 0) % 3];
      else if (t === T.BEACH) tp = "fish";
      else if (t === T.PLAINS || t === T.GRASS) tp = ["wheat","horses"][(rl * 100 | 0) % 2];
      else if (t === T.DESERT) tp = ["gold","gems","spices"][(rl * 100 | 0) % 3];
      else tp = "iron";
      res.set(i, tp);
    }
  }

  const rivers = genRivers(hm, ter, seed);
  const impr = new Uint8Array(N);
  return { hm, mm, tm, ter, res, rivers, impr };
}

// ── Helpers ────────────────────────────────────────────────────────
const isL = (ter, i) => i >= 0 && i < N && ter[i] > T.COAST;
const nb = c => { const x = c % W, y = (c / W) | 0, n = []; if (x > 0) n.push(c - 1); if (x < W - 1) n.push(c + 1); if (y > 0) n.push(c - W); if (y < H - 1) n.push(c + W); return n; };
const bdr = t => { const b = new Set(); for (const c of t) for (const n of nb(c)) if (!t.has(n)) b.add(n); return b; };
const ctr = t => { let sx = 0, sy = 0, c = 0; for (const v of t) { sx += v % W; sy += (v / W) | 0; c++; } return c ? { x: (sx / c) | 0, y: (sy / c) | 0 } : { x: 0, y: 0 }; };
const blend = (c1, c2, r) => { const h = s => parseInt(s, 16); return `rgb(${(h(c1.slice(1, 3)) * (1 - r) + h(c2.slice(1, 3)) * r) | 0},${(h(c1.slice(3, 5)) * (1 - r) + h(c2.slice(3, 5)) * r) | 0},${(h(c1.slice(5, 7)) * (1 - r) + h(c2.slice(5, 7)) * r) | 0})`; };
function findRegions(cells) { const s = new Set(cells), v = new Set(), regs = []; for (const c of s) { if (v.has(c)) continue; const reg = [], q = [c]; v.add(c); while (q.length) { const u = q.pop(); reg.push(u); for (const n of nb(u)) if (s.has(n) && !v.has(n)) { v.add(n); q.push(n); } } regs.push(reg); } return regs; }
const wk = (a, b) => a < b ? `${a}|${b}` : `${b}|${a}`;
const dist = (a, b) => Math.abs(a % W - b % W) + Math.abs(((a / W) | 0) - ((b / W) | 0));

function findPath(from, to, territory, ter) {
  const q = [from], prev = new Map(); prev.set(from, -1);
  while (q.length) {
    const cur = q.shift();
    if (cur === to) { const p = []; let c2 = to; while (c2 !== -1) { p.unshift(c2); c2 = prev.get(c2); } return p; }
    for (const n of nb(cur)) if (!prev.has(n) && territory.has(n) && ter[n] !== T.MTN && ter[n] !== T.SNOW) { prev.set(n, cur); q.push(n); }
  }
  return null;
}

function bestImp(ter, res, cell) {
  const t = ter[cell], r = res.get(cell);
  if ((t === T.MTN || t === T.HILLS) && (r === "iron" || r === "gold" || r === "gems")) return IMP.MINE;
  if (t === T.MTN || t === T.HILLS) return IMP.QUARRY;
  if (t === T.FOREST || t === T.DFOREST || t === T.JUNGLE) return IMP.LUMBER;
  if (r === "horses") return IMP.PASTURE;
  if (CAN_FARM.has(t)) return IMP.FARM;
  return IMP.NONE;
}

// ── Smart Road Building: MST toward capital ───────────────────────
function buildRoad(civ, ter) {
  if (civ.cities.length < 2) return;
  const connKey = new Set();
  for (const r of civ.roads) connKey.add(wk(r.from, r.to));
  const cap = civ.cities.find(c => c.isCapital);
  if (!cap) return;
  const connSet = new Set([cap.cell]);
  let changed = true;
  while (changed) { changed = false; for (const r of civ.roads) { if (connSet.has(r.from) && !connSet.has(r.to)) { connSet.add(r.to); changed = true; } if (connSet.has(r.to) && !connSet.has(r.from)) { connSet.add(r.from); changed = true; } } }
  let bestFrom = -1, bestTo = -1, bestD = Infinity;
  for (const ci of civ.cities) {
    if (connSet.has(ci.cell)) continue;
    for (const cj of civ.cities) {
      if (!connSet.has(cj.cell)) continue;
      const d = dist(ci.cell, cj.cell);
      if (d < bestD) { bestD = d; bestFrom = cj.cell; bestTo = ci.cell; }
    }
  }
  if (bestFrom === -1) return;
  const path = findPath(bestFrom, bestTo, civ.territory, ter);
  if (path && path.length < 50) { civ.roads.push({ from: bestFrom, to: bestTo, path }); civ.gold -= 8; }
}

// ── Civ ────────────────────────────────────────────────────────────
function findSpot(ter, civs, rng) {
  for (let a = 0; a < 600; a++) {
    const x = 5 + (((rng(a * 3.7, a * 2.1) + 1) / 2) * (W - 10)) | 0;
    const y = 5 + (((rng(a * 1.3, a * 4.9) + 1) / 2) * (H - 10)) | 0;
    const i = y * W + x, t = ter[i];
    if (t >= T.BEACH && t <= T.GRASS) {
      let ok = true;
      for (const c of civs) { if (!c.alive) continue; const ct = ctr(c.territory); if (Math.abs(ct.x - x) + Math.abs(ct.y - y) < 18) { ok = false; break; } }
      if (ok) return i;
    }
  }
  return -1;
}

function mkCiv(ter, civs, rivers, seed, tick) {
  const rng = mkNoise(seed + tick * 13 + civId * 7);
  const spot = findSpot(ter, civs, rng);
  if (spot === -1) return null;
  const sx = spot % W, sy = (spot / W) | 0, territory = new Set([spot]);
  for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
    if (Math.abs(dx) + Math.abs(dy) > 3) continue;
    const ni = (sy + dy) * W + (sx + dx); if (isL(ter, ni)) territory.add(ni);
  }
  const cn = gCiN();
  return {
    id: civId++, name: gCN(), leader: gLN(), color: nxtC(), capital: spot, territory,
    cities: [{ cell: spot, name: cn, population: 80, isCapital: true, founded: tick, trade: 10, wealth: 20, nearRiver: cellOnRiver(spot, rivers), coastal: cellCoastal(spot, ter) }],
    population: 100, military: 20, gold: 50, food: 80, tech: 1, culture: 1, age: 0, alive: true,
    integrity: .6 + Math.random() * .35, peacefulness: .2 + Math.random() * .6, wealth: 30,
    farmOutput: 0, mineOutput: 0, tradeOutput: 0, expansionRate: .35 + Math.random() * .4,
    events: [`Year 0: ${cn} founded`], parentName: null, roads: [],
  };
}

// ── Simulation ─────────────────────────────────────────────────────
function tickSim(civs, ter, res, om, wars, rivers, impr, tick, addEv, params) {
  const alive = civs.filter(c => c.alive);

  // Diplomacy
  for (let i = 0; i < alive.length; i++) for (let j = i + 1; j < alive.length; j++) {
    const a = alive[i], b = alive[j], k = wk(a.id, b.id), atW = wars.has(k);
    let border = false; const bd = bdr(a.territory); for (const bc of bd) if (b.territory.has(bc)) { border = true; break; }
    if (!atW && border) {
      const agg = (1 - a.peacefulness + 1 - b.peacefulness) / 2;
      if (Math.random() < agg * .006 + (a.territory.size > b.territory.size * 2 ? .004 : 0)) {
        const att = a.military > b.military ? a : b, def = att === a ? b : a;
        wars.set(k, { aId: att.id, dId: def.id, st: tick });
        addEv(`⚔ Year ${tick}: ${att.name} declared WAR on ${def.name}!`);
        att.events.push(`Year ${att.age}: War on ${def.name}`); def.events.push(`Year ${def.age}: ${att.name} attacked`);
      }
    }
    if (atW) {
      const war = wars.get(k), dur = tick - war.st;
      if (Math.random() < (dur > 20 ? .012 + (dur - 20) * .002 : 0) + (a.military < 15 || b.military < 15 ? .04 : 0) + (a.peacefulness + b.peacefulness) * .003) {
        wars.delete(k); addEv(`🕊 Year ${tick}: ${a.name} & ${b.name} made peace`);
        a.events.push(`Year ${a.age}: Peace with ${b.name}`); b.events.push(`Year ${b.age}: Peace with ${a.name}`);
      }
    }
  }

  for (const civ of alive) {
    civ.age++;
    let farmOut = 0, mineOut = 0, tradeOut = 0, rawGold = 0;
    for (const cell of civ.territory) {
      const imp = impr[cell], riv = cellOnRiver(cell, rivers) ? 1.8 : 1;
      if (imp === IMP.FARM) farmOut += 2.5 * riv;
      else if (imp === IMP.MINE) { mineOut += 2; rawGold += 1.5; }
      else if (imp === IMP.LUMBER) rawGold += .3;
      else if (imp === IMP.QUARRY) { mineOut += 1; rawGold += .5; }
      else if (imp === IMP.PASTURE) farmOut += 1.5;
      const t = ter[cell];
      if (t === T.PLAINS || t === T.GRASS) farmOut += .4;
      if (res.has(cell)) { const r2 = res.get(cell); if (r2 === "wheat") farmOut += 2; else if (r2 === "fish") farmOut += 1.5; else if (r2 === "gold") rawGold += 2; else if (r2 === "gems") rawGold += 1.5; else if (r2 === "iron" || r2 === "stone") mineOut += 1; else rawGold += .5; }
    }

    for (const city of civ.cities) {
      city.nearRiver = cellOnRiver(city.cell, rivers); city.coastal = cellCoastal(city.cell, ter);
      const rivB = city.nearRiver ? 1 + params.riverPref * .15 : 1;
      const coastB = city.coastal ? 1 + params.coastPref * .12 : 1;
      const roadConns = civ.roads.filter(r => r.from === city.cell || r.to === city.cell).length;
      city.trade = (4 + roadConns * 6 + city.population * .04) * rivB * coastB;
      city.wealth = Math.min(city.wealth + city.trade * .008 + rawGold * .001, 9999);
      tradeOut += city.trade;
    }
    civ.farmOutput = farmOut; civ.mineOutput = mineOut; civ.tradeOutput = tradeOut;
    civ.food += farmOut * .3 - civ.population * .025;
    civ.gold += rawGold * .3 + tradeOut * .015 + civ.territory.size * .015;
    civ.wealth = Math.max(0, civ.gold * .3 + tradeOut * .5 + mineOut * .2);

    // Population growth into existing cities; trade boosts growth
    if (civ.food > civ.population * .2) {
      for (const city of civ.cities) { city.population *= 1 + .002 + city.trade * .00008; city.population += farmOut * .003; }
    } else { for (const city of civ.cities) city.population *= .997; }
    civ.population = civ.cities.reduce((s, c) => s + c.population, 0);

    civ.tech += .007 * Math.log2(civ.population + 1) * (1 + civ.cities.length * .08);
    civ.culture += .004 * Math.log2(civ.territory.size + 1);
    civ.military = Math.max(8, civ.population * .14 + civ.tech * 2 + mineOut * .1);
    civ.integrity = Math.min(1, Math.max(.1, civ.integrity + civ.culture * .0002 - (civ.territory.size > 80 ? .001 : 0) - (civ.age > 100 ? .0004 : 0) + (civ.wealth > 100 ? .0001 : 0)));

    // Build improvements
    if (tick % 3 === 0 && civ.gold > 8) {
      const cells = [...civ.territory];
      for (let a2 = 0; a2 < 4; a2++) { const c = cells[(Math.random() * cells.length) | 0]; if (impr[c] === IMP.NONE) { const bi = bestImp(ter, res, c); if (bi !== IMP.NONE) { impr[c] = bi; civ.gold -= 1.5; break; } } }
    }

    // Gather all city cells once for settle scoring and city founding
    const allCityCells = alive.flatMap(c => c.cities.map(ci => ci.cell));

    // City founding — use cached settlement candidate from expansion
    const largestCity = civ.cities.reduce((mx2, c) => c.population > mx2 ? c.population : mx2, 0);
    const shouldFound = civ.territory.size > (civ.cities.length + 1) * 35 && largestCity > 150 && civ.gold > 30 && civ.cities.length < Math.floor(civ.territory.size / 30) + 1;
    const bestCell = civ._settleCandidate ?? -1;
    if (shouldFound && bestCell !== -1 && civ.territory.has(bestCell)) {
      const sc = settleScore(bestCell, ter, rivers, res, allCityCells, params);
      if (sc !== null && sc > 0) {
        const cn = gCiN();
        civ.cities.push({ cell: bestCell, name: cn, population: 25, isCapital: false, founded: tick, trade: 3, wealth: 3, nearRiver: cellOnRiver(bestCell, rivers), coastal: cellCoastal(bestCell, ter) });
        civ.gold -= 20; civ.events.push(`Year ${civ.age}: Founded ${cn}`); addEv(`🏘 Year ${tick}: ${civ.name} founded ${cn}`);
        delete civ._settleCandidate; delete civ._settleScore;
      }
    }

    // Build roads
    if (civ.cities.length >= 2 && civ.gold > 12 && tick % 7 === 0) buildRoad(civ, ter);

    // Expansion — score newly claimed tiles as settlement candidates
    if (civ.food > 15 && civ.population > civ.territory.size * 2 && Math.random() < civ.expansionRate * .2) {
      const borders = bdr(civ.territory);
      let targets = [...borders].filter(c => c >= 0 && c < N && isL(ter, c) && om[c] === 0);
      targets.sort((a, b) => { let sa = 0, sb = 0; if (res.has(a)) sa += 4; if (res.has(b)) sb += 4; if (cellOnRiver(a, rivers)) sa += params.riverPref * .5; if (cellOnRiver(b, rivers)) sb += params.riverPref * .5; if (ter[a] === T.PLAINS || ter[a] === T.GRASS) sa += 2; if (ter[b] === T.PLAINS || ter[b] === T.GRASS) sb += 2; if (ter[a] >= T.MTN) sa -= 3; if (ter[b] >= T.MTN) sb -= 3; return sb - sa; });
      const cnt = Math.min(((civ.territory.size * .03) | 0) + 1, targets.length, 3);
      for (let i2 = 0; i2 < cnt; i2++) { civ.territory.add(targets[i2]); om[targets[i2]] = civ.id; evalSettleCandidate(civ, targets[i2], ter, rivers, res, allCityCells, params); }
    }

    // War combat
    const myW = [...wars.entries()].filter(([, w]) => w.aId === civ.id || w.dId === civ.id);
    for (const [wk2, war] of myW) {
      const eid = war.aId === civ.id ? war.dId : war.aId; const enemy = alive.find(c => c.id === eid);
      if (!enemy || !enemy.alive) { wars.delete(wk2); continue; } if (civ.military < 12) continue;
      const borders = bdr(civ.territory);
      for (const cell of borders) {
        if (cell < 0 || cell >= N || om[cell] !== eid) continue;
        const pr = civ.military / Math.max(1, enemy.military);
        if (pr > .6 && Math.random() < .2 * pr) {
          const cap = [cell]; for (const n of nb(cell)) if (om[n] === eid && Math.random() < .2 * pr) cap.push(n);
          for (const c of cap) { enemy.territory.delete(c); civ.territory.add(c); om[c] = civ.id; const cc = enemy.cities.find(ci => ci.cell === c); if (cc) { enemy.cities = enemy.cities.filter(ci => ci.cell !== c); civ.cities.push({ ...cc, isCapital: false }); addEv(`🔥 Year ${tick}: ${civ.name} took ${cc.name}!`); } }
          civ.military *= .94; enemy.military *= .87; enemy.population *= .98; break;
        }
      }
    }

    civ.cities = civ.cities.filter(c => civ.territory.has(c.cell));
    civ.roads = civ.roads.filter(r => civ.territory.has(r.from) && civ.territory.has(r.to));
    if (civ.cities.length > 0 && !civ.cities.some(c => c.isCapital)) { civ.cities[0].isCapital = true; civ.capital = civ.cities[0].cell; }

    if (civ.territory.size < 2 || civ.population < 8 || civ.food < -60) {
      civ.alive = false; for (const c of civ.territory) { om[c] = 0; impr[c] = 0; } civ.territory.clear();
      addEv(`💀 Year ${tick}: ${civ.name} fell!`);
      for (const [kk] of wars) { const w = wars.get(kk); if (w && (w.aId === civ.id || w.dId === civ.id)) wars.delete(kk); }
    }
  }

  // Fragmentation
  const newCivs = [];
  for (const civ of alive) {
    if (!civ.alive) continue;
    const fc = (1 - civ.integrity) * .004 * (civ.territory.size > 60 ? 1.5 : .3);
    if (civ.territory.size > 50 && civ.age > 60 && Math.random() < fc) {
      const center = ctr(civ.territory), angle = Math.random() * Math.PI, cos = Math.cos(angle), sin = Math.sin(angle);
      const sA = [], sB = []; for (const c of civ.territory) { ((c % W - center.x) * cos + (((c / W) | 0) - center.y) * sin > 0 ? sA : sB).push(c); }
      if (sA.length > 8 && sB.length > 8) {
        const allR = [...findRegions(sA), ...findRegions(sB)].filter(r => r.length > 5).sort((a, b) => b.length - a.length);
        if (allR.length >= 2) {
          const tot = [...civ.territory].length; civ.territory = new Set(allR[0]); civ.population *= allR[0].length / tot; civ.military *= .5; civ.food *= .5;
          civ.cities = civ.cities.filter(c => civ.territory.has(c.cell)); civ.roads = civ.roads.filter(r => civ.territory.has(r.from) && civ.territory.has(r.to));
          if (!civ.cities.some(c => c.isCapital) && civ.cities.length > 0) { civ.cities[0].isCapital = true; civ.capital = civ.cities[0].cell; }
          for (const c of allR[0]) om[c] = civ.id;
          for (let ri = 1; ri < allR.length && ri < 4; ri++) {
            const reg = allR[ri], cc = reg[(reg.length / 2) | 0], cn = gCiN();
            const rebel = { id: civId++, name: gCN(), leader: gLN(), color: nxtC(), capital: cc, territory: new Set(reg),
              cities: [{ cell: cc, name: cn, population: 40, isCapital: true, founded: tick, trade: 5, wealth: 10, nearRiver: cellOnRiver(cc, rivers), coastal: cellCoastal(cc, ter) }],
              population: civ.population * (reg.length / tot) * .8, military: civ.military * .3, gold: civ.gold * .2, food: civ.food * .3, tech: civ.tech * .8, culture: civ.culture * .4,
              age: 0, alive: true, integrity: .5 + Math.random() * .3, peacefulness: .3 + Math.random() * .5, wealth: civ.wealth * .2,
              farmOutput: 0, mineOutput: 0, tradeOutput: 0, expansionRate: .3 + Math.random() * .4, events: [`Year 0: Broke from ${civ.name}`], parentName: civ.name, roads: [] };
            for (const c of reg) om[c] = rebel.id;
            const inh = civ.cities.filter(c => rebel.territory.has(c.cell));
            if (inh.length) { rebel.cities = [...inh.map(c => ({ ...c, isCapital: false })), rebel.cities[0]]; civ.cities = civ.cities.filter(c => !rebel.territory.has(c.cell)); }
            newCivs.push(rebel); addEv(`🏴 Year ${tick}: ${rebel.name} broke from ${civ.name}!`);
          }
          civ.integrity = Math.min(1, civ.integrity + .2); civ.events.push(`Year ${civ.age}: Fragmented`); addEv(`💥 Year ${tick}: ${civ.name} shattered!`);
        }
      }
    }
    if (civ.alive && civ.territory.size > 8) {
      const regs = findRegions([...civ.territory]);
      if (regs.length > 1) { regs.sort((a, b) => b.length - a.length); for (let ri = 1; ri < regs.length; ri++) for (const c of regs[ri]) { civ.territory.delete(c); om[c] = 0; } civ.cities = civ.cities.filter(c => civ.territory.has(c.cell)); }
    }
  }
  return newCivs;
}

// ── Render ─────────────────────────────────────────────────────────
function renderMap(ctx, md, civs, om, wars, impr, showRes, tick, zoom, mapMode) {
  const { ter, res, rivers } = md;
  const cm = new Map(); for (const c of civs) if (c.alive) cm.set(c.id, c);
  const wp = new Set(); for (const [, w] of wars) { wp.add(`${w.aId}|${w.dId}`); wp.add(`${w.dId}|${w.aId}`); }

  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const i = y * W + x, t = ter[i], oid = om[i], civ = oid ? cm.get(oid) : null;
    if (mapMode === "political") { ctx.fillStyle = civ ? civ.color : (t <= T.COAST ? TC[t] : "#2a2a2a"); }
    else { ctx.fillStyle = civ ? blend(TC[t], civ.color, .4) : TC[t]; }
    ctx.fillRect(x * CELL, y * CELL, CELL, CELL);
  }

  if (zoom >= 1.3 && mapMode !== "political") {
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const i = y * W + x, imp = impr[i]; if (!imp) continue; ctx.fillStyle = IMP_COLORS[imp]; ctx.globalAlpha = .3; ctx.fillRect(x * CELL + 1, y * CELL + 1, CELL - 2, CELL - 2); ctx.globalAlpha = 1; }
  }

  // Rivers: smooth center-to-center
  ctx.strokeStyle = "#4aaef0"; ctx.lineWidth = zoom > 1.5 ? 2 : 1.3; ctx.lineCap = "round"; ctx.lineJoin = "round";
  for (const path of rivers.paths) {
    if (path.length < 2) continue;
    ctx.beginPath();
    ctx.moveTo((path[0] % W) * CELL + CELL / 2, ((path[0] / W) | 0) * CELL + CELL / 2);
    for (let i = 1; i < path.length; i++) ctx.lineTo((path[i] % W) * CELL + CELL / 2, ((path[i] / W) | 0) * CELL + CELL / 2);
    ctx.stroke();
  }

  // Roads
  if (zoom >= .7) {
    ctx.strokeStyle = "rgba(160,130,80,.5)"; ctx.lineWidth = zoom > 1.5 ? 1.6 : .9; ctx.setLineDash([3, 2]);
    for (const civ of civs) { if (!civ.alive) continue; for (const road of civ.roads) { if (road.path.length < 2) continue; ctx.beginPath(); ctx.moveTo((road.path[0] % W) * CELL + CELL / 2, ((road.path[0] / W) | 0) * CELL + CELL / 2); for (let i = 1; i < road.path.length; i++) ctx.lineTo((road.path[i] % W) * CELL + CELL / 2, ((road.path[i] / W) | 0) * CELL + CELL / 2); ctx.stroke(); } }
    ctx.setLineDash([]);
  }

  // Borders
  for (const civ of civs) {
    if (!civ.alive) continue;
    for (const cell of civ.territory) {
      const x = cell % W, y = (cell / W) | 0;
      for (const n of nb(cell)) {
        if (civ.territory.has(n)) continue;
        const nO = (n >= 0 && n < N) ? om[n] : 0;
        const isWar = nO && wp.has(`${civ.id}|${nO}`);
        if (isWar) { ctx.strokeStyle = Math.sin(tick * .6 + cell * .15) > 0 ? "rgba(255,50,20,.9)" : "rgba(255,140,0,.85)"; ctx.lineWidth = 2; ctx.setLineDash([3, 2]); }
        else { ctx.strokeStyle = nO ? "rgba(255,255,255,.4)" : "rgba(255,255,255,.12)"; ctx.lineWidth = nO ? 1.2 : .5; ctx.setLineDash([]); }
        const nx2 = n % W, ny = (n / W) | 0; ctx.beginPath();
        if (nx2 === x - 1) { ctx.moveTo(x * CELL, y * CELL); ctx.lineTo(x * CELL, y * CELL + CELL); }
        else if (nx2 === x + 1) { ctx.moveTo(x * CELL + CELL, y * CELL); ctx.lineTo(x * CELL + CELL, y * CELL + CELL); }
        else if (ny === y - 1) { ctx.moveTo(x * CELL, y * CELL); ctx.lineTo(x * CELL + CELL, y * CELL); }
        else if (ny === y + 1) { ctx.moveTo(x * CELL, y * CELL + CELL); ctx.lineTo(x * CELL + CELL, y * CELL + CELL); }
        ctx.stroke();
      }
    }
  } ctx.setLineDash([]);

  if (showRes && zoom >= 1.3) { ctx.font = `${Math.max(5, CELL - 1)}px serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; for (const [i, type] of res) ctx.fillText(RICON[type], (i % W) * CELL + CELL / 2, ((i / W) | 0) * CELL + CELL / 2); }

  // Cities
  for (const civ of civs) {
    if (!civ.alive) continue;
    for (const city of civ.cities) {
      const cx2 = city.cell % W, cy2 = (city.cell / W) | 0, px = cx2 * CELL + CELL / 2, py = cy2 * CELL + CELL / 2;
      const sz = city.isCapital ? 5 : Math.max(2, Math.min(5, Math.sqrt(city.population / 20)));
      const showLabel = city.isCapital || (zoom >= 1.5 && city.population > 60) || zoom >= 2.5;
      if (city.isCapital) {
        ctx.fillStyle = "#fff"; ctx.strokeStyle = civ.color; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(px, py, sz + 1, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.fillStyle = civ.color; ctx.font = `bold ${CELL + 2}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("★", px, py + 1);
      } else if (zoom >= .7) {
        ctx.fillStyle = "#fff"; ctx.strokeStyle = civ.color; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(px, py, sz, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        ctx.fillStyle = civ.color; ctx.beginPath(); ctx.arc(px, py, sz - 1, 0, Math.PI * 2); ctx.fill();
      }
      if (showLabel) {
        const ls = city.isCapital ? 7 : 5.5;
        ctx.font = `bold ${ls}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "top";
        ctx.strokeStyle = "rgba(0,0,0,.7)"; ctx.lineWidth = 2; ctx.strokeText(city.name, px, py + sz + 2);
        ctx.fillStyle = "#eee"; ctx.fillText(city.name, px, py + sz + 2);
      }
    }
  }

  // Nation names
  for (const civ of civs) {
    if (!civ.alive || civ.territory.size < 10) continue;
    let mnX = W, mxX = 0, mnY = H, mxY = 0;
    for (const c of civ.territory) { const x = c % W, y = (c / W) | 0; if (x < mnX) mnX = x; if (x > mxX) mxX = x; if (y < mnY) mnY = y; if (y > mxY) mxY = y; }
    const bw = (mxX - mnX) * CELL;
    let fs = Math.max(7, Math.min(22, Math.sqrt(civ.territory.size) * 1.2));
    const nw = civ.name.length * fs * .55; if (nw > bw * .85) fs = Math.max(5, (bw * .85) / (civ.name.length * .55));
    if (fs * zoom < 4.5) continue;
    const center = ctr(civ.territory), px = center.x * CELL + CELL / 2, py = center.y * CELL + CELL / 2;
    let atW = false; for (const [, w] of wars) if (w.aId === civ.id || w.dId === civ.id) { atW = true; break; }
    ctx.font = `900 ${fs}px Georgia,serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.lineWidth = 2.5; ctx.strokeText(civ.name.toUpperCase(), px, py);
    ctx.fillStyle = civ.color; ctx.shadowColor = atW ? "#f33" : civ.color; ctx.shadowBlur = atW ? 5 : 2;
    ctx.fillText(civ.name.toUpperCase(), px, py); ctx.shadowBlur = 0;
  }

  // War icons
  const drawn = new Set();
  for (const [k, war] of wars) {
    if (drawn.has(k)) continue;
    const cA = cm.get(war.aId), cB = cm.get(war.dId); if (!cA || !cB) continue;
    let bx2 = 0, by2 = 0, bc2 = 0;
    for (const c of cA.territory) { for (const n of nb(c)) { if (cB.territory.has(n)) { bx2 += c % W; by2 += (c / W) | 0; bc2++; if (bc2 > 4) break; } } if (bc2 > 4) break; }
    if (bc2) { drawn.add(k); const px = (bx2 / bc2) * CELL + CELL / 2, py = (by2 / bc2) * CELL + CELL / 2;
      ctx.font = `bold ${(12 + Math.sin(tick * .4) * 2) | 0}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.strokeStyle = "rgba(0,0,0,.5)"; ctx.lineWidth = 2; ctx.strokeText("⚔", px, py);
      ctx.fillStyle = "#f44"; ctx.shadowColor = "#f00"; ctx.shadowBlur = 4; ctx.fillText("⚔", px, py); ctx.shadowBlur = 0; }
  }
}

// ── Component ──────────────────────────────────────────────────────
const DEF_PARAMS = { riverPref: 3, coastPref: 2.5, maxCivs: 14, spawnRate: 35 };

export default function CivSim() {
  const cvs = useRef(null);
  const [seed, setSeed] = useState(42);
  const [md, setMd] = useState(null);
  const [civs, setCivs] = useState([]);
  const [om, setOm] = useState(null);
  const [wars, setWars] = useState(new Map());
  const [tick, setTick] = useState(0);
  const [run, setRun] = useState(false);
  const [spd, setSpd] = useState(1);
  const [sel, setSel] = useState(null);
  const [hover, setHover] = useState(null);
  const [showR, setShowR] = useState(true);
  const [logItems, setLogItems] = useState([]);
  const [vo, setVo] = useState({ x: 0, y: 0 });
  const [zm, setZm] = useState(1);
  const [drag, setDrag] = useState(false);
  const [ds, setDs] = useState({ x: 0, y: 0 });
  const [mapMode, setMapMode] = useState("terrain");
  const [showSettings, setShowSettings] = useState(false);
  const [params, setParams] = useState(DEF_PARAMS);
  const st = useRef({ civs: [], om: null, wars: new Map(), tick: 0, impr: null });

  const init = useCallback(s => {
    cIdx = 0; civId = 1;
    const d = genMap(s), o = new Float64Array(N);
    setMd(d); setOm(o); setCivs([]); setTick(0); setSel(null); setLogItems([]); setWars(new Map()); setVo({ x: 0, y: 0 }); setZm(1);
    st.current = { civs: [], om: o, wars: new Map(), tick: 0, impr: d.impr };
  }, []);
  useEffect(() => { init(seed); }, [seed, init]);

  useEffect(() => {
    if (!run || !md) return;
    const iv = setInterval(() => {
      const s = st.current;
      let nc = s.civs.map(c => ({ ...c, territory: new Set(c.territory), events: [...c.events], cities: c.cities.map(ci => ({ ...ci })), roads: c.roads.map(r => ({ ...r, path: [...r.path] })) }));
      const o = s.om, w = new Map(s.wars), t = s.tick + 1, imp = s.impr;
      const pend = [], aE = m => pend.push(m);
      if ((t % params.spawnRate === 0 && nc.filter(c => c.alive).length < params.maxCivs) || t === 1) {
        const cnt = t === 1 ? 5 : 1;
        for (let i = 0; i < cnt; i++) { const nv = mkCiv(md.ter, nc.filter(c => c.alive), md.rivers, seed + t + i * 77, t); if (nv) { for (const c of nv.territory) o[c] = nv.id; nc.push(nv); aE(`🏛 Year ${t}: ${nv.name} founded`); } }
      }
      const sp = tickSim(nc, md.ter, md.res, o, w, md.rivers, imp, t, aE, params);
      for (const x of sp) nc.push(x);
      st.current = { civs: nc, om: o, wars: w, tick: t, impr: imp };
      setCivs(nc); setTick(t); setWars(new Map(w));
      if (pend.length) setLogItems(p => [...p.slice(-80), ...pend]);
    }, 130 / spd);
    return () => clearInterval(iv);
  }, [run, md, spd, seed, params]);

  useEffect(() => {
    if (!md || !cvs.current) return;
    const c = cvs.current, ctx = c.getContext("2d");
    ctx.clearRect(0, 0, c.width, c.height); ctx.save(); ctx.translate(vo.x, vo.y); ctx.scale(zm, zm);
    renderMap(ctx, md, civs, st.current.om || om, st.current.wars || wars, st.current.impr || md.impr, showR, tick, zm, mapMode);
    ctx.restore();
  }, [md, civs, tick, showR, vo, zm, om, wars, mapMode]);

  const handleWheel = useCallback(e => {
    e.preventDefault();
    const r = cvs.current.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    const d = e.deltaY > 0 ? .88 : 1.14;
    setZm(p => { const nz = Math.max(.3, Math.min(6, p * d)), s = nz / p; setVo(v => ({ x: mx - (mx - v.x) * s, y: my - (my - v.y) * s })); return nz; });
  }, []);

  const zBtn = useCallback(dir => {
    const r = cvs.current?.getBoundingClientRect(); if (!r) return;
    const cx = r.width / 2, cy = r.height / 2, d = dir > 0 ? 1.3 : 1 / 1.3;
    setZm(p => { const nz = Math.max(.3, Math.min(6, p * d)), s = nz / p; setVo(v => ({ x: cx - (cx - v.x) * s, y: cy - (cy - v.y) * s })); return nz; });
  }, []);

  const onMM = e => {
    const r = cvs.current.getBoundingClientRect();
    if (drag) { setVo(p => ({ x: p.x + e.clientX - ds.x, y: p.y + e.clientY - ds.y })); setDs({ x: e.clientX, y: e.clientY }); return; }
    const mx = (e.clientX - r.left - vo.x) / zm, my = (e.clientY - r.top - vo.y) / zm;
    const gx = (mx / CELL) | 0, gy = (my / CELL) | 0;
    if (gx >= 0 && gx < W && gy >= 0 && gy < H && md) {
      const i = gy * W + gx, o2 = st.current.om, oid = o2 ? o2[i] : 0;
      const civ = oid ? civs.find(c => c.id === oid) : null;
      const city = civ ? civ.cities.find(c => c.cell === i) : null;
      const imp = st.current.impr ? st.current.impr[i] : 0;
      setHover({ x: gx, y: gy, terrain: TN[md.ter[i]], alt: (md.hm[i] * 100) | 0, res: md.res.get(i), civ: civ?.name, cc: civ?.color, city: city?.name, cpop: city ? city.population | 0 : null, ctrade: city?.trade, imp: IMP_NAMES[imp], river: cellOnRiver(i, md.rivers), coastal: cellCoastal(i, md.ter), fertile: CAN_FARM.has(md.ter[i]) });
    } else setHover(null);
  };

  const onClick = e => {
    if (drag) return;
    const r = cvs.current.getBoundingClientRect(), mx = (e.clientX - r.left - vo.x) / zm, my = (e.clientY - r.top - vo.y) / zm;
    const gx = (mx / CELL) | 0, gy = (my / CELL) | 0;
    if (gx >= 0 && gx < W && gy >= 0 && gy < H) { const o2 = st.current.om, oid = o2 ? o2[gy * W + gx] : 0; setSel(oid ? civs.find(c => c.id === oid) || null : null); }
  };

  const al = civs.filter(c => c.alive).sort((a, b) => b.territory.size - a.territory.size);
  const dl = civs.filter(c => !c.alive);
  const cw = st.current.wars || wars;
  const sw = sel ? [...cw].filter(([, w]) => w.aId === sel.id || w.dId === sel.id).map(([, w]) => w) : [];
  const wc = [...cw].length;
  const setP = (k, v) => setParams(p => ({ ...p, [k]: v }));

  return (
    <div style={{ width: "100%", height: "100vh", display: "flex", flexDirection: "column", background: "#0d1117", color: "#c9d1d9", fontFamily: "'JetBrains Mono','Fira Code',monospace", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "4px 10px", background: "#161b22", borderBottom: "1px solid #30363d", flexShrink: 0, flexWrap: "wrap" }}>
        <span style={{ fontSize: 15, fontWeight: 800, color: "#f0883e", letterSpacing: 1.5 }}>⚔ CIVITAS</span>
        <span style={{ color: "#8b949e", fontSize: 9 }}>Year {tick}</span>
        <span style={{ color: "#30363d" }}>│</span>
        <span style={{ color: "#58a6ff", fontSize: 9 }}>{al.length} nations</span>
        {wc > 0 && <span style={{ color: "#f85149", fontSize: 9, fontWeight: 600 }}>⚔{wc}</span>}
        <span style={{ color: "#484f58", fontSize: 8 }}>{zm.toFixed(1)}×</span>
        <div style={{ flex: 1 }} />
        <button onClick={() => setMapMode(m => m === "terrain" ? "political" : "terrain")} style={bs(mapMode === "political" ? "#6c5ce7" : "#30363d")}>{mapMode === "political" ? "🗺 Pol" : "🌍 Ter"}</button>
        <button onClick={() => setRun(!run)} style={bs(run ? "#da3633" : "#238636")}>{run ? "⏸" : "▶"}</button>
        <select value={spd} onChange={e => setSpd(+e.target.value)} style={sst}><option value={.5}>½×</option><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></select>
        <button onClick={() => { setRun(false); setSeed((Math.random() * 99999) | 0); }} style={bs("#30363d")}>🔄</button>
        <button onClick={() => setShowSettings(!showSettings)} style={bs(showSettings ? "#58a6ff" : "#30363d")}>⚙</button>
        <label style={{ fontSize: 8, color: "#8b949e", cursor: "pointer", display: "flex", alignItems: "center", gap: 2 }}>
          <input type="checkbox" checked={showR} onChange={e => setShowR(e.target.checked)} />Res</label>
      </div>

      {showSettings && (
        <div style={{ background: "#161b22", borderBottom: "1px solid #30363d", padding: "6px 14px", display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center", fontSize: 9 }}>
          <label style={{ color: "#8b949e" }}>River Pref <input type="range" min="0" max="6" step=".5" value={params.riverPref} onChange={e => setP("riverPref", +e.target.value)} style={{ width: 55, verticalAlign: "middle" }} /> {params.riverPref}</label>
          <label style={{ color: "#8b949e" }}>Coast Pref <input type="range" min="0" max="6" step=".5" value={params.coastPref} onChange={e => setP("coastPref", +e.target.value)} style={{ width: 55, verticalAlign: "middle" }} /> {params.coastPref}</label>
          <label style={{ color: "#8b949e" }}>Max Civs <input type="range" min="4" max="25" step="1" value={params.maxCivs} onChange={e => setP("maxCivs", +e.target.value)} style={{ width: 55, verticalAlign: "middle" }} /> {params.maxCivs}</label>
          <label style={{ color: "#8b949e" }}>Spawn /{" "}<input type="range" min="15" max="80" step="5" value={params.spawnRate} onChange={e => setP("spawnRate", +e.target.value)} style={{ width: 55, verticalAlign: "middle" }} /> {params.spawnRate}yr</label>
        </div>
      )}

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <div style={{ flex: 1, position: "relative", overflow: "hidden", background: "#080c12" }}>
          <canvas ref={cvs} width={PXW} height={PXH} style={{ cursor: drag ? "grabbing" : "grab" }}
            onMouseMove={onMM} onClick={onClick} onWheel={handleWheel}
            onMouseDown={e => { setDrag(true); setDs({ x: e.clientX, y: e.clientY }); }}
            onMouseUp={() => setDrag(false)} onMouseLeave={() => { setDrag(false); setHover(null); }} />
          {hover && (
            <div style={{ position: "absolute", top: 5, left: 5, background: "rgba(13,17,23,.95)", border: "1px solid #30363d", borderRadius: 4, padding: "4px 7px", fontSize: 8, pointerEvents: "none", lineHeight: 1.6, minWidth: 110 }}>
              <div style={{ color: "#6e7681" }}>({hover.x},{hover.y}) {hover.terrain} {hover.alt}m</div>
              {hover.river && <div style={{ color: "#4aaef0" }}>〰 River</div>}
              {hover.coastal && <div style={{ color: "#1a6a9e" }}>⚓ Coastal</div>}
              {hover.fertile && <div style={{ color: "#7ab648" }}>🌱 Fertile</div>}
              {hover.imp !== "—" && <div style={{ color: "#c8a000" }}>{hover.imp}</div>}
              {hover.res && <div>{RICON[hover.res]} {hover.res}</div>}
              {hover.civ && <div style={{ color: hover.cc, fontWeight: 600 }}>{hover.civ}</div>}
              {hover.city && <div>🏘 {hover.city} (pop {hover.cpop}, trade {hover.ctrade | 0})</div>}
            </div>
          )}
          <div style={{ position: "absolute", bottom: 6, right: 6, display: "flex", flexDirection: "column", gap: 2 }}>
            <button onClick={() => zBtn(1)} style={zb}>+</button>
            <button onClick={() => zBtn(-1)} style={zb}>−</button>
            <button onClick={() => { setZm(1); setVo({ x: 0, y: 0 }); }} style={zb}>⌂</button>
          </div>
        </div>

        <div style={{ width: 250, background: "#161b22", borderLeft: "1px solid #30363d", display: "flex", flexDirection: "column", overflow: "hidden", flexShrink: 0 }}>
          <div style={{ flex: 1, overflowY: "auto", padding: 5 }}>
            <div style={{ fontSize: 7, color: "#8b949e", fontWeight: 700, marginBottom: 3, textTransform: "uppercase", letterSpacing: 1 }}>Nations</div>
            {al.map(civ => {
              const atW = [...cw].some(([, w]) => w.aId === civ.id || w.dId === civ.id);
              return (
                <div key={civ.id} onClick={() => setSel(civ)} style={{ padding: "3px 4px", marginBottom: 1, borderRadius: 3, cursor: "pointer", background: sel?.id === civ.id ? "rgba(56,139,253,.12)" : "transparent" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                    <div style={{ width: 7, height: 7, borderRadius: 2, background: civ.color, boxShadow: atW ? "0 0 3px #f85149" : "none" }} />
                    <span style={{ fontSize: 9, fontWeight: 600, color: "#e6edf3" }}>{civ.name}</span>
                    {atW && <span style={{ fontSize: 6, color: "#f85149", fontWeight: 800 }}>WAR</span>}
                    <span style={{ fontSize: 7, color: "#6e7681", marginLeft: "auto" }}>{civ.territory.size}</span>
                  </div>
                  <div style={{ fontSize: 7, color: "#484f58", paddingLeft: 10 }}>{civ.cities.length}c · {civ.population | 0}p · ₿{civ.wealth | 0}</div>
                </div>
              );
            })}
            {dl.length > 0 && (
              <>
                <div style={{ fontSize: 7, color: "#484f58", fontWeight: 700, marginTop: 5, textTransform: "uppercase", letterSpacing: 1 }}>Fallen</div>
                {dl.slice(-5).map(c => (
                  <div key={c.id} onClick={() => setSel(c)} style={{ padding: "1px 4px", opacity: .4, cursor: "pointer", fontSize: 7 }}>
                    <span style={{ color: c.color }}>†</span> {c.name} ({c.age}yr)
                  </div>
                ))}
              </>
            )}
          </div>
          {sel && (
            <div style={{ borderTop: "1px solid #30363d", padding: 6, maxHeight: 300, overflowY: "auto", background: "#0d1117", fontSize: 9 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 3, marginBottom: 3 }}>
                <div style={{ width: 10, height: 10, borderRadius: 2, background: sel.color }} />
                <span style={{ fontSize: 11, fontWeight: 700, color: "#e6edf3" }}>{sel.name}</span>
                {!sel.alive && <span style={{ fontSize: 7, color: "#da3633", fontWeight: 700 }}>FALLEN</span>}
              </div>
              {sel.parentName && <div style={{ fontSize: 7, color: "#6e7681" }}>From {sel.parentName}</div>}
              <div style={{ color: "#8b949e", lineHeight: 1.7 }}>
                <div>👑 {sel.leader}</div>
                <div>👥 {sel.population | 0} · ⚔ {sel.military | 0} · 📐 {sel.territory.size}</div>
                <div>💰 {sel.gold | 0} · ₿ <span style={{ color: "#f0c040" }}>{sel.wealth | 0}</span> · 🍞 {sel.food | 0}</div>
                <div>🔬 {sel.tech.toFixed(1)} · 🎭 {sel.culture.toFixed(1)}</div>
                <div>🛡 <span style={{ color: sel.integrity > .6 ? "#3fb950" : "#f85149" }}>{(sel.integrity * 100) | 0}%</span> · {sel.peacefulness > .5 ? "🕊" : "⚔"}{(sel.peacefulness * 100) | 0}%</div>
                <div>🌾{sel.farmOutput | 0} ⛏{sel.mineOutput | 0} 📦{sel.tradeOutput | 0} 🛤{sel.roads.length}</div>
              </div>
              {sel.cities.length > 0 && (
                <div style={{ marginTop: 3 }}>
                  <div style={{ fontSize: 7, color: "#6e7681", fontWeight: 700 }}>CITIES ({sel.cities.length})</div>
                  {sel.cities.sort((a, b) => b.population - a.population).map((c, i) => (
                    <div key={i} style={{ fontSize: 7, color: "#8b949e", lineHeight: 1.3 }}>
                      {c.isCapital ? "★" : "•"} {c.name} <span style={{ color: "#484f58" }}>{c.population | 0}p t:{c.trade | 0}{c.nearRiver ? "〰" : ""}{c.coastal ? "⚓" : ""}</span>
                    </div>
                  ))}
                </div>
              )}
              {sw.length > 0 && (
                <div style={{ marginTop: 2 }}>
                  <div style={{ fontSize: 7, color: "#f85149", fontWeight: 700 }}>⚔ WARS</div>
                  {sw.map((w, i) => {
                    const eid = w.aId === sel.id ? w.dId : w.aId;
                    const en = civs.find(c => c.id === eid);
                    return (
                      <div key={i} style={{ fontSize: 7, color: "#f85149" }}>{en?.name} ({w.aId === sel.id ? "aggr" : "def"} {tick - w.st}yr)</div>
                    );
                  })}
                </div>
              )}
              {sw.length === 0 && sel.alive && <div style={{ fontSize: 7, color: "#3fb950", marginTop: 2 }}>🕊 Peace</div>}
              {sel.events.length > 0 && (
                <div style={{ marginTop: 2 }}>
                  <div style={{ fontSize: 7, color: "#6e7681", fontWeight: 700 }}>HISTORY</div>
                  {sel.events.slice(-4).map((e2, i) => (
                    <div key={i} style={{ fontSize: 7, color: "#8b949e", lineHeight: 1.2 }}>• {e2}</div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div style={{ borderTop: "1px solid #30363d", padding: 4, maxHeight: 90, overflowY: "auto", fontSize: 7, color: "#6e7681", lineHeight: 1.3, background: "#0d1117" }}>
            <div style={{ fontWeight: 700, color: "#8b949e", marginBottom: 1, textTransform: "uppercase", letterSpacing: 1, fontSize: 7 }}>Events</div>
            {logItems.slice(-10).reverse().map((e2, i) => (
              <div key={i} style={{ borderBottom: "1px solid #1c2028", paddingBottom: 1, marginBottom: 1 }}>{e2}</div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

const bs = bg => ({ background: bg, color: "#fff", border: "none", borderRadius: 4, padding: "3px 8px", fontSize: 9, cursor: "pointer", fontWeight: 600, fontFamily: "inherit" });
const sst = { background: "#21262d", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "2px 5px", fontSize: 9, fontFamily: "inherit", cursor: "pointer" };
const zb = { width: 24, height: 24, background: "rgba(22,27,34,.92)", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, cursor: "pointer", fontSize: 13, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "inherit" };
