# LIMITS

What the Crisis Scenario Engine does not cover. One page, kept in step with the code. Parameter sources are in `docs/CALIBRATION.md` and on the app's Assumptions page.

## What it is
A generator of the quantitative script of a bank crisis for a simulation exercise, with a console to run the exercise. It is not a supervisory model, a stress test or a forecast. Every bank is fictitious.

## The model
- **One bank, one currency, whole days.** Times of day are labels; the outflow by time block is an allocation of the daily figure. No intraday liquidity, no payment systems.
- **Assumptions, not estimates.** Run-off rates, the panic curve, haircuts, flight-prone shares, weekend rules and every challenge effect are assumptions unless the Assumptions page gives a source. The comparison with real cases is an order-of-magnitude check.
- **Ranges are not probabilities.** The sensitivity runs sample assumed intervals.
- **Liquidity first.** Capital moves only through fire-sale losses, portfolio sales and a loss revealed by a news event. No credit risk model, no earnings, no market risk, no interest-rate risk, no derivatives book.
- **Simple funding.** One wholesale ladder; collateral in four classes with fixed haircuts; one ordinary central bank facility and ELA. No repo market, no collateral calls, no intragroup funding.
- **Deposits.** Four categories. Covered deposits are the insured retail deposits; no single-customer view, no depositor concentration.
- **Market shocks** (rating downgrade, fall in share and bond prices) are a panic shock and a cut in wholesale roll-over. The cut is not in the projected outflows behind the "likely to fail" warning. No share price, bond price or rating is modelled.
- **No contagion and no second bank** in the exercise (the old contagion code is not used). No system-wide scenario.
- **No cross-border group**: no parent and subsidiary, no home and host authorities, no resolution college.

## Resolution
- **Order of loss absorption is the model's**, not a statement of law. Legal loss-absorption thresholds, the use of resolution financing arrangements and State aid are not modelled.
- **The valuation is an assumed write-down** of the loan book (low, central, high). No valuation methodology, no "no creditor worse off" comparison.
- **Sale of business is computed like bail-in.** The buyer's price is an assumption that changes no other figure; no bids, no perimeter, no data room. No bridge institution, no asset separation.
- **The run stops when the tool is applied.** Nothing after the bank reopens: no market reaction to the decision, no restructuring plan.
- **Liquidation** is a waterfall on assumed asset values; the deposit guarantee scheme's funding is not modelled.

## The exercise script
- **Wording is a draft** by this project: injects, role cards, rules of play, charter proposals, expected responses and coordination steps have not been reviewed by a practitioner and carry no legal references.
- **Who is consulted and informed is a proposal**, not a statement of legal duties. The competent authority relays every stage change.
- **Five simulated stakeholders** only: bank management, independent valuer, media, finance minister, potential buyers. No shareholders, auditors, market authority, competition authority, foreign authorities, central counterparties or other banks.
- **Information challenges are fixed patterns**: a press conference, an incomplete notification, a notification to the wrong player. Leaks and misinformation are not generated.

## Live mode and closing report
- **No accounts and no security.** Link codes prevent mistakes only; the control console is open to anyone who can reach the app; saved exercises are plain files.
- **Local use.** One machine or a local network; pages refresh by polling, a few seconds behind.
- **Decisions take effect the next simulated day.** The path assumes the planned decisions until their day has passed. Injects already delivered are not rewritten when the path changes.
- **The report records, it does not assess.** "Not recorded as done" means no record. Counterfactuals change one decision, use the central parameters and inherit every limit above.

## Areas of the Handbook's Annex 2 outside the tool
Annex 2 (EBA/REP/2025/20) lists areas a resolution authority could test. The tool gives figures and injects for a few of them and plays none of the work itself.

| Annex 2 area | In the tool |
|---|---|
| Internal preparation (confidentiality protocols, insiders list) | No |
| Hiring legal or strategic advisers and a valuer | No. A valuer is played by the control team |
| Request for access to the entity's information | No |
| Analysis of potential bidders and sales structures | No. One inject: a buyer shows interest, or all withdraw |
| Coordination with competition, market, foreign-investment and non-banking supervisors | No. Only the four authorities of the script |
| Feedback on market interest | No |
| Valuation report: request, preparation, assessment | Partly: a provisional and an updated valuation as figures |
| Setting up a virtual data room | No |
| Receiving binding offers | No |
| Analysing resolution conditions, with or without State aid | Partly: the tool decision and its figures; no public interest assessment, no State aid |
| Drafting the resolution decision; consultation in a resolution college | No |
| Write-down or conversion of capital instruments and bail-in of senior debt | Partly: amounts by layer; no execution |
| Sale of business by buy-side and sell-side teams | No |
| Approving a bridge institution | No |
| Results of the tool for shareholders; restructuring plan | No |

The decisions the tool does script (FoLtF determination, ELA, early intervention, moratorium, payout preparation and payout) are not in Annex 2, which covers the resolution authority's own tasks.
