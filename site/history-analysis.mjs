import {taxPerUnit} from "./planner.mjs";

// Describes reported interval averages and whole-interval units. A point is
// never an executable quote or evidence that a personal order filled.
export function analyzeWindow(points, horizonSeconds, resolutionSeconds, taxExempt = null) {
  const ordered=[...(points||[])].filter(p=>Number.isSafeInteger(p.bucket_start))
    .sort((a,b)=>a.bucket_start-b.bucket_start);
  const newest=ordered.at(-1)?.bucket_start??null;
  const scoped=newest===null?[]:ordered.filter(p=>p.bucket_start>newest-horizonSeconds);
  const valid=scoped.filter(p=>Number.isFinite(p.low_avg)&&p.low_avg>0&&
    Number.isFinite(p.high_avg)&&p.high_avg>0);
  const sorted=v=>[...v].sort((a,b)=>a-b);
  const median=v=>v.length?sorted(v)[Math.floor(v.length/2)]:null;
  const lows=valid.map(p=>p.low_avg),highs=valid.map(p=>p.high_avg);
  const spreads=valid.map(p=>p.high_avg-p.low_avg);
  let peak=0,downsideBps=0;
  for(const p of valid){peak=Math.max(peak,p.low_avg);
    downsideBps=Math.max(downsideBps,Math.round(10_000*(peak-p.low_avg)/peak));}
  const activity=(key,part)=>{
    const values=part.map(p=>p[key]).filter(v=>Number.isSafeInteger(v)&&v>=0);
    return values.length?values.reduce((a,b)=>a+b,0):null;
  };
  const midpoint=Math.floor(scoped.length/2);
  const first=scoped.slice(0,midpoint),last=scoped.slice(midpoint);
  const stressed=typeof taxExempt==="boolean"?valid.filter(p=>{
    const exit=Math.max(1,Math.floor(p.high_avg*.95));
    return exit-taxPerUnit(exit,taxExempt)-p.low_avg>0;
  }).length:null;
  return {
    newest,observedBuckets:scoped.length,twoSidedBuckets:valid.length,
    expectedBuckets:Number.isSafeInteger(resolutionSeconds)&&resolutionSeconds>0?
      Math.ceil(horizonSeconds/resolutionSeconds):null,
    oneSidedOrMissingBuckets:scoped.length-valid.length,
    positiveGrossBuckets:spreads.filter(v=>v>0).length,
    lowRange:lows.length?[Math.min(...lows),Math.max(...lows)]:null,
    highRange:highs.length?[Math.min(...highs),Math.max(...highs)]:null,
    medianGrossSpread:median(spreads),minimumGrossSpread:spreads.length?Math.min(...spreads):null,
    lowerSideRangeBps:lows.length?Math.round(10_000*(Math.max(...lows)-Math.min(...lows))/median(lows)):null,
    chronologicalLowDownsideBps:valid.length?downsideBps:null,
    earlierLowUnits:activity("low_units",first),laterLowUnits:activity("low_units",last),
    earlierHighUnits:activity("high_units",first),laterHighUnits:activity("high_units",last),
    fivePercentExitStressPositiveBuckets:stressed,
    taxRuleKnown:typeof taxExempt==="boolean"
  };
}
