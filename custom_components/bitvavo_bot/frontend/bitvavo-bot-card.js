/* Bitvavo Bot – handelsscherm. Eén kaart, bedoeld voor een paneel-weergave. */
const UP = "#2EBD85", DOWN = "#F6465D", DOWN_TXT = "#FF6B7F", MUTED = "#8E99AB", BLUE = "#4C8DFF", AMBER = "#F4C56A";
const LINE_COLORS = [BLUE, "#E8A23B"];
const E = { live: "switch.bitvavo_bot_live", trading: "switch.bitvavo_bot_handel_actief", stop: "button.bitvavo_bot_noodstop" };
const LIMITS = [
  ["max_per_buy", "number.bitvavo_bot_max_per_koop", "Max per koop"],
  ["max_total", "number.bitvavo_bot_max_totaal", "Max totaal ingezet"],
  ["max_day_loss", "number.bitvavo_bot_max_dagverlies", "Max dagverlies"],
];
const CHECKS = { lab_kandidaat: "Lab heeft een kandidaat", paper_28_dagen: "Paper draait 28 dagen", paper_positief: "Paper netto positief" };
const NAMES = { donchian: "Trend – Donchian-uitbraak", ema_cross: "Trend – EMA-kruising", rsi_dip: "Mean-reversion – RSI-dip", grid: "Grid rond gemiddelde", rel_strength: "Relatieve sterkte (top 3)", donchian_regime: "Trend – Donchian met marktfilter", vol_breakout: "Volatiliteitsuitbraak" };

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (v, d = 2) => (v == null || isNaN(v) ? "–" : Number(v).toLocaleString("nl-NL", { minimumFractionDigits: d, maximumFractionDigits: d }));
const eur = (v, sign = false) => (v == null || isNaN(v) ? "–" : (sign ? (v > 0 ? "+ " : v < 0 ? "− " : "") : v < 0 ? "− " : "") + "€ " + nf(Math.abs(v)));
const pct = (v, d = 2) => (v == null || isNaN(v) ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + nf(Math.abs(v), d) + "%");
const price = (v) => (v == null ? "–" : nf(v, v >= 1000 ? 0 : v >= 10 ? 2 : v >= 1 ? 3 : 4));
const col = (v) => (v > 0 ? UP : v < 0 ? DOWN_TXT : "#E7EAEE");
const when = (ms) => (ms ? new Date(ms).toLocaleString("nl-NL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");

const CSS = `
:host{display:block}
*{box-sizing:border-box}
.root{font-family:Inter,Roboto,system-ui,sans-serif;color:#E7EAEE;background:#0A0D12;min-height:100vh;font-variant-numeric:tabular-nums}
button{font-family:inherit;cursor:pointer}
header{display:flex;flex-wrap:wrap;align-items:center;gap:14px;padding:12px 24px;border-bottom:1px solid #1D2430;background:#0E1218}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:16px}
.brand small{font-weight:400;font-size:12px;color:${MUTED}}
.seg{display:flex;background:#161C26;border-radius:8px;padding:3px;gap:3px}
.seg button{border:0;border-radius:6px;padding:0 18px;min-height:44px;font-size:13px;font-weight:600;background:transparent;color:${MUTED};display:flex;align-items:center;gap:6px}
.seg button.on{background:${BLUE};color:#06101F}
.seg button.on.live{background:${DOWN};color:#fff}
.pill{font-size:12px;color:#C9D1DC;background:#161C26;border:1px solid #252E3D;border-radius:999px;padding:5px 10px}
button.pill{min-height:32px}
.pill.ok{background:#16261F;border-color:#1F4A3A}
.pill.warn{color:${AMBER};background:#2A2212;border-color:#5A4718}
.pill.bad{color:#FF8B9A;background:#2A1218;border-color:#6A2632}
.grow{flex:1 1 0}
.stop{border:1px solid ${DOWN};background:#2A1218;color:#FF8B9A;border-radius:8px;padding:0 18px;min-height:44px;font-size:13px;font-weight:700}
.banner{margin:16px 24px 0;padding:12px 16px;background:#2A2212;border:1px solid #5A4718;border-radius:8px;font-size:13px;color:${AMBER}}
.banner.bad{background:#2A1218;border-color:#6A2632;color:#FF8B9A}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;padding:16px 24px 0}
.card{background:#10151C;border:1px solid #1D2430;border-radius:10px}
.kpi{padding:14px 16px}
.kpi .l,.sub{font-size:12px;color:${MUTED}}
.kpi .v{font-size:24px;font-weight:700;margin-top:4px}
.bar{height:4px;background:#1D2430;border-radius:2px;margin-top:8px}.bar i{display:block;height:4px;background:${BLUE};border-radius:2px}
main{display:flex;flex-wrap:wrap;gap:16px;padding:16px 24px 24px;align-items:flex-start}
.left{flex:999 1 640px;min-width:0;display:flex;flex-direction:column;gap:16px}
aside{flex:1 1 320px;min-width:0;display:flex;flex-direction:column;gap:16px}
.chead{display:flex;flex-wrap:wrap;align-items:center;gap:16px;padding:14px 16px;border-bottom:1px solid #1D2430}
.chead b{font-size:18px}.chead .px{font-size:22px;font-weight:700}
.tfs{display:flex;gap:4px}.tfs button{background:none;border:0;color:${MUTED};padding:0 12px;min-height:36px;border-radius:6px;font-size:12px}
.tfs button.on{background:#1D2636;color:#E7EAEE;font-weight:600}
.legend{display:flex;flex-wrap:wrap;gap:16px;padding:10px 16px 0;font-size:12px;color:${MUTED}}
.chart{height:440px;padding:8px 8px 8px 16px}
.eq{height:160px;padding:0 8px 12px 16px}canvas{width:100%;height:100%;display:block}
.tabs{display:flex;gap:4px;padding:8px 12px 0;border-bottom:1px solid #1D2430}
.tabs button{background:none;border:0;border-bottom:2px solid transparent;color:${MUTED};font-size:13px;font-weight:600;padding:0 14px;min-height:44px}
.tabs button.on{color:#E7EAEE;border-bottom-color:${BLUE}}
.scroll{overflow-x:auto}
tbody tr{transition:background .12s}tbody tr:hover{background:#151C27}tbody tr[data-a]{cursor:pointer}
.mk:hover{background:#141B26}.tabs button:hover,.tfs button:hover{color:#E7EAEE}
.seg button:hover{color:#E7EAEE}.seg button.on:hover{color:#06101F}.seg button.on.live:hover{color:#fff}
.stop:hover{background:#3A1620}.ghost:hover{background:#2A2212}button.pill:hover{border-color:#3A475C}
.kpi{transition:border-color .12s}.kpi:hover{border-color:#2C3748}
#px{cursor:crosshair}#px.drag{cursor:grabbing}#eq{cursor:crosshair}
.hint{font-size:11px;color:#8E99AB;margin-left:auto}
.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;background:#2EBD85;margin-right:6px;animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(46,189,133,.6)}100%{box-shadow:0 0 0 7px rgba(46,189,133,0)}}
@media (prefers-reduced-motion: reduce){.pulse{animation:none}}
table{border-collapse:collapse;width:100%}
th{font-weight:500;color:${MUTED};font-size:12px;text-align:right;padding:8px 12px;border-bottom:1px solid #1D2430;white-space:nowrap}
td{font-size:13px;text-align:right;padding:10px 12px;border-bottom:1px solid #161C26;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
.empty{padding:22px 16px;font-size:13px;color:${MUTED}}
.sub2{font-size:11px;color:${MUTED};margin-top:2px;font-weight:400}
.note{padding:10px 12px 12px;font-size:12px;color:${MUTED};line-height:1.5}
.goal{height:4px;background:#1D2430;border-radius:2px;margin-top:6px}.goal i{display:block;height:4px;background:${UP};border-radius:2px}
td{vertical-align:top}
h2{margin:0;font-size:14px;font-weight:600}
.pad{padding:14px 16px;display:flex;flex-direction:column;gap:10px}
.row{display:flex;justify-content:space-between;gap:12px;font-size:13px}.row span:first-child{color:${MUTED}}
.mk{display:flex;align-items:center;width:100%;gap:8px;background:transparent;border:0;border-top:1px solid #161C26;color:#E7EAEE;padding:0 16px;min-height:44px;font-size:13px;text-align:left}
.mk.on{background:#161E2B}.mk .n{flex:1 1 0;font-weight:600}.mk .p{width:78px;text-align:right}.mk .c{width:62px;text-align:right}.mk .s{width:46px;text-align:right;font-size:11px}
.dot{width:6px;height:6px;border-radius:50%;background:${BLUE};display:inline-block;margin-left:6px}
label.lim{display:flex;justify-content:space-between;align-items:center;font-size:13px;color:${MUTED}}
label.lim input{width:96px;min-height:36px;background:#0A0D12;border:1px solid #252E3D;border-radius:6px;color:#E7EAEE;text-align:right;padding:0 10px;font:inherit}
.chk{display:flex;gap:8px;font-size:13px}
.ghost{background:none;border:1px solid #5A4718;color:${AMBER};border-radius:8px;min-height:44px;font-size:13px;font-weight:600}
.modal{position:fixed;inset:0;background:rgba(4,6,10,.72);display:flex;align-items:center;justify-content:center;z-index:9;padding:16px}
.dlg{background:#10151C;border:1px solid #2A3444;border-radius:12px;padding:20px;max-width:440px;width:100%;display:flex;flex-direction:column;gap:12px}
.dlg p{margin:0;font-size:14px;line-height:1.5;color:#C9D1DC}
.dlg input{min-height:44px;background:#0A0D12;border:1px solid #252E3D;border-radius:8px;color:#E7EAEE;padding:0 12px;font:inherit}
.dlg .btns{display:flex;justify-content:flex-end;gap:8px}
.dlg button{min-height:44px;border-radius:8px;padding:0 16px;font-size:13px;font-weight:600;border:1px solid #2A3444;background:#161C26;color:#E7EAEE}
.dlg button.go{background:${DOWN};border-color:${DOWN};color:#fff}
`;

class BitvavoBotCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.s = { tab: "pos", tf: null, market: null, modal: null, error: null };
    this.view = { n: 160, off: 0 };
    this.hover = null;
    this.eqHover = null;
    this.forming = {};
    this.streamOn = false;
    this._cache = {};
    this.o = null;
    this.chart = null;
  }
  setConfig(config) { this.config = config || {}; }
  getCardSize() { return 20; }
  set hass(h) { this._hass = h; if (!this.o && !this._busy) this.load(); this.subscribe(); }

  connectedCallback() {
    this._t1 = setInterval(() => this.load(), 10000);
    this._t2 = setInterval(() => this.loadChart(), 60000);
    this._ro = new ResizeObserver(() => this.draw());
    this.subscribe();
  }
  disconnectedCallback() {
    clearInterval(this._t1); clearInterval(this._t2); this._ro && this._ro.disconnect();
    if (this._unsub) { this._unsub(); this._unsub = null; }
    this._subscribing = false;
  }

  subscribe() {
    // de integratie stuurt elke seconde de actuele stand; de 10-secondenpoll blijft als vangnet
    if (this._unsub || this._subscribing || !this._hass || !this._hass.connection || !this.isConnected) return;
    this._subscribing = true;
    this._hass.connection.subscribeMessage((ev) => this.onLive(ev), { type: "bitvavo_bot/stream" })
      .then((unsub) => { this._subscribing = false; if (this.isConnected) this._unsub = unsub; else unsub(); })
      .catch(() => { this._subscribing = false; });
  }

  onLive(ev) {
    this.forming = ev.candles || {};
    this.streamOn = !!ev.stream;
    if (this.o && ev.live) {
      this.o = { ...this.o, ...ev.live, book: { ...this.o.book, ...ev.live.book } };
      this.render();
    }
  }

  liveBars() {
    // afgesloten uren van de server + de lopende uurcandle uit de koersstroom
    const c = this.chart, bars = (c && c.bars) || [], f = this.forming && this.forming[this.s.market];
    if (!bars.length || !f || !c.tf_ms || f[0] <= c.last_hour) return bars;
    const bucket = f[0] - (f[0] % c.tf_ms), last = bars[bars.length - 1];
    if (bucket === last[0]) return bars.slice(0, -1).concat([[last[0], last[1], Math.max(last[2], f[2]), Math.min(last[3], f[3]), f[4], last[5] + f[5]]]);
    if (bucket > last[0]) return bars.concat([[bucket, f[1], f[2], f[3], f[4], f[5]]]);
    return bars;
  }

  async load() {
    if (!this._hass || this._busy) return;
    this._busy = true;
    try {
      this.o = await this._hass.callWS({ type: "bitvavo_bot/overview" });
      this.loadErr = null;
      if (!this.s.market && this.o.markets.length) this.s.market = this.o.markets[0].market;
      if (!this.s.tf) this.s.tf = (this.o.plan && this.o.plan.tijdschaal) || "4h";
      if (!this.chart) this.loadChart();
    } catch (e) { this.loadErr = (e && e.message) || "Geen verbinding met Bitvavo Bot"; }
    this._busy = false;
    this.render();
  }

  async loadChart() {
    if (!this._hass || !this.s.market) return;
    const key = this.s.market + this.s.tf;
    try {
      const c = await this._hass.callWS({ type: "bitvavo_bot/chart", market: this.s.market, timeframe: this.s.tf });
      if (key === this.s.market + this.s.tf) { this.chart = c; this.renderLegend(); this.draw(); }
    } catch (e) { /* grafiek blijft staan */ }
  }

  async call(domain, service, data) {
    try { await this._hass.callService(domain, service, data); this.s.error = null; }
    catch (e) { this.s.error = (e && e.message) || "Actie mislukt"; }
    this.s.modal = null;
    await this.load();
  }

  act(a, v) {
    const s = this.s, o = this.o;
    if (a === "tab") s.tab = v;
    else if (a === "tf") { s.tf = v; this.chart = null; this.view = { n: 160, off: 0 }; this.loadChart(); }
    else if (a === "market") { if (v !== s.market) { s.market = v; this.chart = null; this.view = { n: 160, off: 0 }; this.loadChart(); } }
    else if (a === "paper") { if (o.mode === "live") s.modal = "paper"; }
    else if (a === "live") { if (o.mode !== "live") s.modal = o.lock.state === "op slot" ? "locked" : "live"; }
    else if (a === "stop") s.modal = "stop";
    else if (a === "override") s.modal = "override";
    else if (a === "close") s.modal = null;
    else if (a === "dismiss") s.error = null;
    else if (a === "trading") return this.call("switch", o.trading ? "turn_off" : "turn_on", { entity_id: E.trading });
    else if (a === "go-live") return this.call("switch", "turn_on", { entity_id: E.live });
    else if (a === "go-paper") return this.call("switch", "turn_off", { entity_id: E.live });
    else if (a === "go-stop") return this.call("button", "press", { entity_id: E.stop });
    else if (a === "go-restore") return this.call("bitvavo_bot", "restore_lock", {});
    else if (a === "go-override") return this.call("bitvavo_bot", "override_lock", { bevestiging: this.shadowRoot.querySelector("#phrase").value });
    this.render();
  }

  modalHtml() {
    const m = this.s.modal;
    if (!m) return "";
    const box = (title, body, go, label) => `<div class="modal"><div class="dlg" role="dialog" aria-label="${title}"><h2>${title}</h2>${body}<div class="btns"><button data-a="close">Annuleren</button>${go ? `<button class="go" data-a="${go}">${label}</button>` : ""}</div></div></div>`;
    if (m === "stop") return box("Noodstop", "<p>Alle orders worden geannuleerd en alle posities direct verkocht, in paper én live. Live gaat uit en de handel wordt gepauzeerd.</p>", "go-stop", "Alles verkopen");
    if (m === "live") return box("Live handelen met echt geld", `<p>De bot gaat met echt geld handelen binnen jouw grenzen: max ${eur(this.o.limits.max_per_buy)} per koop en ${eur(this.o.limits.max_total)} in totaal. Je kunt het ingezette bedrag verliezen.</p>`, "go-live", "Live aanzetten");
    if (m === "paper") return box("Terug naar paper", "<p>Live gaat uit. Open live-posities blijven staan tot je ze verkoopt of de noodstop gebruikt.</p>", "go-paper", "Live uitzetten");
    if (m === "locked") return box("Live staat op slot", "<p>De voorwaarden zijn nog niet gehaald. Je kunt het slot bewust overbruggen via het blok Live-slot.</p>");
    if (m === "override") return box("Slot overbruggen", `<p>Hiermee kun je live handelen terwijl niet bewezen is dat de strategie geld verdient. Typ ter bevestiging: <b>IK ACCEPTEER HET RISICO</b></p><input id="phrase" aria-label="Bevestiging" autocomplete="off">`, "go-override", "Overbruggen");
    return "";
  }

  put(id, html, keepFocus) {
    // alleen een regio verversen als de inhoud echt anders is: geen geflikker, hover blijft staan
    if (this._cache[id] === html) return;
    const el = this.shadowRoot.getElementById(id);
    if (!el) return;
    if (keepFocus && el.contains(this.shadowRoot.activeElement)) return;
    el.innerHTML = html;
    this._cache[id] = html;
  }

  shell() {
    const root = this.shadowRoot;
    root.innerHTML = `<style>${CSS}</style><div class="root" id="root">
<header id="r-head"></header><div id="r-ban"></div><section class="kpis" id="r-kpi"></section>
<main><div class="left">
  <section class="card"><div class="chead" id="r-chead"></div><div class="legend" id="legend"></div><div class="chart"><canvas id="px" role="img" aria-label="Koersgrafiek met candles, hulplijnen en koop- en verkoopmomenten"></canvas></div></section>
  <section class="card" id="r-table"></section>
  <section class="card"><div class="row" style="padding:14px 16px 8px" id="r-eqhead"></div><div class="eq"><canvas id="eq" role="img" aria-label="Verloop van het resultaat in euro"></canvas></div></section>
</div><aside>
  <section class="card" id="r-mk"></section><section class="card pad" id="r-strat"></section><section class="card pad" id="r-lim"></section><section class="card pad" id="r-lock"></section>
</aside></main><div id="r-modal"></div></div>`;
    this._cache = {};
    const el = root.getElementById("root");
    el.addEventListener("click", (e) => { const t = e.target.closest("[data-a]"); if (t) this.act(t.dataset.a, t.dataset.v); });
    el.addEventListener("change", (e) => { const t = e.target.closest("[data-limit]"); if (!t) return; const v = parseFloat(t.value); if (v > 0) this.call("number", "set_value", { entity_id: t.dataset.limit, value: v }); });
    const canvas = root.getElementById("px"), eq = root.getElementById("eq");
    this.bindChart(canvas);
    eq.addEventListener("mousemove", (e) => { const r = eq.getBoundingClientRect(); this.eqHover = e.clientX - r.left; this.drawEquity(); });
    eq.addEventListener("mouseleave", () => { this.eqHover = null; this.drawEquity(); });
    if (this._ro) { this._ro.disconnect(); this._ro.observe(canvas.parentElement); }
    this._shell = true;
  }

  render() {
    const root = this.shadowRoot;
    if (!this.o) {
      this._shell = false;
      root.innerHTML = `<style>${CSS}</style><div class="root"><div class="banner ${this.loadErr ? "bad" : ""}">${esc(this.loadErr || "Bitvavo Bot wordt geladen…")}</div></div>`;
      return;
    }
    if (!this._shell || !root.getElementById("root")) this.shell();
    const o = this.o, s = this.s, b = o.book, lock = o.lock, plan = o.plan, live = o.mode === "live";
    const sel = o.markets.find((m) => m.market === s.market) || {};
    const used = Math.min(100, (100 * b.ingezet) / (o.limits.max_total || 1));
    const dataPill = o.data.status === "gereed" ? `<span class="pill">Data gereed · ${o.markets.length} markten</span>` : `<span class="pill ${o.data.status === "fout" ? "bad" : "warn"}">Data ${esc(o.data.status)} ${esc(o.data.progress || "")}</span>`;
    const planPill = !plan ? `<span class="pill warn">Wacht op Lab</span>` : plan.bewezen ? `<span class="pill ok">Strategie door Lab</span>` : `<span class="pill warn">Strategie onbewezen</span>`;
    const livePill = this.streamOn ? `<span class="pill ok" title="Koersen komen direct van Bitvavo binnen"><i class="pulse"></i>Live koersen</span>` : `<span class="pill warn" title="De directe koersstroom is er niet; koersen worden per minuut opgehaald">Koersen per minuut</span>`;
    const kpi = (l, v, sub, c) => `<div class="card kpi"><div class="l">${l}</div><div class="v" style="color:${c || "#E7EAEE"}">${v}</div><div class="sub">${sub}</div></div>`;
    const rows = (list, fn, cols, empty) => (list.length ? `<table><thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${list.map(fn).join("")}</tbody></table>` : `<div class="empty">${empty}</div>`);
    const tabs = [["pos", `Open posities (${b.open_posities})`], ["ord", `Open orders (${b.open_orders})`], ["tr", "Laatste trades"], ["lab", "Lab"]];
    let table = "";
    const two = (main, sub, c) => `<div style="color:${c || "#E7EAEE"}">${main}</div><div class="sub2" style="color:${c ? c : MUTED}">${sub}</div>`;
    if (s.tab === "pos") table = rows(b.posities, (p) => {
      let goal = `<span style="color:${MUTED}">geen verkooplimiet</span>`;
      if (p.doel && p.koers_nu) {
        const done = Math.max(0, Math.min(100, (100 * (p.koers_nu - p.koers_in)) / (p.doel - p.koers_in || 1))), togo = (p.doel / p.koers_nu - 1) * 100;
        goal = `<div>${price(p.doel)} <span style="color:${MUTED}">· nog ${pct(togo)}</span></div><div class="goal" title="Van instapkoers naar verkoopdoel"><i style="width:${done}%"></i></div>`;
      }
      return `<tr data-a="market" data-v="${esc(p.markt)}" title="Toon ${esc(p.markt)} in de grafiek"><td><b>${esc(p.markt)}</b>${p.niveau ? `<div class="sub2">gridniveau ${p.niveau}</div>` : ""}</td><td>${two(when(p.sinds), nf(p.aantal, 4) + " stuks")}</td><td>${price(p.koers_in)}</td><td>${two(price(p.koers_nu), p.koers_pct == null ? "" : pct(p.koers_pct), col(p.koers_pct))}</td><td style="min-width:170px">${goal}</td><td>${two(eur(p.inzet), "waarde " + eur(p.waarde))}</td><td>${two(eur(p.resultaat, true), pct(p.pct), col(p.resultaat))}</td></tr>`;
    }, ["Markt", "Gekocht", "Koers in", "Koers nu", "Verkoopdoel", "Inzet", "Resultaat na kosten"], "Geen open posities.") + (b.posities.length ? `<div class="note">Resultaat is wat je overhoudt als je nu verkoopt, na aan- en verkoopkosten (samen ruim 0,5%). Een verse positie begint daarom licht in de min, ook als de koers niet bewoog.</div>` : "");
    else if (s.tab === "ord") table = rows(o.orders, (r) => `<tr data-a="market" data-v="${esc(r.markt)}"><td><b>${esc(r.markt)}</b>${r.niveau ? `<div class="sub2">gridniveau ${r.niveau}</div>` : ""}</td><td style="color:${r.soort === "Kooplimiet" ? UP : DOWN_TXT}">${esc(r.soort)}</td><td>${price(r.limiet)}</td><td>${price(r.koers_nu)}</td><td>${r.afstand_pct == null ? "–" : pct(r.afstand_pct)}</td><td>${eur(r.bedrag)}</td></tr>`, ["Markt", "Soort", "Limiet", "Koers nu", "Afstand tot limiet", "Bedrag"], "Geen orders in het boek.") + (o.orders.length ? `<div class="note">Een kooplimiet wordt pas uitgevoerd als de koers tot de limiet daalt; een verkooplimiet als de koers ertoe stijgt.</div>` : "");
    else if (s.tab === "tr") table = rows(b.laatste_trades, (t) => `<tr><td><b>${esc(t.markt)}</b></td><td>${when(t.in)}</td><td>${when(t.uit)}</td><td>${eur(t.inzet)}</td><td style="color:${col(t.resultaat)}">${eur(t.resultaat, true)}</td><td style="color:${col(t.resultaat)}">${pct(t.pct)}</td><td>${esc(t.reden)}</td></tr>`, ["Markt", "In", "Uit", "Inzet", "Resultaat", "%", "Reden"], "Nog geen afgesloten trades.");
    else table = rows(o.lab.resultaten, (r) => `<tr><td>${r.geslaagd ? "✓" : "✕"} ${esc(NAMES[r.strategie] || r.strategie)} <span style="color:${MUTED}">${esc(r.versie || "")}</span></td><td>${esc(r.tijdschaal)}</td><td>${r.trades}</td><td style="color:${col(r.netto_pct)}">${pct(r.netto_pct, 3)}</td><td style="color:${col(r.alpha_pct)}">${pct(r.alpha_pct, 3)}</td><td>${nf(r.p, 4)}</td><td>${r.markten_positief_pct}%</td></tr>`, ["Hypothese", "Tijd", "Trades", "Netto/trade", "Alpha", "p", "Markten +"], "Het Lab heeft nog niet gedraaid.");
    const checks = Object.entries(CHECKS).map(([k, label]) => { const ok = lock.checks[k]; const extra = k === "paper_28_dagen" ? ` <span style="color:${MUTED}">(${lock.paper_dagen} van ${lock.nodig_dagen})</span>` : ""; return `<div class="chk"><span style="color:${ok ? UP : DOWN_TXT};font-weight:700">${ok ? "✓" : "✕"}</span><span>${label}${extra}</span></div>`; }).join("");
    const lockBtn = lock.state === "overbrugd" ? `<button class="ghost" data-a="go-restore">Slot herstellen</button>` : lock.state === "op slot" ? `<button class="ghost" data-a="override">Slot overbruggen…</button>` : "";
    const lockState = { "op slot": ["Op slot", "warn"], open: ["Open", "ok"], overbrugd: ["Overbrugd", "bad"] }[lock.state] || [lock.state, ""];

    this.put("r-head", `<div class="brand"><svg aria-hidden="true" width="28" height="28" viewBox="0 0 28 28"><rect width="28" height="28" rx="7" fill="#12254F"/><rect x="6" y="12" width="4" height="9" rx="1" fill="${DOWN}"/><rect x="12" y="9" width="4" height="9" rx="1" fill="${UP}"/><rect x="18" y="5" width="4" height="11" rx="1" fill="${UP}"/></svg>Bitvavo Bot <small>v${esc(o.version)}</small></div>
  <div class="seg" role="group" aria-label="Modus"><button class="${live ? "" : "on"}" data-a="paper">Paper</button><button class="${live ? "on live" : ""}" data-a="live">${lock.state === "op slot" ? `<svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>` : ""}Live · echt geld</button></div>
  <button class="pill ${o.trading ? "ok" : "warn"}" data-a="trading" aria-label="Handel aan of uit">${o.trading ? "● Handel actief" : "❚❚ Handel gepauzeerd"}</button>
  ${livePill}${dataPill}${planPill}${b.dagstop ? `<span class="pill bad">Dagstop actief</span>` : ""}
  <div class="grow"></div><button class="stop" data-a="stop">Noodstop</button>`);
    this.put("r-ban", `${s.error ? `<div class="banner bad">${esc(s.error)} <button class="pill" data-a="dismiss">Sluiten</button></div>` : ""}${b.laatste_fout ? `<div class="banner bad">Laatste fout: ${esc(b.laatste_fout)}</div>` : ""}${!plan ? `<div class="banner">Er wordt nog niet gehandeld: de koersdata wordt opgehaald en het Lab moet eerst draaien. Daarna kiest de bot zelf een strategie en start paper trading.</div>` : ""}`);
    this.put("r-kpi", `${kpi(live ? "Resultaat live" : "Resultaat paper", eur(b.resultaat, true), `gerealiseerd ${eur(b.gerealiseerd, true)}`, col(b.resultaat))}
  ${kpi("Vandaag", eur(b.vandaag, true), `dagstop bij − ${eur(o.limits.max_day_loss)}`, col(b.vandaag))}
  <div class="card kpi"><div class="l">Ingezet</div><div class="v">${eur(b.ingezet)}</div><div class="bar"><i style="width:${used}%"></i></div><div class="sub" style="margin-top:4px">van max ${eur(o.limits.max_total)}</div></div>
  ${kpi("Open posities", b.open_posities, `${b.open_orders} orders in het boek`)}
  ${kpi("Trades", b.trades, b.winst_pct_trades == null ? "nog geen afgesloten" : `${nf(b.winst_pct_trades, 0)}% winstgevend`)}
  ${live ? kpi("Saldo Bitvavo", eur(o.balance_eur), "vrij in euro") : kpi("Kopen-en-vasthouden", o.lab.buy_hold_pct == null ? "–" : pct(o.lab.buy_hold_pct, 1), "Lab-periode, ter vergelijking")}`);
    this.put("r-chead", `<b>${esc(s.market || "–")}</b><span class="px">${price(sel.price)}</span><span style="font-size:13px;font-weight:600;color:${col(sel.change)}">${sel.change == null ? "" : pct(sel.change * 100) + " 24u"}</span><span class="sub">Signaal: <span style="color:#E7EAEE;font-weight:600">${esc(sel.signal || "–")}</span></span><div class="grow"></div>
      <div class="tfs">${[["1h", "1u"], ["4h", "4u"], ["1d", "1d"]].map(([k, l]) => `<button class="${s.tf === k ? "on" : ""}" data-a="tf" data-v="${k}">${l}</button>`).join("")}</div>`);
    this.put("r-table", `<div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" class="${s.tab === k ? "on" : ""}" data-a="tab" data-v="${k}">${l}</button>`).join("")}</div><div class="scroll">${table}</div>`);
    this.put("r-eqhead", `<h2>Resultaat sinds start</h2><span class="sub">${live ? "live" : "paper"} · per uur · na kosten</span>`);
    this.put("r-mk", `<h2 style="padding:14px 16px 6px">Markten</h2>${o.markets.map((m) => `<button class="mk ${m.market === s.market ? "on" : ""}" data-a="market" data-v="${esc(m.market)}"><span class="n">${esc(m.market)}${m.held ? `<i class="dot" title="positie open"></i>` : ""}</span><span class="p">${price(m.price)}</span><span class="c" style="color:${col(m.change)}">${m.change == null ? "–" : pct(m.change * 100, 1)}</span><span class="s" style="color:${m.signal === "long" || m.signal === "grid" ? "#7FB0FF" : MUTED}">${esc(m.signal)}</span></button>`).join("") || `<div class="empty">Nog geen markten.</div>`}`);
    this.put("r-strat", `<h2>Strategie</h2>
    <div class="row"><span>Plan</span><span style="font-weight:600;text-align:right">${plan ? esc(NAMES[plan.strategie] || plan.strategie) + " · " + esc(plan.tijdschaal) : "nog niet gekozen"}</span></div>
    <div class="row"><span>Lab-oordeel</span><span style="font-weight:600;color:${o.lab.oordeel === "kandidaat" ? UP : AMBER}">${esc(o.lab.oordeel)}</span></div>
    <div class="row"><span>Parameters ${esc(s.market || "")}</span><span>${plan ? esc(plan.params[s.market] || "geen (niet instappen)") : "–"}</span></div>
    <div class="row"><span>Volgende herkeuze</span><span>${plan ? new Date(plan.herkeuze).toLocaleDateString("nl-NL", { day: "numeric", month: "short", year: "numeric" }) : "–"}</span></div>`);
    this.put("r-lim", `<h2>Risicogrenzen</h2>${LIMITS.map(([k, id, l]) => `<label class="lim">${l}<input type="number" inputmode="decimal" min="5" step="5" value="${o.limits[k]}" data-limit="${id}"></label>`).join("")}`, true);
    this.put("r-lock", `<div class="row"><h2>Live-slot</h2><span class="pill ${lockState[1]}">${lockState[0]}</span></div>${checks}${lockBtn}`);
    this.put("r-modal", this.modalHtml(), true);
    this.renderLegend();
    this.draw();
  }

  renderLegend() {
    const el = this.shadowRoot.querySelector("#legend");
    if (!el) return;
    const lines = (this.chart && this.chart.lines) || [];
    const html = lines.map((l, i) => `<span><span style="color:${LINE_COLORS[i % 2]}">━</span> ${esc(l.name)}</span>`).join("") + `<span><span style="color:${UP}">▲</span> koop</span><span><span style="color:${DOWN_TXT}">▼</span> verkoop</span><span class="hint">scrollen = zoomen · slepen = schuiven · dubbelklik = terug</span>`;
    if (this._cache.legend !== html) { el.innerHTML = html; this._cache.legend = html; }
  }

  drawEquity() {
    const canvas = this.shadowRoot.querySelector("#eq");
    if (!canvas || !this.o) return;
    const box = canvas.parentElement.getBoundingClientRect();
    const W = Math.max(200, box.width - 24), H = Math.max(80, box.height - 12), dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr; canvas.height = H * dpr;
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    g.font = "11px Inter,Roboto,system-ui,sans-serif";
    const pts = this.o.book.verloop || [];
    if (pts.length < 2) { g.fillStyle = MUTED; g.fillText("Het verloop verschijnt na een paar uur handelen.", 8, 24); return; }
    const axis = 64, pw = W - axis, top = 8, ph = H - top - 18;
    let hi = Math.max(0, ...pts.map((p) => p[1])), lo = Math.min(0, ...pts.map((p) => p[1]));
    const pad = (hi - lo || 1) * 0.1; hi += pad; lo -= pad;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
    const x = (t) => ((t - t0) / (t1 - t0 || 1)) * pw, y = (v) => top + ((hi - v) / (hi - lo)) * ph;
    g.strokeStyle = "#252E3D"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(0, y(0)); g.lineTo(pw, y(0)); g.stroke(); g.setLineDash([]);
    g.fillStyle = MUTED; g.fillText(eur(hi - pad), pw + 6, y(hi - pad) + 4); g.fillText(eur(0), pw + 6, y(0) + 4); if (y(lo + pad) - y(0) > 16) g.fillText(eur(lo + pad), pw + 6, y(lo + pad) + 4);
    const last = pts[pts.length - 1][1];
    g.strokeStyle = last >= 0 ? UP : DOWN_TXT; g.lineWidth = 2; g.beginPath();
    pts.forEach((p, i) => (i ? g.lineTo(x(p[0]), y(p[1])) : g.moveTo(x(p[0]), y(p[1]))));
    g.stroke();
    const d = (t) => new Date(t).toLocaleDateString("nl-NL", { day: "numeric", month: "short" });
    g.fillStyle = MUTED; g.fillText(d(t0), 0, H - 2); g.fillText(d(t1), pw - 36, H - 2);
    if (this.eqHover != null && this.eqHover >= 0 && this.eqHover <= pw) {
      const tt = t0 + (this.eqHover / pw) * (t1 - t0);
      let best = pts[0]; for (const p of pts) if (Math.abs(p[0] - tt) < Math.abs(best[0] - tt)) best = p;
      const hx = Math.round(x(best[0])) + 0.5, hy = y(best[1]);
      g.strokeStyle = "#5B6880"; g.lineWidth = 1; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(hx, top); g.lineTo(hx, top + ph); g.stroke(); g.setLineDash([]);
      g.fillStyle = best[1] >= 0 ? UP : DOWN_TXT; g.beginPath(); g.arc(hx, hy, 4, 0, Math.PI * 2); g.fill();
      const txt = eur(best[1], true) + " · " + new Date(best[0]).toLocaleString("nl-NL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      g.font = "bold 11px Inter,Roboto,system-ui,sans-serif"; const tw = g.measureText(txt).width + 12, bx = Math.max(0, Math.min(pw - tw, hx - tw / 2));
      g.fillStyle = "#2A3444"; g.fillRect(bx, 0, tw, 18); g.fillStyle = "#E7EAEE"; g.fillText(txt, bx + 6, 13);
    }
  }

  bindChart(canvas) {
    const pos = (e) => { const r = canvas.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
    const total = () => this.liveBars().length;
    const clamp = () => { const t = total(); this.view.n = Math.max(20, Math.min(this.view.n, t || 20)); this.view.off = Math.max(0, Math.min(this.view.off, Math.max(0, t - this.view.n))); };
    canvas.addEventListener("mousemove", (e) => {
      const p = pos(e);
      if (this._drag && this._geo) {
        const moved = Math.round((p.x - this._drag.x) / this._geo.step);
        this.view.off = this._drag.off + moved; clamp();
      }
      this.hover = p; this.draw();
    });
    canvas.addEventListener("mouseleave", () => { this.hover = null; this._drag = null; canvas.classList.remove("drag"); this.draw(); });
    canvas.addEventListener("mousedown", (e) => { this._drag = { x: pos(e).x, off: this.view.off }; canvas.classList.add("drag"); });
    canvas.addEventListener("mouseup", () => { this._drag = null; canvas.classList.remove("drag"); });
    canvas.addEventListener("dblclick", () => { this.view = { n: 160, off: 0 }; clamp(); this.draw(); });
    canvas.addEventListener("wheel", (e) => {
      if (!this._geo) return;
      e.preventDefault();
      const g = this._geo, p = pos(e), before = this.view.n;
      const fromRight = Math.max(0, (g.pw - p.x) / g.step);  // bars tussen cursor en rechterrand
      this.view.n = Math.round(before * (e.deltaY > 0 ? 1.18 : 1 / 1.18)); clamp();
      // houd de bar onder de cursor op zijn plek
      this.view.off = Math.round(this.view.off + fromRight - fromRight * (this.view.n / before)); clamp();
      this.hover = p; this.draw();
    }, { passive: false });
  }

  draw() {
    this.drawEquity();
    const canvas = this.shadowRoot.querySelector("#px");
    if (!canvas) return;
    const box = canvas.parentElement.getBoundingClientRect();
    const W = Math.max(200, box.width - 24), H = Math.max(160, box.height - 16), dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr; canvas.height = H * dpr;
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const FONT = "11px Inter,Roboto,system-ui,sans-serif", BOLD = "bold 11px Inter,Roboto,system-ui,sans-serif";
    g.font = FONT;
    const all = this.liveBars();
    this._geo = null;
    if (!all.length) { g.fillStyle = MUTED; g.fillText(this.chart ? "Nog geen koersdata voor deze markt." : "Grafiek laden…", 8, 24); return; }
    const v = this.view;
    v.n = Math.max(20, Math.min(v.n, all.length)); v.off = Math.max(0, Math.min(v.off, all.length - v.n));
    const i0 = all.length - v.off - v.n, bars = all.slice(i0, i0 + v.n);
    const axis = 64, volH = 48, timeH = 20, top = 22, ph = H - volH - timeH - top, pw = W - axis, n = bars.length, step = pw / n, bw = Math.max(1, step * 0.66);
    let hi = -Infinity, lo = Infinity, vmax = 0;
    for (const b of bars) { hi = Math.max(hi, b[2]); lo = Math.min(lo, b[3]); vmax = Math.max(vmax, b[5]); }
    const span = hi - lo || 1; hi += span * 0.04; lo -= span * 0.04;
    const y = (val) => top + ((hi - val) / (hi - lo)) * ph, x = (i) => i * step + step / 2;
    this._geo = { pw, step };
    g.strokeStyle = "#1A212C"; g.fillStyle = MUTED; g.lineWidth = 1; g.setLineDash([3, 4]);
    for (let k = 0; k <= 4; k++) { const val = lo + ((hi - lo) * k) / 4, yy = Math.round(y(val)) + 0.5; g.beginPath(); g.moveTo(0, yy); g.lineTo(pw, yy); g.stroke(); g.fillText(price(val), pw + 6, yy + 4); }
    g.setLineDash([]);
    bars.forEach((b, i) => {
      const c = b[4] >= b[1] ? UP : DOWN, cx = Math.round(x(i)) + 0.5;
      g.strokeStyle = c; g.fillStyle = c;
      g.beginPath(); g.moveTo(cx, y(b[2])); g.lineTo(cx, y(b[3])); g.stroke();
      const yt = y(Math.max(b[1], b[4])), bh = Math.max(1, Math.abs(y(b[1]) - y(b[4])));
      g.fillRect(x(i) - bw / 2, yt, bw, bh);
      g.globalAlpha = 0.35; const vh = vmax ? (b[5] / vmax) * (volH - 6) : 0; g.fillRect(x(i) - bw / 2, top + ph + volH - vh, bw, vh); g.globalAlpha = 1;
    });
    const lines = ((this.chart && this.chart.lines) || []).map((l) => ({ name: l.name, values: l.values.slice(i0 - (all.length - l.values.length), i0 - (all.length - l.values.length) + n) }));
    g.save(); g.beginPath(); g.rect(0, top, pw, ph); g.clip();
    lines.forEach((l, li) => {
      g.strokeStyle = LINE_COLORS[li % 2]; g.lineWidth = 1.5; g.beginPath(); let pen = false;
      l.values.forEach((val, i) => { if (val == null) { pen = false; return; } const px = x(i), py = y(val); pen ? g.lineTo(px, py) : g.moveTo(px, py); pen = true; });
      g.stroke();
    });
    g.restore();
    const t0 = bars[0][0], dt = n > 1 ? bars[1][0] - bars[0][0] : 3600000, markAt = {};
    ((this.chart && this.chart.marks) || []).forEach((m) => {
      const i = Math.floor((m.t - t0) / dt);
      if (i < 0 || i >= n) return;
      (markAt[i] = markAt[i] || []).push(m.side);
      const buy = m.side === "buy", px = x(i), py = buy ? y(bars[i][3]) + 12 : y(bars[i][2]) - 12;
      g.fillStyle = buy ? UP : DOWN_TXT; g.beginPath();
      if (buy) { g.moveTo(px, py - 7); g.lineTo(px - 5, py + 2); g.lineTo(px + 5, py + 2); } else { g.moveTo(px, py + 7); g.lineTo(px - 5, py - 2); g.lineTo(px + 5, py - 2); }
      g.fill();
    });
    const last = all[all.length - 1], ly = y(last[4]), lc = last[4] >= last[1] ? UP : DOWN;
    if (ly > top && ly < top + ph) { g.strokeStyle = lc; g.setLineDash([2, 3]); g.beginPath(); g.moveTo(0, Math.round(ly) + 0.5); g.lineTo(pw, Math.round(ly) + 0.5); g.stroke(); g.setLineDash([]); g.fillStyle = lc; g.fillRect(pw + 2, ly - 9, axis - 4, 18); g.fillStyle = "#0A0D12"; g.font = BOLD; g.fillText(price(last[4]), pw + 6, ly + 4); }
    g.font = FONT; g.fillStyle = MUTED;
    const intraday = dt < 86400000, every = Math.max(1, Math.round(n / 6));
    const tlabel = (t, full) => { const d = new Date(t); const day = d.toLocaleDateString("nl-NL", { day: "numeric", month: "short" }); return full && intraday ? day + " " + d.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" }) : day; };
    for (let i = 0; i < n; i += every) g.fillText(tlabel(bars[i][0]), Math.max(0, x(i) - 14), H - 4);

    // ---- muis: kruisdraad, prijs- en tijdlabel, uitlezing van de candle
    const h = this.hover;
    let idx = n - 1, live = false;
    if (h && h.x >= 0 && h.x <= pw) { idx = Math.max(0, Math.min(n - 1, Math.floor(h.x / step))); live = true; }
    const b = bars[idx], prev = idx > 0 ? bars[idx - 1] : all[i0 - 1], chg = prev ? (b[4] / prev[4] - 1) * 100 : null, bc = b[4] >= b[1] ? UP : DOWN_TXT;
    if (live) {
      const cx = Math.round(x(idx)) + 0.5;
      g.strokeStyle = "#5B6880"; g.lineWidth = 1; g.setLineDash([4, 4]);
      g.beginPath(); g.moveTo(cx, top); g.lineTo(cx, top + ph + volH); g.stroke();
      if (h.y >= top && h.y <= top + ph) {
        const hy = Math.round(h.y) + 0.5;
        g.beginPath(); g.moveTo(0, hy); g.lineTo(pw, hy); g.stroke(); g.setLineDash([]);
        g.fillStyle = "#2A3444"; g.fillRect(pw + 2, hy - 9, axis - 4, 18); g.fillStyle = "#E7EAEE"; g.font = BOLD;
        g.fillText(price(hi - ((h.y - top) / ph) * (hi - lo)), pw + 6, hy + 4);
      }
      g.setLineDash([]); g.font = BOLD;
      const tl = tlabel(b[0], true), tw = g.measureText(tl).width + 12, tx = Math.max(0, Math.min(pw - tw, cx - tw / 2));
      g.fillStyle = "#2A3444"; g.fillRect(tx, H - timeH + 2, tw, 18); g.fillStyle = "#E7EAEE"; g.fillText(tl, tx + 6, H - 5);
    }
    g.font = FONT;
    let rx = 0; const put = (label, val, color) => { g.fillStyle = MUTED; g.fillText(label, rx, 12); rx += g.measureText(label).width + 4; g.fillStyle = color || "#E7EAEE"; g.fillText(val, rx, 12); rx += g.measureText(val).width + 12; };
    if (pw > 520 || live) {
      put("O", price(b[1]), bc); put("H", price(b[2]), bc); put("L", price(b[3]), bc); put("S", price(b[4]), bc);
      if (chg != null) put("", pct(chg), col(chg));
      if (pw > 620) put("Vol", nf(b[5], b[5] >= 100 ? 0 : 2));
      if (pw > 760) lines.forEach((l, li) => { if (l.values[idx] != null) put(l.name, price(l.values[idx]), LINE_COLORS[li % 2]); });
      if (live && markAt[idx]) put("", markAt[idx].map((sd) => (sd === "buy" ? "▲ koop" : "▼ verkoop")).join(" "), markAt[idx][0] === "buy" ? UP : DOWN_TXT);
    }
  }
}

if (!customElements.get("bitvavo-bot-card")) customElements.define("bitvavo-bot-card", BitvavoBotCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "bitvavo-bot-card")) window.customCards.push({ type: "bitvavo-bot-card", name: "Bitvavo Bot", description: "Handelsscherm van Bitvavo Bot" });
