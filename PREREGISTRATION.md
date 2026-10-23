# Backtest pre-registration: Energy & Industrials screener score

Written 2026-10-23 15:51 PDT, **before any score or portfolio return was
computed on this data.** Every rule below is fixed. Any later change is added
to the Change log at the bottom, with date, reason and whether results had
been seen. Negative results are reported plainly.

Data file: `backtest_pull_all_values_v2.xlsx` (SHA-256 starts `47e6b0071a7798fd`),
pulled on a Bloomberg terminal on 2026-10-23.

## 1. What was seen before writing this

So that readers can judge how independent the test is:

- **Data checks only:** coverage per sheet, date ranges, error strings, the
  index-membership structure, and duplicate listings. Monthly return
  correlations were computed **only** to identify duplicate securities (§4). No
  score, ranking, portfolio or benchmark return was computed.
- **An earlier exploratory test, on different data** (`backtest_partial.py`,
  2026-10-21) used a partial version of the score (about 40% of its weight) on
  9 monthly periods from January to October 2026, and found no signal.
  - **Those months fall inside the final period below.** That test used a
    different universe (today's 249 names, no delisted companies) and
    different data.
  - **What it did and didn't influence:** the group weights here are the
    screener's weights, fixed before that test. The additions in §6 were
    proposed after seeing it, on the basis of published research, not of its
    results.
- **The weights and metric directions come from the screener's
  `config.py`** (SHA-256 starts `c593638b2342fdb7`), written before any return
  data existed.

## 2. Question

Does the screener's score, computed only from information available at each
month-end, rank S&P 1500 Energy and Industrials stocks by their next-month
return?

Out of scope:
- **The supply chain part (Linked companies)** has no usable history and cannot
  be tested. Nothing here applies to it.
- **Sentiment** is not tested either (no history).

## 3. Universe and dates

- **Universe at month-end t:** companies listed in that month's members of
  `S15ENRS Index` (S&P 1500 Energy) or `S15INDU Index` (S&P 1500 Industrials),
  per the `Members` sheet. Only that month's members count.
- **Delisted companies are included** for the months they were members.
- **Formation date:** the last trading day of month t, the date of the monthly
  price row. Members dates (calendar month-ends) are matched to price rows by
  calendar month.
- **Formation needs a price:** a company also needs a total-return value at the
  formation date. Members without one are counted and excluded that month.
- **Holding period:** month t to month t+1. Periods are assigned by
  **holding (return) month:**

| Period | Return months | Formations | Rule |
|---|---|---|---|
| Development | Jan 2016 to Dec 2019 (48) | Dec 2015 to Nov 2019 | may be run repeatedly, to debug code only |
| Validation | Jan 2020 to Dec 2022 (36) | Dec 2019 to Nov 2022 | run once (lock file) |
| Final | Jan 2023 to Sep 2026 (45) | Dec 2022 to Aug 2026 | run once (lock file), only after validation |

The start (December 2015) is the first month-end with 12 months of history
behind it, which 12-1 momentum and share issuance need.

## 4. Duplicate listings (one company, two tickers)

- **Duplicate group:** two ticker lines form one company when either:
  - they share the same root ticker (e.g. `APA UN` and `APA UW`), or
  - their monthly total returns correlate at least 0.995 over at least 12
    overlapping months (renamed or re-domiciled companies, e.g. `HFC` → `DINO`).
- **What the check found:** 30 pairs (24 same-root exchange switches, 6
  renamed or re-domiciled companies), all listed in
  `results/backtest/duplicate_groups.csv`. Members never lists two lines of one
  group in the same month.
- **Membership:** the company is in the universe in month t if any of its lines
  is a member that month.
- **Field values:** for every field and month, the value comes from the line
  that is the member that month. If that line has no value, it comes from the
  group's other line(s), in order of most monthly price observations.
- **Returns:** the company's return from t to t+1 comes from the first line, in
  that same order, that has prices at both dates. Momentum uses an index
  rebuilt from these spliced returns.

## 5. Point in time

- **Monthly fields** (prices, total return, market cap, P/E, P/B, EV/EBITDA,
  dividend yield, forward EPS, rating, target price, short interest, shares)
  use the value at the formation date.
- **Quarterly fields** count only from their announcement date (`Q_ANNOUNCE`).
  - A figure is usable at formation date d only if it was announced
    **strictly before** d.
  - If `Q_ANNOUNCE` is missing for a quarter, the first `Surprise` announcement
    date between quarter-end minus 30 days and quarter-end plus 180 days is
    used. If neither exists, quarter-end plus 90 days.
  - The agreement between `Q_ANNOUNCE` and `Surprise` dates (within ±5 days) is
    reported.
  - When a quarter becomes available after a newer quarter is already
    available, it is ignored.
  - The latest usable quarter must have ended within 270 days of d; otherwise
    the quarterly metrics are missing.
- **Trailing 12-month sums** (FCF, gross profit, cash from operations, net
  income) need 4 consecutive usable quarters within 400 days.
- **Earnings surprise** uses the latest `Surprise` row announced strictly
  before d and within 120 days of it.
- **Missing values:**
  - Bloomberg's "not available" value (about −2.42e-14) and any value with
    |x| < 1e-9 in the Surprise EPS columns count as missing.
  - Strings starting with `#` count as missing.

## 6. Score (one fixed version, plus one comparison)

Each metric is ranked exactly as in the screener (`scoring.py`):
- **Clip outliers:** winsorize at the 5th and 95th percentile across that
  month's universe.
- **Rank within peers:** percentile within the company's GICS industry if at
  least 8 companies there have the metric; otherwise within its sector;
  otherwise against all companies. Lower-is-better metrics are flipped.
- **Peer groups:**
  - Industry is today's GICS industry from the `Tickers` sheet (blank for the
    165 delisted names, who are then ranked within their sector).
  - Sector is **the index the company belongs to that month** (Energy or
    Industrials), which is point in time.
- **Combine:** a group's value is the average percentile of its available
  metrics. The composite is the weighted average of the available groups, with
  weights renormalized per company.
- **Who gets scored:** a company needs at least 50% data completeness,
  measured the screener's way (group-weighted share of the score's metrics it
  has). Names below that stay in the equal-weight benchmark but not in the
  ranked groups.

**Group weights:** the screener's six-group weights rescaled to sum to 1. Linked
companies and Sentiment are dropped.

| Group | Weight |
|---|---|
| Health | 0.20 / 0.85 = 23.5% |
| Risk | 0.15 / 0.85 = 17.6% |
| Valuation | 0.20 / 0.85 = 23.5% |
| Demand pipeline | 0.10 / 0.85 = 11.8% |
| Expectations | 0.15 / 0.85 = 17.6% |
| Momentum | 0.05 / 0.85 = 5.9% |

**Metrics.** **[B]** marks the five pre-registered additions. (+) = higher is
better, (−) = lower is better.

- **Health**
  - sales growth (latest usable quarter vs the same quarter a year earlier) (+)
  - gross margin (+)
  - operating margin (+)
  - return on equity (+)
  - return on assets (+)
  - FCF yield = trailing-12-month FCF / market cap (+)
  - **[B]** gross profitability = trailing-12-month gross profit / total assets (+)
  - **[B]** earnings quality = (trailing-12-month cash from operations − trailing-12-month net income) / total assets (+)
  - **[B]** net share issuance = shares now / shares 12 months ago − 1 (−)
- **Risk**
  - net debt / EBITDA (−); negative values count as missing when EV/EBITDA is missing or ≤ 0
  - debt / equity (−)
  - current ratio (+)
  - short interest ratio (−)
- **Valuation**
  - EV/EBITDA (−)
  - P/E (−)
  - P/B (−); these three multiples count as missing when ≤ 0
  - EV/EBITDA vs its own average over the previous 60 months, at least 24 (−)
  - 12-month dividend yield (+); missing on a priced stock = 0, as in the screener
- **Demand pipeline**
  - inventory growth minus sales growth, year on year (−)
  - inventory / trailing-12-month sales (−)
  - Capex intensity is off, as in the screener default.
- **Expectations**
  - EPS revision 1 month = (forward EPS now − 1 month ago) / price now (+)
  - EPS revision 3 months, same with 3 months (+)
  - Forward EPS is Bloomberg's blended 12-months-forward consensus (`1BF`).
  - analyst rating change over 3 months (+)
  - analyst rating level (+)
  - target price upside (+)
  - **[B]** earnings surprise = (comparable EPS − consensus estimate) / price at formation (+)
- **Momentum**
  - 1-month return (+)
  - 3-month return (+)
  - **[B]** 12-1 month return = return from t−12 to t−1 (+)
  - The 1-month direction is kept as in the screener. Research suggests
    one-month returns tend to reverse; it is not changed, so that the screener
    is tested as built.

Monthly EPS revisions replace the screener's weekly 4- and 12-week revisions,
because this data is monthly.

- **Score B (primary):** all metrics above.
- **Score A (comparison):** the same without the five [B] additions, same
  weights.

## 7. Portfolios and costs

- **Groups:** each month the scored names are split into quintiles by
  composite score (Q5 = highest). Each quintile is equal weight, held one month.
- **Benchmarks:**
  - **EW:** all universe names with a formation price, equal weight
  - **XLE, XLI, SPY:** total return
- **Trading costs:** 15 basis points per dollar traded. Turnover each month is
  the sum of absolute weight changes against the previous month's weights
  after they drift with returns; the first month buys 100%. Costs apply to the
  quintiles, EW and random baskets, not to the ETFs.
- **Long-short:** Q5 − Q1 is reported net, both legs' costs included.
- **Random baskets:** 1,000 strategies. Each month each one draws, uniformly
  without replacement, as many scored names as Q5 has that month, equal
  weight, redrawn every month. Seed 20261023. Q5's percentile against them is
  reported gross (primary, since it tests selection) and net.

## 8. Delisting returns

For a company held from t to t+1 with a price at t but none at t+1 (after the
§4 splicing):

- **Price data resumes later (a gap):** that month's return is excluded from
  the portfolio average, and the gap is counted.
- **No later price (delisted):**
  - **Bankruptcy-flagged** (root ticker of 4+ characters ending in `Q`,
    Bloomberg's post-bankruptcy suffix; list printed in the results):
    −30%.
  - **Otherwise** (acquisitions, mergers): 0%, i.e. cash at the last price.

**Sensitivity runs:** all delistings at −30%; bankruptcy-flagged at −100%.

## 9. Measures and the decision rule

**Reported for each period and each score:**
- monthly Spearman IC between score and next-month return (gross, company level)
- Q1 to Q5 average monthly returns, gross and net
- Q5 − Q1 net spread
- annualized return, volatility, max drawdown, turnover
- Q5 vs EW, XLE, XLI, SPY
- random-basket percentile

**Primary hypotheses, Score B, final period:**
- **H1:** mean monthly IC > 0 (one-sided t-test, p < 0.05).
- **H2:** mean monthly Q5 − Q1 net spread > 0 (one-sided t-test, p < 0.05).

**Verdict:**
- **"Supported"** only if H1 and H2 both pass in the final period **and** the
  validation period's mean IC and mean net spread are both above 0.
- **"Not supported"** otherwise.
- Q5 vs EW, the ETFs and random baskets is reported as supporting evidence,
  not part of the verdict.

**Secondary, labeled exploratory:**
- Score A
- deciles instead of quintiles
- Energy and Industrials separately
- IC of each group
- 30 bp costs
- the two delisting sensitivities

**Test count:** 2 primary tests (H1, H2) on Score B, plus the secondary list
above. Every additional run is logged below.

## 10. Lock files

- **`backtest.py --period development`** can run any number of times.
- **`--period validation`** and **`--period final`** write
  `results/backtest/locks/<period>.json`, containing:
  - time
  - SHA-256 of this file, the code files, `config.py` and the data file
  - the headline results
- **Re-running a locked period is refused** unless `--rerun-reason "..."` is
  given. That keeps the first result and adds an entry below.
- **Order:** the final period refuses to run before validation is locked. Code
  run for development or validation never computes returns for final-period
  months.

## 11. Known limitations (stated in advance)

- **Labels:** sector and industry labels are today's GICS (sector at each month
  comes from index membership); 165 delisted names have no industry.
- **Restatements:** it is unknown whether Bloomberg's historical fundamentals
  are as first reported or restated. Restated numbers would leak information.
- **Short interest timing:** Bloomberg dates short interest by settlement date,
  which may come slightly before its publication.
- **Delisting returns** are rule-based estimates (monthly data only).
- **Monthly data:** the 1-month reversal effect, intra-month events and
  intraday trading costs are not modeled.
- **One market environment:** one sector pair, ten years, including the 2020
  oil crash. The result may not transfer.
- **Untestable parts:** the supply chain (Linked companies) and Sentiment
  groups cannot be tested; nothing here speaks to them.

## Change log

| Date | Change | Reason | Results seen before the change? |
|---|---|---|---|
| 2026-10-23 | Initial version | | No |
| 2026-10-23 | §4: duplicate count corrected from 22 to 30 pairs (the 22 counted only correlated pairs; same-root pairs with too few overlapping months were missing from the count). The rule itself is unchanged. | Factual correction after running the loader | No (no score or portfolio returns computed yet) |
| 2026-10-23 | Comment-only edits in `loader.py` and `config.py`: two illustrative numbers taken from real screener data replaced with neutral ones, so the public repository holds no Bloomberg values. No code or rule changed. | Publishing the code | Development results yes; validation and final no |
