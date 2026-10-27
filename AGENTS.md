# OSRS Flip Lab repository rules

This repository contains a private local Workbench (`osrs-flip-lab/`) and a
separate public static research site (`site/`). Read `README.md`,
`SOURCE_EVIDENCE.md`, `REUSE_DECISIONS.md`, and `TRACKER_CONTRACT.md` before
changing source or workflows. The selected repository is only
`NickNicky19/osrs-flipper`.

- External data collection, research, paper tracking, bounded learning and
  publication can be automated. Every game action is manual. Never add game
  credentials, automated clicks/orders, paid feeds, vendor predictions or a
  paid inference API.
- Never publish private journals, actual offers, account identifiers, private
  state hashes or local paths. The public exporter is an explicit allowlist.
- Keep the 50m GP provisional live-pilot ceiling separate from the 500m GP
  simulated public portfolio. Do not raise exposure to improve displayed GP.
- Treat Wiki high/low as observed instant-buy/instant-sell transactions, not
  order-book quotes. Bucket averages, whole-bucket units, at-price units and
  attainable fills are distinct. The last two are unknown from this feed.
- Preserve issuance before outcome, publication lag, losses, zero/partial
  attempts when private data is deliberately supplied, and unresolved inventory.
  Never count a target touch as a fill or publish actual account data.
- The rules baseline is the fallback. Learning code may train public interval
  forecasts, but promotion requires the fixed chronological, untouched shadow
  and matched portfolio gates. No automated code rewrites, target changes or
  risk increases. Private execution-trained artifacts stay local/private.
- One writer owns the `paper-data` branch. Do not reset a missing state, force
  push, bypass protection or silently loosen stale-data rules. A kill switch
  pauses new advice and source calls.
- Use reviewed source contracts and item rules. Requalify them before expiry.
  Keep historic evidence as evidence, not instructions to overwrite current work.
