'use strict';
// Plain local browser UI. No external assets, vendor endpoints, or game control.
let researchReport=null, selectedResearchRow=null, lastScenario=null, freezeId=null, researchLoadSerial=0;
const percent=bps=>(bps/100).toFixed(2)+'%';
const age=seconds=>seconds===null?'unknown':seconds>=3600?(seconds/3600).toFixed(1)+'h':seconds>=60?Math.floor(seconds/60)+'m':seconds+'s';
const stamp=seconds=>new Date(seconds*1000).toISOString().replace('T',' ').replace('.000Z',' UTC');
async function researchPost(path,body){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw new Error(data.error||'Research request failed.');return data;
}
window.refreshResearch=async function(){
 const loadSerial=++researchLoadSerial;
 try{
  const windowSeconds=document.getElementById('research-window').value;
  document.getElementById('scenario-panel').classList.add('hidden');lastScenario=null;
  const response=await fetch('/api/research?window='+windowSeconds);const report=await response.json();
  if(!response.ok)throw new Error(report.error||'Could not read research.');
  if(loadSerial!==researchLoadSerial)return;
  researchReport=report;selectedResearchRow=null;lastScenario=null;freezeId=null;
  document.getElementById('scenario-panel').classList.add('hidden');
  const count=report.rows.filter(r=>r.flags.length===0).length;
  document.getElementById('research-status').textContent=(report.namespace==='fixture'?'SYNTHETIC REPLAY · ':report.source_status+' · ')+stamp(report.as_of)+'. '+(report.namespace==='fixture'?'Fixed demonstration clock, not current OSRS prices.':'No direct game or account access.');
  const health=[['Eligible observations',count+' / '+report.rows.length,'Eligibility is not a trade approval'],['Received intervals',report.received_intervals+' / '+report.expected_intervals,'Per-item side coverage is shown below'],['Account-qualified sizes','None','Unknown allowances remain unknown'],['Predictions trained','None','No made-up expected return']];
  document.getElementById('research-health').innerHTML=health.map(v=>`<div class="health"><small>${v[0]}</small><strong>${v[1]}</strong><span>${v[2]}</span></div>`).join('');
  document.getElementById('research-provenance').innerHTML=`<p>Window: ${stamp(report.window_start)} to ${stamp(report.window_end)} (completed intervals).</p><p>Engine: ${escapeText(report.engine)}. Quote-age ceiling: ${report.policy.max_quote_age_seconds}s. Minimum coverage: ${percent(report.policy.min_coverage_bps)}. These are explicit policy settings, not calibrated optimal cutoffs.</p><p>Source flags: ${escapeText(report.global_flags.join(', ')||'No global flags; individual exclusions still apply.')}</p><p>Report hash: <code>${report.report_hash}</code></p><p>Rules hash: <code>${report.rules_hash}</code></p><p>${report.capture_manifest.length} source captures are identified in each saved receipt.</p>`;
  renderResearch();
  const eResponse=await fetch('/api/evidence');const e=await eResponse.json();
  if(!eResponse.ok)throw new Error(e.error||'Evidence read failed.');
  if(loadSerial!==researchLoadSerial)return;
  document.getElementById('execution-evidence').innerHTML='<h3>Recorded attempts—not selected winners</h3>'+['BUY','SELL'].map(side=>{const v=e.by_side[side];return `<div class="result-line"><span>${side}: ${v.attempts} attempts</span><strong>${v.filled_in_full} fully filled · ${v.cancelled_zero_fill} zero-fill cancellations</strong></div>`;}).join('')+`<p class="fine">${e.saved_research_receipts} saved research receipts. No prospective policy comparison has been established. Buy and sell attempts are not complete-flip counts. Recording times are not exact server execution times.</p>`;
  const link=document.getElementById('decision-link'),priorLink=link.value;
  link.innerHTML='<option value="">No linked recommendation</option>'+e.last_receipts.filter(r=>r.item_id!==null).map(r=>`<option value="${escapeText(r.id)}">${escapeText(r.item_name)} · ${escapeText(r.id.slice(0,12))}</option>`).join('');
  if(e.last_receipts.some(r=>r.id===priorLink))link.value=priorLink;
  const valuation=e.inventory_reference_scenario;
  document.getElementById('execution-evidence').innerHTML+=`<h3>Unsold-inventory check</h3><div class="result-line"><span>Positions without a usable reference value</span><strong>${valuation.unpriced_positions}</strong></div><div class="result-line"><span>Portfolio result at lower-side reference prices</span><strong>${valuation.reference_portfolio_net===null?'UNKNOWN — incomplete valuation':gp(valuation.reference_portfolio_net)+' (scenario)'}</strong></div><p class="fine">All recorded collected, uncollected and reserved units are included once. This is not an executable liquidation promise or complete game-account valuation. A profitable sale does not erase a losing open position.</p>`;

 }catch(error){
  if(loadSerial!==researchLoadSerial)return;
  researchReport=null;selectedResearchRow=null;lastScenario=null;
  document.getElementById('research-status').textContent='RESEARCH BLOCKED: '+error.message;
  document.getElementById('research-body').innerHTML='<tr><td colspan="7">No replacement prices are being shown. The journal remains available.</td></tr>';
  document.getElementById('scenario-panel').classList.add('hidden');
 }
};
function renderResearch(){
 if(!researchReport)return;
 const show=document.getElementById('show-excluded').checked;
 const search=document.getElementById('research-search').value.toLowerCase();
 const sort=document.getElementById('research-sort').value;
 let rows=researchReport.rows.filter(r=>(show||!r.flags.length)&&(r.name.toLowerCase().includes(search)||String(r.item_id).includes(search)));
 const score=r=>sort==='activity'?Math.min(r.activity.low_units,r.activity.high_units):sort==='margin'?(r.scenarios[0]?.net_per_unit??-Infinity):(r.scenarios[0]?.conditional_roi_bps??-Infinity);
 rows.sort((a,b)=>Boolean(a.flags.length)-Boolean(b.flags.length)||score(b)-score(a)||a.item_id-b.item_id);
 document.getElementById('research-body').innerHTML=rows.length?rows.map(r=>`<tr class="${r.flags.length?'excluded-row':''}"><td>${escapeText(r.name)}<span class="details">${r.item_id} · ${r.flags.length?escapeText(r.flags.join(' / ')):'Source checks passed for this observation only'}</span></td><td>${r.low===null?'—':units(r.low)} / ${r.high===null?'—':units(r.high)}</td><td>${age(r.low_age)} / ${age(r.high_age)}</td><td>${percent(r.activity.coverage_bps)}<span class="details">${r.activity.low_intervals} / ${r.activity.high_intervals} known side intervals of ${r.activity.expected_intervals}</span></td><td>${units(r.activity.low_units)} / ${units(r.activity.high_units)}</td><td>${r.scenarios.length?gp(r.scenarios[0].net_per_unit):'Rule unknown'}</td><td>${r.flags.length?'<span class="pill warning">EXCLUDED</span>':`<button type="button" data-inspect="${r.item_id}">Explore</button>`}</td></tr>`).join(''):'<tr><td colspan="7">No eligible observations. Check source status or show excluded observations. No synthetic fallback is used in journal mode.</td></tr>';
 document.querySelectorAll('[data-inspect]').forEach(button=>button.addEventListener('click',()=>selectResearch(Number(button.dataset.inspect))));
}
function selectResearch(id){
 notify('');
 selectedResearchRow=researchReport.rows.find(r=>r.item_id===id);lastScenario=null;freezeId=null;
 const row=selectedResearchRow,f=document.getElementById('research-form'),s=row.scenarios[0];
 document.getElementById('scenario-name').textContent=row.name;
 f.elements.buy.value=s.buy;f.elements.sell.value=s.sell;f.elements.quantity.value=1;
 document.getElementById('scenario-result').innerHTML='';
 document.getElementById('freeze-scenario').classList.add('hidden');
 document.getElementById('price-alternatives').innerHTML=row.scenarios.map((s,i)=>`<button class="secondary" type="button" data-alternative="${i}">${escapeText(s.label)} · ${units(s.buy)} → ${units(s.sell)}</button>`).join('');
 document.querySelectorAll('[data-alternative]').forEach(b=>b.addEventListener('click',()=>{const s=row.scenarios[Number(b.dataset.alternative)];f.elements.buy.value=s.buy;f.elements.sell.value=s.sell;notify('');lastScenario=null;document.getElementById('freeze-scenario').classList.add('hidden');document.getElementById('scenario-result').innerHTML='Inputs changed. Calculate again.';}));
 document.getElementById('scenario-panel').classList.remove('hidden');
 document.getElementById('scenario-panel').scrollIntoView({behavior:'smooth',block:'start'});
}
function scenarioInput(){
 const f=document.getElementById('research-form');
 return {report_hash:researchReport.report_hash,item_id:selectedResearchRow.item_id,buy:numeric(f,'buy'),sell:numeric(f,'sell'),quantity:numeric(f,'quantity')};
}
document.getElementById('research-form').addEventListener('submit',async event=>{
 event.preventDefault();const button=event.target.querySelector('[type=submit]');button.disabled=true;
 try{
  const input=scenarioInput(),r=await researchPost('/api/scenario',input);lastScenario={input,result:r};freezeId=crypto.randomUUID();
  document.getElementById('scenario-result').innerHTML=`<div class="scenario-metrics"><div><small>Capital if fully bought</small><strong>${gp(r.capital)}</strong></div><div><small>Net if fully bought and sold</small><strong>${gp(r.conditional_net)}</strong></div><div><small>Minimum break-even sale price</small><strong>${gp(r.scenario.breakeven_sell)}</strong></div></div><div class="table-wrap"><table><thead><tr><th>Hypothetical exit move</th><th>Sale price</th><th>Net outcome if all units sell</th></tr></thead><tbody>${r.stress_scenarios.map(s=>`<tr><td>${s.exit_decline_bps===0?'Stated target':'−'+percent(s.exit_decline_bps)}</td><td>${gp(s.sale_price)}</td><td>${gp(s.conditional_net)}</td></tr>`).join('')}</tbody></table></div><p class="fine">Lower-side quote exit scenario: ${gp(r.lower_quote_exit_scenario)}. This is not an executable liquidation promise. Proposed units equal ${percent(r.entry_share_of_observed_units_bps)} of observed lower-side units and ${percent(r.exit_share_of_observed_units_bps)} of higher-side units; these ratios are NOT fill probabilities.</p><p class="scenario-flags">${escapeText(r.constraint_flags.join(' · '))}</p>`;
  document.getElementById('freeze-scenario').classList.remove('hidden');
 }catch(error){lastScenario=null;document.getElementById('freeze-scenario').classList.add('hidden');notify(error.message,true);}
 finally{button.disabled=false;}
});
document.getElementById('research-form').addEventListener('input',()=>{notify('');lastScenario=null;document.getElementById('freeze-scenario').classList.add('hidden');document.getElementById('scenario-result').innerHTML='Inputs changed. Calculate again.';});
document.getElementById('freeze-scenario').addEventListener('click',async event=>{
 if(!lastScenario)return;event.target.disabled=true;
 try{const r=await researchPost('/api/research/freeze',{...lastScenario.input,id:freezeId});notify('Research receipt saved: '+r.digest.slice(0,16)+'. No offer was placed.');
 const links=document.getElementById('decision-link');
 if(!Array.from(links.options).some(o=>o.value===r.id))links.add(new Option(lastScenario.result.name+' · '+r.id.slice(0,12),r.id));}
 catch(error){notify(error.message,true);}finally{event.target.disabled=false;}
});
document.getElementById('research-refresh').addEventListener('click',()=>window.refreshResearch());
document.getElementById('research-window').addEventListener('change',()=>window.refreshResearch());
['research-search','research-sort','show-excluded'].forEach(id=>document.getElementById(id).addEventListener(id==='research-search'?'input':'change',renderResearch));
