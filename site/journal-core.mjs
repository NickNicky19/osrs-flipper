// Private browser journal. Its records never enter public data.json or the public model.
export const JOURNAL_SCHEMA = "osrs-private-offer-events-1";
export const BACKUP_SCHEMA = "osrs-private-backup-1";
const MAX_AMOUNT = 9_000_000_000_000_000n;
const ACTUAL_PILOT_CEILING = 50_000_000n;

function amount(value, label, minimum=0n) {
  if (typeof value !== "string" || !/^\d+$/.test(value)) throw new Error(label+" must be an exact nonnegative decimal string.");
  const result=BigInt(value);
  if (result<minimum||result>MAX_AMOUNT) throw new Error(label+" is outside the supported range.");
  return result;
}
function ident(value,label) {
  if (typeof value!=="string"||!/^[-A-Za-z0-9_:.]{1,100}$/.test(value)) throw new Error("Invalid "+label+".");
  return value;
}
function timestamp(value,label){if(!Number.isSafeInteger(value)||value<1)throw new Error("Invalid "+label+".");return value;}
function itemName(value){if(typeof value!=="string"||!value.trim()||value.length>160)throw new Error("Invalid item name.");return value.trim();}

export function createPortfolio(openingCashGp,asOf,portfolioId="default") {
  amount(openingCashGp,"Opening cash",1n);timestamp(asOf,"opening date");ident(portfolioId,"portfolio ID");
  return {schema:JOURNAL_SCHEMA,id:portfolioId,openingCashGp,asOf,createdAt:Math.floor(Date.now()/1000),revision:0};
}

export function canonical(value) {
  if(Array.isArray(value))return "["+value.map(canonical).join(",")+"]";
  if(value&&typeof value==="object")return "{"+Object.keys(value).sort().map(k=>JSON.stringify(k)+":"+canonical(value[k])).join(",")+"}";
  return JSON.stringify(value);
}

export function replayJournal(portfolio,rawEvents) {
  if(!portfolio||portfolio.schema!==JOURNAL_SCHEMA)throw new Error("Unsupported private journal version.");
  ident(portfolio.id,"portfolio ID");timestamp(portfolio.asOf,"opening date");
  const opening=amount(portfolio.openingCashGp,"Opening cash",1n);
  if(!Array.isArray(rawEvents)||rawEvents.length>50_000)throw new Error("Invalid or oversized private event list.");
  const seen=new Map(),events=[];
  for(const event of rawEvents){
    if(!event||typeof event!=="object")throw new Error("Invalid private event.");
    ident(event.id,"event ID");
    if(event.portfolioId!==portfolio.id)throw new Error("Private event belongs to another portfolio.");
    timestamp(event.occurredAt,"event time");timestamp(event.recordedAt,"recorded time");
    const body=canonical(event);
    if(seen.has(event.id)){if(seen.get(event.id)!==body)throw new Error("Conflicting duplicate private event ID.");continue;}
    seen.set(event.id,body);events.push(event);
  }
  const voided=new Set();
  const order=new Map(events.map((event,index)=>[event.id,index]));
  for(const [index,event] of events.entries()){
    if(event.type!=="CORRECTION")continue;
    ident(event.targetEventId,"corrected event ID");
    if(!events.slice(0,index).some(x=>x.id===event.targetEventId)||event.targetEventId===event.id||voided.has(event.targetEventId)||
       events.find(x=>x.id===event.targetEventId)?.type==="CORRECTION")throw new Error("Invalid or duplicate correction target.");
    if(typeof event.reason!=="string"||event.reason.trim().length<5||event.reason.length>500)throw new Error("Correction needs a factual reason.");
    voided.add(event.targetEventId);
  }
  let cash=opening,claimable=0n,realized=0n,cashFlows=0n,openingInventoryBasis=0n;
  const offers=new Map(),lots=[],warnings=[],byItem=new Map();
  const totals=(itemId,name)=>{let row=byItem.get(itemId);if(!row){row={itemId,name,bought:0n,sold:0n};byItem.set(itemId,row);}return row;};
  const take=(itemId,quantity,at)=>{
    let remaining=quantity,cost=0n;
    for(const lot of lots){
      if(lot.itemId!==itemId||remaining===0n)continue;
      if(lot.acquiredAt>at)throw new Error("Sale or offer predates collected inventory.");
      const count=remaining<lot.quantity?remaining:lot.quantity;
      const used=lot.cost*count/lot.quantity;
      lot.quantity-=count;lot.cost-=used;remaining-=count;cost+=used;
    }
    if(remaining!==0n)throw new Error("Not enough collected inventory for sale.");
    for(let i=lots.length-1;i>=0;i--)if(lots[i].quantity===0n)lots.splice(i,1);
    return cost;
  };
  const saleNet=event=>{
    if(event.proceedsBasis==="ACTUAL_NET")return amount(event.netReceivedGp,"Actual net sale proceeds");
    if(event.proceedsBasis==="GROSS_WITH_EXACT_TAX"){
      const gross=amount(event.grossGp,"Gross sale total"),tax=amount(event.taxGp,"Exact sale tax");
      if(tax>gross)throw new Error("Tax exceeds gross proceeds.");
      if(typeof event.taxEvidence!=="string"||event.taxEvidence.trim().length<5||event.taxEvidence.length>300)throw new Error("Gross proceeds require a tax evidence note.");
      return gross-tax;
    }
    throw new Error("Sale proceeds basis must be explicit.");
  };
  for(const event of events.filter(e=>e.type!=="CORRECTION"&&!voided.has(e.id))
                            .sort((a,b)=>a.occurredAt-b.occurredAt||order.get(a.id)-order.get(b.id))){
    if(event.occurredAt<portfolio.asOf)throw new Error("Event predates the journal opening state.");
    if(event.occurredAt>event.recordedAt+300)warnings.push("EVENT_TIME_AFTER_RECORDING:"+event.id);
    if(event.linkedPickId!==undefined&&event.linkedPickId!==null)ident(event.linkedPickId,"public pick link");
    switch(event.type){
      case "OPENING_LOT": {
        if(event.occurredAt!==portfolio.asOf)throw new Error("Opening stock must use the journal as-of time.");
        if(!Number.isSafeInteger(event.itemId)||event.itemId<1)throw new Error("Invalid item ID.");
        const name=itemName(event.itemName),quantity=amount(event.quantity,"Opening quantity",1n);
        const cost=amount(event.costBasisGp,"Opening acquisition cost",1n);
        if(cost<quantity)throw new Error("Opening cost is below one GP per unit.");
        lots.push({itemId:event.itemId,name,quantity,cost,acquiredAt:event.occurredAt});
        openingInventoryBasis+=cost;totals(event.itemId,name);
        warnings.push("OPENING_STOCK_SELF_REPORTED:"+event.id);
        break;
      }
      case "QUICK_BUY": {
        if(!Number.isSafeInteger(event.itemId)||event.itemId<1)throw new Error("Invalid item ID.");
        const name=itemName(event.itemName),quantity=amount(event.quantity,"Purchased quantity",1n);
        const paid=amount(event.totalPaidGp,"Exact purchase total",1n);
        if(paid<quantity)throw new Error("Purchase cost is below one GP per unit.");
        cash-=paid;lots.push({itemId:event.itemId,name,quantity,cost:paid,acquiredAt:event.occurredAt});
        totals(event.itemId,name).bought+=quantity;
        if(paid>ACTUAL_PILOT_CEILING)warnings.push("FACTUAL_BUY_OVER_50M_PILOT:"+event.id);
        break;
      }
      case "QUICK_SELL": {
        if(!Number.isSafeInteger(event.itemId)||event.itemId<1)throw new Error("Invalid item ID.");
        const name=itemName(event.itemName),quantity=amount(event.quantity,"Sold quantity",1n);
        const cost=take(event.itemId,quantity,event.occurredAt),net=saleNet(event);
        cash+=net;realized+=net-cost;
        totals(event.itemId,name).sold+=quantity;
        break;
      }
      case "DEPOSIT": case "WITHDRAWAL": {
        const gp=amount(event.amountGp,"Cash flow",1n);
        cash+=event.type==="DEPOSIT"?gp:-gp;
        cashFlows+=event.type==="DEPOSIT"?gp:-gp;
        break;
      }
      case "PLACE": {
        ident(event.offerId,"offer ID");
        if(offers.has(event.offerId))throw new Error("Offer ID already recorded.");
        if(event.side!=="BUY"&&event.side!=="SELL")throw new Error("Invalid offer side.");
        if(!Number.isSafeInteger(event.itemId)||event.itemId<1)throw new Error("Invalid item ID.");
        const name=itemName(event.itemName),quantity=amount(event.quantity,"Offer quantity",1n);
        const unit=amount(event.unitPriceGp,"Offer price",1n);
        const offer={id:event.offerId,side:event.side,itemId:event.itemId,name,quantity,unit,
          filled:0n,escrow:0n,claimable:0n,pendingQty:0n,pendingCost:0n,
          reservedQty:0n,reservedCost:0n,status:"OPEN",placedAt:event.occurredAt};
        if(event.side==="BUY"){
          const commitment=quantity*unit;
          if(commitment>MAX_AMOUNT)throw new Error("Offer commitment exceeds supported range.");
          cash-=commitment;offer.escrow=commitment;
          if(commitment>ACTUAL_PILOT_CEILING)warnings.push("FACTUAL_BUY_OVER_50M_PILOT:"+event.offerId);
          if(event.allowanceVerified!==true)warnings.push("PERSONAL_ALLOWANCE_UNVERIFIED:"+event.offerId);
        }else{
          offer.reservedCost=take(event.itemId,quantity,event.occurredAt);offer.reservedQty=quantity;
        }
        if([...offers.values()].filter(o=>o.status!=="CLOSED").length>=8)warnings.push("MORE_THAN_EIGHT_RECORDED_OPEN_OFFERS");
        offers.set(event.offerId,offer);break;
      }
      case "FILL": {
        const offer=offers.get(ident(event.offerId,"offer ID"));
        if(!offer||offer.status!=="OPEN")throw new Error("Fill has no open offer.");
        if(event.occurredAt<offer.placedAt)throw new Error("Fill predates its offer.");
        const quantity=amount(event.quantity,"Fill quantity",1n);
        if(offer.filled+quantity>offer.quantity)throw new Error("Fill exceeds offer quantity.");
        if(offer.side==="BUY"){
          const paid=amount(event.totalPaidGp,"Exact purchase total",1n);
          if(paid<quantity||paid>quantity*offer.unit||paid>offer.escrow)throw new Error("Purchase total conflicts with offer limit or escrow.");
          offer.escrow-=paid;offer.pendingQty+=quantity;offer.pendingCost+=paid;
          totals(offer.itemId,offer.name).bought+=quantity;
        }else{
          const net=saleNet(event);
          const cost=offer.reservedCost*quantity/offer.reservedQty;
          offer.reservedCost-=cost;offer.reservedQty-=quantity;
          offer.claimable+=net;realized+=net-cost;
          totals(offer.itemId,offer.name).sold+=quantity;
        }
        offer.filled+=quantity;
        if(offer.filled===offer.quantity)offer.status="COMPLETE";
        break;
      }
      case "CANCEL": {
        const offer=offers.get(ident(event.offerId,"offer ID"));
        if(!offer||offer.status!=="OPEN")throw new Error("Only an open offer can be cancelled.");
        if(event.occurredAt<offer.placedAt)throw new Error("Cancellation predates its offer.");
        offer.status="CANCELLED";break;
      }
      case "COLLECT": {
        const offer=offers.get(ident(event.offerId,"offer ID"));
        if(!offer||offer.status==="CLOSED")throw new Error("Nothing to collect for this offer.");
        if(event.occurredAt<offer.placedAt)throw new Error("Collection predates its offer.");
        let changed=false;
        if(offer.side==="BUY"){
          if(offer.pendingQty){lots.push({itemId:offer.itemId,name:offer.name,quantity:offer.pendingQty,cost:offer.pendingCost,acquiredAt:event.occurredAt});
            offer.pendingQty=offer.pendingCost=0n;changed=true;}
          const required=offer.status==="OPEN"?(offer.quantity-offer.filled)*offer.unit:0n;
          const refund=offer.escrow-required;
          if(refund<0n)throw new Error("Buy escrow below remaining offer commitment.");
          cash+=refund;offer.escrow-=refund;changed=changed||refund>0n;
        }else{
          cash+=offer.claimable;changed=offer.claimable>0n;offer.claimable=0n;
          if(offer.status==="CANCELLED"&&offer.reservedQty){
            lots.push({itemId:offer.itemId,name:offer.name,quantity:offer.reservedQty,cost:offer.reservedCost,acquiredAt:event.occurredAt});
            offer.reservedQty=offer.reservedCost=0n;changed=true;
          }
        }
        if(offer.status==="COMPLETE"||offer.status==="CANCELLED"){offer.status="CLOSED";changed=true;}
        if(!changed)throw new Error("No items, proceeds, or refund to collect.");
        break;
      }
      case "REPRICE": {
        const offer=offers.get(ident(event.offerId,"offer ID"));
        if(!offer||offer.status!=="OPEN")throw new Error("Price change needs an open offer.");
        if(event.occurredAt<offer.placedAt)throw new Error("Price change predates its offer.");
        const next=amount(event.newUnitPriceGp,"New offer price",1n);
        const remaining=offer.quantity-offer.filled;
        if(offer.side==="BUY"){
          const change=(next-offer.unit)*remaining;
          cash-=change;offer.escrow+=change;
          if(offer.escrow<0n||offer.escrow>MAX_AMOUNT)throw new Error("Price change exceeds supported escrow.");
          if(next*remaining>ACTUAL_PILOT_CEILING)warnings.push("FACTUAL_BUY_OVER_50M_PILOT:"+event.offerId);
        }
        offer.unit=next;
        break;
      }
      case "REPRICE_NOTE": {
        const offer=offers.get(ident(event.offerId,"offer ID"));
        if(!offer||offer.status!=="OPEN")throw new Error("Price note needs an open offer.");
        if(event.occurredAt<offer.placedAt)throw new Error("Price note predates its offer.");
        amount(event.newUnitPriceGp,"New noted price",1n);
        warnings.push("PRICE_CHANGE_REQUIRES_RECONCILED_OFFER_EVENTS:"+event.offerId);
        break;
      }
      default: throw new Error("Unsupported private event type.");
    }
  }
  const escrow=[...offers.values()].reduce((n,o)=>n+o.escrow,0n);
  const claim=[...offers.values()].reduce((n,o)=>n+o.claimable,0n);
  const pending=[...offers.values()].reduce((n,o)=>n+o.pendingCost+o.reservedCost,0n);
  const inventoryCost=lots.reduce((n,l)=>n+l.cost,0n);
  if(cash+escrow+claim+pending+inventoryCost!==opening+openingInventoryBasis+cashFlows+realized)throw new Error("Private cash/inventory conservation failed.");
  if(escrow+pending+inventoryCost>ACTUAL_PILOT_CEILING)warnings.push("RECORDED_AGGREGATE_EXPOSURE_OVER_50M_PILOT");
  const inventory=[...byItem.values()].map(row=>{
    const owned=lots.filter(l=>l.itemId===row.itemId);
    const reserved=[...offers.values()].filter(o=>o.itemId===row.itemId);
    const quantity=owned.reduce((n,l)=>n+l.quantity,0n)+reserved.reduce((n,o)=>n+o.pendingQty+o.reservedQty,0n);
    const cost=owned.reduce((n,l)=>n+l.cost,0n)+reserved.reduce((n,o)=>n+o.pendingCost+o.reservedCost,0n);
    return {itemId:row.itemId,name:row.name,bought:row.bought.toString(),sold:row.sold.toString(),
      remaining:quantity.toString(),remainingCostGp:cost.toString()};
  });
  const activeOffers=[...offers.values()].filter(o=>o.status!=="CLOSED");
  if(cash<0n)warnings.push("CASH_UNRECONCILED_NEGATIVE");
  return {schema:JOURNAL_SCHEMA,portfolioId:portfolio.id,eventCount:events.length,
    openingCashGp:opening.toString(),openingInventoryBasisGp:openingInventoryBasis.toString(),cashFlowGp:cashFlows.toString(),
    availableCashGp:cash.toString(),escrowGp:escrow.toString(),claimableGp:claim.toString(),
    exposureGp:(escrow+pending+inventoryCost).toString(),
    realizedGp:realized.toString(),inventoryCostGp:inventoryCost.toString(),
    pendingCostGp:pending.toString(),inventory,activeOffers:activeOffers.map(o=>({id:o.id,
      itemId:o.itemId,name:o.name,side:o.side,status:o.status,filled:o.filled.toString(),
      quantity:o.quantity.toString(),escrowGp:o.escrow.toString(),claimableGp:o.claimable.toString()})),
    occupiedSlots:activeOffers.length,attemptsRecorded:offers.size,
    zeroFillAttempts:[...offers.values()].filter(o=>o.filled===0n&&o.status==="CLOSED").length,
    partialFillAttempts:[...offers.values()].filter(o=>o.filled>0n&&o.filled<o.quantity&&o.status==="CLOSED").length,
    fullyReconciled:cash>=0n&&warnings.every(w=>!w.startsWith("PRICE_CHANGE")),
    warnings,scope:"Self-reported records on this browser; not complete game-account equity."};
}
