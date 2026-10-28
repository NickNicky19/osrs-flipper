# Completion v3 acceptance record

The owner supplied the complete v3 continuation specification in this task on
27 October 2026. The named .txt was not accessible as a file. This release
continues NickNicky19/osrs-flipper from bfd4f72 and preserves the existing
paper-data branch and issued history. All game actions remain manual.

## 28 October correction checkpoint

The `codex/v3-acceptance-gaps` continuation started from main `c864b528`.
PR #7 merged normally as `9cef29d63bfc3a1800cd118cbfc7764f6e365fb0`;
its tested head was `3e646bf8d2d7456b40b244ac0f84a26821bdc46a`.
The merged code is deployed at https://nicknicky19.github.io/osrs-flipper/.
Current Windows results: 202 Python application tests passed, 21 Node site
tests passed, and `git diff --check` passed. The Python suite's localhost tests ran
with approved host access. The correction set adds two-hour persisted research
cohorts, more complete model provenance and fixed forward comparison gates;
shadow and active selection now use one two-side rank formula. The bounded
simulator supports predeclared cancel/reprice events, and the site adds numeric
history/stress summaries, recorded item allowance deduction and resilient
publication-receipt polling. A read-only diff review found policy parity,
replay capacity, chart-window, missing-feature fallback and final-bucket
receipt defects; all five were corrected and covered by focused regressions.
No real model has been trained or promoted. The deployed site correctly shows
`DATA_INSUFFICIENT` and keeps the rules baseline active.

The merge-triggered **push** run `40775544952` completed research, Pages
deployment and acknowledgement. Its durable `paper-data` receipt is artifact
`8b6a5d08aa403cb4190ade799a21ae96`, deployment `40775544952-1`, and
export SHA-256 `704e78c9b7695db6a5efa15a70498bfbd189f96a0377c3441ff5f27ebe5a80a9`.
The fetched public `data.json` matched that SHA-256. The deployed project-root
page loaded `./app.js?v=completion-v3-5`. The verified deployment completed at
21:53:29 UTC; the earliest entry reference retained 163 seconds of validity
at that time. A real browser showed four conditional paper plans while valid,
and individual plans expired without a page reload. At a 1m GP High Total GP
preference it showed `NO_ACTION`; 390px and 1280px layouts had no horizontal
overflow. This is software/publication evidence, not fill or profit evidence.

The original schedule query at 23:48 UTC on 27 October was empty **at that
time**. A new check at 21:55 UTC on 28 October found five successful
`event=schedule` runs: 40606674763, 40638443582, 40680762736,
40734426408 and 40774268101. The latest schedule began at 21:43:16 UTC
against the preceding main `c864b528`; it is distinct from the later
merge-triggered push run. Its research, Pages deployment and acknowledgement
completed. The `paper-data` branch subsequently continued to `a8ce5c8` for
the push receipt, with 30 picks, 23 outcomes and 1,061 observations. Scheduled
runs have been separated by hours, so the advertised approximately five-minute collection cadence is
**not established**. Browser validity rules expire old quotes independently.
The exact remaining unattended-operation check is a sustained interval of
scheduled source/state/deploy/receipt runs that leaves useful quote validity
when the artifact becomes visible; no extra scheduler or TTL increase is
authorized by this evidence.

| Requirement | Code and executed evidence | Status |
| --- | --- | --- |
| Reproduce dated defects without resetting newer work | public_pipeline.py, learning.py, site/app.js; final 202 Python and 21 Node tests | PASS |
| Browser A→B refresh, stale expiry, forms and mobile/subpath | refresh.mjs, app.js; local A→B preserved an unsaved quantity; deployed tab changed artifact 8d51cc75 to b18a5796 without reload; 390px deployed viewport had 375px content and scroll widths | PASS |
| Nullable units, Retry-After, denial/circuit, request clocks | public_pipeline.py; captured-format/restart regressions; final live Windows cycle 18 calls, 2,988,788 bytes | PASS_LOCAL |
| Accurate deploy identity and pick publication clock | public-research.yml, verified deploy-job completion, delayed-recovery regression; current Pages receipt 8b6a5d08 / 40775544952-1 matched public export SHA; browser gates current plans on that receipt | PASS |
| Recurrence and durable state across runners | one active workflow; five successful scheduled runs through 40774268101, then push run 40775544952; `paper-data` a8ce5c8, 30 picks/23 outcomes/1,061 observations; schedule gaps of hours | PERSISTENCE_PASS; SCHEDULE_OBSERVED; FIVE_MINUTE_CADENCE_FAIL |
| Broader supported universe, coverage stages, graphs | public-rules-2, source review; deployed capture had 4,551 observed / 4,078 rule-supported / 75 research eligible and four conditional benchmark plans before expiry | PASS; unknown shared limits excluded |
| High Total GP preference and personal budget | planner.mjs, broad 4,551-row allowlisted screener, shared cash/slot/pilot caps; Node arithmetic and deployed browser NO_ACTION at 1m | PASS |
| Browser My Trades and opening state | journal-core.mjs, journal-db.mjs, personal-ui.mjs; exact gross/net, partial sale, open stock, cross-tab, backup merge in local browser | PASS_LOCAL; same-origin privacy boundary disclosed |
| Controlled learning generations | learning.py, fixed ridge family, immutable evidence checksum, future-hour targets, two-generation fixture, verified publication clock for shadow economics; no real trained model | PASS_SOFTWARE; real mature evidence pending |
| Historical replay/simulation | simulator.py fixture: 180 GP modeled at 10% participation; 2/5/10% sensitivities; real capture returns DATA_INSUFFICIENT | PASS_SOFTWARE; real result unavailable |
| Four evidence classes and no invented actual profit | conditional, historical simulation, forward public path, private actual journal separate in Python/site | PASS |
| Copilot, Masterminds, Utilities and license review | REUSE_DECISIONS.md; Copilot live-prices reference only, no vendor API or plugin | PASS |
| Public-export privacy | recursive private canaries, allowlist, no private network request in site source; local browser privacy inspection | PASS_LOCAL |
| PR, merge, Pages URL and artifact receipt | PRs #4 and #5 previously merged; PR #7 merged at 9cef29d; push run 40775544952 deployed artifact 8b6a5d08aa403cb4190ade799a21ae96 with matching public export SHA at https://nicknicky19.github.io/osrs-flipper/ | PASS |
| Actual trading edge and 1b outcome | requires later forward market evidence and deliberately recorded complete manual trades | PENDING_FUTURE_EVIDENCE |

| 28 October correction requirement | Code and current executed evidence | Status |
| --- | --- | --- |
| Repeated refresh and receipt resilience | `site/publication.mjs`, `site/app.js`; Node unchanged-poll and missing-receipt retries; deployed browser observed expiry without reload | PASS |
| Numerical history and downside | `site/history-analysis.mjs`, `public_pipeline.py`; half-open window and chronological downside tests; deployed browser showed recent histories | PASS |
| Personal recorded allowance and adverse arithmetic | `journal-core.mjs`, `planner.mjs`, `personal-ui.mjs`; Node recent-buy/open-offer, final-quantity, 1-GP and 5% stress tests; deployed page showed stresses | PASS |
| Learning feature/policy parity and bounded generations | `learning.py`, `public_pipeline.py`; two-generation, leakage, rank-parity, fixed shadow-window and provenance regressions | PASS_SOFTWARE; real model/forward evidence PENDING |
| Replay capacity and manual changes | `simulator.py`; seven-day 14,112-bar, cancellation, repricing and conservation fixtures | PASS_SOFTWARE; actual fills UNKNOWN |
| Latest deployed artifact and browser layout | PR #7 merge 9cef29d, push run 40775544952, artifact 8b6a5d08aa403cb4190ade799a21ae96, public SHA matched tested export; 390px/1280px browser check | PASS |

Final correction command results: Windows Python `python -m unittest discover -s tests -q` from `osrs-flip-lab/`: 202 tests, OK (localhost socket tests needed approved unrestricted execution; restricted execution returned WinError 10013 for 19 tests). Node `node --test site/tests/*.test.mjs`: 21/21 passed. `git diff --check`: clean. The original handoff's 149 application and 15 diagnostic tests are historical separate suites, not this run's count.

The final local live source cycle local-source-qualify-v3-final-1 reported CAPTURED_WITH_REVIEW, artifact 85b27575d27b1eedb21478f73d7723dc, 81 eligible research rows and four frozen-baseline paper plans. It is a manual local cycle, not a scheduled GitHub run. Real data did not provide a complete entry window for the named simulator assumptions, so no real-data simulated profit is reported. Learning remains DATA_INSUFFICIENT: no real model trained, no mature shadow comparison, no research promotion and no actual-profit validation. The currently empty verified item-only limit registry is an explicit economic-promotion gate; it is not filled from assumptions.

The browser journal is IndexedDB on nicknicky19.github.io. Other projects on that same origin share a browser security boundary even though this project never uploads the journal. It is unencrypted browser storage and exported backups are plaintext; users should keep no secrets there and retain private backups. The localhost Workbench remains the stronger isolation option. Storage can be cleared or denied. This is the boundary the owner specified, not a claim of project-path isolation.

PR #4 and PR #5 release checks passed on their exact heads. Their normal merges completed and the push-triggered collection, data-branch continuation and Pages artifacts were verified. The final artifact at that earlier checkpoint had zero current benchmark picks and displayed NO_ACTION with its matching 23:46:11 UTC deploy receipt. The earlier push artifact had four conditional plans with 164–197 seconds of entry-reference validity remaining at verified deploy completion; no fill is inferred. A fresh deployed browser showed the verified receipt, local journal controls and no horizontal overflow at 390px. The public browser's open tab observed the newer artifact without reload. The earlier durable receipt was paper-data commit 32fb335. Later scheduled execution is documented above; its long gaps leave five-minute unattended freshness unverified. GitHub may delay or drop schedules; the browser never extends quote TTL.
