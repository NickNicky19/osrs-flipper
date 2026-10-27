# OSRS Flip Lab 0.3 — independent research workbench

**Current repository note (27 October 2026):** This file documents the supplied
local Workbench and its original environment. The separate public site and
subsequent source verification are documented in the repository-root
`README.md` and `SOURCE_EVIDENCE.md`. Historical probe and test results below
retain their original date and are not current production status.

Built 25 October 2026. Local, manual, no subscriptions, no runtime AI API,
no RuneLite plugin, no game input. Python 3.11+ standard library only.

**Status:** the market-data adapter, research engine, journal and website are
implemented and tested with synthetic responses. The public feed is NOT verified
in this environment: the actual probe failed DNS resolution. No trained fill
model, optimal quantity, expected GP/hour, or profitable advantage is claimed.
The dated `BUILD_STATUS.json`, `REVIEW_FINDINGS.md` and `TEST_RESULTS.txt`
remain in the original supplied handoff folder as archived evidence.

## Start here

1. Extract the ZIP to a NEW writable folder. Do not overwrite old journals.
2. On Windows run `START_DEMO.bat`. Alternatively run:

   ```sh
   python app.py --mode demo --open
   ```

3. The Opportunities screen contains the new research workbench. All demo items,
   observations, volumes, tax exemptions and rules are synthetic and labeled.
   The replay clock is fixed; it is NOT the current market.
4. Click **Explore**, change prices or quantity, and calculate. Inspect exact
   conditional margin, break-even sale price, downside scenarios and constraints.
   **Save this research receipt** preserves the evidence, not an order.
5. My Offers records only manual actions. Results shows actual recorded net
   cash separately from uncertain unsold inventory. Readiness states limitations.

No extra runtime packages are necessary. Developer browser tests optionally
require Playwright and Chromium; they are NOT required to use the application.
Close the console or press Ctrl+C to stop. No background job is installed.
Windows launchers are provided but have not been executed on Windows here.

## What is new in 0.3

- An opt-in public-source collector with a fixed host/route allowlist, bounded
  responses, explicit identification, schema checks and stop-on-error behavior.
- A separate market SQLite store containing compressed original responses,
  hashes, first availability, repeat confirmations, errors and revisions.
- Point-in-time response views. A later revision does not alter an earlier
  decision. Repeated downloads never multiply reported volume. A response that
  reverts from A to B to A is represented correctly.
- Separate known coverage for each price side. Missing/NULL activity stays
  unknown; explicit reported zero is different. Prices are weighted by units
  only for descriptive summaries, not treated as executable quotes.
- Individual trade age versus download age; expiring local source/rule reviews;
  explicit exclusion reasons, with the option to inspect excluded observations.
- Multiple transparent price scenarios instead of the legacy demo's fixed
  one-GP offsets. NO automatic recommended quantity.
- Exact integer break-even calculation, 0%/1%/3%/5% exit-decline scenarios,
  and quantity/cash/limit warnings. These shocks are not likelihoods or maximum
  possible losses. Reported-volume shares are not capacity estimates.
- Saved research receipts include data hashes, policy/rule hashes, scenarios,
  rejected candidates and the recorded account state. An optional receipt link
  can be attached to a subsequently recorded offer for the SAME item.
- Attempt diagnostics keep zero-fill cancellations and unfinished offers.
  A receipt link is attribution, NOT proof the advice predated the actual game
  action. No recommendation is counted as a fill.
- Unsold-inventory reference scenarios include collected, uncollected purchased
  and sale-reserved units once. Unknown prices prevent a total valuation; a
  low-side reference is NOT guaranteed executable liquidation.

The original demonstration workflow remains behind a disclosure. Its old 1%
volume-share sizing and 5m per-item cap are still arbitrary demo rules. They are
not used by the new public-data research screen.

## Real-data connection: explicit, not automatic

The app itself never fetches public data or starts collection. The collector is
separate so closing the browser does not kill an independently running collector.
Closing the computer/process DOES stop collection.

### 1. One connection check

On Windows, `PROBE_PUBLIC_DATA.bat` asks for an identifying project/contact
User-Agent. Do not enter a game password, API key, or account credentials.
Or use:

```sh
python connection_check.py --user-agent "Your project/contact identification"
```

This makes at most one attempt per supported route and stops on the first
failure. It saves `data/connection-check.json` and original successful responses
in `data/market.sqlite3`. It is NOT an access-policy/semantic approval. The
provisional adapter targets `/api/v1/osrs/mapping`, `/latest`, and `/5m`; their
CURRENT contract must be checked on the target machine. No HTML scraping,
commercial feeds, proxy bypass or vendor recommendation APIs are used.

### 2. Record an actual source review

Copy `config/source-review.example.json` to `config/source-review.local.json`.
It is deliberately unapproved. After checking current provider documentation,
record actual references, review/expiry timestamps, identification and whether
bucket timestamps mean START or END. Set `reviewed` true only after that check.
A successful JSON response is NOT sufficient evidence for that decision.

Review the current source documentation:
https://oldschool.runescape.wiki/w/RuneScape:Real-time_Prices

The documentation was blocked to the review browser, and the execution
container could not resolve the API host. This is an environment limitation,
not a claim that the service is unavailable on the user's machine.

### 3. Explicitly collect

```sh
python collector.py --once --review config/source-review.local.json
python collector.py --watch --review config/source-review.local.json --interval 60
```

Use the interval only when current provider terms permit it; slower settings
may be required. The process fetches latest prices per cycle, five-minute data
at most once per five-minute wall-clock bucket, and metadata daily. There is
no bulk historical backfill or inferred missing history. It stops on an error
rather than hammering the source. Ctrl+C stops it. No scheduler is installed.
An outage leaves known records intact and stops unsupported new-entry analysis.

### 4. Review supported item rules

`config/item-rules.json` is intentionally EMPTY, rather than an invented real
exemption list. Public observations can be viewed without pretending their tax
rules are supported. A reviewed item entry has this structure:

```json
{
  "REAL_ITEM_ID": {
    "tax_regime": "ordinary-2pct-cap-5m",
    "tax_exempt": false,
    "effective_from": 0,
    "reviewed_at": 0,
    "expires_at": 0,
    "reference": "Actual item-specific rule evidence"
  }
}
```

Replace placeholders with VERIFIED values and integer Unix times. Zero dates
are intentionally invalid. False is a boolean, not the string "false". A
verified public item limit is NOT the account's remaining limit; shared groups
and unknown past purchases remain unqualified. The 2025 Jagex tax announcement
is a starting reference, not an exhaustive current item-rule registry:
https://secure.runescape.com/m=news/yama-cas--more?oldschool=1

### 5. View public observations

```sh
python app.py --mode journal --open
```

Use `--market-database PATH` or `--item-rules PATH` for explicitly selected
locations. If source/rules are unsupported or stale, affected calculations are
blocked and the reason is visible. There is no fallback to synthetic prices.
New source captures take effect when you press **Refresh saved data**.
Public scenario views expire after 60 seconds or earlier source-review expiry;
individual quote/rule freshness is also checked on calculation.

## Journal, money and privacy

Demo and real journals are separate; market stores enforce the same separation.
Manual mode starts from the given 500m cash with no inventory or offers. That is
NOT a verified reading of your account. Opening inventory/offers imports,
account purchase-window tracking, shared limits and auditable corrections remain
unfinished. Do not call this a complete account ledger for an unreconciled account.

The unchanged 50m provisional exposure ceiling is a guard, not a stop-loss
promise. Factual exceptions can record an action that ALREADY happened with
explicit reasons/warnings. They never authorize a new trade or invent money.
Fill quantities/cash are NEW deltas. Sale cash is ACTUAL NET proceeds; it is not
taxed again. Recording time is not an asserted server execution time.

No private logs, accounts or identifiers are sent to a vendor. The website binds
to 127.0.0.1 and is not appropriate for public hosting. Data is not encrypted by
this app. Local malware or a user editing every file is not defeated by hashes.
Research exports include recorded account snapshots inside receipts: keep them
private. Do not commit `data/`, local reviews or private exports to Git.

## Backups and compatibility

The journal Export backup control keeps its original behavior. Restore into a
NEW path only:

```sh
python app.py --restore path/to/backup.json --database data/restored.sqlite3
python app.py --mode journal --database data/restored.sqlite3 --open
```

v0.1/v0.2 journal exports are supported and replay-checked. Preserve the original
before migration. No real user database was changed in this delivery.

The separate **Export research archive** saves raw capture bodies, attempts and
receipts; this is a readable research archive, not a one-click full-app restore.
Preserve the research SQLite file with the collector/server stopped when making
a full local backup. Do not claim that journal export alone includes market data.

## Tests and evidence

```sh
python -m unittest discover -s tests -v
```

See `TEST_RESULTS.txt` for the actual run. Browser smoke checks:

```sh
python dev/browser_v03.py
```

The browser test attempts real local HTTP, then explicitly labels a mock fallback
when administrator policy blocks it. Mocked UI uses the actual ledger/research
engine. Direct local HTTP is separately exercised by the Python test suite.
No bypass of the managed-browser policy is attempted.

`evidence/v0.3-source-preflight.json` records the real blocked connection attempt.
`evidence/v0.3-browser.json` distinguishes what passed and what was blocked.
`evidence/v0.3-research-desktop.png` and companion images show actual rendered UI.

## Research boundaries and next milestone

The model does not yet estimate fill probability, waiting time or future prices.
The named sorts describe captured margins/activity; they do not optimize expected
portfolio profit. Conditioning arithmetic on full execution is not forecasting.
Later work needs real attempt observations, well-defined horizons, a retained
baseline and forward evaluation including unsold inventory and attention.

The immediate next task is in `NEXT_STEPS.md`: verify the current public-source
contract and a small real item subset on the user's authorized machine, then
run full target-machine/browser acceptance. Do not rewrite the interface, install
a plugin, buy Copilot, or launch an unbounded model search.
