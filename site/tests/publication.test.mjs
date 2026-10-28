import test from 'node:test';
import assert from 'node:assert/strict';
import {matchingReceipt,publishedAt,currentPlanEligible,requiresReceiptReload,needsReceiptCheck} from '../publication.mjs';

const data={artifact_id:'artifact-a',artifact_run_id:'run-1'};
const pick={id:'pick-1',issued_at:1000,expires_at:1600,
  observed_low_time:1100,observed_high_time:1110,first_public_at:null};
const receipt={artifact_id:'artifact-a',deployment_id:'run-1',
  deployed_at:1200,pick_ids:['pick-1']};

test('current plans wait for the matching verified artifact receipt',()=>{
  assert.equal(matchingReceipt(data,{...receipt,deployment_id:'other'}),null);
  assert.equal(currentPlanEligible(data,pick,null,1230),false);
  assert.equal(currentPlanEligible(data,pick,{...receipt,artifact_id:'other'},1230),false);
  assert.equal(currentPlanEligible(data,pick,receipt,1230),true);
  assert.equal(publishedAt(data,pick,receipt),1200);
});

test('deployment and quote expiry never extend a plan',()=>{
  assert.equal(currentPlanEligible(data,pick,receipt,1410),false);
  assert.equal(currentPlanEligible(data,pick,{...receipt,deployed_at:1410},1411),false);
  assert.equal(currentPlanEligible(data,pick,receipt,1199),false);
  assert.equal(publishedAt(data,{...pick,first_public_at:1180},receipt),1180);
});

test('unchanged successful polls retain a verified receipt through a receipt-server outage',()=>{
  const same={...data,generated_at:1300};
  assert.equal(requiresReceiptReload(data,same),false);
  assert.equal(needsReceiptCheck(data,same,receipt),false);
  assert.equal(needsReceiptCheck(data,same,null),true);
  assert.equal(currentPlanEligible(same,pick,receipt,1230),true);
  assert.equal(requiresReceiptReload(data,{...same,artifact_id:'new-artifact'}),true);
});
