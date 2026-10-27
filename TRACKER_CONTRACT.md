# Future local-first RuneLite import contract (design only)

**No plugin is installed or operated by this release.** A later, reviewed
read-only integration may deliver a user-selected private file to the local
Workbench. It never sends account records to GitHub Actions, Pages or a vendor.

## Versioned envelope

`schema=local-offer-events-v1`, export creation time, source/plugin version,
local account namespace ID, event array, and a content hash. The account ID
is private and must not be a RuneScape name in public logs. Import requires
explicit file selection, validates schema/version/hash, makes an encrypted or
filesystem-protected local backup, and deduplicates by stable event/offer IDs.
Unknown versions pause without guessing.

Each event carries an offer-instance ID that survives slot reuse; account
namespace; slot; item ID/variant; side; offered price and quantity; cumulative
filled quantity and paid/received cash; event type (placed, changed, partial,
completed, cancelled, collected, correction); local observation timestamp;
earliest/latest possible server time; and provenance/confidence. The importer
converts cumulative quantities to nonnegative deltas, rejects regressions
unless a signed correction is present, and never merges two accounts or two
offer instances merely because item/slot match.

Zero-fill cancellations remain attempts, but a cancellation is censoring,
not proof the offer would never fill under another horizon. Partial fills,
uncollected items/cash, price improvement, tax, cancelled remainder and
residual stock remain separate. Offline/login observations record a time
interval, not a fabricated exact execution time. Actual net sale cash is not
taxed twice. Unchosen recommendations have no observed fill label.

Only sufficiently complete, deliberately supplied private records may later
train personal fill-time, attainable-quantity or actual-net models. Training
and artifacts remain local/private. Before any plugin stage, review current
[Jagex client rules](https://legal.jagex.com/docs/rules/macro-and-client-features-not-permitted.md),
[third-party client guidelines](https://secure.runescape.com/m=news/third-party-client-guidelines?oldschool=1),
and the [RuneLite Plugin Hub](https://github.com/runelite/plugin-hub) process.
A fork does not inherit approval. The integration must generate no game input,
click, offer, cancellation or collection.
