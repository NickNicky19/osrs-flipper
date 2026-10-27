# Selective public-source reuse review

Reviewed October 27, 2026. No third-party source code, brand assets, model
weights, backend outputs, account files or dependencies were copied into this
repository. The Python/JavaScript implementation is original continuation of
the supplied Workbench. Public plugin sources informed workflow boundaries.

| Project | Plugin Hub pin | Current repository HEAD | Decision |
| --- | --- | --- | --- |
| [Flipping Copilot](https://github.com/cbrewitt/flipping-copilot) | `de2a7490fd5aaf9e08325fea9e5ca7e3e77ae15b` | same | Borrow explanatory BUY/SELL/WAIT and account-aware *concepts*. Its [handler](https://github.com/cbrewitt/flipping-copilot/blob/de2a7490fd5aaf9e08325fea9e5ca7e3e77ae15b/src/main/java/com/flippingcopilot/controller/ApiRequestHandler.java) sends authenticated backend requests; cloning does not supply that engine or license a vendor service. No vendor calls. |
| [Flipping Masterminds](https://github.com/ca-gray/Flipping-Masterminds) | `5aeba7b2546f75f65a9b371cf1c5afc7b5b1d911` | `ad8cfb39493925623623a6950bea589782350291` | Reimplemented bounded history/cache and side-volume presentation. Current [fetcher](https://github.com/ca-gray/Flipping-Masterminds/blob/ad8cfb39493925623623a6950bea589782350291/src/main/java/com/flippingmasterminds/PriceDataFetcher.java) mixes v1 mapping and v2 market routes; this release verified v2 mapping independently. Price arrows are not fills. |
| [Flipping Utilities](https://github.com/Flipping-Utilities/rl-plugin) | `c08d2e93a5cc2e56672c353708e07b70b9dedb10` | `ede300cd3c1c7f6d3e6c115507fe1b30abb35448` | Borrowed the event/partial/duplicate/account-isolation *questions* for a future private import boundary. The pinned [handler](https://github.com/Flipping-Utilities/rl-plugin/blob/c08d2e93a5cc2e56672c353708e07b70b9dedb10/src/main/java/com/flippingutilities/controller/NewOfferEventPipelineHandler.java) screens duplicate/login events and filters zero-quantity updates in one history path; a positive-fill export is not a complete attempt dataset. No plugin installed or run. |

All three inspected root licenses are BSD 2-Clause. That does not license
private services, account data or trademark assets. Since no code/assets were
copied, `THIRD_PARTY_NOTICES.md` records source attribution only.

Relevant issue reports were treated as leads, not reproduced facts:
[Utilities #73](https://github.com/Flipping-Utilities/rl-plugin/issues/73)
discusses SQLite and GE import;
[Utilities #81](https://github.com/Flipping-Utilities/rl-plugin/issues/81)
reports zero-consumption partial-offer history growth;
[Copilot #6](https://github.com/cbrewitt/flipping-copilot/issues/6)
reports a same-tick record gap; and
[Copilot #114](https://github.com/cbrewitt/flipping-copilot/issues/114)
reports account-scope mixing. The current release does not assert those
reports affect every installed version.

## Completion v3: Copilot live-prices reference

The owner supplied [live prices](https://flippingcopilot.com/live-prices)
and [published terms](https://flippingcopilot.com/legal-and-tos) as optional
references. Ordinary page viewing in this review exposed the page titles but
no inspectable live rows or reusable data contract. The owner's dated
specification reports indexed terms restricting the internal API and scraping;
the present viewer could not independently text-extract the current terms.
No vendor feed, authentication, internal API discovery, scraping, prediction
label or account link is used. Sorting and filter ideas are implemented
independently from the qualified Wiki feed. A future integration would need
separate applicable written permission and review. This release functions
without Copilot.
