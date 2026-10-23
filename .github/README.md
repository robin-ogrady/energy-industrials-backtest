# Energy & Industrials screener: pre-registered backtest

A pre-registered test of whether the score from the
[Energy & Industrials Supply Chain Screener](https://github.com/robin-ogrady/energy-industrials-screener),
computed only from information available at each month-end, ranks S&P 1500
Energy and Industrials stocks by their next-month return (2016 to 2026,
monthly, delisted companies included).

**Start with [PREREGISTRATION.md](../PREREGISTRATION.md).** Every rule
(universe, dates, point-in-time handling, duplicate listings, the score and
its fixed weights, trading costs, delisting returns, the two hypotheses and
the verdict rule) was written down before any score or portfolio return was
computed. Its change log records every later change, with the date and the
reason.

The supply chain part of the screener (Linked companies) has no usable
history and is **not** tested. Nothing here applies to it.

## Verdict: NOT SUPPORTED

Under the pre-registered rule, the score is **not supported**. The rule
required all three conditions; condition 2 failed.

| Condition | Result | Met? |
|---|---|---|
| 1. H1, final period: mean monthly rank correlation (IC) between score and next-month return above 0, one-sided p < 0.05 | IC +0.023, t 1.72, p = 0.046 | yes |
| 2. H2, final period: mean top-minus-bottom quintile return after costs above 0, one-sided p < 0.05 | +0.57% per month, t 1.44, p = 0.078 | **no** |
| 3. Validation period: mean IC and mean top-minus-bottom gap both above 0 | IC +0.035, gap +0.49% per month | yes |

This result is not evidence that the score works or predicts returns.
Supporting comparisons (benchmarks, random baskets) and the secondary
versions were listed in advance as non-deciding and do not change the verdict.

What it does not cover or cannot rule out:
- The supply chain group (Linked companies) and news sentiment were not tested.
- Sector and industry labels are today's (the sector each month comes from
  index membership).
- An earlier exploratory test (January to October 2026, different data)
  overlapped the final period, and its result was seen before the rules were
  written. Without those months the final-period IC is +0.016 and the gap
  +0.35% per month.

## Status

| Period | Return months | Run |
|---|---|---|
| Development | 2016-01 to 2019-12 | repeatedly, for debugging only |
| Validation | 2020-01 to 2022-12 | once, 2026-10-23 16:06 PDT, locked |
| Final | 2023-01 to 2026-09 | once, 2026-10-23 16:11 PDT, locked |

Both lock files record SHA-256 hashes of the code, `config.py`,
`PREREGISTRATION.md` and the data file. The code hashes match commit
`c9926c3`, the commit that published the rules before these runs.

Results are not stored in this repository, because they are built from
licensed Bloomberg data. The lock files record a SHA-256 hash of every code
file and of the data file, so the code that produced each result can be
matched to a commit here.

## Files

| File | What it does |
|---|---|
| `PREREGISTRATION.md` | The rules, written first |
| `bt_loader.py` | Reads the Bloomberg workbook, merges duplicate listings, builds point-in-time panels |
| `backtest.py` | Scores each month, forms quintiles, applies costs and delisting returns, random baskets, report, lock files |
| `config.py` | All settings (the `BACKTEST` section mirrors the pre-registration; the rest is the screener's) |
| `scoring.py`, `loader.py` | The screener's ranking method and cell cleaning, reused unchanged |
| `tests/test_backtest.py` | Tests for point in time, duplicates, delisting returns, costs, periods and locks |

## Running it

Needs Python 3.9+ and your own Bloomberg values-only workbook in the layout
described in the screener repository's `docs/BACKTEST_PULL.md`. No Bloomberg
data is included.

```zsh
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q tests/test_backtest.py
BACKTEST_DATA_FILE=your_pull_values.xlsx .venv/bin/python backtest.py --period development
```
