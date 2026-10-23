"""
bt_loader.py: read the backtest workbook and build point-in-time panels.

Implements PREREGISTRATION.md sections 3 to 5:
  - parse every sheet (column pairs per ticker, Members, Tickers, Surprise)
  - treat Bloomberg's "not available" value (about -2.42e-14) as missing
  - group duplicate listings (same root ticker, or near-identical returns)
    and splice each group into one company
  - make every quarterly number usable only after its announcement date

Main function: load_panels(force=False) -> dict (cached in cache/)
    months       list of month periods (2014-12 .. 2026-09)
    form_date    month -> formation date (last trading day)
    members      DataFrame (month, company, sector)
    companies    DataFrame per company: name, industry, lines, bankrupt flag
    monthly      dict field -> DataFrame (month x company), spliced
    ret          DataFrame (month x company): return from previous month-end
                 to this month-end (spliced), NaN if not computable
    last_price_month  Series company -> last month with a price
    quarterly    dict metric -> DataFrame (month x company), point in time
    surprise     DataFrame (month x company): raw surprise (comp - est), PIT
    benchmarks   DataFrame (month x XLE/XLI/SPY total return index)
    dup_groups   DataFrame of the duplicate pairs found
    checks       dict of data checks for the report
"""
import pickle
import re
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

import config
import loader

BT = config.BACKTEST
SENTINEL = -2.424536266198984e-14
CACHE_VERSION = 1

MONTHLY_SHEETS = {
    "M_TOT_RETURN": "tot_return", "M_PX_LAST": "px", "M_MKT_CAP": "mkt_cap", "M_PE": "pe", "M_PB": "pb",
    "M_EV_EBITDA": "ev_ebitda", "M_DVD_YLD": "dvd_yld", "M_EPS_FWD": "eps_fwd", "M_REC": "rec",
    "M_TARGET": "target", "M_SHORT": "short_ratio", "M_SHARES": "shares",
}
QUARTERLY_SHEETS = {
    "Q_SALES": "sales", "Q_GROSS_PROFIT": "gross_profit", "Q_GROSS_MARGIN": "gross_margin",
    "Q_NET_INCOME": "net_income", "Q_OPER_MARGIN": "oper_margin", "Q_CFO": "cfo", "Q_FCF": "fcf",
    "Q_ASSETS": "assets", "Q_ROE": "roe", "Q_ROA": "roa", "Q_DEBT_EQ": "debt_eq", "Q_ND_EBITDA": "nd_ebitda",
    "Q_CUR_RATIO": "cur_ratio", "Q_INVENTORY": "inventory",
}


def data_path():
    return config.PROJECT_DIR / BT["data_file"]


def cache_file():
    return config.CACHE_DIR / ("bt_panels_%s.pkl" % Path(BT["data_file"]).stem)


# ===========================================================================
# 1. Parsing
# ===========================================================================
def clean(v):
    """Screener cleaning plus Bloomberg's 'not available' number."""
    v = loader.clean_value(v)
    if isinstance(v, float) and abs(v - SENTINEL) < 1e-20:
        return np.nan
    return v


def rows_of(wb, name):
    rows = [tuple(clean(v) for v in r) for r in wb[name].iter_rows(values_only=True)]
    width = max((len(r) for r in rows), default=0)
    return [r + (np.nan,) * (width - len(r)) for r in rows]


def parse_members(rows):
    """Members: column pairs per (index, month-end): row 1 date, row 2 index,
    member tickers (without ' Equity') from row 3."""
    recs = []
    for c in range(0, len(rows[0]), 2):
        d, idx = rows[0][c], rows[1][c]
        if pd.isna(d) or not isinstance(idx, str):
            continue
        month = pd.Timestamp(d).to_period("M")
        for r in rows[2:]:
            t = r[c]
            if isinstance(t, str) and t.strip():
                recs.append((month, idx, t.strip() + " Equity"))
    m = pd.DataFrame(recs, columns=["month", "index", "ticker"])
    m["sector"] = m["index"].map(BT["indices"])
    return m


def parse_surprise(rows):
    """Surprise: blocks of 8 columns per ticker (period, announcement date,
    time, GAAP EPS, comparable EPS, consensus estimate)."""
    recs = []
    for c0 in range(0, len(rows[0]), 8):
        t = rows[0][c0]
        if not isinstance(t, str):
            continue
        for r in rows[2:]:
            vals = r[c0:c0 + 6]
            if pd.isna(vals[1]):
                continue
            recs.append((t, vals[0], vals[1], vals[3], vals[4], vals[5]))
    s = pd.DataFrame(recs, columns=["ticker", "period", "ann_date", "eps_gaap", "eps_comp", "eps_est"])
    s["ann_date"] = pd.to_datetime(s["ann_date"], errors="coerce")
    for c in ["eps_gaap", "eps_comp", "eps_est"]:
        s[c] = pd.to_numeric(s[c], errors="coerce")
        s.loc[s[c].abs() < BT["surprise_zero"], c] = np.nan
    return s.dropna(subset=["ann_date"])


def parse_workbook(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    d = {"members": parse_members(rows_of(wb, "Members"))}
    tk = pd.DataFrame(rows_of(wb, "Tickers")[1:], columns=rows_of(wb, "Tickers")[0])
    d["tickers"] = tk[tk["ticker"].apply(lambda x: isinstance(x, str))].reset_index(drop=True)
    for sheet in list(MONTHLY_SHEETS) + list(QUARTERLY_SHEETS) + ["Q_ANNOUNCE", "Benchmarks"]:
        long, _ = loader.parse_wide(rows_of(wb, sheet), 2, sheet)
        d[sheet] = long
    d["surprise_raw"] = parse_surprise(rows_of(wb, "Surprise"))
    wb.close()
    return d


# ===========================================================================
# 2. Duplicate groups and splicing
# ===========================================================================
def monthly_wide(long, months):
    """Long (ticker, date, value) -> month x ticker table on the month grid."""
    if long.empty:
        return pd.DataFrame(index=months)
    x = long.assign(month=long["date"].dt.to_period("M"))
    w = x.pivot_table(index="month", columns="ticker", values="value", aggfunc="last")
    return w.reindex(months)


def line_returns(tr):
    """Monthly return of each line, only between consecutive months."""
    return tr / tr.shift(1) - 1


def find_duplicate_groups(tickers, line_ret):
    """Union-find over (same root) or (corr >= dup_corr over >= dup_min_overlap months)."""
    parent = {t: t for t in tickers}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    pairs = []
    roots = {}
    for t in tickers:
        roots.setdefault(t.split()[0], []).append(t)
    for root, ts in roots.items():
        for a, b in zip(ts, ts[1:]):
            union(a, b)
            pairs.append((a, b, "same root", np.nan, np.nan))
    r = line_ret[[t for t in tickers if t in line_ret.columns]]
    have = r.notna().astype(int)
    overlap = have.T.dot(have)
    corr = r.corr(min_periods=BT["dup_min_overlap"])
    cols = list(corr.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            if a.split()[0] == b.split()[0]:
                continue
            if overlap.at[a, b] >= BT["dup_min_overlap"] and corr.at[a, b] >= BT["dup_corr"]:
                union(a, b)
                pairs.append((a, b, "near-identical returns", round(corr.at[a, b], 4), int(overlap.at[a, b])))
    groups = {}
    for t in tickers:
        groups.setdefault(find(t), []).append(t)
    return list(groups.values()), pd.DataFrame(pairs, columns=["line_a", "line_b", "reason", "corr", "overlap_months"])


def splice(wide, groups, member_line, priority):
    """One column per company: the member line's value that month, else the
    first line (in priority order) that has a value."""
    out = {}
    for comp, lines in groups.items():
        lines = [l for l in priority[comp] if l in wide.columns]
        if not lines:
            continue
        sub = wide[lines]
        val = pd.Series(np.nan, index=wide.index)
        ml = member_line.get(comp)
        if ml is not None:
            for l in lines:
                mask = ml == l
                val[mask] = sub.loc[mask, l]
        for l in lines:
            val = val.fillna(sub[l])
        out[comp] = val
    return pd.DataFrame(out, index=wide.index)


def splice_returns(line_ret, groups, member_line, priority):
    """Return of a company for month t (from t-1 to t): from the line that was
    the member at t-1 if it has the return, else the first line in priority
    order that has it."""
    prev_member = {c: s.shift(1) for c, s in member_line.items()}
    return splice(line_ret, groups, prev_member, priority)


# ===========================================================================
# 3. Point in time quarterly data
# ===========================================================================
def announcement_dates(q_ann, surprise, quarters):
    """Usable-from date for every (line, quarter): Q_ANNOUNCE, else the first
    Surprise date in [quarter end - 30, quarter end + 180] days, else quarter
    end + 90 days. Also returns the cross-check rate."""
    a = q_ann.copy()
    a["ann"] = pd.to_datetime(a["value"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    a = a.rename(columns={"date": "q"})[["ticker", "q", "ann"]]
    base = quarters.merge(a, on=["ticker", "q"], how="left")
    lo, hi = BT["ann_fallback_window"]
    sur = surprise[["ticker", "ann_date"]].sort_values("ann_date")
    fill, agree, both = [], 0, 0
    sur_by = {t: g["ann_date"].values for t, g in sur.groupby("ticker")}
    for t, q, ann in base[["ticker", "q", "ann"]].itertuples(index=False):
        dates = sur_by.get(t, np.array([], dtype="datetime64[ns]"))
        if pd.notna(ann):
            if len(dates):
                both += 1
                agree += bool((np.abs((dates - np.datetime64(ann)) / np.timedelta64(1, "D")) <= BT["ann_crosscheck_days"]).any())
            fill.append((ann, "Q_ANNOUNCE"))
            continue
        win = dates[(dates >= np.datetime64(q + pd.Timedelta(days=lo))) & (dates <= np.datetime64(q + pd.Timedelta(days=hi)))]
        if len(win):
            fill.append((pd.Timestamp(win.min()), "Surprise"))
        else:
            fill.append((q + pd.Timedelta(days=BT["ann_fallback_days"]), "quarter end + 90 days"))
    base["avail"] = [f[0] for f in fill]
    base["avail_source"] = [f[1] for f in fill]
    check = {"q_announce_vs_surprise_agree": agree / both if both else np.nan, "q_announce_with_surprise": both,
             "avail_source": base["avail_source"].value_counts().to_dict()}
    return base, check


def quarterly_table(d):
    """One row per (line, quarter) with raw fields and derived TTM / YoY values."""
    frames = []
    for sheet, name in QUARTERLY_SHEETS.items():
        x = d[sheet][["ticker", "date", "value"]].rename(columns={"date": "q", "value": name})
        frames.append(x.set_index(["ticker", "q"]))
    q = pd.concat(frames, axis=1).reset_index().sort_values(["ticker", "q"])
    lo, hi = BT["yoy_days"]
    out = []
    for t, g in q.groupby("ticker"):
        g = g.sort_values("q").reset_index(drop=True)
        qd = g["q"]
        # trailing 12 months: 4 consecutive quarters within ttm_max_span_days
        span_ok = (qd - qd.shift(3)).dt.days <= BT["ttm_max_span_days"]
        for f in ["fcf", "gross_profit", "cfo", "net_income", "sales"]:
            s = g[f].rolling(4, min_periods=4).sum()
            g["ttm_" + f] = s.where(span_ok)
        # same quarter a year earlier
        yoy_ok = (qd - qd.shift(4)).dt.days.between(lo, hi)
        prev_sales, prev_inv = g["sales"].shift(4), g["inventory"].shift(4)
        g["sales_growth"] = (100 * (g["sales"] / prev_sales - 1)).where(yoy_ok & (prev_sales > 0))
        inv_g = (g["inventory"] / prev_inv - 1).where(yoy_ok & (prev_inv > 0) & (g["inventory"] > 0))
        sal_g = (g["sales"] / prev_sales - 1).where(yoy_ok & (prev_sales > 0))
        g["inv_minus_sales_growth"] = inv_g - sal_g
        g["inv_to_sales"] = (g["inventory"] / g["ttm_sales"]).where((g["inventory"] > 0) & (g["ttm_sales"] > 0))
        g["gross_prof"] = (g["ttm_gross_profit"] / g["assets"]).where(g["assets"] > 0)
        g["earn_quality"] = ((g["ttm_cfo"] - g["ttm_net_income"]) / g["assets"]).where(g["assets"] > 0)
        out.append(g)
    return pd.concat(out, ignore_index=True)


QUARTERLY_METRICS = ["sales_growth", "gross_margin", "oper_margin", "roe", "roa", "ttm_fcf", "gross_prof",
                     "earn_quality", "nd_ebitda", "debt_eq", "cur_ratio", "inv_minus_sales_growth", "inv_to_sales"]


def point_in_time(qtab, form_date, months):
    """For each line and month: the latest quarter announced strictly before
    the formation date (older quarters announced later are ignored; the
    quarter must have ended within max_quarter_age_days)."""
    fd = np.array([np.datetime64(form_date[m]) for m in months])
    panels = {k: {} for k in QUARTERLY_METRICS}
    for t, g in qtab.groupby("ticker"):
        g = g.dropna(subset=["avail"]).sort_values(["avail", "q"])
        g = g[g["q"] >= g["q"].cummax()]              # drop late-arriving older quarters
        if g.empty:
            continue
        av = g["avail"].values.astype("datetime64[ns]")
        idx = np.searchsorted(av, fd, side="left") - 1     # strictly before
        ok = idx >= 0
        qdates = g["q"].values.astype("datetime64[ns]")
        age = np.where(ok, (fd - qdates[np.clip(idx, 0, None)]) / np.timedelta64(1, "D"), np.inf)
        ok &= age <= BT["max_quarter_age_days"]
        for k in QUARTERLY_METRICS:
            vals = g[k].values
            panels[k][t] = np.where(ok, vals[np.clip(idx, 0, None)], np.nan)
    return {k: pd.DataFrame(v, index=months) for k, v in panels.items()}


def surprise_pit(surprise, form_date, months):
    """Raw surprise (comparable EPS - estimate) of the latest announcement
    strictly before the formation date and within surprise_max_age_days."""
    fd = np.array([np.datetime64(form_date[m]) for m in months])
    out = {}
    for t, g in surprise.dropna(subset=["eps_comp", "eps_est"]).groupby("ticker"):
        g = g.sort_values("ann_date")
        av = g["ann_date"].values.astype("datetime64[ns]")
        idx = np.searchsorted(av, fd, side="left") - 1
        ok = idx >= 0
        age = np.where(ok, (fd - av[np.clip(idx, 0, None)]) / np.timedelta64(1, "D"), np.inf)
        ok &= age <= BT["surprise_max_age_days"]
        raw = (g["eps_comp"] - g["eps_est"]).values
        out[t] = np.where(ok, raw[np.clip(idx, 0, None)], np.nan)
    return pd.DataFrame(out, index=months)


# ===========================================================================
# 4. Build everything
# ===========================================================================
def is_bankrupt_line(t):
    root = t.split()[0]
    return len(root) >= 4 and root.endswith("Q") and not re.match(r"^\d+D$", root)


def build(d):
    tr_long = d["M_TOT_RETURN"]
    all_months = pd.period_range(tr_long["date"].min().to_period("M"), tr_long["date"].max().to_period("M"), freq="M")
    form_date = tr_long.assign(m=tr_long["date"].dt.to_period("M")).groupby("m")["date"].max().reindex(all_months)

    tickers = list(d["tickers"]["ticker"])
    tr_line = monthly_wide(tr_long, all_months).reindex(columns=tickers)
    lret = line_returns(tr_line)
    groups_list, dup_pairs = find_duplicate_groups(tickers, lret)
    n_obs = tr_line.notna().sum()
    groups, priority = {}, {}
    for lines in groups_list:
        order = sorted(lines, key=lambda l: (-n_obs.get(l, 0), l))
        comp = order[0]                                   # company id = line with most history
        groups[comp] = lines
        priority[comp] = order
    line_to_comp = {l: c for c, ls in groups.items() for l in ls}

    mem = d["members"].copy()
    mem["company"] = mem["ticker"].map(line_to_comp)
    member_line = {}
    for comp, g in mem.groupby("company"):
        member_line[comp] = g.drop_duplicates("month").set_index("month")["ticker"].reindex(all_months)
    members = mem.drop_duplicates(["month", "company"])[["month", "company", "sector", "ticker"]]

    monthly = {}
    for sheet, name in MONTHLY_SHEETS.items():
        monthly[name] = splice(monthly_wide(d[sheet], all_months), groups, member_line, priority)
    ret = splice_returns(lret, groups, member_line, priority)
    price_any = splice(tr_line, groups, member_line, priority)
    last_price_month = price_any.apply(lambda s: s.last_valid_index())

    # Quarterly, point in time, per line then spliced.
    qtab = quarterly_table(d)
    ann, ann_check = announcement_dates(d["Q_ANNOUNCE"], d["surprise_raw"], qtab[["ticker", "q"]])
    qtab = qtab.merge(ann[["ticker", "q", "avail", "avail_source"]], on=["ticker", "q"], how="left")
    pit_line = point_in_time(qtab, form_date, all_months)
    quarterly = {k: splice(v.reindex(columns=tickers), groups, member_line, priority) for k, v in pit_line.items()}
    sur_line = surprise_pit(d["surprise_raw"], form_date, all_months).reindex(columns=tickers)
    surprise = splice(sur_line, groups, member_line, priority)

    tk = d["tickers"].set_index("ticker")
    comp_rows = []
    for comp, lines in groups.items():
        comp_rows.append(dict(company=comp, name=tk.at[comp, "NAME"] if comp in tk.index else None,
                              industry=next((tk.at[l, "GICS_INDUSTRY_NAME"] for l in priority[comp]
                                             if l in tk.index and isinstance(tk.at[l, "GICS_INDUSTRY_NAME"], str)), np.nan),
                              lines=", ".join(priority[comp]), bankrupt=any(is_bankrupt_line(l) for l in lines)))
    companies = pd.DataFrame(comp_rows).set_index("company")

    bm = monthly_wide(d["Benchmarks"], all_months)
    checks = dict(ann_check, n_lines=len(tickers), n_companies=len(groups), n_dup_pairs=len(dup_pairs),
                  bankrupt_flagged=sorted(c for c in companies.index if companies.at[c, "bankrupt"]),
                  members_per_month=members.groupby("month").size().describe().round(1).to_dict())
    return dict(months=list(all_months), form_date=form_date, members=members, companies=companies, monthly=monthly,
                ret=ret, last_price_month=last_price_month, quarterly=quarterly, surprise=surprise, benchmarks=bm,
                dup_groups=dup_pairs, checks=checks)


def load_panels(force=False):
    path = data_path()
    if not path.exists():
        raise FileNotFoundError("Backtest data file not found: %s (BACKTEST['data_file'] in config.py)" % path)
    sig = (path.name, path.stat().st_size, int(path.stat().st_mtime), CACHE_VERSION)
    cf = cache_file()
    if cf.exists() and not force:
        with open(cf, "rb") as f:
            p = pickle.load(f)
        if p.get("_sig") == sig:
            return p
    p = build(parse_workbook(path))
    p["_sig"] = sig
    config.CACHE_DIR.mkdir(exist_ok=True)
    with open(cf, "wb") as f:
        pickle.dump(p, f)
    return p


if __name__ == "__main__":
    import sys
    p = load_panels(force="--force" in sys.argv)
    for k, v in p["checks"].items():
        print("%-32s %s" % (k, v))
