import test from "node:test";
import assert from "node:assert/strict";
import {createPortfolio,replayJournal,recordedAllowanceUsage} from "../journal-core.mjs";

const at=1_793_130_000;
const portfolio=createPortfolio("100000000",at);
const base={portfolioId:"default",itemId:4151,itemName:"Fixture item",occurredAt:at+1,recordedAt:at+2};
const buy={...base,id:"buy-1",type:"QUICK_BUY",quantity:"5000",totalPaidGp:"50000000"};
const gross={...base,id:"sell-1",type:"QUICK_SELL",quantity:"2000",proceedsBasis:"GROSS_WITH_EXACT_TAX",
  grossGp:"21000000",taxGp:"420000",taxEvidence:"Fixture stipulated 210 GP per unit"};
const net={...base,id:"sell-1",type:"QUICK_SELL",quantity:"2000",proceedsBasis:"ACTUAL_NET",netReceivedGp:"20580000"};

test("partial gross and already-net sales have identical exact P&L",()=>{
  const a=replayJournal(portfolio,[buy,gross]);
  const b=replayJournal(portfolio,[buy,net]);
  for(const x of [a,b]){
    assert.equal(x.realizedGp,"580000");
    assert.equal(x.inventory[0].remaining,"3000");
    assert.equal(x.inventory[0].remainingCostGp,"30000000");
    assert.equal(x.availableCashGp,"70580000");
  }
  const final=replayJournal(portfolio,[buy,gross,{...net,id:"sell-2",quantity:"3000",netReceivedGp:"30870000"}]);
  assert.equal(final.realizedGp,"1450000");
  assert.equal(final.inventory[0].remaining,"0");
  assert.equal(final.inventory[0].remainingCostGp,"0");
});

test("advanced partial and zero-fill attempts retain cash and stock",()=>{
  const p=createPortfolio("10000",at);
  const records=[
    {...base,id:"place-1",type:"PLACE",offerId:"offer-1",side:"BUY",quantity:"10",unitPriceGp:"100"},
    {...base,id:"fill-1",type:"FILL",offerId:"offer-1",quantity:"5",totalPaidGp:"500"},
    {...base,id:"collect-1",type:"COLLECT",offerId:"offer-1"},
    {...base,id:"cancel-1",type:"CANCEL",offerId:"offer-1"},
    {...base,id:"collect-2",type:"COLLECT",offerId:"offer-1"},
    {...base,id:"place-2",type:"PLACE",offerId:"offer-2",side:"BUY",quantity:"10",unitPriceGp:"100"},
    {...base,id:"cancel-2",type:"CANCEL",offerId:"offer-2"},
    {...base,id:"collect-3",type:"COLLECT",offerId:"offer-2"}
  ];
  const result=replayJournal(p,records);
  assert.equal(result.availableCashGp,"9500");
  assert.equal(result.inventoryCostGp,"500");
  assert.equal(result.inventory[0].remaining,"5");
  assert.equal(result.zeroFillAttempts,1);
  assert.equal(result.partialFillAttempts,1);
  assert.equal(result.occupiedSlots,0);
});

test("recorded buy repricing conserves escrow and does not reset allowance",()=>{
  const p=createPortfolio("10000",at);
  const first={...base,id:"place",type:"PLACE",offerId:"offer",side:"BUY",quantity:"10",unitPriceGp:"100"};
  const raised={...base,id:"higher",type:"REPRICE",offerId:"offer",newUnitPriceGp:"120"};
  const lowered={...base,id:"lower",type:"REPRICE",offerId:"offer",newUnitPriceGp:"90"};
  const a=replayJournal(p,[first,raised]);
  assert.equal(a.availableCashGp,"8800");assert.equal(a.escrowGp,"1200");
  const b=replayJournal(p,[first,raised,lowered]);
  assert.equal(b.availableCashGp,"9100");assert.equal(b.escrowGp,"900");
  assert.equal(b.attemptsRecorded,1);
  const after=replayJournal(p,[first,raised,lowered,
    {...base,id:"fill",type:"FILL",offerId:"offer",quantity:"5",totalPaidGp:"450"},
    {...base,id:"cancel",type:"CANCEL",offerId:"offer"},
    {...base,id:"collect",type:"COLLECT",offerId:"offer"}]);
  assert.equal(after.availableCashGp,"9550");assert.equal(after.inventoryCostGp,"450");
  assert.equal(after.partialFillAttempts,1);
});

test("deposits, corrections, duplicates and over-cap facts stay separate",()=>{
  const buyLarge={...buy,totalPaidGp:"60000000"};
  const rows=[buyLarge,{...base,id:"dep",type:"DEPOSIT",amountGp:"1000000"}];
  const result=replayJournal(portfolio,rows);
  assert.equal(result.realizedGp,"0");
  assert.equal(result.cashFlowGp,"1000000");
  assert.ok(result.warnings.some(x=>x.startsWith("FACTUAL_BUY_OVER_50M_PILOT")));
  assert.ok(result.warnings.includes("RECORDED_AGGREGATE_EXPOSURE_OVER_50M_PILOT"));
  assert.equal(replayJournal(portfolio,[buy,buy]).eventCount,1);
  assert.throws(()=>replayJournal(portfolio,[buy,{...buy,totalPaidGp:"2"}]),/duplicate/);
  assert.throws(()=>replayJournal(portfolio,[buy,gross,{...base,id:"fix",type:"CORRECTION",targetEventId:"buy-1",reason:"Wrong quantity recorded"}]),/inventory/);
  const corrected=replayJournal(portfolio,[buy,{...base,id:"fix",type:"CORRECTION",targetEventId:"buy-1",reason:"Wrong purchase total"},
    {...buy,id:"buy-2",totalPaidGp:"49000000"}]);
  assert.equal(corrected.inventoryCostGp,"49000000");
  assert.throws(()=>replayJournal(portfolio,[{...buy,portfolioId:"another"}]),/another portfolio/);
  assert.throws(()=>replayJournal(portfolio,[{...buy,occurredAt:at-1}]),/predates the journal opening/);
  const p=createPortfolio("100000000",at);
  const two=[{...buy,id:"buy-a",quantity:"4000",totalPaidGp:"40000000"},
    {...buy,id:"buy-b",quantity:"4000",totalPaidGp:"40000000"}];
  assert.ok(replayJournal(p,two).warnings.includes("RECORDED_AGGREGATE_EXPOSURE_OVER_50M_PILOT"));
  assert.throws(()=>replayJournal(p,[{...base,id:"place",type:"PLACE",offerId:"o",side:"BUY",quantity:"1",unitPriceGp:"10",occurredAt:at+20},
    {...base,id:"fill",type:"FILL",offerId:"o",quantity:"1",totalPaidGp:"10",occurredAt:at+10}]),/no open offer|predates/);
});

test("opening stock carries cost without inventing a post-opening purchase",()=>{
  const p=createPortfolio("1000000",at);
  const openingLot={...base,id:"opening-1",type:"OPENING_LOT",occurredAt:at,quantity:"100",costBasisGp:"10000"};
  const sale={...net,id:"sale-opening",quantity:"40",netReceivedGp:"4800",occurredAt:at+5};
  const result=replayJournal(p,[sale,openingLot]); // Late-recorded opening lot replays at its actual clock.
  assert.equal(result.realizedGp,"800");
  assert.equal(result.availableCashGp,"1004800");
  assert.equal(result.inventory[0].remaining,"60");
  assert.equal(result.inventory[0].remainingCostGp,"6000");
  assert.equal(result.openingInventoryBasisGp,"10000");
  assert.equal(result.inventory[0].bought,"0");
  assert.throws(()=>replayJournal(p,[{...openingLot,occurredAt:at+1}]),/as-of/);
});

test("planning deducts recent recorded buys and open buy commitments without resetting on sale",()=>{
  const now=at+100;
  const traded=replayJournal(portfolio,[buy,gross]);
  assert.equal(recordedAllowanceUsage(portfolio,[buy,gross],now,traded).usage[4151],5000);
  const p=createPortfolio("10000",at);
  const place={...base,id:"allow-place",type:"PLACE",offerId:"allow-offer",side:"BUY",quantity:"10",unitPriceGp:"100"};
  const fill={...base,id:"allow-fill",type:"FILL",offerId:"allow-offer",quantity:"5",totalPaidGp:"500"};
  const pending=replayJournal(p,[place,fill]);
  assert.equal(recordedAllowanceUsage(p,[place,fill],now,pending).usage[4151],10);
  const cancel={...base,id:"allow-cancel",type:"CANCEL",offerId:"allow-offer"};
  const collected={...base,id:"allow-collect",type:"COLLECT",offerId:"allow-offer"};
  const finished=replayJournal(p,[place,fill,cancel,collected]);
  assert.equal(recordedAllowanceUsage(p,[place,fill,cancel,collected],now,finished).usage[4151],5);
  assert.deepEqual(recordedAllowanceUsage(p,[place,fill,cancel,collected],now+4*3600,finished).usage,{});
});
