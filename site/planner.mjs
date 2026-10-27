// Browser-only what-if arithmetic. All prices are public references, not fill forecasts.
export const PLANNING_SCHEMA = "osrs-local-planning-1";
export const DEFAULTS = Object.freeze({
  schema: PLANNING_SCHEMA,
  budgetGp: 500_000_000,
  minTotalGp: 1_000_000,
  minUnitGp: 0,
  minRoiBps: 0,
  maxPositionGp: 125_000_000,
  maxSlots: 4,
  reviewHorizonSeconds: 3600,
  useJournalCash: false,
  documentedCommitmentsGp: 0,
  occupiedSlots: 0
});
export const MAX_INPUT_GP = 1_000_000_000_000;
const MAX_SAFE = Number.MAX_SAFE_INTEGER;

export function parseGp(input, {allowZero = false, maximum = MAX_INPUT_GP} = {}) {
  if (typeof input === "number") {
    if (!Number.isSafeInteger(input) || input < (allowZero ? 0 : 1) || input > maximum) throw new Error("Enter a valid whole GP amount.");
    return input;
  }
  const match = String(input ?? "").trim().match(/^((?:\d+|\d{1,3}(?:,\d{3})+))(?:\.(\d{1,3}))?\s*([kmb])?$/i);
  if (!match || (match[2] && !match[3])) throw new Error("Enter whole GP, optionally using k, m or b.");
  const digits = match[1].replaceAll(",", "");
  if (!/^\d+$/.test(digits)) throw new Error("Enter a valid GP amount.");
  const multiplier = {k: 1_000n, m: 1_000_000n, b: 1_000_000_000n}[match[3]?.toLowerCase()] ?? 1n;
  const decimal = match[2] ? BigInt(match[2].padEnd(3, "0")) * multiplier / 1_000n : 0n;
  if (match[2] && BigInt(match[2].padEnd(3, "0")) * multiplier % 1_000n !== 0n) throw new Error("GP must be a whole number.");
  const result = BigInt(digits) * multiplier + decimal;
  if (result < (allowZero ? 0n : 1n) || result > BigInt(maximum) || result > BigInt(MAX_SAFE)) throw new Error("GP amount is outside the supported range.");
  return Number(result);
}

export function validateSettings(input) {
  if (!input || typeof input !== "object" || input.schema !== PLANNING_SCHEMA) throw new Error("Unknown planning settings version.");
  const s = {...DEFAULTS, ...input};
  for (const key of ["budgetGp", "minTotalGp", "minUnitGp", "documentedCommitmentsGp"]) {
    s[key] = parseGp(s[key], {allowZero: key !== "budgetGp"});
  }
  if (!Number.isSafeInteger(s.minRoiBps) || s.minRoiBps < 0 || s.minRoiBps > 100_000) throw new Error("Invalid ROI preference.");
  if (!Number.isSafeInteger(s.maxPositionGp) || s.maxPositionGp < 1 || s.maxPositionGp > DEFAULTS.maxPositionGp) throw new Error("Position cap cannot exceed the reviewed baseline.");
  if (!Number.isInteger(s.maxSlots) || s.maxSlots < 1 || s.maxSlots > DEFAULTS.maxSlots) throw new Error("Slot cap cannot exceed the reviewed baseline.");
  if (!Number.isInteger(s.occupiedSlots) || s.occupiedSlots < 0 || s.occupiedSlots > s.maxSlots) throw new Error("Invalid occupied slot count.");
  if (![1800, 3600, 7200, 28800].includes(s.reviewHorizonSeconds)) throw new Error("Unsupported review horizon.");
  if (typeof s.useJournalCash !== "boolean") throw new Error("Invalid planning cash mode.");
  if (s.documentedCommitmentsGp > s.budgetGp && !s.useJournalCash) throw new Error("Commitments exceed the hypothetical budget.");
  return s;
}

export function taxPerUnit(sell, exempt) {
  if (!Number.isSafeInteger(sell) || sell < 1 || typeof exempt !== "boolean") throw new Error("Unknown price or tax rule.");
  return exempt ? 0 : Math.min(Math.floor(sell / 50), 5_000_000);
}

function positiveInt(value) { return Number.isSafeInteger(value) && value > 0; }

export function currentCandidate(row, now) {
  if (!row || row.status !== "ELIGIBLE_RESEARCH" || (row.flags?.length ?? 0)) return false;
  if (!positiveInt(row.low_time) || !positiveInt(row.high_time)) return false;
  if (now < row.low_time || now < row.high_time || now - row.low_time >= 300 || now - row.high_time >= 300) return false;
  return positiveInt(row.observed_instant_sell_low) && positiveInt(row.observed_instant_buy_high) &&
    positiveInt(row.public_limit) && positiveInt(row.hour_low_units) && positiveInt(row.hour_high_units) &&
    typeof row.tax_exempt === "boolean";
}

export function sizeCandidate(row, cash, settings, allowanceUsed = 0) {
  const buy = row.observed_instant_sell_low, sell = row.observed_instant_buy_high;
  if (!positiveInt(buy) || !positiveInt(sell) || !Number.isSafeInteger(cash) || cash < 0) return null;
  if (!Number.isSafeInteger(allowanceUsed) || allowanceUsed < 0) throw new Error("Invalid recorded allowance use.");
  const tax = taxPerUnit(sell, row.tax_exempt), netPerUnit = sell - tax - buy;
  if (netPerUnit <= 0) return null;
  const caps = {
    PUBLIC_LIMIT_REMAINING_ASSUMED: Math.max(0, row.public_limit - allowanceUsed),
    LOW_SIDE_10_PERCENT_HOURLY_UNITS: Math.floor(row.hour_low_units / 10),
    HIGH_SIDE_10_PERCENT_HOURLY_UNITS: Math.floor(row.hour_high_units / 10),
    POSITION_CAP: Math.floor(settings.maxPositionGp / buy),
    SHARED_CASH: Math.floor(cash / buy)
  };
  const quantity = Math.min(...Object.values(caps));
  if (!positiveInt(quantity)) return null;
  const requiredCash = buy * quantity, conditionalTotal = netPerUnit * quantity;
  if (!Number.isSafeInteger(requiredCash) || !Number.isSafeInteger(conditionalTotal)) return null;
  const binding = Object.entries(caps).filter(([, value]) => value === quantity).map(([name]) => name);
  return {itemId: row.item_id, name: row.name, buy, sell, taxPerUnit: tax,
    grossPerUnit: sell - buy, netPerUnit, quantity, requiredCash,
    conditionalTotal, conditionalRoiBps: Math.floor(10_000 * conditionalTotal / requiredCash),
    binding, publicLimit: row.public_limit, lowTime: row.low_time, highTime: row.high_time,
    ruleVersion: row.rule_version, taxExempt: row.tax_exempt,
    oneGpAdverseBuy: (netPerUnit - 1) * quantity,
    oneGpAdverseSell: (sell - 1 - taxPerUnit(Math.max(1, sell - 1), row.tax_exempt) - buy) * quantity,
    executionScope: "CONDITIONAL_SCENARIO_NO_FILL_INFERRED"};
}

export function allocatePlans(rows, rawSettings, now, options = {}) {
  const settings = validateSettings(rawSettings);
  if (!Number.isSafeInteger(now) || now < 1) throw new Error("Decision clock required.");
  const journal = options.journal ?? null;
  let journalCash=null, journalExposure=null;
  if(settings.useJournalCash){
    try{
      if(!journal?.fullyReconciled)throw new Error("Journal not reconciled");
      journalCash=parseGp(journal.availableCashGp,{allowZero:true});
      journalExposure=parseGp(journal.exposureGp,{allowZero:true});
      if(!Number.isInteger(journal.occupiedSlots)||journal.occupiedSlots<0)throw new Error("Unknown occupied slots");
    }catch{
      return {status:"NO_ACTION",reason:"JOURNAL_CASH_UNRECONCILED",plans:[],smaller:[],cashRemaining:null,considered:0};
    }
  }
  const pilotHeadroom=settings.useJournalCash?Math.max(0,50_000_000-journalExposure):null;
  const effectiveSettings=settings.useJournalCash?{...settings,maxPositionGp:Math.min(settings.maxPositionGp,50_000_000)}:settings;
  const initialCash = settings.useJournalCash ? Math.min(Math.max(0,journalCash-settings.documentedCommitmentsGp),pilotHeadroom) :
    settings.budgetGp - settings.documentedCommitmentsGp;
  const occupied = settings.useJournalCash ? Math.max(settings.occupiedSlots, journal.occupiedSlots ?? 0) : settings.occupiedSlots;
  const availableSlots = Math.max(0, settings.maxSlots - occupied);
  const allowanceUsage = options.allowanceUsage ?? {};
  const pool = (rows ?? []).filter(row => currentCandidate(row, now));
  // This is a deterministic greedy research heuristic, not a global optimum.
  const ranked = pool.map(row => ({row, full: sizeCandidate(row, Math.min(initialCash, effectiveSettings.maxPositionGp), effectiveSettings,
      Number(allowanceUsage[row.item_id] ?? 0))})).filter(x => x.full)
    .sort((a,b) => b.full.conditionalTotal - a.full.conditionalTotal || a.row.item_id - b.row.item_id);
  const plans = [], smaller = [], excluded = {};
  let cash = initialCash;
  for (const {row} of ranked) {
    const plan = sizeCandidate(row,cash,effectiveSettings,Number(allowanceUsage[row.item_id] ?? 0));
    if (!plan) {excluded.SHARED_CASH = (excluded.SHARED_CASH ?? 0)+1; continue;}
    if (plan.netPerUnit < settings.minUnitGp) {excluded.MIN_NET_PER_UNIT=(excluded.MIN_NET_PER_UNIT ?? 0)+1; smaller.push(plan); continue;}
    if (plan.conditionalRoiBps < settings.minRoiBps) {excluded.MIN_CONDITIONAL_ROI=(excluded.MIN_CONDITIONAL_ROI ?? 0)+1; smaller.push(plan); continue;}
    if (plan.conditionalTotal < settings.minTotalGp) {excluded.MIN_CONDITIONAL_TOTAL=(excluded.MIN_CONDITIONAL_TOTAL ?? 0)+1; smaller.push(plan); continue;}
    if (plans.length >= availableSlots) {excluded.SLOTS=(excluded.SLOTS ?? 0)+1; smaller.push(plan); continue;}
    plans.push(plan);
    cash -= plan.requiredCash;
  }
  return {status:plans.length ? "CONDITIONAL_PLANS" : "NO_ACTION",
    reason:plans.length ? null : "NO_QUALIFYING_MAJOR_OPPORTUNITY_IN_SUPPORTED_CURRENT_DATA",
    plans, smaller, cashRemaining:cash, initialCash, availableSlots,
    considered:pool.length, ranked:ranked.length, excluded,
    scope:settings.useJournalCash?
      "Self-reported journal cash; total new research exposure held below the 50m live-pilot headroom. Manual action and personal allowances remain unverified.":
      "Hypothetical what-if budget; greedy shared allocation. This is not a live exposure approval; personal limits and fills are unverified."};
}
