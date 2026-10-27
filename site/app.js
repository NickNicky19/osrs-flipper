"use strict";
const $ = id => document.getElementById(id);
const gp = value => value === null || value === undefined ? "UNKNOWN" : Number(value).toLocaleString("en-US") + " GP";
const number = value => value === null || value === undefined ? "UNKNOWN" : Number(value).toLocaleString("en-US");
const date = value => value ? new Date(Number(value) * 1000).toLocaleString() : "NOT YET OBSERVED";
const node = (tag, cls, value) => { const el = document.createElement(tag); if (cls) el.className = cls; if (value !== undefined) el.textContent = value; return el; };
const add = (parent, tag, cls, value) => { const child = node(tag, cls, value); parent.append(child); return child; };
const detail = (parent, label, value) => { add(parent,"dt",null,label); add(parent,"dd",null,value); };
let payload;
const currentPicks = (data, now, fresh) => fresh ? (data.top_picks || []).filter(p =>
  p.expires_at > now && p.observed_low_time+300 > now && p.observed_high_time+300 > now) : [];

function renderStatus(data) {
  const now = Date.now()/1000;
  const fresh = data.source_status === "CAPTURED_WITH_REVIEW" && data.run_at && now-data.run_at <= 600;
  const badge = $("live-status");
  badge.className = "status " + (fresh ? "status-good" : "status-bad");
  badge.textContent = fresh ? "SOURCE FRESH · PAPER ONLY" : "CURRENT PLANS UNAVAILABLE · " + data.source_status;
  const box = $("portfolio"); box.replaceChildren();
  add(box,"div","eyebrow","SIMULATED PORTFOLIO");
  add(box,"div","big",gp(data.bankroll));
  add(box,"div","caption","Stated bankroll for research, not a connected account");
  const dl = add(box,"dl");
  detail(dl,"Cash available at last run",gp(data.cash_available));
  detail(dl,"Reserved at last run",gp(data.cash_reserved));
  detail(dl,"If currently visible plans fully round-trip",gp(currentPicks(data,now,fresh).reduce((total,p)=>total+p.conditional_after_tax_gp,0)));
  detail(dl,"Simulated realized GP",gp(data.performance.simulated_realized_gp));
  detail(dl,"Unsold inventory at cost",gp(data.performance.simulated_inventory_at_cost));
  detail(dl,"Provisional live pilot ceiling",gp(data.live_pilot_ceiling));
  detail(dl,"Actual profit","NOT TRACKED HERE");
  add(box,"div","caption","Expired plans disappear here immediately; the next collection releases their paper reservations in durable state.");
  return fresh;
}

function renderPicks(data, fresh) {
  const box = $("picks"); box.replaceChildren();
  const now = Date.now()/1000;
  const picks = currentPicks(data,now,fresh);
  if (!picks.length) { add(box,"div","empty",fresh ? "NO_ACTION · No current paper plan passes the evidence and cash filters." : "Current offer plans are unavailable. The last research and source status remain visible below."); return; }
  picks.forEach(p => {
    const card = add(box,"article","pick");
    add(card,"h3",null,p.name);
    add(card,"p","sub","Item #"+p.item_id+" · "+p.evidence+" · expires "+date(p.expires_at));
    add(card,"div","money",gp(p.conditional_after_tax_gp));
    add(card,"div","money-label","Conditional after-tax total if all assumed units buy and later sell");
    const grid = add(card,"div","numbers");
    [["Maximum proposed buy",gp(p.max_buy_offer)], ["Target proposed sell",gp(p.target_sell_offer)],
     ["Break-even sell",gp(p.break_even_sell)], ["Assumed quantity",number(p.quantity_assumption)],
     ["Required shared cash",gp(p.required_cash)], ["Per-unit sale tax",gp(p.tax_per_unit)]].forEach(pair => {const cell=add(grid,"div");add(cell,"span",null,pair[0]);add(cell,"strong",null,pair[1]);});
    add(card,"p","fine","Observed instant-sell side: "+date(p.observed_low_time)+" · instant-buy side: "+date(p.observed_high_time));
    add(card,"p","fine","Review horizon: "+number(p.horizon_seconds/60)+" minutes · Published: "+date(p.first_public_at));
    add(card,"p","fine",p.reason);
    add(card,"p","risk",p.risks.join(" "));
    add(card,"p","fine","5% lower exit scenario: "+gp(p.stress.find(s=>s.exit_decline_bps===500)?.conditional_gp));
  });
}

function renderScreen(data) {
  const rows = data.screener || [];
  $("coverage").textContent = number(data.coverage.screened)+" observed items · "+number(data.coverage.eligible)+" research eligible. Exclusions remain visible.";
  const query = $("search").value.trim().toLowerCase(), filter = $("filter").value;
  const visible = rows.filter(r => (!query || r.name.toLowerCase().includes(query) || String(r.item_id).includes(query)) &&
    (filter === "all" || (filter === "eligible" && r.status === "ELIGIBLE_RESEARCH") || (filter === "excluded" && r.status !== "ELIGIBLE_RESEARCH")));
  const body = $("screen-rows"); body.replaceChildren();
  visible.slice(0,300).forEach(r => {
    const tr = add(body,"tr");
    const first = add(tr,"td"); add(first,"span",null,r.name); add(first,"span","id","#"+r.item_id);
    add(tr,"td",null,gp(r.observed_instant_sell_low)); add(tr,"td",null,gp(r.observed_instant_buy_high));
    add(tr,"td",null,number(r.day_low_units)+" / "+number(r.day_high_units));
    add(tr,"td",null,number(r.quantity_assumption)); add(tr,"td",null,gp(r.conditional_after_tax_gp));
    const status=add(tr,"td"); add(status,"span",r.flags.length ? "chip chip-bad":"chip",r.flags.length ? "EXCLUDED":"RESEARCH");
    if(r.flags.length) add(status,"span","id",r.flags.join(", "));
  });
  $("screen-count").textContent = "Showing "+number(Math.min(300,visible.length))+" of "+number(visible.length)+" matching rows. Search or filter to inspect the full "+number(rows.length)+"-item universe.";
}

function chart(points) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg","svg"); svg.setAttribute("viewBox","0 0 520 180"); svg.setAttribute("role","img"); svg.setAttribute("aria-label","Observed lower and higher side bucket-average prices over time");
  const lows=points.map(p=>p.low_avg).filter(Number.isFinite), highs=points.map(p=>p.high_avg).filter(Number.isFinite);
  if(points.length<2 || !lows.length || !highs.length) return svg;
  let min=Math.min(...lows,...highs), max=Math.max(...lows,...highs); if(min===max) max=min+1;
  [["low_avg","#91cddd"],["high_avg","#bcdf76"]].forEach(([key,color])=>{
    let path="";
    points.forEach((p,i)=>{const v=p[key]; if(!Number.isFinite(v)) return; const x=12+i*496/(points.length-1),y=166-(v-min)*148/(max-min);path+=(path?"L":"M")+x.toFixed(1)+" "+y.toFixed(1)+" ";});
    const el=document.createElementNS("http://www.w3.org/2000/svg","path");el.setAttribute("d",path);el.setAttribute("fill","none");el.setAttribute("stroke",color);el.setAttribute("stroke-width","2");svg.append(el);
  });
  return svg;
}

function renderGraphs(data) {
  const box=$("graph-list");box.replaceChildren();
  for(const pick of data.top_picks||[]) {
    const graph=data.graphs[String(pick.item_id)]; if(!graph) continue;
    const card=add(box,"article","graph-card"); add(card,"h3",null,pick.name);card.append(chart(graph.points));
    const legend=add(card,"p","legend"); const low=add(legend,"span");const lb=add(low,"b");lb.style.background="#91cddd";low.append("Lower-side average");const high=add(legend,"span");const hb=add(high,"b");hb.style.background="#bcdf76";high.append("Higher-side average");
    add(card,"p","small","Lowest observed bucket lower-side average: "+gp(graph.stats.lowest_low_average)+" · units in that whole bucket: "+number(graph.stats.low_bucket_units)+" · exact units at that price: UNKNOWN · attainable fills: UNKNOWN.");
    add(card,"p","small","Graph points: "+number(graph.stats.coverage)+" · resolution: "+number(graph.resolution_seconds/60)+" minutes · "+graph.availability+". First-hand captured recent points: "+number(graph.captured_recent_points)+". Later downloads are never backdated as prospective evidence.");
    add(card,"p","small","Window "+date(graph.stats.first_bucket)+" to "+date(graph.stats.last_bucket)+" · median side spread "+gp(graph.stats.median_spread)+" · 10th percentile lower-side average "+gp(graph.stats.low_p10)+" · past minimum vs latest lower-side average "+number(graph.stats.historical_low_drawdown_bps)+" bps. Reported whole-bucket low/high units: "+number(graph.stats.reported_low_units)+" / "+number(graph.stats.reported_high_units)+". These are historical descriptions, not executable prices or fills.");
  }
  if(!box.children.length) add(box,"div","empty","No current item graphs are available. Captured history begins only after the first successful pipeline run.");
}

function renderHistory(data) {
  const body=$("history-rows");body.replaceChildren();const outcomes=new Map((data.outcomes||[]).map(o=>[o.pick_id,o]));
  (data.history||[]).slice().reverse().forEach(p=>{const tr=add(body,"tr");add(tr,"td",null,date(p.issued_at));add(tr,"td",null,p.name);add(tr,"td",null,gp(p.conditional_after_tax_gp));add(tr,"td",null,p.first_public_at ? date(p.first_public_at) : "NOT YET OBSERVED");const o=outcomes.get(p.id);add(tr,"td",null,o ? o.status+(o.reference_mark_gp === null || o.reference_mark_gp === undefined ? "" : " · full-quantity reference mark "+gp(o.reference_mark_gp)+" (no fill claim)") : "PENDING / NO FILL CLAIM");});
  if(!body.children.length){const tr=add(body,"tr");const td=add(tr,"td",null,"No issued picks yet.");td.colSpan=5;}
}

function renderDetails(data) {
  const source=$("source-details");source.replaceChildren();
  [["Status",data.source_status],["Last source success",date(data.source_success_at)],["Last run",date(data.run_at)],["Quote validity",date(data.quote_valid_until)],["Screen coverage",number(data.coverage.screened)+" observed / "+number(data.coverage.eligible)+" eligible"],["Error",data.source_error||"None"],["Pipeline",data.versions.pipeline],["Last deployment",data.publication?date(data.publication.deployed_at):"NOT YET OBSERVED"]].forEach(x=>detail(source,...x));
  const ml=data.learning, target=$("learning-details");target.replaceChildren();
  [["Mode",ml.status],["Candidate version",ml.model_version||"NO TRAINED MODEL"],["Last training",date(ml.last_training_at)],["Last evaluation",date(ml.last_evaluation_at)],["Data cutoff",date(ml.latest_data_cutoff)],["Shadow samples",ml.shadow?number(ml.shadow.n):"NOT YET OBSERVED"],["Promotion",ml.promotion_gate?(ml.promotion_gate.pass_all?"GATES PASSED":"BLOCKED: "+ml.promotion_gate.reasons.join(", ")):"NOT EVALUATED"]].forEach(x=>detail(target,...x));
}

async function main(){try{const response=await fetch("./data.json",{cache:"no-store"});if(!response.ok)throw new Error("HTTP "+response.status);payload=await response.json();if(payload.schema!=="osrs-public-site-1")throw new Error("Unsupported public schema");const fresh=renderStatus(payload);renderPicks(payload,fresh);renderScreen(payload);renderGraphs(payload);renderHistory(payload);renderDetails(payload);$("search").addEventListener("input",()=>renderScreen(payload));$("filter").addEventListener("change",()=>renderScreen(payload));setInterval(()=>{const fresh=renderStatus(payload);renderPicks(payload,fresh);},15000);}catch(error){const badge=$("live-status");badge.className="status status-bad";badge.textContent="PUBLIC DATA UNAVAILABLE";add($("picks"),"div","empty","Unable to load the public research file. No current plan can be shown.");console.error(error);}}
main();
