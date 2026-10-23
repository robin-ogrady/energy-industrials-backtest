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

## Status

| Period | Return months | Rule |
|---|---|---|
| Development | 2016-01 to 2019-12 | may be run repeatedly, for debugging only |
| Validation | 2020-01 to 2022-12 | run once (lock file) |
| Final | 2023-01 to 2026-09 | run once, after validation (lock file) |

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
