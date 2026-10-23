"""
Tests for the pre-registered backtest rules (bt_loader.py, backtest.py),
on small made-up data. They check the rules most likely to go wrong silently:
point in time, duplicates, delisting returns, costs, locks and periods.

Run:  .venv/bin/python -m pytest -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backtest  # noqa: E402
import bt_loader  # noqa: E402


MONTHS = list(pd.period_range("2020-01", "2020-12", freq="M"))
FORM = pd.Series([m.to_timestamp(how="end").normalize() for m in MONTHS], index=MONTHS)   # month-end dates


def qrow(q, avail, value, ticker="AAA US Equity"):
    row = {k: np.nan for k in bt_loader.QUARTERLY_METRICS}
    row.update(ticker=ticker, q=pd.Timestamp(q), avail=pd.Timestamp(avail), roe=value)
    return row


# ---- point in time ------------------------------------------------------------------
def test_quarter_only_usable_strictly_after_announcement():
    q = pd.DataFrame([qrow("2020-03-31", "2020-04-30", 10.0)])          # announced on April's month-end
    pit = bt_loader.point_in_time(q, FORM, MONTHS)["roe"]["AAA US Equity"]
    assert np.isnan(pit[pd.Period("2020-04", "M")])                       # same day: not yet usable
    assert pit[pd.Period("2020-05", "M")] == 10.0


def test_late_older_quarter_is_ignored_and_stale_quarter_dropped():
    q = pd.DataFrame([qrow("2020-03-31", "2020-04-15", 1.0),
                      qrow("2019-12-31", "2020-05-10", 99.0)])            # older quarter arrives later
    pit = bt_loader.point_in_time(q, FORM, MONTHS)["roe"]["AAA US Equity"]
    assert pit[pd.Period("2020-06", "M")] == 1.0
    # 2020-03-31 is more than 270 days old by 2020-12-31
    assert np.isnan(pit[pd.Period("2020-12", "M")])


def test_surprise_strictly_before_and_within_120_days():
    s = pd.DataFrame({"ticker": ["AAA US Equity"], "ann_date": [pd.Timestamp("2020-02-29")],
                      "eps_comp": [1.2], "eps_est": [1.0]})
    pit = bt_loader.surprise_pit(s, FORM, MONTHS)["AAA US Equity"]
    assert np.isnan(pit[pd.Period("2020-02", "M")])
    assert pit[pd.Period("2020-03", "M")] == pytest.approx(0.2)
    assert np.isnan(pit[pd.Period("2020-08", "M")])                       # older than 120 days


def test_bloomberg_not_available_value_is_missing():
    assert np.isnan(bt_loader.clean(-2.424536266198984e-14))
    assert bt_loader.clean(0.0) == 0.0


# ---- duplicates -----------------------------------------------------------------------
def test_duplicate_groups_same_root_and_near_identical_returns():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.05, 30)
    r = pd.DataFrame({"APA UN Equity": base, "APA UW Equity": np.r_[[np.nan] * 10, base[10:]],
                      "OLD1D UN Equity": base + rng.normal(0, 0.0005, 30),        # renamed company
                      "XYZ UN Equity": rng.normal(0, 0.05, 30)})
    groups, pairs = bt_loader.find_duplicate_groups(list(r.columns), r)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 3]
    assert ["XYZ UN Equity"] in groups


def test_splice_prefers_member_line_then_priority():
    idx = MONTHS[:3]
    wide = pd.DataFrame({"A UN Equity": [1.0, 2.0, np.nan], "A UW Equity": [10.0, 20.0, 30.0]}, index=idx)
    member = {"A UN Equity": pd.Series(["A UN Equity", "A UW Equity", "A UW Equity"], index=idx)}
    out = bt_loader.splice(wide, {"A UN Equity": ["A UN Equity", "A UW Equity"]}, member,
                           {"A UN Equity": ["A UN Equity", "A UW Equity"]})
    assert list(out["A UN Equity"]) == [1.0, 20.0, 30.0]


# ---- delisting returns ------------------------------------------------------------------
def fake_panels():
    comps = ["OK US Equity", "BUST US Equity", "GONE US Equity", "GAP US Equity"]
    ret = pd.DataFrame(np.nan, index=MONTHS, columns=comps)
    ret["OK US Equity"] = 0.05
    ret.loc[pd.Period("2020-04", "M"), "GAP US Equity"] = 0.01
    last = pd.Series({"OK US Equity": MONTHS[-1], "BUST US Equity": MONTHS[1], "GONE US Equity": MONTHS[1],
                      "GAP US Equity": pd.Period("2020-04", "M")})
    comp = pd.DataFrame({"bankrupt": [False, True, False, False]}, index=comps)
    p = dict(months=MONTHS, ret=ret, last_price_month=last, companies=comp, monthly={}, quarterly={})
    return backtest.Panels(p), comps


def test_delisting_rule():
    P, comps = fake_panels()
    r, drift, kind = backtest.holding_returns(P, MONTHS[1], comps, -0.30, 0.0)   # held Feb -> Mar
    assert r["OK US Equity"] == pytest.approx(0.05)
    assert r["BUST US Equity"] == -0.30 and kind["BUST US Equity"] == "delisted (bankrupt)"
    assert r["GONE US Equity"] == 0.0 and kind["GONE US Equity"] == "delisted (other)"
    assert np.isnan(r["GAP US Equity"]) and kind["GAP US Equity"] == "gap"           # excluded from the average
    assert drift["GAP US Equity"] == 0.0


# ---- costs ------------------------------------------------------------------------------------
def test_costs_and_turnover():
    b = backtest.Book(1, ["a", "b", "c"])
    r = np.array([0.10, 0.0, np.nan])
    g, n, t = b.rebalance([np.array([0, 1])], r, np.nan_to_num(r), 0.0015)
    assert t[0] == pytest.approx(1.0)                         # first month buys everything
    assert g[0] == pytest.approx(0.05)
    assert n[0] == pytest.approx(0.05 - 0.0015)
    g, n, t = b.rebalance([np.array([0, 1])], np.zeros(3), np.zeros(3), 0.0015)
    # weights drifted to 0.55/1.05 and 0.45/1.05 (52.4% / 47.6%), rebalanced back to 50 / 50
    assert t[0] == pytest.approx(2 * (0.55 / 1.05 - 0.5))


def test_gap_holding_is_left_out_of_the_average():
    b = backtest.Book(1, ["a", "b"])
    g, _, _ = b.rebalance([np.array([0, 1])], np.array([0.10, np.nan]), np.array([0.10, 0.0]), 0.0)
    assert g[0] == pytest.approx(0.10)


# ---- periods and locks ----------------------------------------------------------------------
def test_periods_do_not_overlap():
    dev, val, fin = (backtest.return_months(p) for p in ["development", "validation", "final"])
    assert max(dev) < min(val) and max(val) < min(fin)
    assert str(min(fin)) == "2023-01"


def test_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest, "LOCKS", tmp_path)
    logged = []
    monkeypatch.setattr(backtest, "log_change", lambda text, seen: logged.append(text))
    with pytest.raises(SystemExit):
        backtest.check_lock("final", None)                     # validation not locked yet
    backtest.check_lock("validation", None)                    # first run allowed
    (tmp_path / "validation.json").write_text("{}")
    with pytest.raises(SystemExit):
        backtest.check_lock("validation", None)                # second run refused
    backtest.check_lock("validation", "fixed a bug")           # allowed with a reason, and logged
    assert logged and not (tmp_path / "validation.json").exists()
    assert list(tmp_path.glob("validation_superseded_*.json"))
    backtest.check_lock("development", None)                   # never locked
