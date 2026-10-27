import test from "node:test";
import assert from "node:assert/strict";
import {SnapshotPoller,snapshotIsNewer} from "../refresh.mjs";

const snap=(id,time,run) => ({schema:"osrs-public-site-1",artifact_id:id,
  generated_at:time,artifact_run_id:run,top_picks:[]});
const response=x=>({ok:true,json:async()=>x});

test("version A updates to B without reload and old payload cannot replace B",async()=>{
  const queue=[snap("a",100,"1-1"),snap("b",200,"2-1"),snap("a",100,"1-1")];
  const seen=[],errors=[];
  const poller=new SnapshotPoller({fetchFn:async()=>response(queue.shift()),
    onSnapshot:x=>seen.push(x.artifact_id),onError:e=>errors.push(e.message),
    visible:()=>true,autoSchedule:false});
  await poller.poll();await poller.poll();await poller.poll();
  assert.deepEqual(seen,["a","b"]);
  assert.deepEqual(errors,["OLDER_PUBLIC_SNAPSHOT"]);
  assert.equal(poller.snapshot.artifact_id,"b");
  assert.equal(snapshotIsNewer(snap("c",200,"3-1"),poller.snapshot),true);
});

test("failed fetch preserves last good snapshot and backs off",async()=>{
  let calls=0;const errors=[];
  const poller=new SnapshotPoller({fetchFn:async()=>{calls++;if(calls===1)return response(snap("a",100,"1-1"));throw Error("offline");},
    onError:(e,last)=>errors.push([e.message,last?.artifact_id]),visible:()=>true,autoSchedule:false});
  await poller.poll();await poller.poll();
  assert.equal(poller.snapshot.artifact_id,"a");
  assert.deepEqual(errors,[["offline","a"]]);
  assert.equal(poller.failures,1);
});

test("resume aborts an outstanding fetch and schedules one replacement",async()=>{
  let aborted=false,calls=0;
  const timers=[];
  const poller=new SnapshotPoller({fetchFn:(url,opts)=>{calls++;if(calls===1)return new Promise(resolve=>{opts.signal.addEventListener("abort",()=>{aborted=true;resolve(response(snap("old",100,"1-1")));});});return Promise.resolve(response(snap("new",200,"2-1")));},
    now:()=>200000,visible:()=>true,setTimer:(fn,ms)=>{const id=timers.length;timers.push({fn,ms});return id;},
    clearTimer:id=>{if(timers[id])timers[id].cancelled=true;}});
  const first=poller.poll();
  poller.resume();
  await first;
  assert.equal(aborted,true);
  assert.equal(poller.snapshot,null);
  const scheduled=timers.filter(t=>!t.cancelled);
  assert.equal(scheduled.length,1);
  assert.equal(scheduled[0].ms,0);
  await scheduled[0].fn();
  await Promise.resolve();
  assert.equal(poller.snapshot.artifact_id,"new");
  assert.equal(calls,2);
  poller.stop();
});
