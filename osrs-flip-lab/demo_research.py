"""Reproducible synthetic histories. No names or IDs masquerading as live offers."""
from pathlib import Path
import json
from market import MarketStore,SourceContract,canonical

DEMO_NOW=1792944300  # Fixed replay clock, not the current clock.

def seed_demo(path:Path):
    store=MarketStore(path,'fixture')
    now=DEMO_NOW
    contract=SourceContract(reviewed=True,reviewed_at=now-86400,expires_at=now+86400,
        permission_reference='SYNTHETIC_FIXTURE',semantics_reference='SYNTHETIC_START_BUCKETS',
        user_agent='offline-test')
    items=[(900101,'Demo durable spread',10000,10400,False),(900102,'Demo tax-exempt supply',580,593,True),
           (900103,'Demo narrow margin',230,240,False),(900104,'Demo stale-side trap',1000,1700,False),
           (900105,'Demo missing-interval trap',2000,2200,False),(900106,'Demo tax-erased spread',10000,10100,False)]
    mapping=[dict(id=i,name=n,members=True,limit=10000) for i,n,*_ in items]
    store.ingest('mapping',canonical(mapping).encode(),now-7200,contract)
    for j in range(12):
        start=now-3600+j*300
        data={}
        for i,name,lo,hi,exempt in items:
            if i==900105 and j<8:continue
            data[str(i)]=dict(avgLowPrice=lo+(j%3),avgHighPrice=hi-(j%2),lowPriceVolume=1000+j*10,highPriceVolume=1200+j*12)
        store.ingest('5m',canonical(dict(timestamp=start,data=data)).encode(),start+300,contract)
    quotes={str(i):dict(low=lo,high=hi,lowTime=now-(1800 if i==900104 else 25),highTime=now-20) for i,n,lo,hi,e in items}
    store.ingest('latest',canonical(dict(data=quotes)).encode(),now,contract)
    rules={str(i):dict(tax_regime='ordinary-2pct-cap-5m',tax_exempt=e,effective_from=now-100000,
        reviewed_at=now-1000,expires_at=now+100000,reference='SYNTHETIC_FIXTURE_NOT_A_REAL_ITEM') for i,n,lo,hi,e in items}
    return store,rules,now
