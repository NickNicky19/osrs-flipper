export const REFRESH_INTERVAL_MS = 60_000;
export const FETCH_TIMEOUT_MS = 12_000;
export const MAX_BACKOFF_MS = 300_000;

function runNumber(value) {
  const match = String(value ?? "").match(/^(\d+)(?:-\d+)?$/);
  return match ? BigInt(match[1]) : 0n;
}

export function snapshotIsNewer(next, current) {
  if (!current) return true;
  if (!Number.isSafeInteger(next.generated_at) || !Number.isSafeInteger(current.generated_at)) return false;
  if (next.generated_at !== current.generated_at) return next.generated_at > current.generated_at;
  if (next.artifact_run_id !== current.artifact_run_id) return runNumber(next.artifact_run_id) > runNumber(current.artifact_run_id);
  return next.artifact_id === current.artifact_id;
}

export class SnapshotPoller {
  constructor({fetchFn = (...args)=>globalThis.fetch(...args), onSnapshot, onError, now = ()=>Date.now(),
               setTimer = (...args)=>setTimeout(...args), clearTimer = (...args)=>clearTimeout(...args),
               visible = () => document.visibilityState === "visible",
               intervalMs = REFRESH_INTERVAL_MS, timeoutMs = FETCH_TIMEOUT_MS,
               autoSchedule = true} = {}) {
    this.fetchFn=fetchFn; this.onSnapshot=onSnapshot; this.onError=onError;
    this.now=now; this.setTimer=setTimer; this.clearTimer=clearTimer;
    this.visible=visible; this.intervalMs=intervalMs; this.timeoutMs=timeoutMs;
    this.autoSchedule=autoSchedule; this.timer=null; this.controller=null;
    this.inFlight=null; this.snapshot=null; this.failures=0; this.stopped=false;
    this.generation=0; this.pendingResume=false;
  }
  schedule(delay) {
    if (!this.autoSchedule || this.stopped) return;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer=null;
    if (this.visible()) this.timer=this.setTimer(() => {this.timer=null; void this.poll();},delay);
  }
  async poll() {
    if (this.stopped || !this.visible()) return null;
    if (this.inFlight) return this.inFlight;
    const serial=++this.generation;
    const controller=new AbortController(); this.controller=controller;
    const timeout=this.setTimer(() => controller.abort(),this.timeoutMs);
    const task=(async () => {
      try {
        const url="./data.json?poll="+Math.floor(this.now()/1000);
        const response=await this.fetchFn(url,{cache:"no-store",signal:controller.signal});
        if (!response.ok) throw new Error("HTTP_"+response.status);
        const candidate=await response.json();
        if (candidate?.schema !== "osrs-public-site-1" || !Number.isSafeInteger(candidate.generated_at) ||
            typeof candidate.artifact_id !== "string") throw new Error("UNSUPPORTED_PUBLIC_SNAPSHOT");
        if (serial !== this.generation || this.stopped) return null;
        if (!snapshotIsNewer(candidate,this.snapshot)) throw new Error("OLDER_PUBLIC_SNAPSHOT");
        this.snapshot=candidate; this.failures=0;
        this.onSnapshot?.(candidate);
        return candidate;
      } catch (error) {
        if (serial === this.generation && !this.stopped) {
          this.failures=Math.min(this.failures+1,5);
          this.onError?.(error,this.snapshot);
        }
        return null;
      } finally {
        this.clearTimer(timeout);
        if (this.controller === controller) this.controller=null;
      }
    })();
    this.inFlight=task;
    try {return await task;} finally {
      if (this.inFlight===task) this.inFlight=null;
      const immediate=this.pendingResume; this.pendingResume=false;
      this.schedule(immediate ? 0 : Math.min(MAX_BACKOFF_MS,this.intervalMs * 2 ** Math.max(0,this.failures-1)));
    }
  }
  resume() {
    if (this.stopped || !this.visible()) return;
    if (this.timer !== null) {this.clearTimer(this.timer); this.timer=null;}
    if (this.inFlight) {this.pendingResume=true;++this.generation;this.controller?.abort();}
    else this.schedule(0);
  }
  stop() {
    this.stopped=true; ++this.generation;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer=null; this.controller?.abort(); this.controller=null;
  }
}
