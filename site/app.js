"use strict";
import {SnapshotPoller} from "./refresh.mjs?v=completion-v3-3";
import {initPersonalUI,renderPersonalUI} from "./personal-ui.mjs?v=completion-v3-5";
import {matchingReceipt,publishedAt,currentPlanEligible,needsReceiptCheck} from "./publication.mjs?v=completion-v3-5";
import {taxPerUnit} from "./planner.mjs?v=completion-v3-5";
import {analyzeWindow} from "./history-analysis.mjs?v=completion-v3-5";
const $ = id => document.getElementById(id);
const gp = value => value === null || value === undefined ? "UNKNOWN" : Number(value).toLocaleString("en-US") + " GP";
const number = value => value === null || value === undefined ? "UNKNOWN" : Number(value).toLocaleString("en-US");
const date = value => value ? new Date(Number(value) * 1000).toLocaleString() : "NOT YET OBSERVED";
const node = (tag, cls, value) => { const el = document.createElement(tag); if (cls) el.className = cls; if (value !== undefined) el.textContent = value; return el; };
const add = (parent, tag, cls, value) => { const child = node(tag, cls, value); parent.append(child); return child; };
const detail = (parent, label, value) => { add(parent,"dt",null,label); add(parent,"dd",null,value); };
let payload, fetchError=null, currentReceipt=null, selectedGraphId=null;
const snapshotFresh = data => {const now=Date.now()/1000;return !fetchError&&
  data.source_status==="CAPTURED_WITH_REVIEW"&&data.generated_at&&now>=data.generated_at&&now-data.generated_at<=600&&
  data.source_success_at&&now>=data.source_success_at&&now-data.source_success_at<=600;};
const displayFresh = data => snapshotFresh(data)&&!!matchingReceipt(data,currentReceipt);
const currentPicks = (data, now, fresh) => fresh ? (data.top_picks || []).filter(p =>
  currentPlanEligible(data,p,currentReceipt,now)) : [];

function renderStatus(data) {
  const now = Date.now()/1000;
  const sourceFresh=snapshotFresh(data),fresh=displayFresh(data);
  const badge = $("live-status");
  badge.className = "status " + (fresh ? "status-good" : "status-bad");
  badge.textContent = fresh ? "SOURCE FRESH · PUBLICATION VERIFIED · PAPER ONLY" :
    (sourceFresh ? "PUBLICATION RECEIPT PENDING · CURRENT PLANS UNAVAILABLE" :
      "HISTORICAL SNAPSHOT · CURRENT PLANS UNAVAILABLE");
  const box = $("portfolio"); box.replaceChildren();
  add(box,"div","eyebrow","SIMULATED PORTFOLIO");
  add(box,"div","big",gp(data.bankroll));
  add(box,"div","caption","Stated bankroll for research, not a connected account");
  const dl = add(box,"dl");
  detail(dl,"Cash available at last run",gp(data.cash_available));
  detail(dl,"Reserved at last run",gp(data.cash_reserved));
  detail(dl,"If currently visible plans fully round-trip",gp(currentPicks(data,now,fresh).reduce((total,p)=>total+p.conditional_after_tax_gp,0)));
  detail(dl,"Historical simulation",data.performance.historical_simulation_status || "NOT_SIMULATED");
  detail(dl,"Modeled fills", "NO_FILLS_MODELED");
  detail(dl,"Provisional live pilot ceiling",gp(data.live_pilot_ceiling));
  detail(dl,"Actual profit","NOT TRACKED HERE");
  add(box,"div","caption","Last-run cash is a paper reservation snapshot. Expired plans disappear here immediately; the next collection releases their reservations in durable state.");
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
    const validUntil=Math.min(p.expires_at,p.observed_low_time+300,p.observed_high_time+300);
    add(card,"p","sub","Item #"+p.item_id+" · "+p.evidence+" · entry reference valid for "+
      Math.max(0,Math.floor((validUntil-now)/60))+" more minutes · expires "+date(validUntil));
    add(card,"div","money",gp(p.conditional_after_tax_gp));
    add(card,"div","money-label","Conditional after-tax total if all assumed units buy and later sell");
    const grid = add(card,"div","numbers");
    const gross=p.target_sell_offer-p.max_buy_offer;
    const net=gross-p.tax_per_unit;
    const oneGpSell=Math.max(1,p.target_sell_offer-1);
    [["Maximum proposed buy",gp(p.max_buy_offer)], ["Target proposed sell",gp(p.target_sell_offer)],
     ["Break-even sell",gp(p.break_even_sell)], ["Assumed quantity",number(p.quantity_assumption)],
     ["Required shared cash",gp(p.required_cash)], ["Gross / tax / net per unit",gp(gross)+" / "+gp(p.tax_per_unit)+" / "+gp(net)],
     ["Conditional ROI",(100*p.conditional_after_tax_gp/p.required_cash).toFixed(2)+"%"]].forEach(pair => {const cell=add(grid,"div");add(cell,"span",null,pair[0]);add(cell,"strong",null,pair[1]);});
    add(card,"p","fine","Observed instant-sell side: "+date(p.observed_low_time)+" ("+Math.max(0,Math.floor(now-p.observed_low_time))+"s old) · instant-buy side: "+date(p.observed_high_time)+" ("+Math.max(0,Math.floor(now-p.observed_high_time))+"s old)");
    add(card,"p","fine","Review horizon: "+number(p.horizon_seconds/60)+" minutes · Verified deploy: "+date(publishedAt(data,p,currentReceipt)));
    add(card,"p","fine",p.reason);
    add(card,"p","risk",p.risks.join(" "));
    add(card,"p","fine","One-GP worse buy: "+gp((net-1)*p.quantity_assumption)+
      " · one-GP worse sell: "+gp((oneGpSell-taxPerUnit(oneGpSell,p.tax_exempt)-p.max_buy_offer)*p.quantity_assumption)+
      " · 5% lower exit scenario: "+gp(p.stress.find(s=>s.exit_decline_bps===500)?.conditional_gp)+
      ". These are arithmetic stresses, not probabilities.");
  });
}

function renderScreen(data) {
  const rows = data.screener || [];
  const c=data.coverage||{};
  $("coverage").textContent = number(c.mapped)+" mapped · "+number(c.screened)+" observed · "+
    number(c.rule_supported)+" rule/limit supported · "+number(c.fresh_two_sided)+" fresh two-sided · "+
    number(c.activity_supported)+" activity supported · "+number(c.positive_net)+" positive baseline-size arithmetic · "+
    number(c.eligible)+" research eligible. Overlapping rejection flags; snapshot "+date(data.generated_at)+
    ". 24h unit context last captured "+date(data.daily_volume_as_of)+".";
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
    const now=Date.now()/1000;
    const rowCurrent=displayFresh(data)&&r.low_time&&r.high_time&&now>=r.low_time&&now>=r.high_time&&
      now<r.low_time+300&&now<r.high_time+300;
    const status=add(tr,"td"); add(status,"span",r.flags.length || !rowCurrent ? "chip chip-bad":"chip",
      !snapshotFresh(data)?"HISTORICAL":(!rowCurrent?"EXPIRED QUOTE":(r.flags.length ? "EXCLUDED":"RESEARCH")));
    if(r.flags.length) add(status,"span","id",r.flags.join(", "));
    if(r.preference_flags?.length)add(status,"span","id","Preference context: "+r.preference_flags.join(", "));
    add(status,"span","id","Lower / higher quote age: "+
      (r.low_time?Math.floor(now-r.low_time)+"s":"UNKNOWN")+" / "+
      (r.high_time?Math.floor(now-r.high_time)+"s":"UNKNOWN")+
      (!rowCurrent ? " · historical only" : ""));
  });
  $("screen-count").textContent = "Showing "+number(Math.min(300,visible.length))+" of "+number(visible.length)+" matching rows. Search or filter to inspect the full "+number(rows.length)+"-item universe.";
}

const svgNS="http://www.w3.org/2000/svg";
function svgEl(tag,attributes={}){const el=document.createElementNS(svgNS,tag);for(const [key,value] of Object.entries(attributes))el.setAttribute(key,String(value));return el;}
function chart(historical,captured,historicalResolution=3600){
  const svg=svgEl("svg",{viewBox:"0 0 600 240",role:"img","aria-label":"Time-scaled lower and higher side bucket-average prices. Dashed lines are later historical downloads; solid lines are first-hand captured intervals."});
  const all=[...historical,...captured].filter(p=>Number.isFinite(p.bucket_start));
  const values=all.flatMap(p=>[p.low_avg,p.high_avg]).filter(Number.isFinite);
  if(all.length<2||!values.length)return svg;
  const minT=Math.min(...all.map(p=>p.bucket_start)),maxT=Math.max(...all.map(p=>p.bucket_start));
  let min=Math.min(...values),max=Math.max(...values);if(min===max)max=min+1;
  const left=62,right=582,top=16,bottom=204;
  const x=t=>left+(t-minT)*(right-left)/Math.max(1,maxT-minT);
  const y=v=>bottom-(v-min)*(bottom-top)/(max-min);
  svg.append(svgEl("line",{x1:left,y1:top,x2:left,y2:bottom,stroke:"#718779"}),
             svgEl("line",{x1:left,y1:bottom,x2:right,y2:bottom,stroke:"#718779"}));
  for(const [value,yy] of [[max,top],[min,bottom]]){const label=svgEl("text",{x:4,y:yy+4,fill:"#a7b7aa","font-size":11});label.textContent=Math.round(value).toLocaleString();svg.append(label);}
  for(const [value,xx] of [[minT,left],[maxT,right]]){const label=svgEl("text",{x:xx,y:228,fill:"#a7b7aa","font-size":10,"text-anchor":xx===left?"start":"end"});label.textContent=new Date(value*1000).toLocaleString();svg.append(label);}
  const draw=(points,key,color,resolution,dashed)=>{
    let path="",prior=null;
    for(const p of [...points].sort((a,b)=>a.bucket_start-b.bucket_start)){
      const value=p[key];
      if(!Number.isFinite(value)){prior=null;continue;}
      const move=prior===null||p.bucket_start-prior>resolution*1.5;
      path+=(move?"M":"L")+x(p.bucket_start).toFixed(1)+" "+y(value).toFixed(1)+" ";
      prior=p.bucket_start;
    }
    if(path){const line=svgEl("path",{d:path,fill:"none",stroke:color,"stroke-width":dashed?1.5:2.5,opacity:dashed?.65:1});if(dashed)line.setAttribute("stroke-dasharray","5 4");svg.append(line);}
  };
  draw(historical,"low_avg","#91cddd",historicalResolution,true);
  draw(historical,"high_avg","#bcdf76",historicalResolution,true);
  draw(captured,"low_avg","#91cddd",300,false);
  draw(captured,"high_avg","#bcdf76",300,false);
  return svg;
}

function renderGraphs(data){
  const box=$("graph-list");box.replaceChildren();
  const graphs=data.graphs||{},ids=Object.keys(graphs);
  const selector=$("graph-item"),prior=selectedGraphId||selector.value;
  selector.replaceChildren();
  const names=new Map((data.screener||[]).map(r=>[String(r.item_id),r.name]));
  for(const p of data.history||[])names.set(String(p.item_id),p.name);
  for(const id of ids){const option=node("option",null,(names.get(id)||"Item")+" #"+id);option.value=id;selector.append(option);}
  selectedGraphId=ids.includes(prior)?prior:(data.top_picks||[]).map(p=>String(p.item_id)).find(id=>ids.includes(id))||ids[0]||null;
  if(selectedGraphId)selector.value=selectedGraphId;
  if(!selectedGraphId){add(box,"div","empty","Numerical history is unavailable in this artifact. Saved pick history remains visible separately.");return;}
  const graph=graphs[selectedGraphId],horizon=Number($("graph-horizon").value);
  const all=[...(graph.historical_points||[]),...(graph.captured_points||[])];
  const newest=Math.max(0,...all.map(p=>p.bucket_start||0)),cutoff=newest-horizon;
  const historical=(graph.historical_points||[]).filter(p=>p.bucket_start>=cutoff);
  const captured=(graph.captured_points||[]).filter(p=>p.bucket_start>=cutoff);
  const card=add(box,"article","graph-card");
  add(card,"h3",null,names.get(selectedGraphId)||"Item #"+selectedGraphId);
  if(historical.length+captured.length<2){add(card,"p","small","INSUFFICIENT_HISTORY for this review window. This does not extend the entry quote validity.");return;}
  card.append(chart(historical,captured,graph.resolution_seconds||3600));
  const legend=add(card,"p","legend");
  const low=add(legend,"span"),lb=add(low,"b");lb.style.background="#91cddd";low.append("Lower-side average");
  const high=add(legend,"span"),hb=add(high,"b");hb.style.background="#bcdf76";high.append("Higher-side average · dashed = later download");
  const analysisLayer=captured.length>=2?captured:historical;
  const analysisName=captured.length>=2?"first-hand captured":"retrospective downloaded";
  const valid=analysisLayer.filter(p=>Number.isFinite(p.low_avg)&&Number.isFinite(p.high_avg));
  const minBucket=valid.length?valid.reduce((a,b)=>a.low_avg<b.low_avg?a:b):null;
  const grossPositive=valid.filter(p=>p.high_avg>p.low_avg).length;
  const lowMin=valid.length?Math.min(...valid.map(p=>p.low_avg)):null,lowMax=valid.length?Math.max(...valid.map(p=>p.low_avg)):null;
  const latestCapture=Math.max(0,...captured.map(p=>p.received_at||0));
  add(card,"p","small","Review window: "+number(horizon/60)+" minutes · statistics use "+analysisName+" points only: "+number(valid.length)+" two-sided buckets; positive gross side difference in "+number(grossPositive)+". Lower-side average range: "+gp(lowMin)+" to "+gp(lowMax)+". These are descriptions, not a selection-policy input or executable quotes.");
  add(card,"p","small","Lowest observed lower-side bucket average: "+gp(minBucket?.low_avg)+" · reported lower-side units in that whole bucket: "+number(minBucket?.low_units)+" · exact units at that price: UNKNOWN · attainable fills: UNKNOWN.");
  add(card,"p","small","First-hand recent points: "+number(captured.length)+" (latest receipt "+date(latestCapture)+"); retrospective points: "+number(historical.length)+" (download receipt "+date(graph.received_at)+"). Source-window age: "+(newest?number(Math.max(0,Math.floor(Date.now()/1000-newest)))+" seconds":"UNKNOWN")+". Gaps break lines; downloaded history is never backdated as a prospective signal.");
  const ruleRow=(data.screener||[]).find(r=>String(r.item_id)===selectedGraphId);
  const range=pair=>pair?gp(pair[0])+" to "+gp(pair[1]):"UNKNOWN";
  const activity=(a,b)=>number(a)+" → "+number(b)+" reported units (earlier / later half)";
  for(const [label,points,resolution] of [["First-hand captured",captured,300],
      ["Retrospective downloaded",historical,graph.resolution_seconds||3600]]){
    if(!points.length)continue;
    const stats=analyzeWindow(points,horizon,resolution,ruleRow?.tax_exempt);
    add(card,"p","small",label+": "+number(stats.twoSidedBuckets)+" two-sided / "+
      number(stats.expectedBuckets)+" expected buckets at this resolution; "+
      number(stats.oneSidedOrMissingBuckets)+" observed one-sided or incomplete buckets. "+
      "Lower range "+range(stats.lowRange)+"; higher range "+range(stats.highRange)+
      "; median/minimum gross spread "+gp(stats.medianGrossSpread)+" / "+gp(stats.minimumGrossSpread)+
      "; positive gross spread in "+number(stats.positiveGrossBuckets)+" buckets. Missing intervals reduce coverage.");
    add(card,"p","small",label+" downside: chronological lower-side peak-to-later-trough "+
      number(stats.chronologicalLowDownsideBps)+" basis points; lower-side range "+
      number(stats.lowerSideRangeBps)+" basis points of its median. Side activity: lower "+
      activity(stats.earlierLowUnits,stats.laterLowUnits)+"; higher "+
      activity(stats.earlierHighUnits,stats.laterHighUnits)+". Five-percent lower-exit after-tax arithmetic remains positive in "+
      number(stats.fivePercentExitStressPositiveBuckets)+" buckets "+
      (stats.taxRuleKnown?"under the current reviewed rule.":"only when a reviewed rule exists; here tax is UNKNOWN."));
  }
  if(!displayFresh(data))add(card,"p","risk","Historical or unverified snapshot only; current entry plans are unavailable.");
}

function renderHistory(data) {
  const body=$("history-rows");body.replaceChildren();const outcomes=new Map((data.outcomes||[]).map(o=>[o.pick_id,o]));
  (data.history||[]).slice().reverse().forEach(p=>{const tr=add(body,"tr");add(tr,"td",null,date(p.issued_at));add(tr,"td",null,p.name);add(tr,"td",null,gp(p.conditional_after_tax_gp));const observed=publishedAt(data,p,currentReceipt);add(tr,"td",null,observed?date(observed):"NOT YET VERIFIED");const o=outcomes.get(p.id);add(tr,"td",null,o ? o.status+(o.reference_mark_gp === null || o.reference_mark_gp === undefined ? "" : " · full-quantity reference mark "+gp(o.reference_mark_gp)+" (no fill claim)") : "PENDING / NO FILL CLAIM");});
  if(!body.children.length){const tr=add(body,"tr");const td=add(tr,"td",null,"No issued picks yet.");td.colSpan=5;}
}

function renderDetails(data) {
  const source=$("source-details");source.replaceChildren();
  const matched=matchingReceipt(data,currentReceipt);
  const autoAge=data.last_automatic_source_success_at?Date.now()/1000-data.last_automatic_source_success_at:null;
  const autoHealth=autoAge===null?"NOT_YET_OBSERVED":autoAge>900?"STALE_AUTOMATIC_COLLECTION · "+Math.floor(autoAge/60)+" minutes":"OBSERVED · "+Math.floor(autoAge/60)+" minutes ago";
  [["Status",fetchError ? "HISTORICAL · refresh failed: "+fetchError : data.source_status],
   ["Current artifact",data.artifact_id||"UNKNOWN"],["Artifact run",data.artifact_run_id||"UNKNOWN"],
   ["Export generated",date(data.generated_at)],["Last source success",date(data.source_success_at)],
   ["Cycle start / end",date(data.run_at)+" / "+date(data.run_end_at)],
   ["Last automatic collection",date(data.last_automatic_source_success_at)],
   ["Last automatic publication",date(data.last_automatic_publication_at)],
   ["Automatic-run health",autoHealth],
   ["Earliest visible plan expiry",date(data.quote_valid_until)],
   ["Screen coverage",number(data.coverage.screened)+" observed / "+number(data.coverage.eligible)+" eligible"],
   ["24h unit context captured",date(data.daily_volume_as_of)],
   ["Recent route counts",Object.entries(data.recent_route_captures||{}).map(([route,x])=>route+": "+x.accepted+" accepted / "+x.quarantined+" quarantined at "+date(x.received_at)).join(" · ")||"UNAVAILABLE"],
   ["Known missing history",data.known_data_gap?date(data.known_data_gap.from_bucket)+" to "+date(data.known_data_gap.to_bucket)+" · "+data.known_data_gap.reason:"None recorded"],
   ["Error",data.source_error||"None"],["Pipeline",data.versions.pipeline],
   ["Matching Pages receipt",matched?date(currentReceipt.deployed_at)+" · "+currentReceipt.deployment_id:
     "Pending or unavailable for this artifact"],
   ["Prior deployment",data.publication?date(data.publication.deployed_at)+" · "+data.publication.deployment_id:
     "NOT YET OBSERVED"]].forEach(x=>detail(source,...x));
  const ml=data.learning, target=$("learning-details");target.replaceChildren();
  const rd=ml.readiness||{};
  [["Mode",ml.status],["Active research model",ml.active_model_id||"RULES_BASELINE"],
   ["Candidate version",ml.model_version||"NO TRAINED MODEL"],
   ["Candidate code commit",ml.model_code_commit||"NO TRAINED MODEL"],
   ["Candidate rule / source route versions",(ml.model_rule_version||"UNKNOWN")+" / "+(ml.model_source_route_versions||[]).join(", ")],
   ["Target / feature schema",(ml.target_schema||"UNKNOWN")+" / "+(ml.feature_schema||"UNKNOWN")],
   ["Last readiness check",date(rd.last_check_at)],["Next bounded check",date(rd.next_due_at)],
   ["Usable finalized examples",number(rd.usable_examples)+" / "+number(rd.required_examples)],
   ["Items / decision clocks",number(rd.usable_items)+" / "+number(rd.required_items)+" items · "+number(rd.decision_clocks)+" / "+number(rd.required_decision_clocks)+" clocks"],
   ["Label coverage",number(rd.finalized_coverage_bps)+" / "+number(rd.required_coverage_bps)+" basis points"],
   ["New matured since prior generation",number(rd.new_matured_examples)+" / "+number(rd.required_new_matured)+" examples · "+number(rd.new_matured_clocks)+" / "+number(rd.required_new_clocks)+" clocks"],
   ["Last training",date(ml.last_training_at)+" · "+number(ml.last_training_duration_ms)+" ms"],["Last evaluation",date(ml.last_evaluation_at)],
   ["Data cutoff",date(ml.latest_data_cutoff)],
   ["Shadow samples",ml.shadow?number(ml.shadow.n)+" · "+number(ml.shadow.days)+" days · "+number(ml.shadow.coverage_bps)+" bp coverage":"NOT YET OBSERVED"],
   ["Economic comparison",ml.economic_shadow_comparison?.evidence_class||"NOT YET OBSERVED"],
   ["Promotion",ml.promotion_gate?(ml.promotion_gate.pass_all?"GATES PASSED":"BLOCKED: "+ml.promotion_gate.reasons.join(", ")):"NOT EVALUATED"],
   ["Readiness reason",ml.learning_reason?.reason||ml.learning_reason?.status||"NONE"]].forEach(x=>detail(target,...x));
}

function renderAll(){
  if(!payload){const badge=$("live-status");badge.className="status status-bad";badge.textContent="PUBLIC DATA UNAVAILABLE";return;}
  const scroll=window.scrollY;
  const fresh=renderStatus(payload);renderPicks(payload,fresh);renderScreen(payload);
  renderGraphs(payload);renderHistory(payload);renderDetails(payload);
  renderPersonalUI(payload,{fresh:!!fresh});
  if(window.scrollY!==scroll) window.scrollTo(window.scrollX,scroll);
}

async function loadMatchingReceipt(data){
  currentReceipt=null;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);
  try{
    const url="https://raw.githubusercontent.com/NickNicky19/osrs-flipper/paper-data/publication.json?artifact="+encodeURIComponent(data.artifact_id);
    const response=await fetch(url,{cache:"no-store",signal:controller.signal,credentials:"omit",referrerPolicy:"no-referrer"});
    if(!response.ok) return;
    const receipt=await response.json();
    if(payload?.artifact_id===data.artifact_id && matchingReceipt(data,receipt)) currentReceipt=receipt;
  }catch{ /* A missing receipt leaves the current artifact clearly unacknowledged. */ }
  finally{clearTimeout(timeout);if(payload?.artifact_id===data.artifact_id)renderAll();}
}

function main(){
  initPersonalUI();
  $("search").addEventListener("input",()=>payload&&renderScreen(payload));
  $("filter").addEventListener("change",()=>payload&&renderScreen(payload));
  $("graph-item").addEventListener("change",()=>{selectedGraphId=$("graph-item").value;if(payload)renderGraphs(payload);});
  $("graph-horizon").addEventListener("change",()=>payload&&renderGraphs(payload));
  const poller=new SnapshotPoller({onSnapshot:data=>{const check=needsReceiptCheck(payload,data,currentReceipt);
      payload=data;fetchError=null;renderAll();
      if(check)void loadMatchingReceipt(data);},
    onError:(error,last)=>{fetchError=String(error?.message||error);payload=last||payload;renderAll();}});
  document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible")poller.resume();});
  setInterval(()=>{if(payload)renderAll();},15000);
  void poller.poll();
}
main();
