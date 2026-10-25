#!/usr/bin/env python
"""Build the investor presentation from the Dunnhumby panel.

This is presentation tooling, not new analysis: every number and chart re-runs the
same computation as `notebooks/EDA.ipynb` (§2 trend, §3 seasonality, §4 repeat-year,
§5.2 promotion economics, §5.3 baskets, §5.5 household acquisition). The printed
table at the end is the audit trail for the numbers hard-coded in the deck.

Run from the repository root or from this directory:

    uv run python presentation/build_deck.py

Outputs `presentation/index.html` with the SVG charts embedded, so the deck is one
portable file. Set DECK_PREVIEW=1 to also write PNG previews to /tmp/opencode.
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
DATA = ROOT / "data" / "dunnhumby - Breakfast at the Frat.xlsx"
TEMPLATE = HERE / "template.html"
OUT = HERE / "index.html"
PREVIEW = Path("/tmp/opencode/deck_preview")

# ---------------------------------------------------------------------------
# palette and chart style
# ---------------------------------------------------------------------------
INK = "#171a17"
MUTED = "#6b716a"
FAINT = "#9aa099"
RULE = "#dedbd2"
ACCENT = "#1f6b56"
ACCENT_LIGHT = "#8fb8aa"
CLAY = "#9f4a3d"
AMBER = "#a97722"
BLUE = "#486b80"

plt.rcParams.update({
    "font.family": "Adwaita Sans",
    "font.size": 10.5,
    "axes.edgecolor": RULE,
    "axes.linewidth": 0.8,
    "axes.labelcolor": MUTED,
    "axes.labelsize": 9.5,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "text.color": INK,
    "axes.titlecolor": INK,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.04,
    "svg.fonttype": "path",
    "svg.hashsalt": "deck",
})


def finish(ax, *, grid_axis="y") -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(RULE)
    ax.grid(axis=grid_axis, color="#eeece5", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def svg_of(fig: plt.Figure, name: str) -> str:
    """Return an inline-able SVG string, with unique ids and no XML prolog."""
    if os.environ.get("DECK_PREVIEW"):
        PREVIEW.mkdir(parents=True, exist_ok=True)
        fig.savefig(PREVIEW / f"{name}.png", dpi=160)
    buf = io.StringIO()
    plt.rcParams["svg.hashsalt"] = name
    fig.savefig(buf, format="svg", metadata={"Date": None})
    plt.close(fig)
    svg = buf.getvalue()
    start = svg.index("<svg")
    svg = svg[start:]

    # Inlining six SVGs in one document means the generated ids have to be
    # namespaced, or every chart would share figure_1 / patch_1 / glyph ids.
    ids = set(re.findall(r'id="([^"]+)"', svg))
    mapping = {old: f"{name}-{old}" for old in ids}
    svg = re.sub(r'id="([^"]+)"', lambda m: f'id="{mapping[m.group(1)]}"', svg)
    svg = re.sub(r"url\(#([^)]+)\)",
                 lambda m: f"url(#{mapping.get(m.group(1), m.group(1))})", svg)
    svg = re.sub(r'(xlink:href|href)="#([^"]+)"',
                 lambda m: f'{m.group(1)}="#{mapping.get(m.group(2), m.group(2))}"', svg)
    return svg


# ---------------------------------------------------------------------------
# data and statistics
# ---------------------------------------------------------------------------
def load_panel() -> tuple[pd.DataFrame, pd.DataFrame]:
    xl = pd.ExcelFile(DATA, engine="openpyxl")
    tx = pd.read_excel(xl, "dh Transaction Data", header=1)
    stores = pd.read_excel(xl, "dh Store Lookup", header=1)
    products = pd.read_excel(xl, "dh Products Lookup", header=1)

    stores = stores.drop_duplicates("STORE_ID").copy()
    stores["SIZE_TIER"] = pd.qcut(
        stores.SALES_AREA_SIZE_NUM, 3, labels=["small", "mid", "large"]
    )
    tx = tx.merge(
        products[["UPC", "DESCRIPTION", "CATEGORY", "MANUFACTURER"]],
        on="UPC", how="left",
    )
    tx = tx.merge(
        stores[["STORE_ID", "SEG_VALUE_NAME", "MSA_CODE", "SIZE_TIER"]],
        left_on="STORE_NUM", right_on="STORE_ID", how="left",
    )
    tx["ANY_PROMO"] = (
        (tx.DISPLAY == 1) | (tx.FEATURE == 1) | (tx.TPR_ONLY == 1)
    ).astype(int)
    tx["ISO_YEAR"] = tx.WEEK_END_DATE.dt.isocalendar().year.astype(int)
    tx["ISO_WEEK"] = tx.WEEK_END_DATE.dt.isocalendar().week.astype(int)
    return tx, stores


def ols_slope(y, X, lag=4):
    """Slope with Newey-West HAC(lag) standard error."""
    X = np.column_stack([np.ones(len(y)), X])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta
    XtX_inv = np.linalg.inv(X.T @ X)
    meat = (X * resid[:, None]).T @ (X * resid[:, None])
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        G = (X[k:] * resid[k:, None]).T @ (X[:-k] * resid[:-k, None])
        meat += w * (G + G.T)
    return beta[1], np.sqrt((XtX_inv @ meat @ XtX_inv)[1, 1])


def classical_decomp(y, period=52):
    y = np.asarray(y, dtype=float)
    trend = pd.Series(y).rolling(period, center=True, min_periods=period // 2).mean()
    detrended = y - trend
    seasonal = np.array([np.nanmean(detrended[i::period]) for i in range(period)])
    seasonal -= seasonal.mean()
    return trend.to_numpy(), seasonal


def seasonal_share(detrended, period=52):
    phase = np.array([np.nanmean(detrended[i::period]) for i in range(period)])
    phase -= phase.mean()
    return np.var(phase)


def twoway_fe(y, x, cell, week, iters=500, tol=1e-11):
    """Absorb cell and week fixed effects; cluster the SE on the cell."""
    y = np.asarray(y, dtype=float).copy()
    x = np.asarray(x, dtype=float).copy()
    y -= y.mean()
    x -= x.mean()
    cc, wc = pd.factorize(cell)[0], pd.factorize(week)[0]
    for _ in range(iters):
        prev_y, prev_x = y.copy(), x.copy()
        for codes in (cc, wc):
            counts = np.bincount(codes)
            y -= (np.bincount(codes, weights=y) / counts)[codes]
            x -= (np.bincount(codes, weights=x) / counts)[codes]
        if max(np.abs(y - prev_y).max(), np.abs(x - prev_x).max()) < tol:
            break
    beta = (x @ y) / (x @ x)
    resid = y - beta * x
    score = np.bincount(cc, weights=x * resid)
    n_cells = len(np.unique(cc))
    k = 1 + n_cells + len(np.unique(wc)) - 1
    dof = (n_cells / (n_cells - 1)) * ((len(y) - 1) / (len(y) - k))
    return beta, np.sqrt((score ** 2).sum() * dof) / (x @ x), len(y) - k


def cost_interval(incr, spend, draws=2_000, seed=0):
    incr, spend = np.asarray(incr), np.asarray(spend)
    rng = np.random.default_rng(seed)
    out = np.empty(draws)
    for i in range(draws):
        idx = rng.integers(0, len(incr), len(incr))
        out[i] = spend[idx].sum() / incr[idx].sum()
    return spend.sum() / incr.sum(), *np.percentile(out, [2.5, 97.5])


def analyse(tx: pd.DataFrame, stores: pd.DataFrame) -> dict:
    """Recompute the numbers the deck quotes. Mirrors EDA.ipynb."""
    weekly = (
        tx.groupby("WEEK_END_DATE")
        .agg(UNITS=("UNITS", "sum"), SPEND=("SPEND", "sum"))
        .asfreq("W-WED")
    )
    t = np.arange(len(weekly))
    D = pd.get_dummies(weekly.index.isocalendar().week, drop_first=True).to_numpy(dtype=float)

    trend = {}
    for col in ["UNITS", "SPEND"]:
        y = weekly[col].to_numpy(dtype=float)
        b_raw, se_raw = ols_slope(y, t.reshape(-1, 1))
        b_adj, se_adj = ols_slope(y, np.column_stack([t, D]))
        mk = stats.kendalltau(t, y)
        slope, _ = np.polyfit(t, y, 1)
        trend[col] = {
            "per_year": float(slope * 52 / y.mean()),
            "p_hac": float(2 * stats.t.sf(abs(b_raw / se_raw), len(y) - 2)),
            "p_adj": float(2 * stats.t.sf(abs(b_adj / se_adj), len(y) - 2 - D.shape[1])),
            "p_mk": float(mk.pvalue),
        }

    decomp = {}
    rng = np.random.default_rng(0)
    for col in ["UNITS", "SPEND"]:
        y = np.log1p(weekly[col].to_numpy(dtype=float))
        trend_c, seasonal = classical_decomp(y)
        amp = seasonal.max() - seasonal.min()
        detrended = y - trend_c
        observed = seasonal_share(detrended) / np.var(y)
        null = np.array(
            [seasonal_share(rng.permutation(detrended)) / np.var(y) for _ in range(2_000)]
        )
        decomp[col] = {
            "trend_share": float(100 * np.nanvar(trend_c) / np.var(y)),
            "seasonal_share": float(100 * np.var(seasonal) / np.var(y)),
            "swing": float(100 * np.expm1(amp)),
            "p_perm": float(np.mean(null >= observed)),
            "profile": 100 * np.expm1(seasonal[:52]),
        }

    # repeat-year correlations, Fisher z + BH
    iso_cov = tx.groupby("ISO_YEAR").WEEK_END_DATE.nunique()
    full_years = iso_cov[iso_cov == 52].index.tolist()
    pair = tx[tx.ISO_YEAR.isin(full_years)]
    repeat_rows = []
    for stratum in ["SEG_VALUE_NAME", "SIZE_TIER", "MSA_CODE"]:
        wide = (
            pair.groupby([stratum, "ISO_WEEK", "ISO_YEAR"], observed=True)
            .SPEND.mean().unstack("ISO_YEAR").dropna()
        )
        for name, d in wide.groupby(level=0):
            r, n = d[full_years[0]].corr(d[full_years[1]]), len(d)
            z = np.arctanh(r)
            half = stats.norm.ppf(0.975) / np.sqrt(n - 3)
            t_stat = r * np.sqrt((n - 2) / max(1 - r ** 2, 1e-12))
            repeat_rows.append(
                {"r": float(r), "lo": float(np.tanh(z - half)),
                 "hi": float(np.tanh(z + half)),
                 "p": float(2 * stats.t.sf(abs(t_stat), n - 2))}
            )
    rep = pd.DataFrame(repeat_rows).sort_values("p").reset_index(drop=True)
    rep["q"] = (rep.p * len(rep) / (rep.index + 1)).cummax().clip(upper=1)

    # promotion cells
    pr = tx[(tx.PRICE > 0) & (tx.BASE_PRICE > 0)].copy()
    pr["DISCOUNT"] = (pr.BASE_PRICE - pr.PRICE).clip(lower=0)
    pr["DEPTH"] = pr.DISCOUNT / pr.BASE_PRICE
    pr["DISC_SPEND"] = pr.DISCOUNT * pr.UNITS
    cell = (
        pr.groupby(["UPC", "STORE_NUM", "ANY_PROMO"])
        .agg(units=("UNITS", "mean"), spend=("SPEND", "mean"), n=("UNITS", "size"))
        .unstack("ANY_PROMO")
    )
    usable = (cell[("units", 0)] > 0) & (cell[("n", 0)] >= 4) & (cell[("n", 1)] >= 1)
    unit_ratio = cell[("units", 1)][usable] / cell[("units", 0)][usable]
    spend_ratio = cell[("spend", 1)][usable] / cell[("spend", 0)][usable]

    # three crediting rules for the payback ratio
    baseline = cell[("spend", 0)][usable]
    baseline = baseline[baseline > 0]
    promo_rows = pr[pr.ANY_PROMO == 1].set_index(["UPC", "STORE_NUM"])
    credit = pd.Series(promo_rows.index.map(baseline), index=promo_rows.index)
    matched = credit.notna()
    discount_cost = pr.loc[pr.ANY_PROMO == 1, "DISC_SPEND"].sum()
    incr_spend = (credit[matched] * (spend_ratio.median() - 1)).sum()
    ratio = incr_spend / discount_cost

    disc_per_cell = (
        pr.loc[pr.ANY_PROMO == 1]
        .groupby(["UPC", "STORE_NUM"]).DISC_SPEND.sum()
        .reindex(baseline.index).fillna(0.0)
    )
    boot = pd.DataFrame({
        "baseline": baseline,
        "n_promo": cell[("n", 1)][usable],
        "lift": spend_ratio,
        "promo_spend": cell[("spend", 1)][usable],
        "discount": disc_per_cell,
    }).dropna()
    b = boot.baseline.to_numpy()
    n_promo = boot.n_promo.to_numpy()
    lift_v = boot.lift.to_numpy()
    disc_v = boot.discount.to_numpy()
    rng = np.random.default_rng(0)
    B = 2_000
    draws = np.empty(B)
    for i in range(B):
        idx = rng.integers(0, len(boot), len(boot))
        draws[i] = ((np.median(lift_v[idx]) - 1) * (b[idx] * n_promo[idx]).sum()
                    / disc_v[idx].sum())
    lo, hi = np.percentile(draws, [2.5, 97.5])

    boot["incr_obs"] = (boot.promo_spend - boot.baseline) * boot.n_promo
    observed = boot.incr_obs.sum() / disc_v.sum()
    cell_ratio = (boot.incr_obs / boot.discount).replace([np.inf, -np.inf], np.nan).dropna()
    incr_v = boot.incr_obs.to_numpy()
    cr = cell_ratio.to_numpy()
    draws_obs = np.empty(B)
    draws_cell = np.empty(B)
    for i in range(B):
        idx = rng.integers(0, len(boot), len(boot))
        draws_obs[i] = incr_v[idx].sum() / disc_v[idx].sum()
        jdx = rng.integers(0, len(cr), len(cr))
        draws_cell[i] = np.median(cr[jdx])
    payback = {
        "cells": len(boot),
        "discount": float(disc_v.sum()),
        "median_rule": (float(ratio), float(lo), float(hi), float(np.mean(draws < 1))),
        "observed": (float(observed), float(np.percentile(draws_obs, 2.5)),
                     float(np.percentile(draws_obs, 97.5))),
        "per_cell": (float(np.median(cr)), float(np.percentile(draws_cell, 2.5)),
                     float(np.percentile(draws_cell, 97.5))),
        "cells_clear": float((cell_ratio > 1).mean()),
        "cells_negative": float((boot.incr_obs < 0).mean()),
    }

    # household reach, mechanism and cost
    hh = pr.copy()
    hh = hh[(hh.UNITS > 0) & (hh.HHS > 0)].copy()
    hh["CELL"] = hh.STORE_NUM.astype(str) + "_" + hh.UPC.astype(str)
    hh["log_UNITS"] = np.log(hh.UNITS)
    hh["log_HHS"] = np.log(hh.HHS)
    hh["log_UPH"] = np.log(hh.UNITS / hh.HHS)
    n = hh.groupby(["CELL", "ANY_PROMO"]).size().unstack("ANY_PROMO")
    paired_cells = n[0].fillna(0).ge(8) & n[1].fillna(0).ge(8)
    means = (
        hh.groupby(["CELL", "ANY_PROMO"])[["log_UNITS", "log_HHS", "log_UPH"]]
        .mean().unstack("ANY_PROMO")[paired_cells]
    )
    d_units = means[("log_UNITS", 1)] - means[("log_UNITS", 0)]
    d_hhs = means[("log_HHS", 1)] - means[("log_HHS", 0)]
    d_uph = means[("log_UPH", 1)] - means[("log_UPH", 0)]

    fe = {}
    for col, label in [("log_HHS", "reach"), ("log_UPH", "depth")]:
        beta, se, dof = twoway_fe(hh[col], hh.ANY_PROMO.to_numpy(), hh.CELL, hh.WEEK_END_DATE)
        tc = stats.t.ppf(0.975, dof)
        fe[label] = {
            "point": float(np.expm1(beta)),
            "lo": float(np.expm1(beta - tc * se)),
            "hi": float(np.expm1(beta + tc * se)),
        }

    hh["MECH"] = "none"
    hh.loc[hh.TPR_ONLY == 1, "MECH"] = "TPR only"
    hh.loc[(hh.FEATURE == 1) & (hh.DISPLAY == 0), "MECH"] = "feature only"
    hh.loc[(hh.DISPLAY == 1) & (hh.FEATURE == 0), "MECH"] = "display only"
    hh.loc[(hh.FEATURE == 1) & (hh.DISPLAY == 1), "MECH"] = "feature+display"
    hh.loc[hh.ANY_PROMO == 0, "MECH"] = "none"

    mech = {}
    for name in ["TPR only", "display only", "feature only", "feature+display"]:
        d = (hh.MECH == name).to_numpy().astype(float)
        b_h, se_h, dof_h = twoway_fe(hh.log_HHS, d, hh.CELL, hh.WEEK_END_DATE)
        b_u, se_u, dof_u = twoway_fe(hh.log_UPH, d, hh.CELL, hh.WEEK_END_DATE)
        tc = stats.t.ppf(0.975, dof_h)
        mech[name] = {
            "rows": int(d.sum()),
            "reach": float(np.expm1(b_h)),
            "reach_lo": float(np.expm1(b_h - tc * se_h)),
            "reach_hi": float(np.expm1(b_h + tc * se_h)),
            "depth": float(np.expm1(b_u)),
            "depth_lo": float(np.expm1(b_u - tc * se_u)),
            "depth_hi": float(np.expm1(b_u + tc * se_u)),
            "se_h": float(se_h),
        }
    pairwise = {}
    names = list(mech)
    for i, a in enumerate(names):
        for b_ in names[i + 1:]:
            diff = np.log1p(mech[a]["reach"]) - np.log1p(mech[b_]["reach"])
            se = np.hypot(mech[a]["se_h"], mech[b_]["se_h"])
            pairwise[f"{a} vs {b_}"] = abs(diff) > 1.96 * se

    season = hh.groupby("WEEK_END_DATE").UNITS.sum()
    season = season / season.mean()
    hh["SEAS"] = hh.WEEK_END_DATE.map(season)
    agg = (
        hh.groupby(["CELL", "ANY_PROMO"])
        .agg(hhs=("HHS", "mean"), n=("HHS", "size"), seas=("SEAS", "mean"))
        .unstack("ANY_PROMO")[paired_cells]
    )
    disc = (
        hh.loc[hh.ANY_PROMO == 1].groupby("CELL").DISC_SPEND.sum()
        .reindex(agg.index).fillna(0.0)
    )
    extra_raw = (agg[("hhs", 1)] - agg[("hhs", 0)]) * agg[("n", 1)]
    extra = (
        agg[("hhs", 1)] - agg[("hhs", 0)] * agg[("seas", 1)] / agg[("seas", 0)]
    ) * agg[("n", 1)]
    cat = hh.groupby("CELL").CATEGORY.first().reindex(agg.index)

    overall = cost_interval(extra, disc)
    costs = {}
    for name in cat.value_counts().index:
        pick = (cat == name).to_numpy()
        point, lo_, hi_ = cost_interval(extra.to_numpy()[pick], disc.to_numpy()[pick])
        costs[name] = {
            "point": float(point), "lo": float(lo_), "hi": float(hi_),
            "cells": int(pick.sum()), "households": float(extra.to_numpy()[pick].sum()),
        }
    biggest = extra.sort_values(ascending=False).head(int(0.05 * len(extra)))
    cost = {
        "cells": int(paired_cells.sum()),
        "discount": float(disc.sum()),
        "overall": (float(overall[0]), float(overall[1]), float(overall[2])),
        "raw": float(disc.sum() / extra_raw.sum()),
        "categories": costs,
        "concentration": float(biggest.sum() / extra.sum()),
        "negative_share": float((extra < 0).mean()),
        "reach_share": float(d_hhs.mean() / d_units.mean()),
    }

    # basket economics, paired within store
    sw = (
        tx.groupby(["STORE_NUM", "WEEK_END_DATE"])
        .agg(spend=("SPEND", "sum"), units=("UNITS", "sum"), baskets=("VISITS", "sum"))
        .reset_index()
    )
    sw["per_basket"] = sw.spend / sw.baskets
    sw["units_per_basket"] = sw.units / sw.baskets
    sw["ISO_YEAR"] = sw.WEEK_END_DATE.dt.isocalendar().year.astype(int)
    by_year = sw[sw.ISO_YEAR < 2012].groupby("ISO_YEAR")[["per_basket", "units_per_basket"]].median()
    basket = {"by_year": by_year}
    paired = sw[sw.ISO_YEAR.isin([2009, 2011])].groupby(
        ["STORE_NUM", "ISO_YEAR"]
    )[["per_basket", "units_per_basket"]].median().unstack("ISO_YEAR")
    for col in ["per_basket", "units_per_basket"]:
        before, after = paired[(col, 2009)], paired[(col, 2011)]
        change = (after - before).dropna()
        basket[col] = {
            "median": float(change.median()),
            "pct": float(change.median() / before.median()),
            "p_t": float(stats.ttest_rel(after, before, nan_policy="omit").pvalue),
            "p_w": float(stats.wilcoxon(after, before, nan_policy="omit").pvalue),
            "n": len(change),
        }

    return {
        "weekly": weekly,
        "trend": trend,
        "decomp": decomp,
        "repeat": {
            "n": len(rep),
            "exclude_zero": int((rep.lo > 0).sum()),
            "bh": int((rep.q < 0.05).sum()),
        },
        "promo": {
            "share_rows": float(pr.ANY_PROMO.mean()),
            "median_depth": float(pr.loc[pr.ANY_PROMO == 1, "DEPTH"].median()),
            "unit_ratio": float(unit_ratio.median()),
            "spend_ratio": float(spend_ratio.median()),
            "cells": int(usable.sum()),
            "discount": float(discount_cost),
        },
        "payback": payback,
        "hh": {
            "cells": int(paired_cells.sum()),
            "d_units": float(np.expm1(d_units.mean())),
            "d_hhs": float(np.expm1(d_hhs.mean())),
            "d_uph": float(np.expm1(d_uph.mean())),
            "fe": fe,
            "mech": mech,
            "pairwise_all_separate": all(pairwise.values()),
            "r2": float(np.corrcoef(hh.log_UNITS, hh.log_HHS)[0, 1] ** 2),
            "one_unit_share": float((hh.UNITS / hh.HHS).round(6).eq(1).mean()),
        },
        "cost": cost,
        "basket": basket,
        "shape": {
            "rows": int(tx.shape[0]),
            "weeks": int(tx.WEEK_END_DATE.nunique()),
            "stores": int(tx.STORE_NUM.nunique()),
            "upcs": int(tx.UPC.nunique()),
            "categories": int(tx.CATEGORY.nunique()),
            "msas": int(stores.MSA_CODE.nunique()),
            "states": int(stores.ADDRESS_STATE_PROV_CODE.nunique()),
            "start": tx.WEEK_END_DATE.min(),
            "end": tx.WEEK_END_DATE.max(),
        },
    }


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------
def chart_trend(weekly: pd.DataFrame, trend: dict) -> str:
    fig, ax = plt.subplots(figsize=(12.4, 2.95))
    spend = weekly.SPEND / weekly.SPEND.iloc[0] * 100
    units = weekly.UNITS / weekly.UNITS.iloc[0] * 100
    spend_s = spend.rolling(13, center=True, min_periods=7).mean()
    units_s = units.rolling(13, center=True, min_periods=7).mean()
    t = np.arange(len(weekly))

    # raw weeks stay visible but faint; the 13-week average carries the eye
    ax.plot(weekly.index, units, color=BLUE, lw=0.8, alpha=0.20)
    ax.plot(weekly.index, spend, color=ACCENT, lw=0.8, alpha=0.18)
    ax.plot(weekly.index, units_s, color=BLUE, lw=2.0, label="Units · 13-week avg")
    ax.plot(weekly.index, spend_s, color=ACCENT, lw=2.2, label="Spend · 13-week avg")

    slope, intercept = np.polyfit(t, spend, 1)
    ax.plot(weekly.index, intercept + slope * t, color=ACCENT, lw=1.5,
            ls=(0, (5, 3)), label="Spend trend")

    ax.axhline(100, color=RULE, lw=1, ls=":")
    ax.set_ylabel("index · first week = 100")
    ax.set_xlim(weekly.index.min() - pd.Timedelta(days=25), weekly.index.max() + pd.Timedelta(days=25))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.xaxis.set_minor_locator(mdates.MonthLocator((1, 7)))
    ax.tick_params(axis="x", which="minor", length=0)

    ax.set_ylim(38, 150)
    finish(ax)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.10), ncols=3,
              frameon=False, fontsize=9, handlelength=1.6, columnspacing=1.4)
    return svg_of(fig, "trend")


def chart_seasonality(decomp: dict) -> str:
    fig, ax = plt.subplots(figsize=(7.0, 4.55))
    x = np.arange(1, 53)
    units = decomp["UNITS"]["profile"]
    spend = decomp["SPEND"]["profile"]

    ax.fill_between(x, units, 0, color=ACCENT, alpha=0.07, linewidth=0)
    ax.plot(x, spend, color=AMBER, lw=1.35, alpha=0.9, label="Spend")
    ax.plot(x, units, color=ACCENT, lw=2.0, label="Units")
    ax.axhline(0, color=MUTED, lw=0.9)

    peak = int(np.argmax(units)) + 1
    trough = int(np.argmin(units)) + 1
    ax.scatter([peak, trough], [units[peak - 1], units[trough - 1]],
               color=ACCENT, s=22, zorder=4)
    ax.annotate(f"peak +{units[peak - 1]:.0f}%", (peak, units[peak - 1]),
                xytext=(8, 8), textcoords="offset points", fontsize=9,
                color=ACCENT, fontweight="bold")
    trough_label = f"trough {units[trough - 1]:.0f}%".replace("-", "−")
    ax.annotate(trough_label, (trough, units[trough - 1]),
                xytext=(8, -13), textcoords="offset points", fontsize=9,
                color=ACCENT, fontweight="bold")

    ax.set_xticks([1, 13, 26, 39, 52], ["Jan", "Apr", "Jul", "Oct", "Dec"])
    ax.set_xlim(1, 52)
    ax.set_ylim(-34, 47)
    ax.set_xlabel("week of year")
    ax.set_ylabel("% vs fitted trend")
    finish(ax)
    ax.legend(loc="upper left", frameon=False, ncols=2, fontsize=9,
              handlelength=1.5, columnspacing=1.4)
    return svg_of(fig, "seasonality")


def chart_reach(fe: dict) -> str:
    fig, ax = plt.subplots(figsize=(6.3, 3.7))
    labels = ["Reach\nbuying households", "Depth\nunits per household"]
    values = [fe["reach"]["point"] * 100, fe["depth"]["point"] * 100]
    lows = [fe["reach"]["lo"] * 100, fe["depth"]["lo"] * 100]
    highs = [fe["reach"]["hi"] * 100, fe["depth"]["hi"] * 100]
    colors = [ACCENT, "#a9afa8"]
    y = [1, 0]

    ax.barh(y, values, height=0.42, color=colors, zorder=2)
    for yi, v, lo, hi in zip(y, values, lows, highs):
        ax.errorbar(v, yi, xerr=[[v - lo], [hi - v]], fmt="none",
                    ecolor=INK, elinewidth=1, capsize=3, capthick=1, zorder=3)
        ax.text(hi + 2.5, yi, f"+{v:.1f}%", va="center", fontsize=14,
                fontweight="bold", color=INK)
        ax.text(hi + 2.5, yi - 0.22, f"[{lo:.1f}, {hi:.1f}]",
                va="center", fontsize=8.4, color=FAINT)
    ax.set_yticks(y, labels)
    ax.set_xlim(0, 95)
    ax.set_xlabel("lift vs own non-promo weeks (%) · cell + week fixed effects")
    finish(ax, grid_axis="x")
    ax.tick_params(axis="y", labelsize=10)
    return svg_of(fig, "reach")


def chart_cost(cost: dict) -> str:
    fig, ax = plt.subplots(figsize=(7.4, 3.9))
    order = ["BAG SNACKS", "COLD CEREAL", "ORAL HYGIENE PRODUCTS", "FROZEN PIZZA"]
    pretty = {
        "BAG SNACKS": "Bag snacks",
        "COLD CEREAL": "Cold cereal",
        "ORAL HYGIENE PRODUCTS": "Oral hygiene",
        "FROZEN PIZZA": "Frozen pizza",
    }
    rows = [(pretty[k], cost["categories"][k]) for k in order]
    y = np.arange(len(rows))[::-1]

    for yi, (label, d) in zip(y, rows):
        ax.errorbar(d["point"], yi, xerr=[[d["point"] - d["lo"]], [d["hi"] - d["point"]]],
                    fmt="o", color=ACCENT, ecolor=ACCENT,
                    capsize=3, ms=7, lw=1.2, zorder=3)
        ax.text(d["hi"] + 0.07, yi, f"${d['point']:.2f}", va="center",
                fontsize=11.5, fontweight="bold", color=ACCENT)
    ax.set_yticks(y, [label for label, _ in rows])
    ax.set_xlim(0, 3.45)
    ax.set_xlabel("discount per extra household-week ($)")
    finish(ax, grid_axis="x")
    ax.tick_params(axis="y", labelsize=10)
    return svg_of(fig, "cost")


def chart_mechanism(mech: dict) -> str:
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    order = ["feature+display", "feature only", "display only", "TPR only"]
    pretty = {
        "feature+display": "Feature + display",
        "feature only": "Feature only",
        "display only": "Display only",
        "TPR only": "Price cut only",
    }
    vals = [mech[k]["reach"] * 100 for k in order]
    los = [mech[k]["reach_lo"] * 100 for k in order]
    his = [mech[k]["reach_hi"] * 100 for k in order]
    y = np.arange(len(order))[::-1]

    color = [ACCENT, ACCENT, ACCENT, "#a9afa8"]
    ax.barh(y, vals, height=0.46, color=color, zorder=2)
    for yi, v, lo, hi in zip(y, vals, los, his):
        ax.errorbar(v, yi, xerr=[[v - lo], [hi - v]], fmt="none",
                    ecolor=INK, elinewidth=1, capsize=3, capthick=1, zorder=3)
        ax.text(hi + 5, yi, f"+{v:.0f}%", va="center", fontsize=13,
                fontweight="bold", color=INK)
        ax.text(hi + 5, yi - 0.23, f"[{lo:.0f}, {hi:.0f}]",
                va="center", fontsize=8.2, color=FAINT)
    ax.set_yticks(y, [pretty[k] for k in order])
    ax.set_xlim(0, 275)
    ax.set_xlabel("reach lift vs own non-promo weeks (%) · cell + week fixed effects")
    finish(ax, grid_axis="x")
    ax.tick_params(axis="y", labelsize=10)
    return svg_of(fig, "mechanism")


def chart_unit_spend(promo: dict) -> str:
    fig, ax = plt.subplots(figsize=(7.0, 3.6))
    labels = ["Units", "Spend"]
    vals = [promo["unit_ratio"], promo["spend_ratio"]]
    bars = ax.bar(labels, vals, width=0.42, color=[ACCENT, FAINT], zorder=2)
    ax.axhline(1, color=INK, lw=1, ls=(0, (3, 3)))
    ax.text(1.45, 1.03, "no change", fontsize=8.2, color=MUTED, ha="right", va="bottom")
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.025, f"{v:.2f}×",
                ha="center", va="bottom", fontsize=15, fontweight="bold", color=INK)
    ax.set_ylim(0, 2.0)
    ax.set_ylabel("promo week vs own non-promo weeks (×)")
    ax.set_xlabel("median across paired product–store cells")
    finish(ax, grid_axis="y")
    ax.tick_params(axis="x", labelsize=10)
    return svg_of(fig, "unit_spend")


def chart_payback(payback: dict) -> str:
    fig, ax = plt.subplots(figsize=(11.2, 1.85))
    rows = [
        ("Observed promo spend", payback["observed"]),
        ("Per-cell median", payback["per_cell"]),
        ("Median lift × mean baseline", payback["median_rule"][:3]),
    ]
    y = np.arange(len(rows))[::-1]
    for yi, (label, d) in zip(y, rows):
        point, lo, hi = d
        color = ACCENT if point >= 1 else CLAY
        ax.errorbar(point, yi, xerr=[[point - lo], [hi - point]], fmt="o",
                    color=color, ecolor=color, capsize=3, ms=7, lw=1.1, zorder=3)
        ax.text(hi + 0.02, yi, f"{point:.2f}×", va="center", fontsize=11,
                fontweight="bold", color=color)
    ax.axvline(1, color=INK, lw=1, ls=(0, (3, 3)))
    ax.text(1.005, 2.42, "break-even", fontsize=8.2, color=MUTED)
    ax.set_yticks(y, [label for label, _ in rows])
    ax.set_xlim(0.74, 1.47)
    ax.set_ylim(-0.55, 2.65)
    ax.set_xlabel("incremental revenue per $1 of discount · not profit (no cost field)")
    finish(ax, grid_axis="x")
    ax.tick_params(axis="y", labelsize=9.2)
    return svg_of(fig, "payback")


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------
def pct(x: float, digits: int = 1, sign: bool = False) -> str:
    text = f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"
    return text.replace("-", "−")


def money(x: float) -> str:
    return f"${x:,.2f}"


def pval(x: float, digits: int = 3) -> str:
    if x < 0.001:
        return f"{x:.1e}"
    return f"{x:.{digits}f}"


def build() -> None:
    tx, stores = load_panel()
    a = analyse(tx, stores)

    # Two claims in the template are stated as prose counts rather than placeholders.
    # Fail the build rather than ship them if the panel ever stops supporting them.
    assert a["hh"]["pairwise_all_separate"], "pairwise mechanism differences no longer separate"
    cat_order = ["BAG SNACKS", "COLD CEREAL", "ORAL HYGIENE PRODUCTS", "FROZEN PIZZA"]
    cat_ints = [a["cost"]["categories"][k] for k in cat_order]
    assert all(cat_ints[i]["hi"] < cat_ints[i + 1]["lo"] for i in range(len(cat_order) - 1)), \
        "category cost intervals now overlap"

    figs = {
        "FIG_TREND": chart_trend(a["weekly"], a["trend"]),
        "FIG_SEASON": chart_seasonality(a["decomp"]),
        "FIG_REACH": chart_reach(a["hh"]["fe"]),
        "FIG_COST": chart_cost(a["cost"]),
        "FIG_MECH": chart_mechanism(a["hh"]["mech"]),
        "FIG_UNIT_SPEND": chart_unit_spend(a["promo"]),
        "FIG_PAYBACK": chart_payback(a["payback"]),
    }

    shape = a["shape"]
    trend = a["trend"]
    dec = a["decomp"]
    promo = a["promo"]
    hh = a["hh"]
    cost = a["cost"]
    basket = a["basket"]
    pb = a["payback"]

    values = {
        "N_ROWS": f"{shape['rows']:,}",
        "N_WEEKS": str(shape["weeks"]),
        "N_STORES": str(shape["stores"]),
        "N_UPCS": str(shape["upcs"]),
        "N_CATEGORIES": str(shape["categories"]),
        "N_MSAS": str(shape["msas"]),
        "N_STATES": str(shape["states"]),
        "PROMO_SHARE": pct(promo["share_rows"], 2),
        "PROMO_DEPTH": pct(promo["median_depth"], 2),
        "PROMO_UNIT_RATIO": f"{promo['unit_ratio']:.2f}",
        "PROMO_SPEND_RATIO": f"{promo['spend_ratio']:.2f}",
        "PROMO_CELLS": f"{promo['cells']:,}",
        "SPEND_GROWTH": pct(trend["SPEND"]["per_year"], 2, True),
        "SPEND_GROWTH_PLAIN": pct(trend["SPEND"]["per_year"], 2),
        "UNITS_GROWTH": pct(trend["UNITS"]["per_year"], 2, True),
        "SPEND_P_HAC": pval(trend["SPEND"]["p_hac"], 3),
        "SPEND_P_ADJ": pval(trend["SPEND"]["p_adj"], 3),
        "SPEND_P_MK": pval(trend["SPEND"]["p_mk"], 3),
        "UNITS_P_HAC": pval(trend["UNITS"]["p_hac"], 2),
        "UNITS_P_ADJ": pval(trend["UNITS"]["p_adj"], 2),
        "UNITS_P_MK": pval(trend["UNITS"]["p_mk"], 2),
        "SEASON_UNITS": pct(dec["UNITS"]["seasonal_share"] / 100, 1),
        "SEASON_SPEND": pct(dec["SPEND"]["seasonal_share"] / 100, 1),
        "TREND_UNITS": pct(dec["UNITS"]["trend_share"] / 100, 1),
        "TREND_SPEND": pct(dec["SPEND"]["trend_share"] / 100, 1),
        "SWING_UNITS": pct(dec["UNITS"]["swing"] / 100, 1),
        "SWING_SPEND": pct(dec["SPEND"]["swing"] / 100, 1),
        "SEASON_P_UNITS": f"{dec['UNITS']['p_perm']:.3f}",
        "SEASON_P_SPEND": f"{dec['SPEND']['p_perm']:.3f}",
        "REPEAT_EXCL": str(a["repeat"]["exclude_zero"]),
        "REPEAT_BH": str(a["repeat"]["bh"]),
        "REPEAT_N": str(a["repeat"]["n"]),
        "REACH": pct(hh["fe"]["reach"]["point"], 1, True),
        "REACH_PLAIN": pct(hh["fe"]["reach"]["point"], 1),
        "REACH_LO": pct(hh["fe"]["reach"]["lo"], 1),
        "REACH_HI": pct(hh["fe"]["reach"]["hi"], 1),
        "DEPTH": pct(hh["fe"]["depth"]["point"], 1, True),
        "DEPTH_PLAIN": pct(hh["fe"]["depth"]["point"], 1),
        "DEPTH_LO": pct(hh["fe"]["depth"]["lo"], 1),
        "DEPTH_HI": pct(hh["fe"]["depth"]["hi"], 1),
        "REACH_SHARE": pct(cost["reach_share"], 1),
        "REACH_PAIRED": pct(hh["d_hhs"], 1, True),
        "ONE_UNIT_SHARE": pct(hh["one_unit_share"], 1),
        "HH_R2": pct(hh["r2"], 1),
        "HH_CELLS": f"{hh['cells']:,}",
        "COST": money(cost["overall"][0]),
        "COST_LO": money(cost["overall"][1]),
        "COST_HI": money(cost["overall"][2]),
        "COST_RAW": money(cost["raw"]),
        "COST_DISCOUNT": f"${cost['discount'] / 1e6:,.2f}M",
        "COST_CONC": pct(cost["concentration"], 1),
        "COST_NEG": pct(cost["negative_share"], 1),
        "COST_BAG": money(cost["categories"]["BAG SNACKS"]["point"]),
        "COST_CEREAL": money(cost["categories"]["COLD CEREAL"]["point"]),
        "COST_ORAL": money(cost["categories"]["ORAL HYGIENE PRODUCTS"]["point"]),
        "COST_PIZZA": money(cost["categories"]["FROZEN PIZZA"]["point"]),
        "COST_RATIO": f"{cost['categories']['FROZEN PIZZA']['point'] / cost['categories']['BAG SNACKS']['point']:.1f}×",
        "COST_BAG_LO": money(cost["categories"]["BAG SNACKS"]["lo"]),
        "COST_BAG_HI": money(cost["categories"]["BAG SNACKS"]["hi"]),
        "COST_CEREAL_LO": money(cost["categories"]["COLD CEREAL"]["lo"]),
        "COST_CEREAL_HI": money(cost["categories"]["COLD CEREAL"]["hi"]),
        "COST_ORAL_LO": money(cost["categories"]["ORAL HYGIENE PRODUCTS"]["lo"]),
        "COST_ORAL_HI": money(cost["categories"]["ORAL HYGIENE PRODUCTS"]["hi"]),
        "COST_PIZZA_LO": money(cost["categories"]["FROZEN PIZZA"]["lo"]),
        "COST_PIZZA_HI": money(cost["categories"]["FROZEN PIZZA"]["hi"]),
        "MECH_FD": pct(hh["mech"]["feature+display"]["reach"], 0, True),
        "MECH_FD_PLAIN": pct(hh["mech"]["feature+display"]["reach"], 0),
        "MECH_FD_LO": pct(hh["mech"]["feature+display"]["reach_lo"], 0),
        "MECH_FD_HI": pct(hh["mech"]["feature+display"]["reach_hi"], 0),
        "MECH_F": pct(hh["mech"]["feature only"]["reach"], 0, True),
        "MECH_F_PLAIN": pct(hh["mech"]["feature only"]["reach"], 0),
        "MECH_D": pct(hh["mech"]["display only"]["reach"], 0, True),
        "MECH_D_PLAIN": pct(hh["mech"]["display only"]["reach"], 0),
        "MECH_TPR": pct(hh["mech"]["TPR only"]["reach"], 0, True),
        "MECH_TPR_PLAIN": pct(hh["mech"]["TPR only"]["reach"], 0),
        "MECH_TPR_LO": pct(hh["mech"]["TPR only"]["reach_lo"], 0),
        "MECH_TPR_HI": pct(hh["mech"]["TPR only"]["reach_hi"], 0),
        "MECH_TPR_ROWS": f"{hh['mech']['TPR only']['rows']:,}",
        "MECH_WORST_RATIO": f"{hh['mech']['feature+display']['reach_lo'] / hh['mech']['TPR only']['reach_hi']:.0f}×",
        "MECH_DEPTH_MIN": pct(min(v["depth"] for v in hh["mech"].values()), 1, True),
        "MECH_DEPTH_MAX": pct(max(v["depth"] for v in hh["mech"].values()), 1, True),
        "PB_MEDIAN": f"{pb['median_rule'][0]:.2f}",
        "PB_MEDIAN_LO": f"{pb['median_rule'][1]:.2f}",
        "PB_MEDIAN_HI": f"{pb['median_rule'][2]:.2f}",
        "PB_CELL": f"{pb['per_cell'][0]:.2f}",
        "PB_CELL_LO": f"{pb['per_cell'][1]:.2f}",
        "PB_CELL_HI": f"{pb['per_cell'][2]:.2f}",
        "PB_OBS": f"{pb['observed'][0]:.2f}",
        "PB_OBS_LO": f"{pb['observed'][1]:.2f}",
        "PB_OBS_HI": f"{pb['observed'][2]:.2f}",
        "PB_CELLS": f"{pb['cells']:,}",
        "PB_DISCOUNT": f"${pb['discount'] / 1e6:,.2f}M",
        "PB_CLEAR": pct(pb["cells_clear"], 1),
        "PB_NEG": pct(pb["cells_negative"], 1),
        "BASKET_SPEND": f"${basket['per_basket']['median']:.2f}",
        "BASKET_SPEND_PCT": pct(basket["per_basket"]["pct"], 1, True),
        "BASKET_SPEND_P": pval(basket["per_basket"]["p_t"], 0).replace("e-38", "×10⁻³⁸"),
        "BASKET_UNITS_PCT": pct(basket["units_per_basket"]["pct"], 1, True),
        "BASKET_UNITS_P": pval(basket["units_per_basket"]["p_t"], 0).replace("e-09", "×10⁻⁹"),
        "DATE_START": f"{shape['start']:%b %Y}",
        "DATE_END": f"{shape['end']:%b %Y}",
    }

    template = TEMPLATE.read_text()
    html = template
    for key, val in {**figs, **values}.items():
        html = html.replace("{{" + key + "}}", val)
    assert "{{" not in html, "unreplaced placeholder remains: " + html[html.index("{{"):html.index("{{") + 40]
    OUT.write_text(html)

    # audit trail
    print("\nDeck numbers (recomputed from the panel)")
    print(f"  panel              {shape['rows']:,} rows · {shape['weeks']} weeks · "
          f"{shape['stores']} stores · {shape['upcs']} UPCs · "
          f"{shape['msas']} MSAs / {shape['states']} states")
    print(f"  trend SPEND        {trend['SPEND']['per_year']:+.2%}/yr  "
          f"p HAC {trend['SPEND']['p_hac']:.3f} · adj {trend['SPEND']['p_adj']:.3f} · "
          f"MK {trend['SPEND']['p_mk']:.3f}")
    print(f"  trend UNITS        {trend['UNITS']['per_year']:+.2%}/yr  "
          f"p HAC {trend['UNITS']['p_hac']:.2f} · adj {trend['UNITS']['p_adj']:.2f} · "
          f"MK {trend['UNITS']['p_mk']:.2f}")
    print(f"  seasonality        units {dec['UNITS']['seasonal_share']:.1f}% vs trend "
          f"{dec['UNITS']['trend_share']:.1f}% · swing {dec['UNITS']['swing']:.1f}% · "
          f"perm p {dec['UNITS']['p_perm']:.4f}")
    print(f"  promotion          {promo['share_rows']:.2%} rows · depth "
          f"{promo['median_depth']:.1%} · unit ratio {promo['unit_ratio']:.2f}x")
    print(f"  reach / depth      {hh['fe']['reach']['point']:+.1%} "
          f"[{hh['fe']['reach']['lo']:+.1%}, {hh['fe']['reach']['hi']:+.1%}] / "
          f"{hh['fe']['depth']['point']:+.1%} "
          f"[{hh['fe']['depth']['lo']:+.1%}, {hh['fe']['depth']['hi']:+.1%}]")
    print(f"  mechanisms         pairwise differences all separate: "
          f"{hh['pairwise_all_separate']}")
    print(f"  cost per household {cost['overall'][0]:.2f} "
          f"[{cost['overall'][1]:.2f}, {cost['overall'][2]:.2f}] · "
          f"{cost['cells']:,} cells · ${cost['discount']:,.0f} discount")
    for key in ["BAG SNACKS", "COLD CEREAL", "ORAL HYGIENE PRODUCTS", "FROZEN PIZZA"]:
        c = cost["categories"][key]
        print(f"    {key:24s} ${c['point']:.2f} [${c['lo']:.2f}, ${c['hi']:.2f}]")
    print(f"  category intervals  disjoint in display order: "
          f"{all(cat_ints[i]['hi'] < cat_ints[i + 1]['lo'] for i in range(len(cat_ints) - 1))}")
    print(f"  payback            {pb['median_rule'][0]:.2f} "
          f"[{pb['median_rule'][1]:.2f}, {pb['median_rule'][2]:.2f}] · "
          f"{pb['per_cell'][0]:.2f} [{pb['per_cell'][1]:.2f}, {pb['per_cell'][2]:.2f}] · "
          f"{pb['observed'][0]:.2f} [{pb['observed'][1]:.2f}, {pb['observed'][2]:.2f}]")
    print(f"  basket             spend {basket['per_basket']['pct']:+.2%} "
          f"(p {basket['per_basket']['p_t']:.1e}) · units "
          f"{basket['units_per_basket']['pct']:+.2%} "
          f"(p {basket['units_per_basket']['p_t']:.1e})")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    build()
