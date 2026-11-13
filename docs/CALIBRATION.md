# CALIBRATION

Status on 2026-11-11: part **a** (archetype proportions) is done for two of the four archetypes.
Part **b** (comparison with real cases) is done as a comparison only: no parameter was tuned.
Everything not listed here as sourced is an ASSUMPTION; the app's Assumptions page shows the
status of every value.

## 1. Data files

Source: EBA, 2025 EU-wide Transparency Exercise, full database, as listed on
<https://www.eba.europa.eu/risk-and-data-analysis/risk-analysis/eu-wide-transparency-exercise>.
Downloaded on 2026-11-11 from `https://www.eba.europa.eu/assets/TE2025/Full_database/883401/`
into `data/raw/TE2025/` (not committed to git). The EBA page states that the exercise is
discontinued from June 2025 and that the data will no longer be updated.

| File | Size (bytes) | SHA-256 |
|---|---|---|
| `tr_oth.csv` | 15,110,064 | `1785417e172907b3a49623eb13e2cc6c7af26a02ae982bfe2ba3a70f782e4104` |
| `TR_Metadata.xlsx` | 2,782,361 | `8110aea2ed614bc86cb5e00058787e8fe2b5e1ce816a5ec1f9346647668eb5df` |
| `SDD.xlsx` | 58,751 | `8b7fdf40bacb79d0bd51564c4cc157f68261a2290a209db18df9fa8eb6ee66cb` |

`tr_oth.csv` holds the templates Capital, Leverage, RWA OV1, P&L, Assets and Liabilities for
120 reporting entities at four dates (`Period` 202409, 202412, 202503, 202506). Its columns are
`LEI_Code, NSA, Period, Item, Label, ASSETS_FV, ASSETS_Stages, Exposure, Financial_instruments,
Amount, Fin_end_year, n_quarters, Row, Column, Sheet`. Amounts are in EUR million.
`TR_Metadata.xlsx` gives the bank names (sheet "List of Institutions") and the code lists
(sheets "Exposure" and "Financial_instruments"). `SDD.xlsx` is the item dictionary.

Not downloaded: `tr_cre.csv` (credit risk), `tr_sov.csv` (sovereign), `tr_mrk.csv` (market risk).

## 2. How to reproduce

```
.venv/bin/python scripts/calibrate_te2025.py
```

The script prints the checksums, the peer banks, the median shares and the resulting blocks of
`config/banks.yaml`. `tests/test_calibration.py` checks that the config equals the script output
whenever the raw file is present.

## 3. Which number comes from which column

All rows are filtered on `Period = 202506`, `ASSETS_FV = 0`, `ASSETS_Stages = 0`, and the value
is the column `Amount`. The row with `LEI_Code = XXXXXXXXXXXXXXXXXXXX` (an "All Other banks"
aggregate) is excluded. 107 banks report every item below. Each item is divided by the same
bank's total assets; the archetype uses the **median across the peer group** of that share.

| Model line (`config/banks.yaml`) | `Item` | `Financial_instruments` | `Exposure` | Label in the file |
|---|---|---|---|---|
| (denominator) | 2521010 | 0 | 0 | Total Assets |
| `cash_reserves` | 2521001 | 0 | 0 | Cash, cash balances at central banks and other demand deposits |
| `insured_retail` + `uninsured_retail` | 2521215 | 30 (Deposits) | 401 (Households) | Financial liabilities - by instrument and by counterparty sector |
| `corporate` | 2521215 | 30 | 301 (Non-financial corporations) + 102 (General governments) | same |
| `financial` | 2521215 | 30 | 202 (Financial corporations other than credit institutions) + 201 (Credit institutions) | same |
| `wholesale_funding` (total) | 2521215 | 40 (Debt securities issued) | 0 | same |
| `cet1` | 2520102 | 0 | 0 | COMMON EQUITY TIER 1 CAPITAL (net of deductions and after applying transitional adjustments) |
| `rwa` | 2520138 | 0 | 0 | TOTAL RISK EXPOSURE AMOUNT |

Derived lines:
- `illiquid_assets` = total assets - cash - HQLA Level 1 - HQLA Level 2 - other eligible collateral.
- `other_liabilities` = total assets - deposits - wholesale funding - CET1. It therefore contains
  deposits from central banks (item 2521215, instrument 30, counterparty 101), derivatives, other
  liabilities and the part of total equity (item 2521216) that is not CET1.

Mapping choices (approved 2026-11-11): government deposits count as corporate deposits; deposits
from credit institutions count as financial deposits; "cash" includes other demand deposits, so
it is an approximation of cash and central bank reserves.

## 4. Peer groups and results

Rules: **universal** = total assets (item 2521010) of EUR 250 bn or more; **retail** = total
assets below EUR 250 bn and household deposits of at least 40% of total assets.

Median share of total assets, June 2025:

| Item | Universal (28 banks, 10 countries) | Retail (40 banks, 19 countries) |
|---|---|---|
| Cash | 9.94% | 7.95% |
| Deposits from households | 29.56% | 54.34% |
| Deposits from non-financial corporations | 16.93% | 15.02% |
| Deposits from general governments | 2.61% | 2.86% |
| Deposits from other financial corporations | 7.01% | 2.52% |
| Deposits from credit institutions | 5.96% | 1.68% |
| Deposits from central banks (in other liabilities) | 1.03% | 0.00% |
| Debt securities issued | 18.13% | 6.13% |
| Total equity (not used directly) | 5.93% | 9.34% |
| CET1 capital | 4.76% | 7.52% |
| Total risk exposure amount | 30.33% | 39.55% |

Medians are taken item by item, so they do not add up to 100%; the two derived lines absorb the
difference. The shares are applied to the fictitious size of each archetype (EUR 1,000 bn and
EUR 60 bn), which is unchanged.

The retail rule is purely numerical. It therefore also selects some online or specialised banks
that happen to be funded by household deposits; this was not corrected by hand.

### Universal peer group

| Country | LEI | Name as in `TR_Metadata.xlsx` |
|---|---|---|
| FR | `R0MUWSFPU8MPRO8K5P83` | BNP Paribas |
| FR | `FR969500TJ5KRTCJQWXH` | Groupe Crédit Agricole |
| ES | `5493006QMFDDMYWIAM13` | Banco Santander, S.A. |
| FR | `FR9695005MSX1OYEMGDF` | Groupe BPCE |
| DE | `7LTWFZYICNSX8D621K86` | DEUTSCHE BANK AKTIENGESELLSCHAFT |
| FR | `O2RNE8IBXP4R0TD8PU41` | Société générale S.A. |
| NL | `549300NYKK9MWM7GGW15` | ING Groep N.V. |
| FR | `9695000CG7B84NLR5984` | Confédération Nationale du Crédit Mutuel |
| IT | `549300TRUWO2CD2G5692` | UNICREDIT, SOCIETA' PER AZIONI |
| IT | `2W8N8UU78PMDQKZENC08` | Intesa Sanpaolo S.p.A. |
| ES | `K8MS7FD7N5Z2WQ51AZ71` | Banco Bilbao Vizcaya Argentaria, S.A. |
| NL | `DG3RU1DBUFHT4ZF9WN62` | Coöperatieve Rabobank U.A.2 |
| DE | `851WYGNLUQLFZBSYGB56` | COMMERZBANK Aktiengesellschaft |
| ES | `7CUNS533WID6K7DGFI87` | CaixaBank, S.A. |
| DE | `529900HNOAA1KXQJUQ27` | DZ BANK AG Deutsche Zentral-Genossenschaftsbank, Frankfurt am Main |
| FI | `529900ODI3047E2LIV03` | Nordea Bank Abp |
| DK | `MAES062Z21O4RZ2U7M96` | Danske Bank A/S |
| NL | `BFXS5XCH7N0Y05NIXW11` | ABN AMRO Bank N.V. |
| DE | `B81CK4ESI35472RHJ606` | Landesbank Baden-Württemberg |
| BE | `213800X3Q9LSAKRUWY91` | KBC Groupe |
| SE | `F3JS33DEI6XQ4ZBPTN86` | Skandinaviska Enskilda Banken - gruppen |
| FR | `96950066U5XAAIRCPA78` | La Banque Postale |
| SE | `NHBDILHZTYCNBV5UYZ31` | Svenska Handelsbanken - gruppen |
| NO | `549300GKFG0RYRRQ1414` | DNB BANK ASA |
| DE | `VDYMYTQGZZ6DU0912C88` | Bayerische Landesbank |
| DK | `LIU16F6VZJSD6UKHD557` | Nykredit Realkredit A/S |
| FR | `F0HUI1NY1AZMJMD8LP67` | HSBC Continental Europe |
| ES | `SI5RG2M0WQQLZCXKRM20` | Banco de Sabadell, S.A. |

### Retail peer group

| Country | LEI | Name as in `TR_Metadata.xlsx` |
|---|---|---|
| IT | `NNVPP80YIZGEY2314M97` | ICCREA BANCA S.P.A. |
| IT | `N747OI7JINV7RUUH6190` | BPER Banca S.p.A. |
| IE | `635400AKJBGNS5WNQL34` | AIB Group plc |
| IE | `635400C8EK6DRI12LJ39` | Bank of Ireland Group plc |
| PL | `P4GTT6GF1W40CVIMFR43` | Powszechna Kasa Oszczednosci Bank Polski S.A. |
| HU | `529900W3MOO00A18X956` | OTP-csoport |
| PT | `TO822O0VT80V06K0FH57` | Caixa Geral de Depósitos, S.A. |
| PT | `JU1U6S0DG9YLT7N8ZV32` | Banco Comercial Português, SA |
| GR | `JEUVK5RWVJEN8W0C9M24` | Eurobank Ergasias Services and Holdings S.A. |
| ES | `5493007SJLLCTM6J6M37` | Unicaja Banco, S.A. |
| IT | `LOO0AWXR8GF142JCO404` | CASSA CENTRALE BANCA |
| ES | `54930056IRBXK0Q1FP96` | Abanca Corporacion Bancaria, S.A. |
| GR | `M6AD1Y1KW32H8THQ6F76` | Piraeus Financial Holdings |
| PL | `5493000LKS7B3UTF7H35` | Bank Polska Kasa Opieki S.A. |
| GR | `5UMCZOEYKCVFAW8ZLO05` | National Bank of Greece, S.A. |
| NL | `724500A1FNICHSDF2I11` | de Volksbank N.V. |
| FR | `96950001WI712W7PQG45` | RCI Banque |
| AT | `529900S9YO2JHTIIDG38` | BAWAG Group AG |
| GR | `213800DBQIB6VBNU5C64` | Alpha Bank S.A. |
| ES | `549300U4LIZV0REEQQ46` | Kutxabank, S.A. |
| ES | `95980020140005881190` | Banco de Crédito Social Cooperativo |
| BE | `5493008QOCP58OLEN998` | Investeringsmaatschappij Argenta |
| DE | `529900JZTYE3W7WQH904` | HASPA Finanzholding |
| BE | `549300DYPOFMXOR7XM56` | Crelan |
| DE | `391200EEGLNXBBCVKC73` | Erwerbsgesellschaft der S-Finanzgruppe mbH & Co. KG |
| ES | `549300OLBL49CW8CT155` | Ibercaja Banco, S.A. |
| RO | `549300RG3H390KEL8896` | Banca Transilvania |
| IT | `7LVZJ6XRIE7VNZ4UBX81` | BANCA MEDIOLANUM S.P.A. |
| IT | `549300L7YCATGO57ZE10` | FINECOBANK S.P.A. |
| AT | `AT0000000000043000VB` | VOLKSBANK WIEN AG VB |
| LI | `529900OE1FOAM50XLP72` | Liechtensteinische Landesbank AG |
| SI | `5493001BABFV7P27OW30` | Nova Ljubljanska Banka d.d., Ljubljana |
| LT | `485100FX5Y9YLAQLNP12` | Revolut Holdings Europe UAB |
| CY | `635400L14KNHZXPUZM19` | Bank of Cyprus Holdings Public Limited Company |
| RO | `2138008AVF4W7FMW8W87` | CEC BANK SA |
| MT | `529900RWC8ZYB066JF16` | Bank of Valletta Plc |
| SI | `213800HDJ876ACJXXD05` | AIKGROUP (CY) LIMITED |
| MT | `213800TC9PZRBHMJW403` | MDB Group Limited |
| LT | `549300TK038P6EV4YU51` | Akcinė bendrovė Šiaulių bankas |
| LV | `2138009Y59EAR7H1UO97` | Akciju sabiedrība "Citadele banka" |

Real bank names appear only in this document, never in the app.

## 5. What the data does not cover (still ASSUMPTION)

- HQLA Level 1, HQLA Level 2 and other eligible collateral: no liquidity template in these files.
- The split of retail deposits into insured and uninsured: only the household total is sourced.
- The maturity ladder of wholesale funding: only the total is sourced.
- The capital requirement of each archetype.
- The **digital** and **specialised** archetypes entirely: no field identifies such banks.
- The size of every archetype (fictitious by design).

A possible source for the liquidity lines is the EBA Pillar 3 Data Hub
(<https://www.eba.europa.eu/risk-and-data-analysis/pillar-3-data-hub>); it has not been explored.

## 6. Model change triggered by the calibration

Real large banks hold more financial-sector deposits than the placeholder archetype did (13.0%
of assets against 4.0%). With the LCR proxy's 100% outflow rate on financial deposits, the
calibrated universal archetype opened at an LCR proxy of 95%, i.e. already in the recovery stage.
On 2026-11-11 `lcr_outflow_rate_financial` was lowered from 1.00 to 0.60. The value 0.60 is an
ASSUMPTION, not a sourced figure. Opening LCR proxy afterwards: universal 118%, retail 139%,
digital 215% (127% since the rebuild in section 8), specialised 152%.

Failure day of the central run, before and after calibration:

| Scenario | Universal before | Universal after | Retail before | Retail after |
|---|---|---|---|---|
| Fast run | 7 | 4 | 5 | 4 |
| Slow burn | 26 | 16 | 25 | 21 |
| Cyber outage | 12 | 7 | 8 | 7 |
| Geopolitical | 12 | 8 | 11 | 9 |

The digital and specialised archetypes were unchanged by this calibration.

## 7. Run-off speed against real cases (part b)

Purpose: check whether the speed of deposit loss in the model is of the same order as in
well-documented runs. This is a comparison only. No real-case figure is used as a model input,
and every run-off, panic, haircut, trigger and contagion parameter remains an ASSUMPTION.

### 7.1 Figures from primary sources

Only figures read in the document itself are listed, with the place where they appear. All
documents were read on 2026-11-11.

| # | Case | Figure stated in the source | Source and place |
|---|---|---|---|
| 1 | Silicon Valley Bank, 9 March 2023 | Deposit withdrawals of about USD 42 bn in one day, nearly 25% of about USD 166 bn of total deposits | [S2], pp. 9 and 15 |
| 2 | Silicon Valley Bank, 10 March 2023 | Withdrawal requests pending for the next day: USD 100 bn | [S2], p. 15 |
| 3 | Silicon Valley Bank, 9-10 March 2023 | Over USD 40 bn lost on 9 March and over USD 100 bn more expected on 10 March; together roughly 85% of the deposit base | [S1], Executive Summary |
| 4 | First Republic Bank, 10 March 2023 | Deposit outflows of about USD 25 bn by the end of the day, about 17% of total deposits | [S3] |
| 5 | First Republic Bank, 13 March 2023 | About USD 40 bn of deposit outflows on that day | [S3] |
| 6 | Credit Suisse, end of Q3 2022 | Client deposits of about CHF 370 bn | [S4], p. 34 |
| 7 | Credit Suisse, Q4 2022 | CHF 138 bn of deposits withdrawn in the quarter | [S4], pp. 34 and 63 |
| 8 | Credit Suisse, October 2022 | Outflows of around CHF 92 bn in the month | [S4], p. 63 |
| 9 | Credit Suisse, 13-17 March 2023 | Daily outflows of CHF 1.6 bn, 2.7 bn, 13.2 bn, 17.1 bn and 10.1 bn | [S4], pp. 36-37 |

Sources:
- [S1] Board of Governors of the Federal Reserve System, "Review of the Federal Reserve's
  Supervision and Regulation of Silicon Valley Bank", April 2023, Executive Summary.
  <https://www.federalreserve.gov/publications/2023-April-SVB-Executive-Summary.htm>
- [S2] Office of Inspector General, Board of Governors of the Federal Reserve System, "Material
  Loss Review of Silicon Valley Bank", Evaluation Report 2023-SR-B-013, 25 September 2023.
  <https://oig.federalreserve.gov/reports/board-material-loss-review-silicon-valley-bank-sep2023.pdf>
- [S3] Federal Deposit Insurance Corporation, statement of the Chairman, "Oversight of Financial
  Regulators: Financial Stability, Supervision, and Consumer Protection in the Wake of Recent Bank
  Failures", May 2023. <https://www.fdic.gov/news/speeches/2023/spmay1723.html>
- [S4] FINMA, "FINMA Report - Lessons Learned from the CS Crisis", Bern, 19 December 2023.
  <https://www.finma.ch/en/~/media/finma/dokumente/dokumentencenter/myfinma/finma-publikationen/cs-bericht/20231219-finma-bericht-cs.pdf>

Page numbers are the page of the PDF file. For [S4] the printed page number is the same.

### 7.2 Cases examined and left out

- **Northern Rock (September 2007).** The Treasury Committee report "The run on the Rock"
  (HC 56-I, session 2007-08, <https://publications.parliament.uk/pa/cm200708/cmselect/cmtreasy/56/5602.htm>)
  gives retail deposits and funds of GBP 22.6 bn at the end of 2006 (paragraph 17), but no amount
  withdrawn during the run of 14-17 September 2007 was found in it. No other primary document
  was read. Left out.
- **Banco Popular Espanol (June 2017).** The Single Resolution Board's case page
  (<https://www.srb.europa.eu/en/content/banco-popular>) and the published resolution decision
  give no deposit outflow figure; the decision is a scanned image without readable text. The
  only figures seen came from press reports quoted in a European Parliament briefing. Left out.

### 7.3 Derived shares (own arithmetic)

These are computed here from the rows of 7.1 and are not stated in the sources.

| Case | Calculation | Result |
|---|---|---|
| Credit Suisse, October 2022 | 92 / 370 (rows 8, 6) | about 25% of deposits in one month |
| Credit Suisse, Q4 2022 | 138 / 370 (rows 7, 6) | about 37% of deposits in one quarter |
| First Republic, 10 and 13 March 2023 | deposit base implied by row 4: 25 / 0.17 = about 147; then (25 + 40) / 147 (rows 4, 5) | about 44% of deposits over two business days |

For Credit Suisse in March 2023 (row 9) the source gives no deposit base for that date, so no
share is computed.

### 7.4 Model figures

Cumulative deposit loss in the central (deterministic) run, as a share of opening deposits,
from the `dep_loss%` column of `python -m engine.run --bank <bank> --scenario <scenario>` with
the configuration of the phase C1 commit (SPEC_V2). A run stops at the payments default day.

| Scenario | Bank | After day 1 | After day 2 | After day 5 | At the end (day) | Largest one-day loss |
|---|---|---|---|---|---|---|
| Fast run | Universal | 15.2% | 38.1% | 57.1% | 57.1% (5) | 22.9% |
| Fast run | Retail | 9.4% | 23.8% | 40.7% | 40.7% (5) | 14.4% |
| Fast run | Digital | 12.5% | 28.8% | - | 28.8% (2) | 16.3% |
| Fast run | Specialised | 16.4% | 42.6% | - | 62.0% (4) | 26.2% |
| Slow burn | Universal | 0.8% | 1.7% | 4.5% | 24.5% (30) | 1.9% |
| Slow burn | Retail | 0.5% | 1.0% | 2.7% | 13.5% (30) | 0.7% |
| Slow burn | Digital | 0.7% | 1.4% | 3.6% | 17.8% (30) | 1.3% |
| Slow burn | Specialised | 0.9% | 1.8% | 4.9% | 29.4% (30) | 2.3% |

### 7.5 Reading

- **Fast run against the 2023 US runs.** The specialised and universal archetypes lose 15-16% of
  deposits on day 1 and 38-43% by day 2. First Republic lost about 17% in one day and about 44%
  over two business days; Silicon Valley Bank lost about 25% in one day and faced requests for
  most of the rest the next day. The model's fast run is of the same order as First Republic and
  slower than Silicon Valley Bank.
- **Digital archetype.** At 13% on day 1 and 29% by day 2 it still loses deposits more slowly
  than either US case, but it has so few liquid assets that it defaults on day 2. Its balance
  sheet and its speed factor are ASSUMPTION (section 8).
- **Slow burn against Credit Suisse.** The universal archetype now loses 24% of deposits in 30
  days, against about 25% for Credit Suisse in its worst month. This match was obtained by
  setting the severity (section 8): it is an order-of-magnitude check, not an estimate.

### 7.6 Limits of the comparison

- Three cases, two of them in the same week and country. The US banks had very high shares of
  uninsured deposits; the model's archetypes are European and partly assumed.
- The real figures mix measures: withdrawals, withdrawal requests and net outflows. Silicon
  Valley Bank's second-day figure is requests that were not all paid out.
- The model has daily steps and stops at the payments default day, whereas the real banks kept losing
  deposits until an authority intervened.
- The fast run was not tuned. The slow-burn severity was set on one figure of one case
  (section 8), which is why it must not be read as a calibration.

## 8. Changes in SPEC_V2 phase A

Three inputs changed after the comparison above was first written. None of them is an estimate.

- **Slow-burn severity: 1.0 -> 0.3** (range 0.2 to 0.5), in `config/scenarios.yaml`. It was set
  by trial so that the universal archetype loses 25.3% of its deposits over the 30-day horizon.
  The reference is row 8 over row 6 of section 7.1: about CHF 92 bn out of about CHF 370 bn, or
  about 25% in one month, for Credit Suisse in October 2022 ([S4], pp. 34 and 63). This is an
  **order-of-magnitude check, not an estimate**: one case, one month, a different bank, and a
  model whose other run-off parameters are all assumptions. The parameter stays `ASSUMPTION`.
  With this severity no archetype reaches a payments default in the slow burn within 30 days.
- **Digital archetype rebuilt**, in `config/banks.yaml`: cash and liquid securities cut from
  7.0 to 4.0, other eligible collateral from 2.0 to 1.0, uninsured retail deposits raised from
  3.5 to 5.0, and a run-off speed factor of 1.5 (range 1.0 to 2.5) that multiplies every run-off
  rate of this archetype. All of it is `ASSUMPTION`: no data identifies digital banks
  (section 5). Its opening LCR proxy is now 127%.
- **Panic decay: 15% per day** (range 5% to 30%), in `config/model.yaml`, `ASSUMPTION`. With a
  decay of zero the model behaves as before.

Outcome of the 16 bank and scenario pairs after these changes: `docs/STATUS.md`.

## 9. Changes in SPEC_V2 phase B

- **Scenario pressure that fades**, in `config/scenarios.yaml`. With a constant severity no
  combination of decisions could keep a bank alive, because deposits kept leaving at the
  scenario's pace however calm depositors became. The cyber outage now eases from day 7 and
  the geopolitical shock from day 10, towards a severity of 0.5, halving the distance every 4
  and 3 days. These six values are `ASSUMPTION`, not taken from any data. The cyber outage
  values were replaced in phase B2 (section 10).
- **Decision effects**, in `config/decisions.yaml`: the size of a statement's effect, the
  share, discount and settlement time of a portfolio sale, the ELA cap and the payout times
  are all `ASSUMPTION`. The two-day moratorium length comes from `docs/SPEC_V2.md`.

## 10. Changes in SPEC_V2 phase B2

- **Stable core**, in `config/model.yaml`. Only a flight-prone share of each deposit category
  can leave: 20% of insured retail, 70% of uninsured retail, 85% of corporate and 100% of
  financial deposits (ranges in the file). All four are `ASSUMPTION`; no data in this project
  separates stable from flight-prone balances. The run-off rates of section 7 remain shares
  of the whole stock and are divided by the flight-prone share in the engine, so the first
  day of a run is unchanged and the comparison with the real cases in section 7 still holds
  for day 1 (day 2 is 0.7 to 1.1 points lower than before). The shares were set so that the
  fast run still ends in a payments default for every archetype without decisions.
- **Cyber outage made milder**, in `config/scenarios.yaml`: severity 2.0 -> 0.8, news shocks
  halved, and the pressure eases from day 5 (half-life 2 days, floor 0.2). The day the queue
  is released now costs the universal archetype 12% of its deposits instead of 41%, about
  half the fast run's worst day. These are design choices for a credible technical incident,
  `ASSUMPTION`, not estimates.
- **Outcome thresholds**, in `config/model.yaml`: a bank still open at day 30 is "stabilised"
  when, on each of the last 3 days, the flag conditions do not hold, the liquidity buffer is
  positive and the daily outflow is below 0.2% of opening deposits. Both numbers are
  `ASSUMPTION`.

## 11. Changes in SPEC_V2 phase C1

- **Weekend rules**, in `config/model.yaml`: on a weekend day 30% of a business day's
  withdrawal requests still come in (range 10% to 60%), `ASSUMPTION`. They are paid from cash
  only; the rest waits for Monday.
- **Slow-burn severity: 0.3 -> 0.38** (range 0.25 to 0.6), in `config/scenarios.yaml`.
  Weekends slow a run, so the target of section 8 (about a quarter of the universal
  archetype's deposits lost in 30 days, the order of the documented case) needs a higher
  severity. The universal archetype now loses 24.5%. Still an order-of-magnitude check.
- **Intraday shares**, in `config/model.yaml`: 30% of a day's outflow in the morning, 25% at
  midday, 30% in the afternoon and 15% after 18:00. `ASSUMPTION`, used only to allocate the
  daily figure for display and for bank management's injects.
- **Start weekday**: the geopolitical shock starts on a Wednesday so that the showcase run
  reaches its second Friday on day 10. A design choice, not data.
- The table in section 7.4 was re-run: the fast-run rows are unchanged (the run is over
  before the first weekend) and the slow-burn rows reflect the new severity. In the slow
  burn the largest one-day loss is now a Monday, when the weekend's requests are paid.

## 12. Changes in SPEC_V2 phase C2

- **AT1 and Tier 2**, in `config/banks.yaml` (`resolution.at1_t2`), for the universal and
  retail archetypes: peer median of (item 2520128, Additional Tier 1 capital, plus item
  2520134, Tier 2 capital) divided by item 2521010 (total assets), same file, period and peer
  groups as section 4. Result: 1.34% of total assets for the universal group (28 banks) and
  1.33% for the retail group (40 banks), that is 13.45 and 0.80 in the archetypes' units.
  `scripts/calibrate_te2025.py` prints both. In the balance sheet this amount is part of
  "other liabilities".
- **Everything else in the stack is `ASSUMPTION`**: senior non-preferred debt (taken out of
  the wholesale funding beyond the horizon), the MREL requirement as a share of RWA (27%,
  24%, 22%, 22%), and the whole stack of the digital and specialised archetypes. The
  Transparency Exercise file used here has no MREL or creditor-hierarchy items.
- **Write-down at FoLtF**: 2%, 4% or 8% of the loan book, and **liquidation haircut**: 10%,
  20% or 35%, in `config/model.yaml`. All `ASSUMPTION`; the control team picks the level.
- The order in which losses are absorbed is the order used in this model, not a statement
  of law, and legal loss-absorption thresholds are not modelled.

## 13. Changes in SPEC_V3 phase G, part a

- **Solvency-led scenario "Loss revealed"**, in `config/scenarios.yaml`: on day 3 a loss of 6%
  of the loan book is written off against CET1, with a panic shock of 0.10 that day and 0.05
  the next, at a severity of 0.6. All `ASSUMPTION`: the loss size was chosen so that three
  of the four archetypes fall below their capital requirement on that day, and the
  severity so that little happens before the loss is revealed. It is not based on any case.
- **Taxonomy labels** of the scenarios follow paragraph 115 of the EBA Handbook
  (EBA/REP/2025/20). Placing a scenario in the taxonomy is a judgement of this project, not
  data.

## 14. Changes in SPEC_V3 phase G, part b

Three parameters were added to `config/model.yaml`, all `ASSUMPTION`. None is based on data.

- `hostile_liquidity_uplift` (0.25): under hostile assumptions the liquidity need at
  reopening is shown a second time, raised by this share, for a negative market reaction.
  It is computed after the run and changes none of its figures.
- `buyer_indicative_bid_share` (0.5): the non-binding price named in the "interested buyer"
  inject, as a share of the CET1 the bank holds once it is back at its capital requirement
  after the valuation write-down. A round number; it changes no other figure, and sale of
  business is still computed like bail-in.
- `rating_downgrade_panic_shock` (0.10): the news shock of the "rating downgrade" challenge
  inject, the same size as the shock of the day the loss is revealed in section 13. The
  inject is off by default and is the only challenge inject that changes the run. It is
  placed one day after early intervention on the path without decisions.

The role cards, the expected responses and the mapping of each inject to a purpose (EBA
Handbook, paragraph 128) are draft wording by this project, not taken from the Handbook.
