"""
scoring.py: turn the feature table into transparent 1-to-10 scores.

Steps (all settings in config.py):
  1. Winsorize each metric (clip at the 5th and 95th percentile).
  2. Rank each metric as a percentile (0 = worst, 1 = best) within the
     company's GICS industry, or sector if the industry has fewer than 8
     companies with that number, or all companies as a last resort.
     For "lower is better" metrics the percentile is flipped (1 - p).
  3. Group value = average percentile of the metrics the company has.
  4. PASS 1 (standalone): weighted average of every group except Linked
     companies, weights renormalized over the groups the company has.
  5. PASS 2 (linked): for each core company, the exposure-weighted average
     of the STANDALONE scores of its scored suppliers and customers.
     Using standalone scores avoids going in circles (A's score depending
     on B's which depends on A's).
  6. Final score = weighted average of all available groups, scaled:
     score = 1 + 9 x composite (composite between 0 and 1).

Because the weights sum to 1, every score splits exactly into
    5.5 (neutral) + sum of group contributions,
where a group's contribution = 9 x weight x (group value - 0.5). That is how
explain() says which factors pushed a score up or down and by how much.

Main functions:
    rank_metrics(feats)                 -> percentiles (does not depend on weights)
    aggregate(feats, ranked, links, w)  -> scores (fast, rerun when sliders move)
    score_all(feats, links, w)          -> both in one call
    explain(result, ticker)             -> drivers and plain-English reasons
"""
import numpy as np
import pandas as pd

import config

SCFG = config.CONFIG["scoring"]
METRICS = config.METRICS
LINKED = config.LINKED_GROUP
STANDALONE = config.STANDALONE_GROUPS


# Optional headline score metric (only used when switched on, see config.py).
HEADLINE_METRIC = dict(key="headline_lm", label="Headline tone (Loughran-McDonald)", direction=1, unit="num")


def active_metrics(options=None):
    """The metric list per group after applying the on/off switches.

    options: dict that can override config, e.g. from the dashboard sidebar:
      use_capex_intensity  (default CONFIG scoring.use_capex_intensity)
      use_headline_score   (default CONFIG features.headline_lm_in_score)
    """
    options = options or {}
    use_capex = options.get("use_capex_intensity", SCFG["use_capex_intensity"])
    use_head = options.get("use_headline_score", config.CONFIG["features"]["headline_lm_in_score"])
    out = {}
    for g, ms in METRICS.items():
        ms = [m for m in ms if use_capex or m["key"] != "capex_intensity"]
        if g == "Sentiment" and use_head:
            ms = ms + [HEADLINE_METRIC]
        out[g] = ms
    return out


def metric_defs(options=None):
    """Flat list of active metric definitions, each with its group added."""
    out = []
    for g, ms in active_metrics(options).items():
        for m in ms:
            out.append(dict(m, group=g))
    return out


def normalized_weights(weights=None):
    """Group weights from config (or the sliders), scaled to sum to 1."""
    w = dict(SCFG["group_weights"])
    if weights:
        w.update(weights)
    total = sum(max(v, 0) for v in w.values())
    if total <= 0:
        raise ValueError("At least one group weight must be above zero.")
    return {g: max(w.get(g, 0), 0) / total for g in METRICS}


# ===========================================================================
# Step 1-2: winsorize and rank within peers
# ===========================================================================
def winsorize(s, lo=None, hi=None):
    """Clip a Series at its lo and hi quantiles (missing values stay missing)."""
    lo, hi = SCFG["winsor_pct"] if lo is None else (lo, hi)
    if s.notna().sum() < 3:
        return s
    ql, qh = s.quantile(lo), s.quantile(hi)
    return s.clip(ql, qh)


def pct_rank(values):
    """Percentile 0..1 inside one group: 0 = lowest value, 1 = highest.
    Ties share the average rank. A group of one gets 0.5 (no comparison)."""
    n = values.notna().sum()
    if n <= 1:
        return pd.Series(0.5, index=values.index).where(values.notna())
    return (values.rank(method="average") - 1) / (n - 1)


def peer_percentile(values, industry, sector, direction, min_peers=None):
    """Rank one metric within industry, else sector, else all companies.

    The fallback is decided per metric: a company is ranked in its industry
    only if at least min_peers industry companies HAVE this metric.
    Returns a DataFrame: pct, peer_level, peer_name, n_peers.
    """
    min_peers = SCFG["min_peers"] if min_peers is None else min_peers
    v = values.dropna()
    cols = ["pct", "peer_level", "peer_name", "n_peers"]
    if v.empty:
        return pd.DataFrame(columns=cols)
    ind = industry.reindex(v.index)
    sec = sector.reindex(v.index)

    # Percentile at every level, then pick the right level per company.
    p_ind = v.groupby(ind).transform(pct_rank) if ind.notna().any() else pd.Series(np.nan, index=v.index)
    p_sec = v.groupby(sec).transform(pct_rank) if sec.notna().any() else pd.Series(np.nan, index=v.index)
    p_all = pct_rank(v)
    n_ind = ind.map(ind.value_counts())
    n_sec = sec.map(sec.value_counts())

    use_ind = ind.notna() & (n_ind >= min_peers)
    use_sec = ~use_ind & sec.notna() & (n_sec >= min_peers)
    out = pd.DataFrame(index=v.index)
    out["pct"] = np.where(use_ind, p_ind, np.where(use_sec, p_sec, p_all))
    out["peer_level"] = np.where(use_ind, "industry", np.where(use_sec, "sector", "all"))
    out["peer_name"] = np.where(use_ind, ind, np.where(use_sec, sec, "all companies"))
    out["n_peers"] = np.where(use_ind, n_ind, np.where(use_sec, n_sec, len(v))).astype(int)
    if direction < 0:
        out["pct"] = 1 - out["pct"]   # lower is better: flip
    return out


def rank_metrics(feats, options=None):
    """Winsorize and rank every ranked metric. Independent of the weights.

    Returns a long DataFrame, one row per company and metric with a value:
    ticker, group, metric, label, direction, value (raw), value_w
    (winsorized), pct (0..1, higher = better), peer_level, peer_name, n_peers.
    """
    rows = []
    for m in metric_defs(options):
        if not m.get("rank", True) or m["key"] not in feats.columns:
            continue
        raw = pd.to_numeric(feats[m["key"]], errors="coerce")
        w = winsorize(raw)
        pp = peer_percentile(w, feats["industry"], feats["sector"], m["direction"])
        if pp.empty:
            continue
        pp["value"] = raw.reindex(pp.index)
        pp["value_w"] = w.reindex(pp.index)
        pp["metric"], pp["group"], pp["label"], pp["direction"] = m["key"], m["group"], m["label"], m["direction"]
        rows.append(pp.rename_axis("ticker").reset_index())
    if not rows:
        return pd.DataFrame(columns=["ticker", "group", "metric", "label", "direction", "value", "value_w",
                                     "pct", "peer_level", "peer_name", "n_peers"])
    return pd.concat(rows, ignore_index=True)


# ===========================================================================
# Step 5: linked companies (pass 2)
# ===========================================================================
def linked_scores(links, standalone):
    """Exposure-weighted average standalone score of each company's linked
    suppliers and customers that have a score.

    weight = exposure_pct x cost-category weight (SGA counts 25 percent).
    Links to companies outside the universe, unscored companies, or with no
    exposure number are left out (and counted, so the dashboard can say so).

    Returns (per_company_df, link_detail_df).
    """
    cols = ["linked_suppliers", "linked_customers", "n_links", "n_scored_links_sup", "n_scored_links_cus",
            "n_related", "n_related_scored"]
    if links is None or links.empty:
        return pd.DataFrame(columns=cols), pd.DataFrame()
    cw = SCFG["cost_type_weights"]
    L = links.copy()
    L["cost_weight"] = L["cost_type"].map(cw).fillna(cw["MISSING"])
    L["related_score"] = L["universe_ticker"].map(standalone)
    L["used"] = (L["in_universe"] & L["related_score"].notna() & L["exposure_pct"].gt(0)
                 & (L["universe_ticker"] != L["main_ticker"]))
    L["link_weight"] = np.where(L["used"], L["exposure_pct"] * L["cost_weight"], 0.0)

    out = {}
    for t, g in L.groupby("main_ticker"):
        # Linked coverage: distinct related companies, and how many have a score.
        r = {"n_links": len(g), "n_related": g["related_key"].nunique() if "related_key" in g else len(g),
             "n_related_scored": g.loc[g["used"], "universe_ticker"].nunique()}
        for direction, key, nkey in [("Supplier", "linked_suppliers", "n_scored_links_sup"),
                                     ("Customer", "linked_customers", "n_scored_links_cus")]:
            u = g[(g["direction"] == direction) & g["used"]]
            r[nkey] = len(u)
            r[key] = (u["related_score"] * u["link_weight"]).sum() / u["link_weight"].sum() if len(u) else np.nan
        out[t] = r
    return pd.DataFrame.from_dict(out, orient="index").reindex(columns=cols), L


# ===========================================================================
# Peer group caveat badge
# ===========================================================================
def peer_caveats(feats, ranked):
    """Text badge per company ('' = no caveat). Three reasons:
    1. most metrics ranked against ALL companies (industry and sector too small)
    2. a poor peer group, from the hand-made list in config.py
    3. an extreme ratio (far outside the range of other companies)
    """
    out = {t: [] for t in feats.index}
    if len(ranked):
        share_all = (ranked["peer_level"] == "all").groupby(ranked["ticker"]).mean()
        for t, sh in share_all.items():
            if sh >= SCFG["caveat_all_peers_share"] and t in out:
                out[t].append("ranked against all companies (industry and sector too small)")
    for t, why in config.CONFIG["peer_group_caveats"].items():
        if t in out:
            out[t].append(why)
    labels = {m["key"]: (m["label"], m["unit"]) for m in metric_defs({"use_capex_intensity": True})}
    k = SCFG["extreme_iqr_mult"]
    for c in SCFG["extreme_ratio_metrics"]:
        if c not in feats.columns:
            continue
        v = pd.to_numeric(feats[c], errors="coerce")
        q1, q3 = v.quantile(0.25), v.quantile(0.75)
        iqr = q3 - q1
        if not iqr > 0:
            continue
        for t in v[(v > q3 + k * iqr) | (v < q1 - k * iqr)].index:
            lab, unit = labels.get(c, (c, "num"))
            out[t].append("extreme value: %s %s" % (lab, fmt_value(v[t], unit)))
    return pd.Series({t: "; ".join(x) for t, x in out.items()})


# ===========================================================================
# Steps 3, 4, 6: combine
# ===========================================================================
def _combine(group_vals, w, groups):
    """Weighted average of the available group values, renormalized.

    Returns (score 1..10, contributions DataFrame in score points,
    effective weights DataFrame)."""
    gv = group_vals[groups]
    wt = pd.DataFrame({g: np.where(gv[g].notna(), w[g], 0.0) for g in groups}, index=gv.index)
    total = wt.sum(axis=1)
    eff = wt.div(total.replace(0, np.nan), axis=0)          # renormalized weights
    composite = (gv.fillna(0) * eff).sum(axis=1, min_count=1).where(total > 0)
    score = 1 + 9 * composite
    contrib = 9 * eff * (gv - 0.5)                           # points vs a neutral 5.5
    return score, contrib, eff


def aggregate(feats, ranked, links=None, weights=None, options=None):
    """Turn percentiles into group values, standalone and final scores.

    Fast enough to rerun every time a weight slider moves.
    """
    w = normalized_weights(weights)
    idx = feats.index
    pct = ranked.pivot_table(index="ticker", columns="metric", values="pct").reindex(idx)

    # Group values (average percentile of available metrics) and counts.
    gvals, n_avail, n_total = {}, {}, {}
    for g, ms in active_metrics(options).items():
        if g == LINKED:
            continue
        keys = [m["key"] for m in ms if m["key"] in pct.columns]
        gvals[g] = pct[keys].mean(axis=1) if keys else pd.Series(np.nan, index=idx)
        n_avail[g] = pct[keys].notna().sum(axis=1) if keys else pd.Series(0, index=idx)
        n_total[g] = len(ms)
    gvals = pd.DataFrame(gvals, index=idx)

    # PASS 1: standalone score (no Linked companies group).
    standalone, _, _ = _combine(gvals, w, STANDALONE)

    # PASS 2: linked companies, built from the standalone scores.
    lk, link_detail = linked_scores(links, standalone)
    lk = lk.reindex(idx).astype(float)
    linked_vals = pd.DataFrame({
        "linked_suppliers": (lk["linked_suppliers"] - 1) / 9,   # 1..10 score -> 0..1
        "linked_customers": (lk["linked_customers"] - 1) / 9,
    }, index=idx)
    gvals[LINKED] = linked_vals.mean(axis=1)
    n_avail[LINKED] = linked_vals.notna().sum(axis=1)
    n_total[LINKED] = 2

    final, contrib, eff = _combine(gvals, w, list(METRICS))

    # Completeness: share of the standalone data the company has, weighted
    # by group weight. Linked companies is left out because it only exists
    # for the core names by design (its data is shown separately).
    sw = sum(w[g] for g in STANDALONE)
    completeness = sum(w[g] * n_avail[g] / n_total[g] for g in STANDALONE) / sw if sw > 0 else pd.Series(np.nan, index=idx)

    res = pd.DataFrame(index=idx)
    res["score"] = final
    res["standalone_score"] = standalone
    res["completeness"] = completeness
    res["low_data"] = res["completeness"] < SCFG["min_completeness"]
    res["n_scored_links"] = lk[["n_scored_links_sup", "n_scored_links_cus"]].sum(axis=1, min_count=1)
    res["n_links"] = lk["n_links"]
    res["drivers"] = contrib.apply(top_drivers, axis=1)
    res["rank"] = res["score"].rank(ascending=False, method="min")
    # Percentile rank among all scored companies: rank 10 of 200 = top 5%.
    n_scored = res["score"].notna().sum()
    res["top_pct"] = np.ceil(100 * res["rank"] / n_scored) if n_scored else np.nan
    # Top half says "top 4%"; bottom half says "bottom 2%" (reads better than "top 99%").
    res["bottom_pct"] = np.ceil(100 * (n_scored - res["rank"] + 1) / n_scored) if n_scored else np.nan
    res["top_pct_label"] = ["" if pd.isna(t) else ("top %d%%" % t if t <= 50 else "bottom %d%%" % b)
                            for t, b in zip(res["top_pct"], res["bottom_pct"])]
    res["n_related"] = lk["n_related"]
    res["n_related_scored"] = lk["n_related_scored"]
    res["linked_coverage"] = [("%d of %d scored" % (a, b)) if pd.notna(b) else "no links"
                              for a, b in zip(res["n_related_scored"], res["n_related"])]
    res["peer_caveat"] = peer_caveats(feats, ranked)

    # Per-metric contributions: a group's points split equally over the
    # metrics it actually has.
    det = ranked.copy()
    det["n_in_group"] = det.apply(lambda r: n_avail[r["group"]].get(r["ticker"], np.nan), axis=1) if len(det) else []
    det["eff_weight"] = [eff.at[t, g] if t in eff.index else np.nan for t, g in zip(det["ticker"], det["group"])]
    det["contribution"] = 9 * det["eff_weight"] * (det["pct"] - 0.5) / det["n_in_group"]
    # Flag values that rest on an assumption, so the reasons can say so.
    det["note"] = ""
    if "div_assumed_zero" in feats.columns and len(det):
        assumed = det["ticker"].map(feats["div_assumed_zero"]).fillna(False).astype(bool)
        det.loc[(det["metric"] == "EQY_DVD_YLD_IND") & assumed, "note"] = "no dividend data, assumed 0"
    # Linked metrics as detail rows too, so explain() can talk about them.
    lrows = []
    for key, label in [("linked_suppliers", "Linked suppliers' avg score"), ("linked_customers", "Linked customers' avg score")]:
        v = linked_vals[key].dropna()
        for t, p in v.items():
            lrows.append(dict(ticker=t, group=LINKED, metric=key, label=label, direction=1, value=lk.at[t, key],
                              value_w=lk.at[t, key], pct=p, peer_level="links", peer_name="linked companies",
                              n_peers=lk.at[t, "n_scored_links_sup" if key == "linked_suppliers" else "n_scored_links_cus"],
                              n_in_group=n_avail[LINKED][t], eff_weight=eff.at[t, LINKED],
                              contribution=9 * eff.at[t, LINKED] * (p - 0.5) / n_avail[LINKED][t]))
    if lrows:
        det = pd.concat([det, pd.DataFrame(lrows)], ignore_index=True)

    return {
        "scores": res,
        "group_values": gvals,
        "contributions": contrib,
        "effective_weights": eff,
        "metric_detail": det,
        "link_detail": link_detail,
        "weights": w,
    }


def score_all(feats, links=None, weights=None, options=None):
    """Rank and aggregate in one call. Returns the aggregate() dict plus 'ranked'."""
    ranked = rank_metrics(feats, options)
    out = aggregate(feats, ranked, links, weights, options)
    out["ranked"] = ranked
    return out


# ===========================================================================
# Explanations
# ===========================================================================
def top_drivers(contrib_row, n=3):
    """'Valuation +, Health +, Risk -': the n groups that moved the score most."""
    c = contrib_row.dropna()
    c = c[c.abs() > 1e-9]
    top = c.reindex(c.abs().sort_values(ascending=False).index)[:n]
    return ", ".join("%s %s" % (g, "+" if v > 0 else "-") for g, v in top.items())


def fmt_value(value, unit):
    """Format a metric value for a sentence."""
    if pd.isna(value):
        return "n/a"
    if unit == "pct":
        return "%.1f%%" % value
    if unit == "frac":
        return "%+.1f%%" % (100 * value) if abs(value) < 10 else "%.0f%%" % (100 * value)
    if unit == "signed":
        return "%+.2f" % value
    if unit == "rating":
        return "%.1f" % value
    if unit == "above_below":
        return "%.0f%% %s" % (abs(100 * value), "above" if value >= 0 else "below")
    if unit == "faster_slower":
        return "%.1f points %s than" % (abs(100 * value), "faster" if value >= 0 else "slower")
    if unit == "ratio":
        return "%.1f%%" % (100 * value)
    if unit == "x":
        return "%.1fx" % value
    if unit == "score":
        return "%.1f" % value
    return "%.2f" % value


def peer_phrase(level, name):
    if level == "industry":
        return "%s peers" % name
    if level == "sector":
        return "%s sector peers" % name
    return "all scored companies"


def pct_phrase(p, level, name):
    """'85% of Machinery peers', or 'all Machinery peers' at 100%."""
    peers = peer_phrase(level, name)
    n = int(round(100 * p))
    if n >= 100:
        return "all " + peers if not peers.startswith("all ") else peers
    return "%d%% of %s" % (n, peers)


def reason_text(row):
    """One plain-English sentence for one metric of one company.

    Uses the good / bad sentence for the metric in config.METRICS, filled in
    with the actual value and how many peers it beats (or trails)."""
    m = next((d for d in metric_defs({"use_capex_intensity": True, "use_headline_score": True})
              if d["key"] == row["metric"]), {})
    unit = m.get("unit", "num")
    if row["group"] == LINKED:
        who = "suppliers" if row["metric"] == "linked_suppliers" else "customers"
        return "Its %d scored %s average a standalone score of %.1f (only part of its supply chain is scored)." % (
            row["n_peers"], who, row["value"])
    p = row["pct"]
    note = row.get("note")
    note = " (%s)" % note if isinstance(note, str) and note else ""
    good = p >= 0.5
    pp = pct_phrase(p if good else 1 - p, row["peer_level"], row["peer_name"])
    tmpl = m.get("good" if good else "bad")
    if tmpl:
        text = tmpl.format(v=fmt_value(row["value"], unit), pp=pp, note=note)
        if note and "{note}" not in tmpl:
            text = text[:-1] + note + "."
        return text
    return "%s of %s%s is %s %s." % (row["label"], fmt_value(row["value"], unit), note,
                                     "better than" if good else "worse than", pp)


def explain(result, ticker, n_reasons=3):
    """Everything needed to explain one company's score.

    Returns a dict with: score, completeness, group table (value, weight,
    points), metric table, and reasons (sentences for the metrics that
    moved the score most, positive and negative).
    """
    s = result["scores"].loc[ticker]
    groups = pd.DataFrame({
        "group_value": result["group_values"].loc[ticker],
        "weight_used": result["effective_weights"].loc[ticker],
        "points": result["contributions"].loc[ticker],
    })
    det = result["metric_detail"]
    det = det[det["ticker"] == ticker].copy()
    det = det.reindex(det["contribution"].abs().sort_values(ascending=False).index)
    det["reason"] = det.apply(reason_text, axis=1) if len(det) else []
    # Only metrics clearly away from the middle become reasons.
    clear = (det["pct"] - 0.5).abs() >= SCFG["reason_min_distance"] if len(det) else []
    ups = det[(det["contribution"] > 0) & clear].head(n_reasons)
    downs = det[(det["contribution"] < 0) & clear].head(n_reasons)
    return {
        "ticker": ticker,
        "score": s["score"],
        "standalone_score": s["standalone_score"],
        "completeness": s["completeness"],
        "low_data": bool(s["low_data"]),
        "drivers": s["drivers"],
        "groups": groups,
        "metrics": det,
        "reasons_up": ups["reason"].tolist(),
        "reasons_down": downs["reason"].tolist(),
    }


if __name__ == "__main__":
    import loader
    import features
    d = loader.load_data()
    feats, extras = features.build_features(d)
    r = score_all(feats, d["links"])
    s = r["scores"].join(feats[["NAME", "industry"]]).sort_values("score", ascending=False)
    pd.set_option("display.width", 220)
    print(s[["NAME", "industry", "score", "standalone_score", "completeness", "drivers"]].head(15).round(2))
    print(s["score"].describe().round(2))
