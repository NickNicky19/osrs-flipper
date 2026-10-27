import {allocatePlans, DEFAULTS, parseGp, validateSettings} from "./planner.mjs";
import {createPortfolio, replayJournal} from "./journal-core.mjs";
import {appendJournalEvents, createJournal, exportJournalBackup, importJournalBackup, readJournal, requestDurableStorage} from "./journal-db.mjs";

const $=id=>document.getElementById(id);
const SETTINGS_KEY="osrs-flip-lab-planning-v1";
const gp=value=>Number(value).toLocaleString("en-US")+" GP";
const node=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;};
const put=(parent,tag,text,cls)=>{const e=node(tag,text,cls);parent.append(e);return e;};
const val=(form,name)=>String(form.elements.namedItem(name)?.value??"").trim();
const at=value=>{const ms=new Date(value).getTime();if(!Number.isFinite(ms)||ms<=0||ms>Date.now()+300_000)throw new Error("Enter a real date and time no later than now.");return Math.floor(ms/1000);};
const whole=(value,label,{zero=false}={})=>{try{return String(parseGp(value,{allowZero:zero}));}catch{throw new Error(label+" must be exact whole GP or units within the supported range.");}};
const eventBase=(portfolioId,occurredAt)=>({id:crypto.randomUUID(),portfolioId,occurredAt,recordedAt:Math.floor(Date.now()/1000)});
let data=null,settings={...DEFAULTS},journalRecord=null,journalResult=null,storageError=null,lastItemArtifact=null,channel=null,currentFresh=false;
const pendingForms=new WeakSet();

function message(id,text,bad=false){const el=$(id);el.textContent=text;el.className=bad?"small message-error":"small";}
function showError(error,id){message(id,error?.message||"The change could not be saved.",true);}
function itemFrom(value){
  const raw=String(value||"").trim();
  const match=raw.match(/^(.*?)\s*\(#(\d+)\)$/)||raw.match(/^#?(\d+)$/);
  if(!match)throw new Error("Choose an exact item from the list, including its item ID.");
  const id=Number(match[2]??match[1]);
  if(!Number.isSafeInteger(id)||id<1)throw new Error("Invalid item ID.");
  const rows=data?.screener||[];
  const found=rows.find(r=>r.item_id===id);
  const prior=journalResult?.inventory.find(r=>r.itemId===id);
  const name=found?.name||prior?.name||(match[2]?match[1].trim():null);
  if(!name)throw new Error("Item ID is not in the public mapping or recorded inventory; include its name and ID.");
  if(match[2]&&match[1].trim()&&found&&match[1].trim()!==found.name)throw new Error("Item name and ID do not match the public mapping.");
  return {itemId:id,itemName:name};
}

function linkedPick(form){const id=val(form,"linkedPickId");return id?{linkedPickId:id}:{};}
function saleFields(form){
  const basis=val(form,"proceedsBasis"),amount=whole(val(form,"saleAmountGp"),"Sale amount",{zero:true});
  if(basis==="ACTUAL_NET")return {proceedsBasis:basis,netReceivedGp:amount};
  if(basis==="GROSS_WITH_EXACT_TAX")return {proceedsBasis:basis,grossGp:amount,
    taxGp:whole(val(form,"taxGp"),"Exact tax",{zero:true}),taxEvidence:val(form,"taxEvidence")};
  throw new Error("Choose an exact sale proceeds basis.");
}

function settingsFromForm(form){
  const percent=Number(val(form,"minRoiPercent"));
  if(!Number.isFinite(percent)||percent<0||!Number.isSafeInteger(percent*100))throw new Error("ROI preference must be a nonnegative hundredth of a percent.");
  return validateSettings({...DEFAULTS,budgetGp:parseGp(val(form,"budgetGp")),
    minTotalGp:parseGp(val(form,"minTotalGp"),{allowZero:true}),
    minUnitGp:parseGp(val(form,"minUnitGp"),{allowZero:true}),
    minRoiBps:percent*100,documentedCommitmentsGp:parseGp(val(form,"documentedCommitmentsGp"),{allowZero:true}),
    occupiedSlots:Number(val(form,"occupiedSlots")),maxPositionGp:parseGp(val(form,"maxPositionGp")),
    maxSlots:Number(val(form,"maxSlots")),reviewHorizonSeconds:Number(val(form,"reviewHorizonSeconds")),
    useJournalCash:form.elements.namedItem("useJournalCash").checked});
}

function fillSettingsForm(){
  const form=$("planning-form");
  for(const key of ["budgetGp","minTotalGp","minUnitGp","documentedCommitmentsGp","occupiedSlots","maxPositionGp","maxSlots","reviewHorizonSeconds"])
    form.elements.namedItem(key).value=settings[key];
  form.elements.namedItem("minRoiPercent").value=(settings.minRoiBps/100).toString();
  form.elements.namedItem("useJournalCash").checked=settings.useJournalCash;
}

function loadSettings(){
  try{
    const raw=localStorage.getItem(SETTINGS_KEY);
    if(raw){const parsed=JSON.parse(raw);settings=validateSettings(parsed);}
  }catch{settings={...DEFAULTS};message("planning-message","Saved planning settings could not be read; using defaults in this tab.",true);}
  fillSettingsForm();
}

function refreshItems(next){
  if(!next||next.artifact_id===lastItemArtifact)return;
  lastItemArtifact=next.artifact_id;
  const datalist=$("item-options");datalist.replaceChildren();
  const rows=next.screener||[];
  const fragment=document.createDocumentFragment();
  for(const row of rows){const option=node("option",row.name+" (#"+row.item_id+")");option.value=row.name+" (#"+row.item_id+")";fragment.append(option);}
  datalist.append(fragment);
}

function renderPlanning(fresh){
  const summary=$("personal-summary"),box=$("personal-picks"),coverage=$("personal-coverage");box.replaceChildren();
  if(!data){summary.textContent="Public candidate data is unavailable.";coverage.textContent="";return;}
  const c=data.coverage||{};
  coverage.textContent=`Exported ${data.screener?.length||0} mapped rows; ${c.rule_supported??"UNKNOWN"} rule supported, ${c.fresh_two_sided??"UNKNOWN"} fresh two-sided, ${c.eligible??"UNKNOWN"} research eligible. Rejection flags can overlap. Public benchmark and issued picks are unchanged.`;
  if(!fresh){summary.textContent="HISTORICAL SNAPSHOT · No current personal entry plan. Existing market observations are retained for inspection.";return;}
  const result=allocatePlans(data.screener||[],settings,Math.floor(Date.now()/1000),{journal:journalResult});
  summary.textContent=(result.plans.length?`${result.plans.length} conditional plan(s). `:"No qualifying major opportunity in the supported current data. ")+
    `${result.considered} current candidates considered; ${result.ranked??0} with positive feasible arithmetic. ${result.initialCash===undefined?"Journal cash requires reconciliation.":"Shared new cash "+gp(result.initialCash)+", remaining "+gp(result.cashRemaining)+"."} ${result.scope||""}`;
  for(const plan of result.plans){
    const card=put(box,"article",plan.name+" · "+gp(plan.conditionalTotal)+" conditional net","personal-card");
    put(card,"p",`Proposed buy offer up to ${gp(plan.buy)}; proposed later sell offer ${gp(plan.sell)}. Quantity ${plan.quantity.toLocaleString()}; cash required ${gp(plan.requiredCash)}; net ${gp(plan.netPerUnit)} per unit after ${gp(plan.taxPerUnit)} tax; conditional ROI ${(plan.conditionalRoiBps/100).toFixed(2)}%.`);
    put(card,"p",`Observed instant-sell and instant-buy sides are ${Math.floor(Date.now()/1000-plan.lowTime)}s / ${Math.floor(Date.now()/1000-plan.highTime)}s old. Entry validity ends in ${Math.max(0,Math.min(plan.lowTime+300,plan.highTime+300)-Math.floor(Date.now()/1000))}s; review preference ${settings.reviewHorizonSeconds/60}m does not extend it.`);
    put(card,"p",`Binding: ${plan.binding.join(", ")}. One-GP worse buy: ${gp(plan.oneGpAdverseBuy)}; one-GP worse sell: ${gp(plan.oneGpAdverseSell)}. Exact fills and personal allowance: UNKNOWN.`);
    const button=put(card,"button","Record my trade (actual values required)");button.type="button";
    button.addEventListener("click",()=>{$("journal-quick-form").elements.namedItem("item").value=plan.name+" (#"+plan.itemId+")";
      $("my-trades").scrollIntoView({behavior:"smooth"});});
  }
  if(result.smaller?.length)put(box,"p",`${result.smaller.length} smaller or resource-limited candidate(s) remain in the full screener. Preference exclusions: ${JSON.stringify(result.excluded)}.`,"small");
}

function renderJournal(){
  const banner=$("journal-banner"),summary=$("journal-summary"),inventory=$("journal-inventory"),events=$("journal-events");
  summary.replaceChildren();inventory.replaceChildren();events.replaceChildren();
  if(storageError){banner.textContent="PRIVATE STORAGE UNAVAILABLE · "+storageError+" No record was saved.";banner.className="notice notice-error";return;}
  if(!journalRecord){journalResult=null;banner.textContent="No private journal on this browser. Create an opening state, then export backups regularly.";banner.className="notice";put(summary,"p","Actual GP and positions are not yet recorded on this browser.");return;}
  banner.textContent="Private journal saved on this browser/device. Not synced. Export a backup; cleared storage or device loss can erase it.";banner.className="notice";
  journalResult=replayJournal(journalRecord.portfolio,journalRecord.events);
  put(summary,"h3","Self-reported actual records");
  put(summary,"p",`Recorded realized after-tax flip GP: ${gp(journalResult.realizedGp)}. Available recorded cash: ${gp(journalResult.availableCashGp)}. Cost tied in remaining stock: ${gp(journalResult.inventoryCostGp)}; offer escrow: ${gp(journalResult.escrowGp)}; uncollected proceeds: ${gp(journalResult.claimableGp)}. This is not complete in-game account equity.`);
  put(summary,"p",`Opening available cash: ${gp(journalResult.openingCashGp)} as of ${new Date(journalRecord.portfolio.asOf*1000).toLocaleString()}. Later deposits/withdrawals: ${gp(journalResult.cashFlowGp)} (not flip profit). ${journalResult.eventCount} events; ${journalResult.attemptsRecorded} recorded offer attempts, including ${journalResult.zeroFillAttempts} zero-fill and ${journalResult.partialFillAttempts} partial-fill closed attempts.`);
  if(journalResult.warnings.length)put(summary,"p","Warnings: "+journalResult.warnings.join(" · "),"message-error");
  for(const row of journalResult.inventory.filter(r=>rowNonzero(r)))put(inventory,"p",`${row.name} #${row.itemId}: ${row.remaining} units remain; acquisition cost ${gp(row.remainingCostGp)}.`);
  if(!inventory.children.length)put(inventory,"p","No remaining collected or reserved stock is recorded.");
  put(events,"h4","Recent event audit trail");
  for(const event of journalRecord.events.slice(-30).reverse())put(events,"p",`${event.type} · ${new Date(event.occurredAt*1000).toLocaleString()} · ${event.id}${event.itemId?" · #"+event.itemId:""}${event.targetEventId?" · corrects "+event.targetEventId:""}`);
  if(!journalRecord.events.length)put(events,"p","No trade or cash-flow events yet.");
}
function rowNonzero(row){return BigInt(row.remaining)>0n;}

async function reloadJournal(){
  try{journalRecord=await readJournal();storageError=null;renderJournal();renderPlanning(currentFresh);}
  catch(error){storageError=error.message;journalRecord=journalResult=null;renderJournal();}
}
async function saveEvents(events,form,messageId){
  if(pendingForms.has(form))return;
  pendingForms.add(form);const button=form.querySelector('button[type="submit"]');if(button)button.disabled=true;
  try{
    journalRecord=await appendJournalEvents(events);storageError=null;renderJournal();renderPlanning(currentFresh);
    form.reset();message(messageId,"Saved locally. Export a private backup after this session.");channel?.postMessage("changed");
  }catch(error){showError(error,messageId);}
  finally{pendingForms.delete(form);if(button)button.disabled=false;}
}

function onQuickSubmit(event){
  event.preventDefault();const form=event.currentTarget;
  try{
    if(!journalRecord)throw new Error("Create an opening portfolio first.");
    const item=itemFrom(val(form,"item")),buyAt=at(val(form,"buyAt"));
    const base=eventBase(journalRecord.portfolio.id,buyAt),link=linkedPick(form);
    const buy={...base,...item,...link,type:"QUICK_BUY",quantity:whole(val(form,"buyQuantity"),"Buy quantity"),totalPaidGp:whole(val(form,"buyPaidGp"),"Purchase total")};
    const saleQty=val(form,"sellQuantity"),saleAmount=val(form,"saleAmountGp");
    const additions=[buy];
    if(saleQty||saleAmount){
      if(!saleQty||!saleAmount||!val(form,"sellAt"))throw new Error("A partial sale needs quantity, amount and sale time.");
      const sellAt=at(val(form,"sellAt"));if(sellAt<buyAt)throw new Error("Sale cannot predate the purchase.");
      additions.push({...eventBase(journalRecord.portfolio.id,sellAt),...item,...link,type:"QUICK_SELL",quantity:whole(saleQty,"Sale quantity"),...saleFields(form)});
    }
    void saveEvents(additions,form,"journal-quick-message");
  }catch(error){showError(error,"journal-quick-message");}
}

function onSellSubmit(event){
  event.preventDefault();const form=event.currentTarget;
  try{
    if(!journalRecord)throw new Error("Create an opening portfolio first.");
    const item=itemFrom(val(form,"item"));
    const sale={...eventBase(journalRecord.portfolio.id,at(val(form,"sellAt"))),...item,...linkedPick(form),type:"QUICK_SELL",
      quantity:whole(val(form,"quantity"),"Sale quantity"),...saleFields(form)};
    void saveEvents([sale],form,"journal-sell-message");
  }catch(error){showError(error,"journal-sell-message");}
}

function onAdvancedSubmit(event){
  event.preventDefault();const form=event.currentTarget;
  try{
    if(!journalRecord)throw new Error("Create an opening portfolio first.");
    const type=val(form,"type"),base=eventBase(journalRecord.portfolio.id,
      type==="OPENING_LOT"?journalRecord.portfolio.asOf:at(val(form,"occurredAt")));
    let record={...base,type};
    if(type==="OPENING_LOT")record={...record,...itemFrom(val(form,"item")),
      quantity:whole(val(form,"quantity"),"Opening quantity"),costBasisGp:whole(val(form,"moneyGp"),"Opening acquisition cost")};
    else if(type==="DEPOSIT"||type==="WITHDRAWAL")record.amountGp=whole(val(form,"moneyGp"),"Cash flow");
    else if(type==="CORRECTION"){record.targetEventId=val(form,"targetEventId");record.reason=val(form,"note");}
    else if(type==="PLACE")record={...record,...itemFrom(val(form,"item")),offerId:val(form,"offerId"),side:val(form,"side"),
      quantity:whole(val(form,"quantity"),"Offer quantity"),unitPriceGp:whole(val(form,"unitPriceGp"),"Offer price")};
    else if(type==="FILL"){
      const offerId=val(form,"offerId"),offer=journalResult?.activeOffers.find(o=>o.id===offerId);
      if(!offer)throw new Error("Choose an existing open offer ID for a fill.");
      record={...record,offerId,quantity:whole(val(form,"quantity"),"Fill quantity")};
      if(offer.side==="BUY"){
        if(val(form,"taxGp"))throw new Error("Tax is not a buy-fill field.");
        record.totalPaidGp=whole(val(form,"moneyGp"),"Purchase total");
      }else if(val(form,"proceedsBasis")==="ACTUAL_NET"){
        if(val(form,"taxGp"))throw new Error("Actual net proceeds must not have tax deducted again.");
        record.proceedsBasis="ACTUAL_NET";record.netReceivedGp=whole(val(form,"moneyGp"),"Net sale proceeds",{zero:true});
      }else{
        record.proceedsBasis="GROSS_WITH_EXACT_TAX";record.grossGp=whole(val(form,"moneyGp"),"Gross sale proceeds",{zero:true});
        record.taxGp=whole(val(form,"taxGp"),"Exact tax",{zero:true});record.taxEvidence=val(form,"note");
      }
    }
    else if(type==="CANCEL"||type==="COLLECT")record.offerId=val(form,"offerId");
    else if(type==="REPRICE"||type==="REPRICE_NOTE"){
      record.offerId=val(form,"offerId");record.newUnitPriceGp=whole(val(form,"newUnitPriceGp"),"New offer price");}
    void saveEvents([record],form,"journal-event-message");
  }catch(error){showError(error,"journal-event-message");}
}

export function renderPersonalUI(next,{fresh=false}={}){data=next;currentFresh=fresh;refreshItems(next);renderPlanning(fresh);}

export function initPersonalUI(){
  loadSettings();
  $("planning-form").addEventListener("submit",event=>{
    event.preventDefault();try{
      settings=settingsFromForm(event.currentTarget);
      try{localStorage.setItem(SETTINGS_KEY,JSON.stringify(settings));message("planning-message","Saved on this browser/device; the public benchmark is unchanged.");}
      catch{message("planning-message","Planning settings are active in this tab but local storage could not save them.",true);}
      renderPlanning(currentFresh);
    }catch(error){showError(error,"planning-message");}
  });
  for(const button of document.querySelectorAll("[data-min-preset]"))button.addEventListener("click",()=>{
    $("planning-form").elements.namedItem("minTotalGp").value=button.dataset.minPreset;$("planning-form").requestSubmit();});
  $("journal-open-form").addEventListener("submit",async event=>{
    event.preventDefault();const form=event.currentTarget;
    if(pendingForms.has(form))return;
    pendingForms.add(form);const button=form.querySelector('button[type="submit"]');if(button)button.disabled=true;
    try{
      const opening=whole(val(form,"openingCashGp"),"Opening cash"),asOf=at(val(form,"asOf"));
      journalRecord=await createJournal(createPortfolio(opening,asOf,"portfolio-"+crypto.randomUUID()));
      const durable=await requestDurableStorage();storageError=null;renderJournal();
      $("journal-banner").textContent+=(durable?" Browser granted durable-storage request.":" Durable storage was not granted; keep exported backups.");
      channel?.postMessage("changed");
    }catch(error){storageError=null;showError(error,"journal-banner");}
    finally{pendingForms.delete(form);if(button)button.disabled=false;}
  });
  $("journal-quick-form").addEventListener("submit",onQuickSubmit);
  $("journal-sell-form").addEventListener("submit",onSellSubmit);
  $("journal-event-form").addEventListener("submit",onAdvancedSubmit);
  $("journal-export").addEventListener("click",async()=>{
    try{const text=await exportJournalBackup(),url=URL.createObjectURL(new Blob([text],{type:"application/json"}));
      const link=document.createElement("a");link.href=url;link.download="osrs-private-trades-"+new Date().toISOString().slice(0,10)+".json";link.click();
      setTimeout(()=>URL.revokeObjectURL(url),30000);message("journal-backup-message","Private backup downloaded. Keep it outside public GitHub.");
    }catch(error){showError(error,"journal-backup-message");}
  });
  $("journal-import").addEventListener("click",async()=>{
    try{const file=$("journal-import-file").files?.[0];if(!file)throw new Error("Choose a private backup file first.");
      if(file.size>8_000_000)throw new Error("Backup exceeds the supported size.");
      journalRecord=await importJournalBackup(await file.text());storageError=null;renderJournal();
      message("journal-backup-message","Backup validated and merged without deleting existing events.");channel?.postMessage("changed");
    }catch(error){showError(error,"journal-backup-message");}
  });
  if(globalThis.BroadcastChannel){channel=new BroadcastChannel("osrs-flip-lab-journal-changed");channel.onmessage=()=>void reloadJournal();}
  const now=new Date();now.setMinutes(now.getMinutes()-now.getTimezoneOffset());
  for(const id of ["journal-open-form","journal-quick-form","journal-sell-form","journal-event-form"]){
    const form=$(id);for(const field of form.querySelectorAll('input[type="datetime-local"]'))if(!field.value)field.value=now.toISOString().slice(0,16);
  }
  void reloadJournal();
}
