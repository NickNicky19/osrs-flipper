# OSRS Flip Lab

Public, same-item Grand Exchange research for **manual** play. The static site
shows up to four conditional offer plans, a full market screener, numerical
history, issue/outcome history and source/learning status. A separate Python
Workbench retains the original v0.3 local journal and server. The website
cannot see a RuneLite account and does not report actual trading profit.

## What is live, and what is hypothetical

The public Wiki API reports observed instant-buy (`high`) and instant-sell
(`low`) transactions. Those are past executions, not the price or depth of an
available order. The 5m/1h/24h data give side-specific **averages** and whole
interval reported units. Neither exact volume at one price nor a personal
order's attainable fill is present. Top Picks use a reviewed item-rule subset
and a 10% hourly-volume sizing assumption, capped again by public buy limit,
shared 500m GP simulated cash, four paper slots and 125m GP per position.
Conditional after-tax GP assumes *every* proposed buy and later sell completes.
It is not expected or earned profit. A configurable 50k GP minimum keeps the
screen focused on material positions; all rejected items remain in the screener.

The 50m GP live-pilot ceiling remains unchanged. No game action is automated.
There is no guaranteed path or deadline from 500m to 1b GP.

## Components

- `osrs-flip-lab/core.py`, `market.py`, `research.py`, `app.py`: supplied v0.3
  local Workbench, with backwards-compatible per-route source support and a
  collector transient-retry fix. The supplied local Workbench's dated build,
  probe and test records remain historical evidence.
- `osrs-flip-lab/public_pipeline.py`: bounded source client, endpoint-specific
  parse/quarantine, delayed-bucket backfill, one feasible paper reservation
  ledger, immutable pick/outcome records, allowlisted export and deployment
  acknowledgement.
- `osrs-flip-lab/learning.py`: one deterministic standard-library ridge
  candidate for next captured 5m lower-side average and reported lower-side
  units. It uses chronological splits and a one-look later shadow gate. It
  does **not** model fills, personal outcomes, or real profit.
- `site/`: static, project-subpath-safe Pages viewer. Browser-side expiry hides
  stale plans even when GitHub misses a scheduled run.
- `.github/workflows/public-research.yml`: one writer, five-minute-offset
  best-effort schedule, state branch persistence, Pages deploy and post-deploy
  acknowledgement. GitHub may delay or drop scheduled jobs.

## Local verification

Python 3.11+ standard library only. From `osrs-flip-lab/` run
`python -m unittest discover -s tests -v`. The original handoff application
suite was 149 tests; the isolated diagnostic kit was 15. Neither proves
live source semantics or a trading edge. The site is static and can be served
from the repository root with `python -m http.server 8766`; open
`http://127.0.0.1:8766/site/` to test the project subpath.

The public pipeline requires a reviewed source file and the durable state
file. It deliberately refuses to recreate missing state. A new deployment is
bootstrapped once with `python osrs-flip-lab/public_pipeline.py init --state
state.json --output site/data.json`; subsequent runs use `cycle --state
state.json --archive-dir archive --raw-dir raw-evidence --output site/data.json
--run-id UNIQUE_ID`.
Do not point the public pipeline at any private Workbench database.

## State, recovery and controls

`paper-data` is the authoritative public-only branch. It stores `state.json`
and compact older pick/outcome archives; Actions artifacts/cache are never the
sole state. Every run has a stable ID, an atomic state write and one serialized
workflow writer. Latest captured observations are retained 10 days for the
bounded learner; older pick/outcome detail moves to daily gzip archives after
one day, with recent history in the live state. The state has a 12 MB hard
limit; archives have a 50 MB hard limit, at which new advice pauses rather
than discarding evidence. The source request cap is 18 per run, raw response
cap 18 MB, with 15-second HTTP timeouts and bounded retries. A fresh live
manual cycle measured nine requests, 2.19 MB raw and about 2.2 seconds of
wall time on Windows; hosted-runner timings must be measured separately.
Historical chart requests are capped at 16 per UTC day, and raw source
responses are uploaded only once per successful UTC day or on a failed run,
with seven-day retention. Pages artifacts expire after one day. This bounds
artifact storage; review repository usage if hosting limits change.

The fixed learner checks readiness once per day. It requires at least 240
finalized next-bucket examples, five items, 48 distinct decision clocks and
80% label coverage. It fits one candidate on past data with a horizon gap,
uses a later holdout once, then records untouched shadow predictions. Promotion
to research ranking requires the predeclared price and unit forecast gates,
seven shadow days, 400 shadow pairs and matched 500m GP reference portfolio
checks. If those conditions never pass, the rules baseline remains active.
Reference portfolio marks assume full immediate acquisition at displayed
prices and are never described as actual fills or profit.

Set `kill_switch: true` in the data-branch state and commit normally to pause
source calls and new advice. `public_pause: true` pauses new advice while
allowing source-health checks. Restore a known-good `state.json` and archives
from a prior data-branch commit if a write is damaged; never run `init` over
existing history. `rollback` switches a research model back to the rules
baseline without changing policy limits. Source denials and review expiry
produce an unavailable view, not synthetic live picks.

The provider contract review expires 30 days after the captured check, and
the item-rule review also expires November 26, 2026. A maintainer must
recheck semantics and dates before renewing them. That prevents stale source
approval from silently persisting indefinitely.
