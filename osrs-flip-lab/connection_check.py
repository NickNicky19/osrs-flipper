"""One explicit source preflight, never a policy approval or a scheduled task."""
import argparse
import json
from pathlib import Path
from collector import collect_once
from market import MarketStore,SourceContract
ROOT=Path(__file__).resolve().parent

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--user-agent',help='Identify your project/contact. Never enter game credentials.')
    p.add_argument('--output',type=Path,default=ROOT/'data'/'connection-check.json')
    p.add_argument('--database',type=Path,default=ROOT/'data'/'market.sqlite3')
    args=p.parse_args()
    identity=args.user_agent or input('Project/contact User-Agent (not a game login or password): ').strip()
    if not identity:p.error('A nonempty identifying User-Agent is required.')
    # Deliberately NOT reviewed. A response can be captured but not trusted as
    # a usable source until the current semantics/permission review is recorded.
    contract=SourceContract.from_dict(dict(user_agent=identity))
    result=collect_once(MarketStore(args.database,'public'),contract,probe=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    print('No ongoing job was installed. A successful probe is not source or item-rule approval.')
    return 2 if result['status']=='BLOCKED' else 0
if __name__=='__main__':raise SystemExit(main())
