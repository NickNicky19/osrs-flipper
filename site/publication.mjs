"use strict";

export function matchingReceipt(data,receipt){
  return receipt&&receipt.artifact_id===data?.artifact_id&&
    receipt.deployment_id===data?.artifact_run_id&&Array.isArray(receipt.pick_ids)&&
    Number.isSafeInteger(receipt.deployed_at)&&receipt.deployed_at>0?receipt:null;
}

export function publishedAt(data,pick,receipt){
  const verified=matchingReceipt(data,receipt);
  if(verified?.pick_ids.includes(pick.id))return pick.first_public_at||verified.deployed_at;
  return pick.first_public_at||null;
}

export function currentPlanEligible(data,pick,receipt,now){
  const verified=matchingReceipt(data,receipt);
  if(!verified||!verified.pick_ids.includes(pick.id))return false;
  const deadline=Math.min(pick.expires_at,pick.observed_low_time+300,pick.observed_high_time+300);
  return pick.issued_at<=verified.deployed_at&&verified.deployed_at<=now&&
    pick.observed_low_time<=now&&pick.observed_high_time<=now&&deadline>verified.deployed_at&&deadline>now;
}
