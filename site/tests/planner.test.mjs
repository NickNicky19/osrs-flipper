import test from "node:test";
import assert from "node:assert/strict";
import {DEFAULTS, parseGp, validateSettings, sizeCandidate, allocatePlans} from "../planner.mjs";
import {createPortfolio,replayJournal} from "../journal-core.mjs";

const now = 1_793_132_577;
function row(id, buy, sell, limit, hourly) {
  return {item_id:id,name:`Item ${id}`,status:"ELIGIBLE_RESEARCH",flags:[],
    observed_instant_sell_low:buy,observed_instant_buy_high:sell,
    low_time:now-20,high_time:now-20,public_limit:limit,
    hour_low_units:hourly,hour_high_units:hourly,tax_exempt:false,
    rule_version:"public-rules-2"};
}

test("exact GP parsing and caps reject invalid budgets", () => {
  assert.equal(parseGp("1.25m"),1_250_000);
  assert.equal(parseGp("500,000,000"),500_000_000);
  for (const bad of ["", "-1", "0", "1,23", "1.2345m", "1000000000001", "1.1"]) {
    assert.throws(() => parseGp(bad));
  }
  assert.throws(() => validateSettings({...DEFAULTS,maxPositionGp:125_000_001}));
  assert.throws(() => validateSettings({...DEFAULTS,maxSlots:5}));
  assert.throws(() => validateSettings({...DEFAULTS,budgetGp:0}));
  assert.throws(() => validateSettings({...DEFAULTS,budgetGp:50,documentedCommitmentsGp:51}));
});

test("historical screenshot arithmetic stays conditional", () => {
  const ruby = sizeCandidate(row(21944,2849,2938,6159,61590),500_000_000,DEFAULTS);
  assert.equal(ruby.quantity,6159);
  assert.equal(ruby.taxPerUnit,58);
  assert.equal(ruby.netPerUnit,31);
  assert.equal(ruby.requiredCash,17_546_991);
  assert.equal(ruby.conditionalTotal,190_929);
  const ancient = sizeCandidate(row(27616,15,16,227797,2_277_970),500_000_000,DEFAULTS);
  assert.equal(ancient.conditionalTotal,227_797);
  assert.equal(ancient.requiredCash,3_416_955);
  const result = allocatePlans([row(21944,2849,2938,6159,61590),
    row(27616,15,16,227797,2_277_970)],DEFAULTS,now);
  assert.equal(result.status,"NO_ACTION");
  assert.equal(result.plans.length,0);
  assert.ok(result.smaller.length>0);
});

test("major threshold applies after shared cash allocation", () => {
  const pair=[row(1,1_000_000,1_030_000,125,1250),row(2,1_000_000,1_030_000,125,1250)];
  const low=allocatePlans(pair,{...DEFAULTS,budgetGp:200_000_000},now);
  assert.equal(low.plans.length,1);
  assert.equal(low.plans[0].conditionalTotal,1_175_000);
  assert.equal(low.cashRemaining,75_000_000);
  assert.equal(low.excluded.MIN_CONDITIONAL_TOTAL,1);
  const high=allocatePlans(pair,{...DEFAULTS,budgetGp:500_000_000},now);
  assert.equal(high.plans.length,2);
  assert.equal(high.cashRemaining,250_000_000);
  assert.equal(high.plans.reduce((n,p)=>n+p.requiredCash,0)+high.cashRemaining,500_000_000);
  const below=allocatePlans(pair,{...DEFAULTS,budgetGp:50_000_000},now);
  assert.equal(below.plans.length,0);
});

test("tax, unit preference and quote age remain separate gates", () => {
  assert.equal(sizeCandidate(row(3,100,102,1000,10000),100_000,DEFAULTS),null);
  const tiny=row(4,15,16,1_000_000,10_000_000);
  assert.equal(allocatePlans([tiny],DEFAULTS,now).plans[0].conditionalTotal,1_000_000);
  assert.equal(allocatePlans([tiny],{...DEFAULTS,minUnitGp:2},now).plans.length,0);
  assert.equal(allocatePlans([tiny],DEFAULTS,now+301).plans.length,0);
  assert.equal(allocatePlans([tiny],DEFAULTS,now,{allowanceUsage:{4:1_000_000}}).plans.length,0);
});

test("journal-linked planning cannot raise the 50m aggregate live pilot",()=>{
  const candidate=row(7,1_000_000,1_030_000,125,1250);
  const settings={...DEFAULTS,useJournalCash:true,minTotalGp:0};
  const result=allocatePlans([candidate],settings,now,{journal:{fullyReconciled:true,
    availableCashGp:500_000_000,exposureGp:40_000_000,occupiedSlots:0}});
  assert.equal(result.initialCash,10_000_000);
  assert.equal(result.plans[0].requiredCash,10_000_000);
  assert.equal(allocatePlans([candidate],settings,now,{journal:{fullyReconciled:true,
    availableCashGp:500_000_000,exposureGp:55_000_000,occupiedSlots:0}}).plans.length,0);
  const portfolio=createPortfolio("500000000",now-100);
  const journal=replayJournal(portfolio,[{id:"b",portfolioId:"default",type:"QUICK_BUY",
    itemId:1,itemName:"Other",quantity:"40",totalPaidGp:"40000000",occurredAt:now-90,recordedAt:now-80}]);
  const integrated=allocatePlans([candidate],settings,now,{journal});
  assert.equal(integrated.initialCash,10_000_000);
  assert.equal(integrated.plans[0].requiredCash,10_000_000);
  const lowCash=replayJournal(createPortfolio("30000000",now-100),[]);
  const committed=allocatePlans([candidate],{...settings,documentedCommitmentsGp:5_000_000},now,{journal:lowCash});
  assert.equal(committed.initialCash,25_000_000);
});
