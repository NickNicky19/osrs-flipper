// Browser-local, origin-scoped private storage. No network operation occurs here.
import {BACKUP_SCHEMA, JOURNAL_SCHEMA, canonical, replayJournal} from "./journal-core.mjs";

export const JOURNAL_DB_NAME="osrs-flip-lab-private-v1";
const KEY="portfolio";
const MAX_BACKUP_BYTES=8_000_000;
const MAX_RECORD_BYTES=7_000_000;

function openDb(){
  return new Promise((resolve,reject)=>{
    if(!globalThis.indexedDB){reject(new Error("Private browser storage is unavailable."));return;}
    const request=indexedDB.open(JOURNAL_DB_NAME,1);
    request.onupgradeneeded=()=>{if(!request.result.objectStoreNames.contains("records"))request.result.createObjectStore("records");};
    request.onerror=()=>reject(new Error("Private browser storage could not open."));
    request.onblocked=()=>reject(new Error("Another tab is blocking the private journal upgrade."));
    request.onsuccess=()=>resolve(request.result);
  });
}

function requestResult(request){return new Promise((resolve,reject)=>{
  request.onsuccess=()=>resolve(request.result);
  request.onerror=()=>reject(new Error("Private browser storage request failed."));
});}

async function transaction(change){
  const db=await openDb();
  try{
    return await new Promise((resolve,reject)=>{
      const tx=db.transaction("records","readwrite"),store=tx.objectStore("records");
      let result,failed=false,validationError=null;
      tx.oncomplete=()=>resolve(result);
      tx.onabort=()=>reject(validationError||new Error("Private journal save failed; no change was committed."));
      tx.onerror=()=>reject(new Error("Private journal save failed; no change was committed."));
      const get=store.get(KEY);
      get.onerror=()=>{failed=true;tx.abort();};
      get.onsuccess=()=>{
        if(failed)return;
        try{
          const before=get.result||null;
          const after=change(before);
          if(after){
            validateRecord(after);
            store.put(after,KEY);
          }
          result=after||before;
        }catch(error){failed=true;validationError=error;tx.abort();}
      };
    });
  }finally{db.close();}
}

function validateRecord(record){
  if(!record||record.schema!==JOURNAL_SCHEMA||!Number.isSafeInteger(record.revision)||record.revision<0||
    !Array.isArray(record.events)||record.events.length>5_000)throw new Error("Invalid or oversized private journal record.");
  if(new TextEncoder().encode(JSON.stringify(record)).length>MAX_RECORD_BYTES)throw new Error("Private journal is near its local backup limit; export and start a new portfolio version.");
  replayJournal(record.portfolio,record.events);
  return record;
}

export async function readJournal(){
  const db=await openDb();
  try{
    const result=await requestResult(db.transaction("records","readonly").objectStore("records").get(KEY));
    return result?validateRecord(result):null;
  }finally{db.close();}
}

export async function createJournal(portfolio){
  return transaction(before=>{
    if(before)throw new Error("A journal already exists on this browser. Export it before creating another portfolio.");
    replayJournal(portfolio,[]);
    return {schema:JOURNAL_SCHEMA,revision:1,portfolio,events:[]};
  });
}

export async function appendJournalEvent(event){
  return appendJournalEvents([event]);
}

export async function appendJournalEvents(additions){
  if(!Array.isArray(additions)||!additions.length||additions.length>20)throw new Error("Invalid batch of private events.");
  return transaction(before=>{
    if(!before)throw new Error("Create an opening portfolio first.");
    const prior=validateRecord(before);
    const seen=new Map(prior.events.map(e=>[e.id,e]));
    let changed=false;
    for(const event of additions){
      const same=seen.get(event.id);
      if(same){
        if(canonical(same)!==canonical(event))throw new Error("Conflicting duplicate event ID.");
      }else{seen.set(event.id,event);changed=true;}
    }
    if(!changed)return null; // Exact retry is idempotent.
    const after={...prior,revision:prior.revision+1,events:[...seen.values()]};
    validateRecord(after);
    return after;
  });
}

async function sha256(value){
  if(!globalThis.crypto?.subtle)throw new Error("Secure browser hashing is unavailable; backup cannot be verified.");
  const bytes=new TextEncoder().encode(value);
  const digest=await crypto.subtle.digest("SHA-256",bytes);
  return [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,"0")).join("");
}

export async function exportJournalBackup(){
  const record=await readJournal();
  if(!record)throw new Error("There is no private journal to export.");
  const body={schema:BACKUP_SCHEMA,exportedAt:Math.floor(Date.now()/1000),record};
  const text=JSON.stringify({...body,sha256:await sha256(canonical(body))});
  if(new TextEncoder().encode(text).length>MAX_BACKUP_BYTES)throw new Error("Private backup exceeds the supported limit.");
  return text;
}

export async function importJournalBackup(text,{merge=true}={}){
  if(typeof text!=="string"||new TextEncoder().encode(text).length>MAX_BACKUP_BYTES)throw new Error("Backup file is too large or invalid.");
  let backup;
  try{backup=JSON.parse(text);}catch{throw new Error("Backup is not valid JSON.");}
  if(!backup||backup.schema!==BACKUP_SCHEMA||typeof backup.sha256!=="string")throw new Error("Unsupported private backup version.");
  const {sha256:expected,...body}=backup;
  if(await sha256(canonical(body))!==expected)throw new Error("Backup integrity check failed.");
  validateRecord(body.record);
  return transaction(before=>{
    if(before&&(!merge||before.portfolio.id!==body.record.portfolio.id||
      canonical(before.portfolio)!==canonical(body.record.portfolio)))throw new Error("Backup belongs to another opening portfolio; existing records were preserved.");
    const seen=new Map((before?.events||[]).map(e=>[e.id,e]));
    for(const event of body.record.events){
      const prior=seen.get(event.id);
      if(prior&&canonical(prior)!==canonical(event))throw new Error("Conflicting backup event ID; no records were changed.");
      if(!prior)seen.set(event.id,event);
    }
    if(before&&seen.size===before.events.length)return null; // Restoring the same backup is an exact no-op.
    const after={schema:JOURNAL_SCHEMA,portfolio:body.record.portfolio,
      revision:(before?.revision||0)+1,events:[...seen.values()]};
    validateRecord(after);
    return after;
  });
}

export async function requestDurableStorage(){
  try{return !!(await navigator.storage?.persist?.());}catch{return false;}
}
