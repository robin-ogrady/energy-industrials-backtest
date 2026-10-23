"""
backtest.py: the pre-registered backtest (see PREREGISTRATION.md).

    .venv/bin/python backtest.py --period development     # any number of times
    .venv/bin/python backtest.py --period validation      # once (lock file)
    .venv/bin/python backtest.py --period final           # once, after validation

Re-running a locked period needs --rerun-reason "...", which keeps the first
result and appends an entry to the change log in PREREGISTRATION.md.

Writes results/backtest/<period>_summary.md, <period>_monthly.csv and
<period>_chart.png (results/ is gitignored: built from licensed data).
"""
import argparse
import hashlib
import json
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import bt_loader  # noqa: E402
import config  # noqa: E402
import scoring  # noqa: E402

BT = config.BACKTEST
OUT = config.PROJECT_DIR / "results" / "backtest"
LOCKS = OUT / "locks"
HASHED = ["PREREGISTRATION.md", "backtest.py", "bt_loader.py", "config.py", "scoring.py"]

# (key, group, direction, pre-registered addition?)  -- PREREGISTRATION.md section 6
METRICS = [
    ("sales_growth", "Health", 1, False), ("gross_margin", "Health", 1, False), ("oper_margin", "Health", 1, False),
    ("roe", "Health", 1, False), ("roa", "Health", 1, False), ("fcf_yield", "Health", 1, False),
    ("gross_prof", "Health", 1, True), ("earn_quality", "Health", 1, True), ("net_issuance", "Health", -1, True),
    ("nd_ebitda", "Risk", -1, False), ("debt_eq", "Risk", -1, False), ("cur_ratio", "Risk", 1, False),
    ("short_ratio", "Risk", -1, False),
    ("ev_ebitda", "Valuation", -1, False), ("pe", "Valuation", -1, False), ("pb", "Valuation", -1, False),
    ("ev_vs_history", "Valuation", -1, False), ("dvd_yld", "Valuation", 1, False),
    ("inv_minus_sales_growth", "Demand pipeline", -1, False), ("inv_to_sales", "Demand pipeline", -1, False),
    ("eps_rev_1m", "Expectations", 1, False), ("eps_rev_3m", "Expectations", 1, False),
    ("rec_change_3m", "Expectations", 1, False), ("rec", "Expectations", 1, False),
    ("target_upside", "Expectations", 1, False), ("surprise", "Expectations", 1, True),
    ("ret_1m", "Momentum", 1, False), ("ret_3m", "Momentum", 1, False), ("mom_12_1", "Momentum", 1, True),
]
GROUPS = list(BT["group_weights"])


def score_metrics(score):
    """Score B = everything; Score A = without the five additions."""
    return [m for m in METRICS if score == "B" or not m[3]]


# ===========================================================================
# Features at one formation month (PREREGISTRATION.md sections 5 and 6)
# ===========================================================================
class Panels:
    """Small helper around the loader output for month arithmetic."""

    def __init__(self, p):
        self.p = p
        self.months = p["months"]
        self.pos = {m: i for i, m in enumerate(self.months)}
        self.cache = {}            # (month, score) -> features and scores, reused across variants

    def shift(self, m, k):
        i = self.pos[m] - k
        return self.months[i] if 0 <= i < len(self.months) else None

    def mon(self, field, m, comps):
        df = self.p["monthly"][field]
        if m is None or m not in df.index:
            return pd.Series(np.nan, index=comps)
        return df.reindex(columns=comps).loc[m]

    def qtr(self, field, m, comps):
        return self.p["quarterly"][field].reindex(columns=comps).loc[m]

    def ret(self, m, comps):
        df = self.p["ret"]
        if m is None or m not in df.index:
            return pd.Series(np.nan, index=comps)
        return df.reindex(columns=comps).loc[m]

    def compound(self, m_last, n, comps):
        """Return over the n months ending at month m_last (all months needed)."""
        rs = [self.ret(self.shift(m_last, k), comps) for k in range(n)]
        out = pd.Series(1.0, index=comps)
        for r in rs:
            out = out * (1 + r)
        return out - 1


def features(P, m, comps, sectors):
    f = pd.DataFrame(index=comps)
    f["sector"] = sectors.reindex(comps)
    f["industry"] = P.p["companies"]["industry"].reindex(comps)
    px = P.mon("px", m, comps)
    ev = P.mon("ev_ebitda", m, comps)
    for k in ["sales_growth", "gross_margin", "oper_margin", "roe", "roa", "gross_prof", "earn_quality",
              "debt_eq", "cur_ratio", "inv_minus_sales_growth", "inv_to_sales"]:
        f[k] = P.qtr(k, m, comps)
    f["fcf_yield"] = P.qtr("ttm_fcf", m, comps) / P.mon("mkt_cap", m, comps).where(lambda s: s > 0)
    nd = P.qtr("nd_ebitda", m, comps)
    f["nd_ebitda"] = nd.mask((nd < 0) & ~(ev > 0))
    f["short_ratio"] = P.mon("short_ratio", m, comps)
    for k in ["ev_ebitda", "pe", "pb"]:
        v = P.mon(k, m, comps)
        f[k] = v.where(v > 0)
    look, need = BT["ev_history_months"]
    hist = pd.concat([P.mon("ev_ebitda", P.shift(m, k), comps) for k in range(1, look + 1)], axis=1)
    hist = hist.where(hist > 0)
    avg = hist.mean(axis=1).where(hist.notna().sum(axis=1) >= need)
    f["ev_vs_history"] = f["ev_ebitda"] / avg - 1
    dy = P.mon("dvd_yld", m, comps)
    f["dvd_yld"] = dy.where(dy.notna() | px.isna(), 0.0)          # missing on a priced stock = 0
    sh, sh12 = P.mon("shares", m, comps), P.mon("shares", P.shift(m, 12), comps)
    f["net_issuance"] = (sh / sh12 - 1).where(sh12 > 0)
    eps = P.mon("eps_fwd", m, comps)
    pxp = px.where(px > 0)
    f["eps_rev_1m"] = (eps - P.mon("eps_fwd", P.shift(m, 1), comps)) / pxp
    f["eps_rev_3m"] = (eps - P.mon("eps_fwd", P.shift(m, 3), comps)) / pxp
    rec = P.mon("rec", m, comps)
    f["rec"] = rec
    f["rec_change_3m"] = rec - P.mon("rec", P.shift(m, 3), comps)
    f["target_upside"] = P.mon("target", m, comps) / pxp - 1
    f["surprise"] = P.p["surprise"].reindex(columns=comps).loc[m] / pxp
    f["ret_1m"] = P.ret(m, comps)
    f["ret_3m"] = P.compound(m, 3, comps)
    f["mom_12_1"] = P.compound(P.shift(m, 1), 11, comps)          # t-12 .. t-1
    return f


def score_month(f, metrics):
    """Screener ranking: winsorize, percentile within industry/sector/all,
    group averages, weighted composite renormalized per company."""
    w = {g: BT["group_weights"][g] / sum(BT["group_weights"].values()) for g in GROUPS}
    pct = {}
    for key, group, direction, _ in metrics:
        pp = scoring.peer_percentile(scoring.winsorize(f[key]), f["industry"], f["sector"], direction)
        pct[key] = pp["pct"].reindex(f.index) if len(pp) else pd.Series(np.nan, index=f.index)
    pct = pd.DataFrame(pct, index=f.index)
    gv, comp_num, wsum, complete = {}, 0, 0, 0
    for g in GROUPS:
        keys = [k for k, gg, _, _ in metrics if gg == g]
        gv[g] = pct[keys].mean(axis=1)
        avail = gv[g].notna()
        comp_num = comp_num + gv[g].fillna(0) * w[g] * avail
        wsum = wsum + w[g] * avail
        complete = complete + w[g] * pct[keys].notna().sum(axis=1) / len(keys)
    composite = (comp_num / wsum.replace(0, np.nan))
    return composite, complete, pd.DataFrame(gv)


# ===========================================================================
# Returns with the delisting rule (section 8)
# ===========================================================================
def holding_returns(P, m, comps, delist_bankrupt, delist_other):
    """Return from month m to m+1 for each company held at m.
    Returns (returns used for the portfolio, returns used to drift weights, kind)."""
    m1 = P.shift(m, -1)
    r = P.ret(m1, comps)
    last = P.p["last_price_month"].reindex(comps)
    bankrupt = P.p["companies"]["bankrupt"].reindex(comps).fillna(False).astype(bool)
    kind = pd.Series("ok", index=comps)
    miss = r.isna()
    delisted = miss & (last <= m)
    gap = miss & ~delisted
    r = r.copy()
    r[delisted & bankrupt] = delist_bankrupt
    r[delisted & ~bankrupt] = delist_other
    kind[delisted & bankrupt] = "delisted (bankrupt)"
    kind[delisted & ~bankrupt] = "delisted (other)"
    kind[gap] = "gap"
    drift = r.fillna(0.0)                     # a gap holding is assumed flat for drifting weights
    return r, drift, kind


# ===========================================================================
# Portfolio engine
# ===========================================================================
class Book:
    """Equal-weight portfolio(s) with drifted weights and trading costs.
    Rows = portfolios (1 for a quintile, n for random baskets)."""

    def __init__(self, n_rows, universe):
        self.idx = {c: i for i, c in enumerate(universe)}
        self.w = np.zeros((n_rows, len(universe)))

    def rebalance(self, picks_rows, r_used, r_drift, cost):
        """picks_rows: list (per row) of arrays of column indices to hold.
        Returns gross return, net return and turnover per row."""
        n_rows = self.w.shape[0]
        target = np.zeros_like(self.w)
        for i, picks in enumerate(picks_rows):
            if len(picks):
                target[i, picks] = 1.0 / len(picks)
        turnover = np.abs(target - self.w).sum(axis=1)
        valid = ~np.isnan(r_used)
        rv = np.where(valid, r_used, 0.0)
        held_valid = (target * valid).sum(axis=1)
        gross = np.where(held_valid > 0, (target * rv).sum(axis=1) / np.where(held_valid > 0, held_valid, 1), np.nan)
        grown = target * (1 + r_drift)
        tot = grown.sum(axis=1, keepdims=True)
        self.w = np.where(tot > 0, grown / np.where(tot > 0, tot, 1), 0.0)
        net = gross - cost * turnover
        return gross, net, turnover


def run(P, return_months, score="B", n_groups=None, cost_bps=None, delist=None, sector_split=None,
        n_random=0, seed=None):
    """Run one configuration over the given return months.
    Returns a monthly DataFrame and (if n_random) the random basket monthly returns."""
    n_groups = n_groups or BT["n_groups"]
    cost = (BT["cost_bps"] if cost_bps is None else cost_bps) / 1e4
    db, do = delist or (BT["delist_return_bankrupt"], BT["delist_return_other"])
    metrics = score_metrics(score)
    universe_all = list(P.p["companies"].index)
    books = {k: Book(1, universe_all) for k in ["EW"] + ["Q%d" % q for q in range(1, n_groups + 1)]}
    rnd = Book(n_random, universe_all) if n_random else None
    rng = np.random.default_rng(seed if seed is not None else BT["seed"])
    bm = P.p["benchmarks"]
    mem = P.p["members"]
    rows, rnd_gross, rnd_net = [], [], []
    for rm in return_months:
        m = P.shift(rm, 1)                                   # formation month
        mm = mem[mem["month"] == m]
        sectors = mm.drop_duplicates("company").set_index("company")["sector"]
        tr = P.mon("tot_return", m, list(sectors.index))
        comps = list(tr.index[tr.notna()])                     # members with a formation price
        n_members = len(sectors)
        if (m, score) not in P.cache:
            f = features(P, m, comps, sectors)
            P.cache[(m, score)] = (f,) + score_month(f, metrics)
        f, composite, complete, gv = P.cache[(m, score)]
        scored = composite[(complete >= BT["min_completeness"]) & composite.notna()]
        if sector_split:
            scored = scored[f.loc[scored.index, "sector"] == sector_split]
        r_used, r_drift, kind = holding_returns(P, m, comps, db, do)
        ru = np.full(len(universe_all), np.nan)
        rd = np.zeros(len(universe_all))
        ci = np.array([books["EW"].idx[c] for c in comps], dtype=int)
        ru[ci], rd[ci] = r_used.values, r_drift.values
        row = {"return_month": str(rm), "formation": str(m), "n_members": n_members, "n_universe": len(comps),
               "n_scored": len(scored), "n_gap": int((kind == "gap").sum()),
               "n_delisted": int(kind.str.startswith("delisted").sum()),
               "n_delisted_bankrupt": int((kind == "delisted (bankrupt)").sum())}
        ew_names = scored.index if sector_split else comps
        g, n_, t_ = books["EW"].rebalance([np.array([books["EW"].idx[c] for c in ew_names], dtype=int)], ru, rd, cost)
        row.update(EW_gross=g[0], EW_net=n_[0], EW_turnover=t_[0])
        if len(scored) >= n_groups:
            bucket = pd.qcut(scored.rank(method="first"), n_groups, labels=False) + 1
            for q in range(1, n_groups + 1):
                names = bucket.index[bucket == q]
                g, n_, t_ = books["Q%d" % q].rebalance([np.array([books["EW"].idx[c] for c in names], dtype=int)], ru, rd, cost)
                row.update({"Q%d_gross" % q: g[0], "Q%d_net" % q: n_[0], "Q%d_turnover" % q: t_[0], "Q%d_n" % q: len(names)})
            ok = scored.index[r_used.reindex(scored.index).notna()]
            row["ic"] = stats.spearmanr(scored[ok], r_used[ok]).correlation if len(ok) > 10 else np.nan
            for gname in GROUPS:
                gs = gv.loc[ok, gname]
                good = gs.notna()
                row["ic_" + gname] = stats.spearmanr(gs[good], r_used[ok][good]).correlation if good.sum() > 10 else np.nan
            if rnd is not None:
                n_top = int((bucket == n_groups).sum())
                pool = np.array([books["EW"].idx[c] for c in scored.index], dtype=int)
                picks = [rng.choice(pool, n_top, replace=False) for _ in range(n_random)]
                g, n_, _ = rnd.rebalance(picks, ru, rd, cost)
                rnd_gross.append(g)
                rnd_net.append(n_)
        for etf in ["XLE US Equity", "XLI US Equity", "SPY US Equity"]:
            a, b = bm.at[m, etf] if m in bm.index else np.nan, bm.at[rm, etf] if rm in bm.index else np.nan
            row[etf.split()[0]] = b / a - 1
        rows.append(row)
    df = pd.DataFrame(rows)
    df["spread_net"] = df["Q%d_net" % n_groups] - df["Q1_net"]
    df["spread_gross"] = df["Q%d_gross" % n_groups] - df["Q1_gross"]
    rand = (np.array(rnd_gross), np.array(rnd_net)) if n_random else None
    return df, rand


# ===========================================================================
# Statistics
# ===========================================================================
def ann_return(r):
    r = pd.Series(r).dropna()
    return (1 + r).prod() ** (12 / len(r)) - 1 if len(r) else np.nan


def max_drawdown(r):
    w = (1 + pd.Series(r).fillna(0)).cumprod()
    return float((w / w.cummax() - 1).min())


def one_sided(x):
    x = pd.Series(x).dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return np.nan, np.nan
    t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
    return float(t), float(stats.t.sf(t, len(x) - 1))


def summarize(df, rand, n_groups):
    top, bot = "Q%d" % n_groups, "Q1"
    s = {"months": len(df)}
    ic_t, ic_p = one_sided(df["ic"])
    sp_t, sp_p = one_sided(df["spread_net"])
    s.update(ic_mean=df["ic"].mean(), ic_t=ic_t, ic_p=ic_p, ic_hit=float((df["ic"] > 0).mean()),
             spread_net_mean=df["spread_net"].mean(), spread_t=sp_t, spread_p=sp_p,
             spread_hit=float((df["spread_net"] > 0).mean()))
    for k in ["Q%d" % q for q in range(1, n_groups + 1)] + ["EW"]:
        for kind in ["gross", "net"]:
            s["%s_%s_ann" % (k, kind)] = ann_return(df["%s_%s" % (k, kind)])
        s["%s_mean_monthly_net" % k] = df["%s_net" % k].mean()
        s["%s_turnover" % k] = df["%s_turnover" % k].mean()
    for etf in ["XLE", "XLI", "SPY"]:
        s[etf + "_ann"] = ann_return(df[etf])
    s["top_vol"] = float(df[top + "_net"].std() * np.sqrt(12))
    s["top_maxdd"] = max_drawdown(df[top + "_net"])
    s["EW_maxdd"] = max_drawdown(df["EW_net"])
    for k in GROUPS:
        s["ic_" + k] = df["ic_" + k].mean()
    if rand is not None:
        rg = np.array([ann_return(rand[0][:, j]) for j in range(rand[0].shape[1])])
        rn = np.array([ann_return(rand[1][:, j]) for j in range(rand[1].shape[1])])
        s["random_pct_gross"] = float((rg < s[top + "_gross_ann"]).mean())
        s["random_pct_net"] = float((rn < s[top + "_net_ann"]).mean())
        s["random_gross_p05_p50_p95"] = [float(np.percentile(rg, q)) for q in (5, 50, 95)]
        s["random_net_p05_p50_p95"] = [float(np.percentile(rn, q)) for q in (5, 50, 95)]
    return s


# ===========================================================================
# Locks
# ===========================================================================
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes():
    h = {f: sha(config.PROJECT_DIR / f) for f in HASHED}
    h[BT["data_file"]] = sha(bt_loader.data_path())
    return h


def check_lock(period, rerun_reason):
    if period == "development":
        return
    LOCKS.mkdir(parents=True, exist_ok=True)
    if period == "final" and not (LOCKS / "validation.json").exists():
        raise SystemExit("The final period can only run after the validation period is locked.")
    lock = LOCKS / ("%s.json" % period)
    if lock.exists():
        if not rerun_reason:
            raise SystemExit("%s is locked (%s). Re-running needs --rerun-reason \"...\"; the first result is kept."
                             % (period, lock))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        lock.rename(LOCKS / ("%s_superseded_%s.json" % (period, stamp)))
        log_change("Re-run of the %s period: %s" % (period, rerun_reason), seen="Yes")


def log_change(text, seen):
    p = config.PROJECT_DIR / "PREREGISTRATION.md"
    p.write_text(p.read_text().rstrip("\n") + "\n| %s | %s | logged by backtest.py | %s |\n"
                 % (datetime.now().strftime("%Y-%m-%d"), text, seen))


def write_lock(period, headline):
    LOCKS.mkdir(parents=True, exist_ok=True)
    (LOCKS / ("%s.json" % period)).write_text(json.dumps(
        {"period": period, "time": datetime.now().isoformat(timespec="seconds"), "hashes": hashes(),
         "headline": headline}, indent=2, default=float))


# ===========================================================================
# Report
# ===========================================================================
def pct(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else ("%+." + str(d) + "f%%") % (100 * x)


def verdict(period, s):
    a = BT["alpha"]
    h1 = s["ic_p"] < a and s["ic_mean"] > 0
    h2 = s["spread_p"] < a and s["spread_net_mean"] > 0
    lines = ["H1 (mean IC > 0, one-sided p < %.2f): %s (IC %+.3f, t %.2f, p %.3f)" % (a, "PASS" if h1 else "FAIL", s["ic_mean"], s["ic_t"], s["ic_p"]),
             "H2 (mean Q5-Q1 net spread > 0, p < %.2f): %s (%s per month, t %.2f, p %.3f)" % (
                 a, "PASS" if h2 else "FAIL", pct(s["spread_net_mean"]), s["spread_t"], s["spread_p"])]
    if period == "final":
        val = json.loads((LOCKS / "validation.json").read_text())["headline"]
        val_ok = val["ic_mean"] > 0 and val["spread_net_mean"] > 0
        lines.append("Validation period mean IC and mean net spread both > 0: %s" % ("yes" if val_ok else "no"))
        lines.append("VERDICT: %s" % ("SUPPORTED" if (h1 and h2 and val_ok) else "NOT SUPPORTED"))
    else:
        lines.append("(The verdict is only decided on the final period; this period is reported as is.)")
    return lines


def report(period, main, extra, monthly):
    s = main
    q = BT["n_groups"]
    L = ["# Backtest results: %s period" % period, "",
         "Generated %s by backtest.py. Rules: PREREGISTRATION.md. %d return months (%s to %s)." % (
             datetime.now().strftime("%Y-%m-%d %H:%M"), s["months"], monthly["return_month"].iloc[0],
             monthly["return_month"].iloc[-1]), "",
         "**Not validated as a trading signal. Nothing here applies to the supply chain (Linked companies) "
         "part, which cannot be tested.**", "", "## Primary (Score B, quintiles, 15 bp costs)", ""]
    L += ["- " + x for x in verdict(period, s)]
    L += ["", "| Portfolio | Annualized gross | Annualized net | Mean monthly net | Avg turnover |", "|---|---|---|---|---|"]
    for k in ["Q%d" % i for i in range(q, 0, -1)] + ["EW"]:
        L.append("| %s | %s | %s | %s | %.0f%% |" % (k, pct(s[k + "_gross_ann"]), pct(s[k + "_net_ann"]),
                                                      pct(s[k + "_mean_monthly_net"]), 100 * s[k + "_turnover"]))
    for etf in ["XLE", "XLI", "SPY"]:
        L.append("| %s (ETF) | %s | | | |" % (etf, pct(s[etf + "_ann"])))
    L += ["", "- Q5 net vs equal-weight net: %s per year" % pct(s["Q%d_net_ann" % q] - s["EW_net_ann"]),
          "- IC positive in %.0f%% of months; net spread positive in %.0f%% of months" % (100 * s["ic_hit"], 100 * s["spread_hit"]),
          "- Q5 volatility %.1f%%, max drawdown %s (EW %s)" % (100 * s["top_vol"], pct(s["top_maxdd"], 1), pct(s["EW_maxdd"], 1)),
          "- Random baskets (1,000, same size as Q5): Q5 beats %.0f%% gross and %.0f%% net. Random 5th/50th/95th percentile "
          "annualized gross: %s / %s / %s" % (100 * s["random_pct_gross"], 100 * s["random_pct_net"],
                                               *[pct(v) for v in s["random_gross_p05_p50_p95"]]),
          "- Mean IC by group: " + ", ".join("%s %+.3f" % (g, s["ic_" + g]) for g in GROUPS),
          "- Delisted holdings handled: %d (bankrupt %d); gap months excluded: %d" % (
              monthly["n_delisted"].sum(), monthly["n_delisted_bankrupt"].sum(), monthly["n_gap"].sum()),
          "", "## Secondary (exploratory, pre-listed)", "",
          "| Variant | Mean IC | Mean monthly net spread | t | Top net ann. | EW net ann. |", "|---|---|---|---|---|---|"]
    for name, e in extra.items():
        L.append("| %s | %+.3f | %s | %.2f | %s | %s |" % (name, e["ic_mean"], pct(e["spread_net_mean"]), e["spread_t"],
                                                         pct(e["top_net_ann"]), pct(e["EW_net_ann"])))
    path = OUT / ("%s_summary.md" % period)
    path.write_text("\n".join(L) + "\n")
    return path, L


def chart(period, monthly, rand, s):
    q = BT["n_groups"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    x = pd.PeriodIndex(monthly["return_month"], freq="M").to_timestamp()
    for col, color, lab in [("Q%d_net" % q, "#2a78d6", "Top quintile (net)"), ("EW_net", "#3a3a3a", "Equal weight (net)"),
                            ("Q1_net", "#eb6834", "Bottom quintile (net)"), ("SPY", "#9a9a9a", "SPY")]:
        y = (1 + monthly[col].fillna(0)).cumprod()
        axes[0].plot(x, y, color=color, linewidth=2 if col != "SPY" else 1.5, linestyle="-" if col != "SPY" else ":", label=lab)
    axes[0].set_title("Growth of $1", fontsize=10)
    axes[0].legend(frameon=False, fontsize=8)
    means = [monthly["Q%d_net" % i].mean() * 100 for i in range(1, q + 1)]
    shades = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#1c5cab"][:q]
    axes[1].bar(["Q%d" % i for i in range(1, q + 1)], means, color=shades)
    axes[1].axhline(monthly["EW_net"].mean() * 100, color="#3a3a3a", linestyle="--", linewidth=1)
    axes[1].set_title("Mean monthly net return by quintile (dashed = EW)", fontsize=10)
    axes[1].set_ylabel("percent per month")
    rg = np.array([ann_return(rand[0][:, j]) for j in range(rand[0].shape[1])]) * 100
    axes[2].hist(rg, bins=40, color="#c8c8c8")
    axes[2].axvline(s["Q%d_gross_ann" % q] * 100, color="#2a78d6", linewidth=2)
    axes[2].set_title("Top quintile vs 1,000 random baskets (annualized gross)", fontsize=10)
    axes[2].set_xlabel("percent per year")
    for ax in axes:
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    fig.suptitle("Backtest, %s period (%s to %s). Pre-registered; not a trading signal." % (
        period, monthly["return_month"].iloc[0], monthly["return_month"].iloc[-1]), fontsize=10)
    fig.tight_layout()
    path = OUT / ("%s_chart.png" % period)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


# ===========================================================================
# Main
# ===========================================================================
def return_months(period):
    a, b = BT["periods"][period]
    return list(pd.period_range(a, b, freq="M"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, choices=list(BT["periods"]))
    ap.add_argument("--rerun-reason", default=None)
    args = ap.parse_args()
    check_lock(args.period, args.rerun_reason)

    P = Panels(bt_loader.load_panels())
    OUT.mkdir(parents=True, exist_ok=True)
    P.p["dup_groups"].to_csv(OUT / "duplicate_groups.csv", index=False)
    months = return_months(args.period)          # never includes months of a later period
    q = BT["n_groups"]

    monthly, rand = run(P, months, score="B", n_random=BT["n_random"])
    main_s = summarize(monthly, rand, q)
    monthly.to_csv(OUT / ("%s_monthly.csv" % args.period), index=False)

    extra = {}

    def add(name, df, n_groups=q):
        e = summarize(df, None, n_groups)
        extra[name] = dict(ic_mean=e["ic_mean"], spread_net_mean=e["spread_net_mean"], spread_t=e["spread_t"],
                           top_net_ann=e["Q%d_net_ann" % n_groups], EW_net_ann=e["EW_net_ann"])

    add("Score A (no additions)", run(P, months, score="A")[0])
    add("Deciles (Score B)", run(P, months, n_groups=10)[0], 10)
    add("Energy only (Score B)", run(P, months, sector_split="Energy")[0])
    add("Industrials only (Score B)", run(P, months, sector_split="Industrials")[0])
    add("30 bp costs", run(P, months, cost_bps=30)[0])
    add("All delistings -30%", run(P, months, delist=(-0.30, -0.30))[0])
    add("Bankrupt delistings -100%", run(P, months, delist=(-1.0, 0.0))[0])

    path, lines = report(args.period, main_s, extra, monthly)
    chart(args.period, monthly, rand, main_s)
    if args.period != "development":
        write_lock(args.period, main_s)
    print("\n".join(lines))
    print("\nWrote %s" % path)


if __name__ == "__main__":
    main()
