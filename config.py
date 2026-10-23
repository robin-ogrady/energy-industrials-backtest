"""
config.py: every setting for the screener lives here.

Other files import from this module. Nothing else should hardcode file paths,
weights, thresholds, or window lengths. Change a number here and every part of
the tool (loader, scoring, dashboard, PDF report) picks it up.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# The project folder is the folder this file sits in.
PROJECT_DIR = Path(__file__).resolve().parent

# The Bloomberg values-only workbook, relative to the project folder (an
# absolute path also works). This is the ONLY place the file name is set.
# To use another file without editing this line, set the environment
# variable SCREENER_DATA_FILE, e.g.
#   SCREENER_DATA_FILE=sample/sample_workbook.xlsx .venv/bin/streamlit run app.py
DATA_FILE = os.environ.get("SCREENER_DATA_FILE", "bloomberg_data_pull_wide_v12_values.xlsx")

# Folder for parsed data, links.csv, review lists, downloaded word lists.
# It is gitignored because it contains licensed Bloomberg numbers.
CACHE_DIR = PROJECT_DIR / "cache"


def data_path():
    """Full path to the workbook (PROJECT_DIR + DATA_FILE)."""
    return PROJECT_DIR / DATA_FILE


# ---------------------------------------------------------------------------
# Settings dictionary
# ---------------------------------------------------------------------------
CONFIG = {
    # ---- Loader: how the workbook is laid out --------------------------------
    "loader": {
        # Width (number of columns) of one company's group on each time series
        # sheet. Group i starts at column 1 + i * width (1-based).
        "group_width": {
            "Prices_D": 2,
            "Fundamentals_Q": 5,
            "Estimates_W": 3,
            "Sentiment_D": 2,
            "Commodities_D": 2,
        },
        # Supply chain tabs: one block per core name (Universe demo_set = Y,
        # in Universe order), each 23 rows tall, 20 data rows max.
        # sc_n_blocks None = as many blocks as there are core names.
        "sc_block_height": 23,
        "sc_max_rows": 20,
        "sc_n_blocks": None,
        # Strings that mean "missing". Any string starting with "#" is also
        # treated as missing (Bloomberg errors like "#N/A N/A").
        "missing_strings": ["--", "", "N/A", "NA"],
        # Snapshot fields that returned 100 percent errors in our pull. The
        # loader drops ANY field that is 100 percent missing; this list is
        # only used to report whether your pull behaves the same way.
        "expected_broken_fields": ["INVENTORY_TURNOVER", "BACKLOG", "NEWS_HEAT_PUB_AVG"],
        # A field with less missing data than this counts as "working",
        # regardless of its field_status label in the Data_Dictionary.
        "working_field_max_missing": 0.05,
    },

    # ---- Supply chain name matching ------------------------------------------
    "matching": {
        # A related company is "in the universe" when its Bloomberg ticker
        # exactly equals a Snapshot ticker. If not, we compare company names
        # with rapidfuzz (token_sort_ratio on cleaned names, 0 to 100).
        # At or above this score we accept the match (and list it for review).
        "fuzzy_accept": 95,
        # Between this and fuzzy_accept: NOT matched, listed as "uncertain"
        # in the review file so you can decide by hand.
        "fuzzy_review": 85,
    },

    # ---- Features: window lengths --------------------------------------------
    "features": {
        # Time series whose last value is older than this (vs the Snapshot
        # asof date) are treated as stale and not used.
        "max_stale_days": 30,
        # EV/EBITDA vs own history needs at least this many quarters of
        # history (about 2 years) for the average to mean anything.
        "valuation_history_min_quarters": 8,
        # Inventory vs sales: compare the latest quarter with the same
        # quarter one year earlier (4 quarters back), which avoids seasonality.
        "inventory_lookback_quarters": 4,
        # EPS estimate revisions over 4 and 12 weeks (1 and 3 months).
        "eps_revision_weeks": [4, 12],
        # Change in the analyst rating over 12 weeks.
        "rec_change_weeks": 12,
        # Momentum windows in trading days (21 = about 1 month, 63 = 3 months).
        "momentum_days": {"ret_1m": 21, "ret_3m": 63},
        # Commodity betas: last 252 trading days (1 year); need at least 120
        # common days or the beta is left missing.
        "beta_window_days": 252,
        "beta_min_obs": 120,
        "commodities": {
            "CL1 Comdty": "WTI crude",
            "CO1 Comdty": "Brent crude",
            "NG1 Comdty": "Natural gas",
            "HG1 Comdty": "Copper",
            "HRC1 Comdty": "Hot-rolled steel",
        },
        # News: average over 30 calendar days, trend = 7-day avg minus
        # 30-day avg. Fewer than 5 news days in the 30 = "low coverage" and
        # sentiment is left out of that company's score.
        "sentiment_long_days": 30,
        "sentiment_short_days": 7,
        "sentiment_min_coverage": 5,
        # Optional Loughran-McDonald headline scoring. OFF by default.
        # enabled: compute and show it. in_score: also add it to the
        # Sentiment group. Both must be switched on deliberately.
        "headline_lm_enabled": False,
        "headline_lm_in_score": False,
        # Download locations for the word list, tried in order. This is the
        # official Notre Dame SRAF Google Drive file (1993-2023 version,
        # about 9 MB). Free for academic use.
        "lm_urls": [
            "https://drive.google.com/uc?export=download&id=1iq2RUf8qGFEAk1g8wQntP3habOnR3fXF",
        ],
    },

    # ---- Scoring ---------------------------------------------------------------
    "scoring": {
        # Clip each metric at its 5th and 95th percentile (across all scored
        # companies) before ranking, so one wild number cannot dominate.
        "winsor_pct": [0.05, 0.95],
        # Rank within GICS industry when at least this many companies in the
        # industry have the metric; otherwise sector; otherwise all companies.
        "min_peers": 8,
        # Scores built on less than 60 percent of the data get an asterisk.
        "min_completeness": 0.60,
        # Weight of each factor group in the final score (they sum to 1).
        # Health and Valuation get the most: they rest on reported numbers
        # for every company. Sentiment and Momentum are kept small on purpose.
        # Linked companies only exists for the core names; for everyone
        # else its weight is spread over the other groups (renormalized).
        "group_weights": {
            "Health": 0.20,
            "Risk": 0.15,
            "Valuation": 0.20,
            "Demand pipeline": 0.10,
            "Expectations": 0.15,
            "Linked companies": 0.10,
            "Sentiment": 0.05,
            "Momentum": 0.05,
        },
        # Supply chain link weight by cost category (multiplied by exposure).
        # SGA links are overhead (rent, software) and say little about the
        # business, so they count 25 percent of a COGS or CAPEX link.
        # Missing category is treated like COGS but flagged.
        "cost_type_weights": {"COGS": 1.0, "CAPEX": 1.0, "SGA": 0.25, "MISSING": 1.0},
        # Capex intensity (|CAPITAL_EXPEND| / last 4 quarters of sales,
        # higher = better) in the Demand pipeline group. OFF by default: the
        # direction is debatable (more investment can mean expected demand or
        # just a capital-hungry business) and BEST_CAPEX could not be used
        # because its period is unclear. Demand pipeline then uses only the
        # two inventory measures. Set True (or use the sidebar toggle) to add it.
        "use_capex_intensity": False,
        # Peer group caveat badge, part 1 (automatic): flag a company when at
        # least this share of its metrics had to be ranked against ALL
        # companies because its industry and sector were both too small.
        "caveat_all_peers_share": 0.5,
        # Part 2 (automatic): flag extreme values in these ratio metrics, i.e.
        # more than extreme_iqr_mult interquartile ranges beyond the 25th or
        # 75th percentile of all scored companies (e.g. a price/book of 400x).
        # EPS revisions are left out: most are near 0, so any real revision
        # would look "extreme".
        "extreme_ratio_metrics": [
            "EV_TO_T12M_EBITDA", "PE_RATIO", "PX_TO_BOOK_RATIO", "NET_DEBT_TO_EBITDA",
            "TOT_DEBT_TO_COM_EQY", "RETURN_COM_EQY", "OPER_MARGIN", "CUR_RATIO",
            "FREE_CASH_FLOW_YIELD", "ev_vs_history", "inv_to_sales",
        ],
        "extreme_iqr_mult": 5,
        # Plain-English reasons skip metrics within 15 percentile points of
        # the middle: they barely moved the score.
        "reason_min_distance": 0.15,
    },

    # Peer group caveat badge, part 3 (by hand): companies whose GICS industry
    # is a poor comparison group. Edit freely; the reason is shown as the badge.
    "peer_group_caveats": {
        "LEU US Equity": "uranium enrichment ranked against oil and gas companies",
        "UEC US Equity": "uranium miner ranked against oil and gas companies",
        "CCJ US Equity": "uranium miner ranked against oil and gas companies",
        "OKLO US Equity": "pre-revenue nuclear developer ranked against operating utilities",
        "SMR US Equity": "pre-revenue reactor designer ranked against electrical equipment makers",
    },
}

# ---------------------------------------------------------------------------
# Backtest settings. They mirror PREREGISTRATION.md and must not change after
# results are seen (any change goes in its change log with date and reason).
# ---------------------------------------------------------------------------
BACKTEST = {
    "data_file": os.environ.get("BACKTEST_DATA_FILE", "backtest_pull_all_values_v2.xlsx"),
    "indices": {"S15ENRS Index": "Energy", "S15INDU Index": "Industrials"},
    # Periods by holding (return) month, inclusive.
    "periods": {
        "development": ("2016-01", "2019-12"),
        "validation": ("2020-01", "2022-12"),
        "final": ("2023-01", "2026-09"),
    },
    # Duplicate listings: same root ticker, or monthly returns this correlated
    # over at least this many overlapping months.
    "dup_corr": 0.995,
    "dup_min_overlap": 12,
    # Point in time.
    "ann_fallback_window": (-30, 180),   # days around quarter end to search Surprise dates
    "ann_fallback_days": 90,             # quarter end + 90 days if no date at all
    "ann_crosscheck_days": 5,
    "max_quarter_age_days": 270,
    "ttm_max_span_days": 400,            # 4 consecutive quarters within this span
    "yoy_days": (330, 400),              # "a year earlier" quarter must be this far back
    "surprise_max_age_days": 120,
    "surprise_zero": 1e-9,               # |EPS| below this = Bloomberg "not available"
    "ev_history_months": (60, 24),       # look back 60 months, need at least 24
    # Score.
    "group_weights": {"Health": 0.20, "Risk": 0.15, "Valuation": 0.20, "Demand pipeline": 0.10,
                      "Expectations": 0.15, "Momentum": 0.05},     # rescaled to sum to 1 in code
    "min_completeness": 0.5,
    # Portfolios.
    "n_groups": 5,
    "cost_bps": 15,
    "n_random": 1000,
    "seed": 20261023,
    # Delisting returns: bankruptcy flag = root of 4+ characters ending in Q.
    "delist_return_bankrupt": -0.30,
    "delist_return_other": 0.0,
    # Hypothesis tests.
    "alpha": 0.05,
}

# Display names for factor groups (dashboard and report).
GROUP_LABELS = {"Linked companies": "Linked companies (limited coverage)"}


def group_label(group):
    return GROUP_LABELS.get(group, group)

# ---------------------------------------------------------------------------
# Metric definitions: which group each metric belongs to and its direction.
#   direction +1 = higher is better, -1 = lower is better (rank is flipped)
#   unit: how to print the value ("pct" = already in percent units,
#         "frac" = a change shown as +/- percent, "ratio" = a fraction
#         shown as percent, "x" = a multiple, "num" = plain number,
#         "signed" = number with +/-, "rating" = one decimal,
#         "above_below" = "70% above" / "20% below",
#         "faster_slower" = "5.0 points faster than" / "slower than")
#   good / bad: plain-English sentence used when the metric pushed the score
#         up / down. {v} = the value, {pp} = e.g. "85% of Machinery peers",
#         {note} = an assumption note such as "(no dividend data, assumed 0)".
# Linked companies metrics are not ranked: they are already 1 to 10 scores.
# ---------------------------------------------------------------------------
METRICS = {
    "Health": [
        dict(key="SALES_GROWTH", label="Sales growth", direction=1, unit="pct",
             good="Sales grew {v} over the past year, faster than {pp}.",
             bad="Sales changed {v} over the past year, slower than {pp}."),
        dict(key="GROSS_MARGIN", label="Gross margin", direction=1, unit="pct",
             good="Keeps {v} of sales after direct costs (gross margin), more than {pp}.",
             bad="Keeps only {v} of sales after direct costs (gross margin), less than {pp}."),
        dict(key="OPER_MARGIN", label="Operating margin", direction=1, unit="pct",
             good="Operating margin of {v} is higher than {pp}.",
             bad="Operating margin of {v} is lower than {pp}."),
        dict(key="RETURN_COM_EQY", label="Return on equity", direction=1, unit="pct",
             good="Earns {v} on shareholders' equity, more than {pp}.",
             bad="Earns only {v} on shareholders' equity, less than {pp}."),
        dict(key="RETURN_ON_ASSET", label="Return on assets", direction=1, unit="pct",
             good="Earns {v} on its assets, more than {pp}.",
             bad="Earns only {v} on its assets, less than {pp}."),
        dict(key="FREE_CASH_FLOW_YIELD", label="Free cash flow yield", direction=1, unit="pct",
             good="Generates free cash equal to {v} of its market value, more than {pp}.",
             bad="Generates free cash equal to {v} of its market value, less than {pp}."),
    ],
    "Risk": [
        dict(key="NET_DEBT_TO_EBITDA", label="Net debt / EBITDA", direction=-1, unit="x",
             good="Net debt is {v} its yearly EBITDA, lighter debt than {pp}.",
             bad="Net debt is {v} its yearly EBITDA, heavier debt than {pp}."),
        dict(key="TOT_DEBT_TO_COM_EQY", label="Debt / equity", direction=-1, unit="pct",
             good="Debt is {v} of equity, lower than {pp}.",
             bad="Debt is {v} of equity, higher than {pp}."),
        dict(key="CUR_RATIO", label="Current ratio", direction=1, unit="x",
             good="Short-term assets cover short-term debts {v}, more cushion than {pp}.",
             bad="Short-term assets cover short-term debts only {v}, less cushion than {pp}."),
        dict(key="SHORT_INT_RATIO", label="Short interest (days to cover)", direction=-1, unit="num",
             good="Fewer short sellers betting against it than {pp} ({v} days to cover).",
             bad="More short sellers betting against it than {pp} ({v} days to cover)."),
    ],
    "Valuation": [
        dict(key="EV_TO_T12M_EBITDA", label="EV/EBITDA", direction=-1, unit="x",
             good="Cheaper than {pp} on EV/EBITDA ({v}).",
             bad="More expensive than {pp} on EV/EBITDA ({v})."),
        dict(key="PE_RATIO", label="P/E", direction=-1, unit="x",
             good="Cheaper than {pp} on price/earnings ({v}).",
             bad="More expensive than {pp} on price/earnings ({v})."),
        dict(key="PX_TO_BOOK_RATIO", label="Price / book", direction=-1, unit="x",
             good="Cheaper than {pp} on price/book ({v}).",
             bad="More expensive than {pp} on price/book ({v})."),
        dict(key="ev_vs_history", label="EV/EBITDA vs own 5-yr average", direction=-1, unit="above_below",
             good="EV/EBITDA is {v} its own 5-year average, cheaper against its history than {pp}.",
             bad="EV/EBITDA is {v} its own 5-year average, pricier against its history than {pp}."),
        dict(key="EQY_DVD_YLD_IND", label="Dividend yield", direction=1, unit="pct",
             good="Dividend yield of {v} is higher than {pp}.",
             bad="Dividend yield of {v}{note} is lower than {pp}."),
    ],
    "Demand pipeline": [
        dict(key="inv_minus_sales_growth", label="Inventory growth minus sales growth (1 yr)", direction=-1, unit="faster_slower",
             good="Inventory grew {v} sales over the past year, leaner than {pp}.",
             bad="Inventory grew {v} sales over the past year, more stock building up than {pp}."),
        dict(key="inv_to_sales", label="Inventory / sales", direction=-1, unit="ratio",
             good="Holds inventory worth {v} of a year's sales, leaner than {pp}.",
             bad="Holds inventory worth {v} of a year's sales, heavier than {pp}."),
        dict(key="capex_intensity", label="Capex / sales", direction=1, unit="ratio",
             good="Invests {v} of sales in new capacity, more than {pp}.",
             bad="Invests only {v} of sales in new capacity, less than {pp}."),
    ],
    "Expectations": [
        dict(key="eps_rev_4w", label="EPS estimate revision (4 wk)", direction=1, unit="frac",
             good="Analysts changed profit forecasts by {v} in 4 weeks, better than {pp}.",
             bad="Analysts changed profit forecasts by {v} in 4 weeks, worse than {pp}."),
        dict(key="eps_rev_12w", label="EPS estimate revision (12 wk)", direction=1, unit="frac",
             good="Analysts changed profit forecasts by {v} in 12 weeks, better than {pp}.",
             bad="Analysts changed profit forecasts by {v} in 12 weeks, worse than {pp}."),
        dict(key="rec_change_12w", label="Analyst rating change (12 wk)", direction=1, unit="signed",
             good="Analyst rating moved {v} points in 12 weeks, better than {pp}.",
             bad="Analyst rating moved {v} points in 12 weeks, worse than {pp}."),
        dict(key="EQY_REC_CONS", label="Analyst rating (1 sell to 5 buy)", direction=1, unit="rating",
             good="Analysts rate it {v} out of 5 (5 = buy), higher than {pp}.",
             bad="Analysts rate it {v} out of 5 (5 = buy), lower than {pp}."),
        dict(key="target_upside", label="Target price upside", direction=1, unit="frac",
             good="Analyst price targets are {v} from today's price, more upside than {pp}.",
             bad="Analyst price targets are {v} from today's price, less upside than {pp}."),
    ],
    "Linked companies": [
        dict(key="linked_suppliers", label="Linked suppliers' avg score", direction=1, unit="score", rank=False),
        dict(key="linked_customers", label="Linked customers' avg score", direction=1, unit="score", rank=False),
    ],
    "Sentiment": [
        dict(key="sent_30d", label="News sentiment (30-day avg)", direction=1, unit="num",
             good="News over the last 30 days ({v}) was more positive than for {pp}.",
             bad="News over the last 30 days ({v}) was more negative than for {pp}."),
        dict(key="sent_trend", label="News sentiment trend (7d minus 30d)", direction=1, unit="signed",
             good="News tone this week improved more than for {pp} ({v}).",
             bad="News tone this week worsened more than for {pp} ({v})."),
    ],
    "Momentum": [
        dict(key="ret_1m", label="1-month return", direction=1, unit="frac",
             good="Stock moved {v} in the last month, stronger than {pp}.",
             bad="Stock moved {v} in the last month, weaker than {pp}."),
        dict(key="ret_3m", label="3-month return", direction=1, unit="frac",
             good="Stock moved {v} in the last 3 months, stronger than {pp}.",
             bad="Stock moved {v} in the last 3 months, weaker than {pp}."),
    ],
}

# Groups computed in pass 1 (everything except Linked companies).
LINKED_GROUP = "Linked companies"
STANDALONE_GROUPS = [g for g in METRICS if g != LINKED_GROUP]
