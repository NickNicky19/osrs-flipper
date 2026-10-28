import test from "node:test";
import assert from "node:assert/strict";
import {analyzeWindow} from "../history-analysis.mjs";

test("time-ordered chart statistics separate coverage, downside and both-side activity",()=>{
  const points=[
    {bucket_start:300,low_avg:100,high_avg:120,low_units:10,high_units:20},
    {bucket_start:600,low_avg:120,high_avg:130,low_units:15,high_units:25},
    {bucket_start:900,low_avg:60,high_avg:70,low_units:5,high_units:8},
    {bucket_start:1200,low_avg:null,high_avg:100,low_units:null,high_units:3}
  ];
  const stats=analyzeWindow(points,1200,300,false);
  assert.equal(stats.expectedBuckets,4);
  assert.equal(stats.twoSidedBuckets,3);
  assert.equal(stats.oneSidedOrMissingBuckets,1);
  assert.deepEqual(stats.lowRange,[60,120]);
  assert.deepEqual(stats.highRange,[70,130]);
  assert.equal(stats.chronologicalLowDownsideBps,5000);
  assert.equal(stats.positiveGrossBuckets,3);
  assert.equal(stats.earlierLowUnits,25);
  assert.equal(stats.laterLowUnits,5);
  assert.equal(stats.earlierHighUnits,45);
  assert.equal(stats.laterHighUnits,11);
  assert.equal(stats.fivePercentExitStressPositiveBuckets,3);
});

test("no reviewed tax rule leaves after-tax stress unavailable",()=>{
  const stats=analyzeWindow([{bucket_start:300,low_avg:100,high_avg:120}],300,300,null);
  assert.equal(stats.fivePercentExitStressPositiveBuckets,null);
  assert.equal(stats.taxRuleKnown,false);
});

test("a complete half-hour of five-minute buckets has six points",()=>{
  const points=Array.from({length:7},(_,i)=>({
    bucket_start:300+i*300,low_avg:100,high_avg:120,
    low_units:10,high_units:10}));
  const stats=analyzeWindow(points,1800,300,false);
  assert.equal(stats.expectedBuckets,6);
  assert.equal(stats.observedBuckets,6);
  assert.equal(stats.twoSidedBuckets,6);
});
