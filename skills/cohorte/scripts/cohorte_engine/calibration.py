"""Calibration: learn how Cohorte's predicted potential maps to real outcomes, per subject kind.

Each time a real outcome is known (a film's audience rating, a page's conversion rate, an app's D30 retention),
record it next to the simulation that predicted it. With enough cases, a linear fit turns raw potential into a
calibrated estimate with a prediction interval, and lifts the cap on the confidence index.

Stored in <home>/calibration/<kind>.json.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .util import CohorteError, read_json, write_json

MIN_PAIRS_TO_FIT = 3
MIN_PAIRS_TO_APPLY = 5
FULL_CONFIDENCE_PAIRS = 30


def _path(home: str, kind: str) -> str:
    return os.path.join(home, "calibration", f"{kind}.json")


def load(home: str, kind: str) -> Dict[str, Any]:
    return read_json(_path(home, kind), default={"kind": kind, "pairs": [], "fit": None})


def add(home: str, kind: str, sim_name: str, predicted: float, actual: float, lo: float, hi: float,
        metric: str = "", note: str = "") -> Dict[str, Any]:
    if hi <= lo:
        raise CohorteError("--scale must be MIN MAX with MAX > MIN.")
    data = load(home, kind)
    data["pairs"] = [p for p in data["pairs"] if p["sim"] != sim_name]
    data["pairs"].append({
        "sim": sim_name, "predicted": round(predicted, 3),
        "actual": round((actual - lo) / (hi - lo) * 100, 3), "actual_raw": actual, "scale": [lo, hi],
        "metric": metric, "note": note, "at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })
    write_json(_path(home, kind), data)
    return data


def fit(home: str, kind: str) -> Dict[str, Any]:
    data = load(home, kind)
    pairs = data["pairs"]
    n = len(pairs)
    if n < MIN_PAIRS_TO_FIT:
        raise CohorteError(f"Need at least {MIN_PAIRS_TO_FIT} outcome pairs for '{kind}' (have {n}).")
    xs = [p["predicted"] for p in pairs]
    ys = [p["actual"] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    r = sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    resid_sd = math.sqrt(sum(e * e for e in resid) / max(1, n - 2))
    data["fit"] = {
        "n": n, "slope": round(slope, 4), "intercept": round(intercept, 3), "r": round(r, 3),
        "mae_raw_model": round(sum(abs(x - y) for x, y in zip(xs, ys)) / n, 2),
        "mae_calibrated": round(sum(abs(e) for e in resid) / n, 2), "resid_sd": round(resid_sd, 2),
        "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    write_json(_path(home, kind), data)
    return data


def apply(home: str, kind: str, potential: float) -> Optional[Dict[str, Any]]:
    data = load(home, kind)
    f = data.get("fit")
    if not f or f["n"] < MIN_PAIRS_TO_APPLY:
        return None
    est = max(0.0, min(100.0, f["intercept"] + f["slope"] * potential))
    half = 1.28 * f["resid_sd"]  # ~80% prediction interval
    out = {"estimate": round(est, 1), "low": round(max(0, est - half), 1), "high": round(min(100, est + half), 1),
           "n": f["n"], "r": f["r"], "mae": f["mae_calibrated"]}
    last = data["pairs"][-1]
    lo, hi = last["scale"]

    def raw(v: float) -> float:
        return round(lo + v / 100 * (hi - lo), 3)

    out["in_units"] = {"metric": last.get("metric") or "outcome", "estimate": raw(out["estimate"]),
                       "low": raw(out["low"]), "high": raw(out["high"])}
    return out


def confidence_cap(home: str, kind: str) -> Dict[str, Any]:
    """Uncalibrated predictions are capped at 60/100; only proven accuracy raises the ceiling toward 100."""
    f = load(home, kind).get("fit")
    if not f or f["n"] < MIN_PAIRS_TO_APPLY:
        return {"cap": 60, "reason": f"uncalibrated for '{kind}' (needs ≥{MIN_PAIRS_TO_APPLY} real outcomes)"}
    cap = 60 + 40 * max(0.0, f["r"]) * min(1.0, f["n"] / FULL_CONFIDENCE_PAIRS)
    return {"cap": round(cap), "reason": f"calibrated on {f['n']} real outcomes (r = {f['r']})"}


def show(home: str, kind: Optional[str] = None) -> str:
    root = os.path.join(home, "calibration")
    kinds = [kind] if kind else sorted(os.path.splitext(f)[0] for f in os.listdir(root)) if os.path.isdir(root) else []
    if not kinds:
        return "No calibration data yet. Record real outcomes with `calibrate add`."
    out = []
    for k in kinds:
        d = load(home, k)
        f = d.get("fit")
        out.append(f"## {k}: {len(d['pairs'])} outcome pair(s)")
        for p in d["pairs"]:
            out.append(f"- {p['sim']}: predicted {p['predicted']:.1f} · actual {p['actual']:.1f}/100 "
                       f"({p['actual_raw']} on {p['scale']}{', ' + p['metric'] if p['metric'] else ''})")
        if f:
            out.append(f"fit: actual ≈ {f['intercept']} + {f['slope']} × predicted · r = {f['r']} · "
                       f"MAE raw {f['mae_raw_model']} → calibrated {f['mae_calibrated']} · n = {f['n']}")
        cap = confidence_cap(home, k)
        out.append(f"confidence cap: {cap['cap']}/100 ({cap['reason']})")
    return "\n".join(out)
