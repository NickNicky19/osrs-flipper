'use strict';
const token = document.querySelector('meta[name="local-token"]').content;
let current;
const gp = n => new Intl.NumberFormat('en-US').format(n) + ' GP';
const units = n => new Intl.NumberFormat('en-US').format(n);
const escapeText = v => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notify(text, error=false){const box=document.getElementById('message');box.textContent=text;box.className=error?'error':'';}
function view(name){document.querySelectorAll('.view').forEach(s=>s.classList.toggle('hidden',s.id!==name));document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.view===name));}
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>view(t.dataset.view)));
function numeric(form,key){const s=form.elements[key].value;if(!/^\d+$/.test(s))throw new Error(key+' must be a positive whole number.');const n=Number(s);if(!Number.isSafeInteger(n)||n<1)throw new Error(key+' is invalid.');return n;}
async function refresh(){
 const response=await fetch('/api/state');if(!response.ok)throw new Error('Could not read the local journal.');current=await response.json();
 const s=current.summary, demo=current.mode==='demo';
 const warningBox=document.getElementById('recording-warnings'), warnings=s.recording_warnings||[];
 warningBox.classList.toggle('hidden',warnings.length===0);
 warningBox.textContent=warnings.length?`FACTUAL RECORD EXCEPTIONS: ${warnings.length}. These actions were recorded, NOT approved. `+(s.exposure_over_cap?'Recorded exposure is above the unchanged ceiling. ':'')+warnings.map(w=>`${w.offer_id}: ${w.flags.join(', ')} — ${w.reason}`).join(' | '):'';
 document.getElementById('mode-banner').textContent=demo?'DEMONSTRATION MODE · Every market row is synthetic. This is a paper journal, not an instruction to buy these items.':'MANUAL JOURNAL MODE · Research uses separately captured public data when available. No account-qualified recommendations. Your inputs are records, not verified account data.';
 const metrics=[['Free cash',gp(s.free_cash),'Recorded, not a game-account read'],['Committed at cost',gp(s.committed_at_cost),'Includes buy escrow and unsold items'],['Realized net result',gp(s.realized_profit),'Unsold value changes are excluded'],['Recorded slots',`${s.active_slots} / 8`,'Completed offers remain until collected']];
 document.getElementById('metrics').innerHTML=metrics.map(m=>`<article class="metric"><small>${m[0]}</small><strong>${m[1]}</strong><em>${m[2]}</em></article>`).join('');
 document.getElementById('market-note').textContent=demo?'Example prices and volumes illustrate the workflow. Tax rules and purchase limits here are synthetic fixtures, not a verified current item registry.':'The research view reads only captured public responses. Source access and rules must be reviewed before calculations are enabled; no demo prices substitute for missing public data.';
 document.getElementById('legacy-demo').classList.toggle('hidden',!demo);
 document.getElementById('market-body').innerHTML=current.opportunities.length?current.opportunities.map((r,i)=>`<tr><td>${escapeText(r.name)}<span class="details">Synthetic ID ${r.item_id} · ${units(r.low_units)} / ${units(r.high_units)} example 5m units</span></td><td>${units(r.buy)}</td><td>${units(r.sell)}</td><td>${units(r.quantity)}</td><td>${gp(r.cost)}</td><td>${gp(r.conditional_profit)}</td><td><button data-scenario="${i}">Use in demo</button></td></tr>`).join(''):'<tr><td colspan="7" class="empty">No supported market scenarios. There are no substituted or invented live quotes.</td></tr>';
 document.querySelectorAll('[data-scenario]').forEach(b=>b.addEventListener('click',()=>{const r=current.opportunities[Number(b.dataset.scenario)],f=document.getElementById('place-form');for(const [k,v]of Object.entries({side:'BUY',item_id:r.item_id,name:r.name,quantity:r.quantity,price:r.buy}))f.elements[k].value=v;f.elements.allowance_checked.checked=false;f.elements.record_external_action.checked=false;f.elements.exception_reason.value='';view('offers');notify('Demo form prefilled. Nothing has been recorded or placed in OSRS.');}));
 const offers=Object.values(current.state.offers), open=offers.filter(o=>o.status!=='CLOSED');
 const picker=document.getElementById('offer-picker'),old=picker.value;
 picker.innerHTML=open.map(o=>`<option value="${escapeText(o.id)}">${escapeText(o.side+' '+o.name)} — ${o.status} (${o.filled}/${o.quantity})</option>`).join('');if(open.some(o=>o.id===old))picker.value=old;
 document.getElementById('offer-cards').innerHTML=open.length?open.map(o=>`<article class="offer"><span class="pill">${o.status}</span><h3>${o.side} · ${escapeText(o.name)}</h3><p>Filled ${units(o.filled)} of ${units(o.quantity)} · Limit ${gp(o.price)}</p><p>Buy escrow ${gp(o.escrow)} · Claimable net cash ${gp(o.claimable)}</p><p>Uncollected bought units ${units(o.pending_qty)} · Reserved sell units ${units(o.reserved_qty)}</p><code>${escapeText(o.id)}</code></article>`).join(''):'<p class="empty">No recorded open offers.</p>';
 document.getElementById('inventory').innerHTML=s.inventory.length?s.inventory.map(i=>`<div class="result-line"><span>${escapeText(i.name)} · ${units(i.quantity)} units</span><strong>${gp(i.cost)} cost</strong></div>`).join(''):'<p class="empty">No collected inventory.</p>';
 const closed=offers.filter(o=>o.status==='CLOSED'),zero=closed.filter(o=>o.filled===0),complete=closed.filter(o=>o.filled===o.quantity);
 const lines=[['Starting recorded cash',gp(current.state.initial_cash)],['Free cash',gp(s.free_cash)],['Unspent buy escrow',gp(s.escrow)],['Claimable NET sale proceeds',gp(s.claimable_net_cash)],['Unsold items at acquisition cost',gp(s.inventory_cost)],['Accounting total AT COST (not market equity)',gp(s.accounting_total_at_cost)],['Realized result from recorded fills',gp(s.realized_profit)],['Recorded attempts / fully filled closed offers',`${offers.length} / ${complete.length}`],['Closed zero-fill attempts',String(zero.length)]];
 document.getElementById('results-content').innerHTML='<div class="panel">'+lines.map(l=>`<div class="result-line"><span>${l[0]}</span><strong>${l[1]}</strong></div>`).join('')+'</div>';
 if(window.refreshResearch)await window.refreshResearch();
}
async function save(form,event){
 const btn=form.querySelector('button[type="submit"]');btn.disabled=true;
 // Retry of an identical form submission reuses the stable ID; changed data gets a new ID.
 const fingerprint=JSON.stringify(event);if(form.dataset.fingerprint!==fingerprint){form.dataset.eventId=crypto.randomUUID();form.dataset.fingerprint=fingerprint;}
 try{const response=await fetch('/api/event',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify({...event,id:form.dataset.eventId})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Save failed.');await refresh();delete form.dataset.eventId;delete form.dataset.fingerprint;if(form.id==='place-form'){form.elements.allowance_checked.checked=false;form.elements.record_external_action.checked=false;form.elements.exception_reason.value='';form.elements.decision_id.value='';}notify('Saved to the local journal only. No game action was performed.');}
 catch(error){notify(error.message,true);}finally{btn.disabled=false;}
}
document.getElementById('place-form').addEventListener('submit',e=>{e.preventDefault();const f=e.target;try{save(f,{type:'place',side:f.elements.side.value,item_id:numeric(f,'item_id'),name:f.elements.name.value,quantity:numeric(f,'quantity'),price:numeric(f,'price'),allowance_checked:f.elements.allowance_checked.checked,record_external_action:f.elements.record_external_action.checked,exception_reason:f.elements.exception_reason.value,...(f.elements.decision_id.value?{decision_id:f.elements.decision_id.value}:{})});}catch(error){notify(error.message,true);}});
document.getElementById('event-form').addEventListener('submit',e=>{e.preventDefault();const f=e.target;try{const event={type:f.elements.type.value,offer_id:f.elements.offer_id.value};if(event.type==='fill'){event.quantity=numeric(f,'quantity');event.amount=numeric(f,'amount');event.amount_basis='ACTUAL_NET';}save(f,event);}catch(error){notify(error.message,true);}});
document.getElementById('event-type').addEventListener('change',e=>{document.getElementById('fill-fields').classList.toggle('hidden',e.target.value!=='fill');document.getElementById('cash-help').classList.toggle('hidden',e.target.value!=='fill');});
refresh().catch(e=>notify(e.message,true));
