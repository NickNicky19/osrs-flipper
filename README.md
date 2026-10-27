# OSRS Flip Lab

Public, same-item Grand Exchange research for **manual** play. The static site
shows up to four conditional offer plans, a full market screener, numerical
history, issue/outcome history and source/learning status. My Portfolio adds
browser-local planning and My Trades stores manually entered actual trades
in this browser. A separate Python Workbench retains the original v0.3 local
journal and server. The website cannot see a RuneLite account.

## What is live, and what is hypothetical

The public Wiki API reports observed instant-buy (`high`) and instant-sell
(`low`) transactions. Those are past executions, not the price or depth of an
available order. The 5m/1h/24h data give side-specific **averages** and whole
interval reported units. Neither exact volume at one price nor a personal
order's attainable fill is present. Frozen public Top Picks use the original
44-item reviewed baseline; the broader v2 rule universe supports the personal
research candidate pool. Top Picks use a 10% hourly-volume sizing assumption,
capped again by public buy limit,
shared 500m GP simulated cash, four paper slots and 125m GP per position.
Conditional after-tax GP assumes *every* proposed buy and later sell completes.
It is not expected or earned profit. The baseline's 50k GP floor is frozen;
the personal High Total GP view starts at an editable 1m GP preference. All
rejected items remain inspectable in the screener.

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
- `osrs-flip-lab/learning.py`: a fixed small standard-library ridge family
  for future one-hour completed lower/higher side averages and reported units.
  It uses chronological gaps, train-only transforms, bounded new generations,
  prospective shadow records and a matched portfolio gate. It does **not**
  model fills, personal outcomes, or real profit.
- `osrs-flip-lab/simulator.py`: bounded sequential historical replay under
  explicit interval-cross fill assumptions, with shared cash, slots,
  allowances, collection delay, tax and unsold stock. Its results are separate
  from forward paper observations and actual records.
- `site/`: static, project-subpath-safe Pages viewer. Browser-side expiry hides
  stale plans even when GitHub misses a scheduled run. A roughly 60-second
  browser refresh is independent of collection; IndexedDB keeps private trades
  on this device and never sends them to the repository or public model.
- `.github/workflows/public-research.yml`: one writer, five-minute-offset
  best-effort schedule, state branch persistence, Pages deploy and post-deploy
  acknowledgement. If that acknowledgement fails after a successful deploy,
  the next run verifies the prior deploy before conservatively recovering it.
  GitHub may delay or drop scheduled jobs.

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
finalized future-hour examples, five items, 48 distinct decision clocks and
80% label coverage. A later generation requires new mature examples and clocks.
At most four reviewed candidates may be trained, one undergoing forward
comparison at a time. It fits preprocessing on training data only, uses
chronological gaps, then records later shadow predictions before outcomes.
Promotion to the versioned research ranking requires predeclared lower and
higher side price/unit gates, seven shadow days, 400 shadow pairs and a matched
after-tax sequential portfolio comparison with validated resource assumptions.
Shadow replay uses the verified Pages deployment completion clock plus a
60-second hypothetical manual-action delay. Quotes that expire before a
complete future interval remain zero-action observations. The fixed economic
gate requires at least 500k GP of *simulated terminal equity advantage* under
those assumptions, modeled sales on both sides, and no worse drawdown. The
currently empty verified item-only limit registry blocks economic promotion
until its groups are reviewed; no assumption is upgraded automatically.
A reference-price mark alone cannot satisfy that economic gate. If the gates
do not pass, the rules baseline remains active. No market edge or actual
profit has been demonstrated by the software fixtures.

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

## Personal walkthrough and private backup

In **My Portfolio**, enter an exact hypothetical starting budget (or use a
validated `k`, `m`, `b` suffix), then save. Use the 500k, 1m or 2m controls
to set the minimum *conditional net GP after final shared allocation*. An
optional net GP per item or ROI preference is separate. The deterministic
greedy plan recomputes from currently supported candidates with one cash
balance and slot cap; it is not a full-market optimum or expected GP/hour.
Additional commitments reduce available cash. The review window is a planning
preference, not a quote-validity extension. The public 500m paper history is
unchanged. Personal settings may lower position/slot caps but cannot lift the
provisional 50m actual-pilot ceiling.

In **My Trades · this browser**, create an opening available-cash state and
record any existing stock as opening lots with cost basis. Choose an exact
item ID, enter bought quantity and actual total paid. Enter an optional
partial sale with sold quantity and either actual *net* proceeds or gross
proceeds plus documented exact tax. Net is never taxed twice. Quick entries
assume collection has completed; advanced offer events keep uncollected cash
and stock unavailable. Sold units create realized GP; remaining units keep
their allocated FIFO cost. Corrections append linked audit events; deposits
and withdrawals do not count as flip profit. An over-cap past trade is kept
with a warning rather than hidden.

Download a private backup after each session and use **Validate and merge
backup** to restore it. The SHA-256 check and stable IDs reject corrupt or
conflicting imports; an exact repeated restore is a no-op. Browser storage
can fail or be cleared, and another app on the same `nicknicky19.github.io`
origin shares its security boundary. Keep backups outside public GitHub.
The localhost Python journal offers stronger isolation. No browser trade
records, preferences or account IDs are uploaded to Pages, Actions or models.

## Evidence classes

`CONDITIONAL_SCENARIO` is arithmetic if assumed trades complete.
`HISTORICAL_SIMULATION` is a retrospective sequential replay with named
interval-cross and 10%-participation *fill assumptions*; invoke
`python osrs-flip-lab/simulator.py --input fixture.json`. It returns
`DATA_INSUFFICIENT` when required completed intervals are missing.
`FORWARD_PAPER` holds picks saved before later public observations and makes
no personal fill claim. `ACTUAL_USER_RECORDED` is private journal data only.
These classes are never added together or called AI profit.
