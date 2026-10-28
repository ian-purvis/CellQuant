"""Nonlinear regression (curve fitting) of an XY table's data sets: dose-response, enzyme kinetics, exponentials, a
Gaussian peak and polynomials, by least squares.

Each built-in model finds its own starting values from the data, then the fit is run from those and from a few
nearby starts and the best (smallest sum of squares) is kept. Parameters can be fixed to a constant. Standard
errors and confidence intervals are asymptotic (from the curvature of the sum of squares at the best fit), as in
the usual curve-fitting programs; the confidence and prediction bands come from the same covariance (delta method).

Two fits of the same data are compared by the extra sum-of-squares F test (when one model is a special case of the
other) and by AICc (always). Data sets are compared by fitting them with one or all parameters shared and testing
that against fitting each on its own. Rows with Y but no X are unknowns: their X can be read off the curve
(interpolation from a standard curve).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize, stats

from cellquant.errors import StatisticsError
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import AnalysisResult, fmt, fmt_ci
from cellquant.stats.xy import XYTable

LN2 = math.log(2.0)


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    params: tuple[str, ...]
    equation: str
    f: Callable[[np.ndarray, np.ndarray], np.ndarray]
    guess: Callable[[np.ndarray, np.ndarray], np.ndarray]
    linear: bool = False  # linear in its parameters: one start is enough


def _by_x(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each X once (sorted) with the mean of its Ys."""

    ux = np.unique(x)
    return ux, np.array([y[x == value].mean() for value in ux])


def _rising(ux: np.ndarray, my: np.ndarray) -> bool:
    return bool(np.polyfit(ux, my, 1)[0] >= 0) if ux.size > 1 else True


def _x_at(ux: np.ndarray, my: np.ndarray, target: float) -> float:
    return float(ux[int(np.argmin(np.abs(my - target)))])


def _log_dose(x, p):
    bottom, top, log_ec50, hill = p
    return bottom + (top - bottom) / (1.0 + np.power(10.0, (log_ec50 - x) * hill))


def _log_dose_guess(x, y):
    ux, my = _by_x(x, y)
    bottom, top = float(my.min()), float(my.max())
    return np.array([bottom, top, _x_at(ux, my, (bottom + top) / 2), 1.0 if _rising(ux, my) else -1.0])


def _dose(x, p):
    bottom, top, ec50, hill = p
    with np.errstate(divide="ignore"):
        return bottom + (top - bottom) / (1.0 + np.power(ec50 / x, hill))


def _dose_guess(x, y):
    ux, my = _by_x(x, y)
    bottom, top = float(my.min()), float(my.max())
    positive = ux > 0
    ec50 = _x_at(ux[positive], my[positive], (bottom + top) / 2) if positive.any() else 1.0
    return np.array([bottom, top, ec50, 1.0 if _rising(ux, my) else -1.0])


def _michaelis(x, p):
    vmax, km = p
    return vmax * x / (km + x)


def _michaelis_guess(x, y):
    ux, my = _by_x(x, y)
    vmax = float(my.max())
    positive = ux > 0
    km = _x_at(ux[positive], my[positive], vmax / 2) if positive.any() else float(np.ptp(ux)) / 2 or 1.0
    return np.array([vmax, km])


def _one_phase(x, p):
    y0, plateau, k = p
    return plateau + (y0 - plateau) * np.exp(-k * x)


def _one_phase_guess(x, y):
    ux, my = _by_x(x, y)
    y0, plateau = float(my[0]), float(my[-1])
    span = float(np.ptp(ux)) or 1.0
    half = _x_at(ux, my, (y0 + plateau) / 2) - float(ux[0])
    return np.array([y0, plateau, LN2 / max(half, span / 10)])


def _growth(x, p):
    y0, k = p
    return y0 * np.exp(k * x)


def _growth_guess(x, y):
    ux, my = _by_x(x, y)
    positive = my > 0
    if positive.sum() >= 2 and np.ptp(ux[positive]) > 0:
        k, log_y0 = np.polyfit(ux[positive], np.log(my[positive]), 1)
        return np.array([math.exp(log_y0), k])
    return np.array([float(my[0]) or 1.0, 1.0 / (float(np.ptp(ux)) or 1.0)])


def _gaussian(x, p):
    amplitude, mean, sd = p
    return amplitude * np.exp(-0.5 * ((x - mean) / sd) ** 2)


def _gaussian_guess(x, y):
    ux, my = _by_x(x, y)
    peak = int(np.argmax(np.abs(my)))
    amplitude = float(my[peak])
    wide = ux[np.abs(my) >= abs(amplitude) / 2]
    span = float(np.ptp(ux)) or 1.0
    return np.array([amplitude, float(ux[peak]), max(float(np.ptp(wide)) / 2.3548, span / 20)])


def _polynomial(order: int):
    def f(x, p):
        return sum(coefficient * x**power for power, coefficient in enumerate(p))

    def guess(x, y):
        return np.polyfit(x, y, order)[::-1].copy()

    return f, guess


_LINE, _LINE_GUESS = _polynomial(1)
_POLY2, _POLY2_GUESS = _polynomial(2)
_POLY3, _POLY3_GUESS = _polynomial(3)

MODELS: dict[str, Model] = {model.key: model for model in (
    Model("log_dose_response", "Dose-response, X is log(dose)", ("Bottom", "Top", "LogEC50", "HillSlope"),
          "Y = Bottom + (Top − Bottom) / (1 + 10^((LogEC50 − X) × HillSlope))", _log_dose, _log_dose_guess),
    Model("dose_response", "Dose-response, X is dose", ("Bottom", "Top", "EC50", "HillSlope"),
          "Y = Bottom + (Top − Bottom) / (1 + (EC50 / X)^HillSlope)", _dose, _dose_guess),
    Model("michaelis_menten", "Michaelis-Menten (enzyme kinetics)", ("Vmax", "Km"), "Y = Vmax × X / (Km + X)", _michaelis,
          _michaelis_guess),
    Model("one_phase", "One-phase exponential (decay or rise to a plateau)", ("Y0", "Plateau", "K"),
          "Y = Plateau + (Y0 − Plateau) × exp(−K × X)", _one_phase, _one_phase_guess),
    Model("exponential_growth", "Exponential growth", ("Y0", "K"), "Y = Y0 × exp(K × X)", _growth, _growth_guess),
    Model("gaussian", "Gaussian (bell-shaped peak)", ("Amplitude", "Mean", "SD"), "Y = Amplitude × exp(−0.5 × ((X − Mean) / SD)²)",
          _gaussian, _gaussian_guess),
    Model("line", "Straight line", ("Intercept", "Slope"), "Y = Intercept + Slope × X", _LINE, _LINE_GUESS, linear=True),
    Model("polynomial_2", "Second-order polynomial (quadratic)", ("B0", "B1", "B2"), "Y = B0 + B1 × X + B2 × X²", _POLY2, _POLY2_GUESS,
          linear=True),
    Model("polynomial_3", "Third-order polynomial (cubic)", ("B0", "B1", "B2", "B3"), "Y = B0 + B1 × X + B2 × X² + B3 × X³", _POLY3,
          _POLY3_GUESS, linear=True),
)}

# (simpler, more complex) pairs where the simpler is the complex one with a parameter set to 0.
_NESTED = {("line", "polynomial_2"), ("line", "polynomial_3"), ("polynomial_2", "polynomial_3")}


def model_of(key: str) -> Model:
    try:
        return MODELS[key]
    except KeyError:
        raise StatisticsError(f"Unknown model {key!r}; choose one of: {', '.join(MODELS)}.") from None


def _clean_fixed(model: Model, fixed: dict | None) -> dict[str, float]:
    out = {}
    for name, value in (fixed or {}).items():
        if name not in model.params:
            raise StatisticsError(f"{model.name} has no parameter {name!r} (its parameters are {', '.join(model.params)}).")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise StatisticsError(f"The fixed value of {name} must be a number.") from None
        if not math.isfinite(value):
            raise StatisticsError(f"The fixed value of {name} must be a finite number.")
        out[name] = value
    if len(out) == len(model.params):
        raise StatisticsError("Every parameter is fixed, so there is nothing to fit; leave at least one free.")
    return out


def _check_x(model: Model, x: np.ndarray) -> None:
    if model.key in ("dose_response", "michaelis_menten") and (x < 0).any():
        hint = " For log doses choose “Dose-response, X is log(dose)”." if model.key == "dose_response" else ""
        raise StatisticsError(f"{model.name} needs X ≥ 0 (a concentration).{hint}")


def _least_squares(residual: Callable, starts: list[np.ndarray]) -> optimize.OptimizeResult | None:
    """The best of the fits from ``starts``. When the first start converges cleanly, only two more are tried."""

    max_nfev = 150 * (starts[0].size + 1)  # a fit that has not settled by then is running off (an ambiguous fit)
    best = None
    for index, start in enumerate(starts):
        if index == 3 and best is not None and best.status > 0 and not _covariance(best.jac)[1]:
            break
        try:
            with np.errstate(all="ignore"):
                found = optimize.least_squares(residual, start, method="trf", x_scale="jac", jac=lambda q: _jacobian(residual, q),
                                               max_nfev=max_nfev)
        except (ValueError, np.linalg.LinAlgError):
            continue
        if np.isfinite(found.cost) and (best is None or found.cost < best.cost * (1 - 1e-12)):
            best = found
    return best


def _jacobian(residual: Callable, q: np.ndarray) -> np.ndarray:
    """Central differences with a step relative to each parameter, so an EC50 of 1e-8 M is differentiated as finely as a
    Top of 100 (scipy's default step is absolute below 1)."""

    q = np.asarray(q, float)
    columns = []
    for i in range(q.size):
        h = 6e-6 * abs(q[i]) if q[i] != 0 else 6e-6
        up, down = q.copy(), q.copy()
        up[i] += h
        down[i] -= h
        columns.append((residual(up) - residual(down)) / (up[i] - down[i]))
    return np.column_stack(columns)


def _starts(start: np.ndarray, linear: bool) -> list[np.ndarray]:
    if linear:
        return [start]
    rng = np.random.default_rng(0)
    out = [start]
    for _ in range(6):
        out.append(start * (1 + 0.5 * rng.standard_normal(start.size)) + 0.05 * rng.standard_normal(start.size))
    return out


def _covariance(jac: np.ndarray) -> tuple[np.ndarray, bool]:
    """(J'J)⁻¹ and whether the fit is ambiguous (the data do not pin down every parameter: a parameter's dependency on
    the others is above 0.9999, or J'J is singular). The columns are scaled to
    unit length first, so parameters in very different units (an EC50 of 1e-9 M beside a Top of 100) are not mistaken
    for an ambiguous fit."""

    k = jac.shape[1]
    norms = np.linalg.norm(jac, axis=0)
    if k == 0 or not np.isfinite(jac).all() or (norms == 0).any():
        return np.full((k, k), np.nan), True
    _u, s, vt = np.linalg.svd(jac / norms, full_matrices=False)
    if s[-1] <= s[0] * 1e-7:
        return np.full((k, k), np.nan), True
    scaled = (vt.T / s**2) @ vt
    # Dependency of each parameter, 1 − 1/(C_ii · (C⁻¹)_ii): above 0.9999 the others can stand in for it almost entirely.
    if (1 - 1 / np.diag(scaled) > 0.9999).any():
        return np.full((k, k), np.nan), True
    return scaled / np.outer(norms, norms), False


def aicc(ss: float, n: int, k: int) -> float:
    """AICc of a least-squares fit with ``k`` fitted parameters (the variance counts as one more)."""

    k += 1
    if n - k - 1 <= 0:
        return float("nan")
    return n * math.log(max(ss, 1e-300) / n) + 2 * k + 2 * k * (k + 1) / (n - k - 1)


def fit_curve(model: str | Model, x: np.ndarray, y: np.ndarray, fixed: dict | None = None) -> dict:
    """Least-squares fit of ``model`` to (x, y), with ``fixed`` parameters held constant. Raises when it cannot be fitted."""

    model = model_of(model) if isinstance(model, str) else model
    fixed = _clean_fixed(model, fixed)
    x, y = np.asarray(x, float), np.asarray(y, float)
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    _check_x(model, x)
    free = [name for name in model.params if name not in fixed]
    n, k = x.size, len(free)
    if n <= k:
        raise StatisticsError(f"{model.name} has {k} free parameter(s), so it needs at least {k + 1} points (this data set has {n}).")
    if np.unique(x).size < min(k, 2):
        raise StatisticsError(f"{model.name} needs at least {min(k, 2)} different X values.")
    index = [model.params.index(name) for name in free]
    base = np.array([fixed.get(name, 0.0) for name in model.params], float)
    big = 1e6 * (float(np.abs(y).max()) + float(np.ptp(y)) + 1.0)

    def full(q):
        p = base.copy()
        p[index] = q
        return p

    def residual(q):
        r = model.f(x, full(q)) - y
        return np.where(np.isfinite(r), r, big)

    with np.errstate(all="ignore"):
        guess = model.guess(x, y)
    start = np.where(np.isfinite(guess), guess, 1.0)[index]
    found = _least_squares(residual, _starts(start, model.linear))
    if found is None or not np.isfinite(found.x).all():
        raise StatisticsError(f"{model.name} could not be fitted to these data (the fit did not converge).")
    if model.key == "gaussian" and "SD" in free and found.x[free.index("SD")] < 0:  # the curve is the same either way
        found.x[free.index("SD")] *= -1
        found.jac[:, free.index("SD")] *= -1
    params = full(found.x)
    with np.errstate(all="ignore"):
        fitted = model.f(x, params)
    if not np.isfinite(fitted).all():
        raise StatisticsError(f"{model.name} could not be fitted to these data (the curve is undefined at some X).")
    ss_resid = float(((y - fitted) ** 2).sum())
    ss_total = float(((y - y.mean()) ** 2).sum())
    if ss_resid <= 1e-12 * max(ss_total, float((y**2).sum()), 1e-300):
        ss_resid = 0.0
    df = n - k
    ms = ss_resid / df
    unscaled, ambiguous = _covariance(found.jac)
    cov = unscaled * ms
    se = {name: float(math.sqrt(cov[i, i])) if np.isfinite(cov[i, i]) and cov[i, i] >= 0 else float("nan") for i, name in enumerate(free)}
    return {"model": model.key, "params": dict(zip(model.params, map(float, params))), "free": free, "fixed": fixed,
            "cov": cov, "se": se, "ss_resid": ss_resid, "df": df, "n": n, "ms": ms, "sy_x": math.sqrt(ms),
            "r2": 1.0 - ss_resid / ss_total if ss_total > 0 else float("nan"), "aicc": aicc(ss_resid, n, k),
            "x_min": float(x.min()), "x_max": float(x.max()), "converged": bool(found.status > 0), "ambiguous": ambiguous}


def predict(fit: dict, xs) -> np.ndarray:
    model = MODELS[fit["model"]]
    with np.errstate(all="ignore"):
        return model.f(np.asarray(xs, float), np.array([fit["params"][name] for name in model.params]))


def _gradient(fit: dict, xs: np.ndarray) -> np.ndarray:
    """d prediction / d free parameter at each X (central differences), shaped (len(xs), free)."""

    model = MODELS[fit["model"]]
    p = np.array([fit["params"][name] for name in model.params])
    out = np.empty((xs.size, len(fit["free"])))
    for column, name in enumerate(fit["free"]):
        i = model.params.index(name)
        h = 1e-6 * abs(p[i]) if p[i] != 0 else 1e-8
        up, down = p.copy(), p.copy()
        up[i] += h
        down[i] -= h
        with np.errstate(all="ignore"):
            out[:, column] = (model.f(xs, up) - model.f(xs, down)) / (2 * h)
    return out


def curve_band(fit: dict, xs, ci: float = 0.95, prediction: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """The confidence band of the curve (or the prediction band of new points) at ``xs``."""

    xs = np.asarray(xs, float)
    g = _gradient(fit, xs)
    variance = np.einsum("ij,jk,ik->i", g, fit["cov"], g) + (fit["ms"] if prediction else 0.0)
    half = stats.t.ppf(0.5 + ci / 2, fit["df"]) * np.sqrt(np.maximum(variance, 0.0))
    center = predict(fit, xs)
    return center - half, center + half


def interpolate_x(fit: dict, y: float) -> list[float]:
    """Every X within the fitted range where the curve equals ``y`` (no extrapolation), lowest first."""

    grid = np.linspace(fit["x_min"], fit["x_max"], 2001)
    diff = predict(fit, grid) - y
    roots = []
    for i in range(grid.size - 1):
        a, b = diff[i], diff[i + 1]
        if not (np.isfinite(a) and np.isfinite(b)):
            continue
        if a == 0:
            roots.append(float(grid[i]))
        elif a * b < 0:
            roots.append(float(optimize.brentq(lambda v: float(predict(fit, [v])[0]) - y, grid[i], grid[i + 1], xtol=1e-12 * (1 + abs(grid[i])))))
    if diff[-1] == 0:
        roots.append(float(grid[-1]))
    return roots


def global_fit(model: str | Model, data: list[tuple[np.ndarray, np.ndarray]], shared: list[str], fixed: dict | None = None,
               starts: list[dict] | None = None) -> dict:
    """One fit of several data sets at once, with the ``shared`` parameters common to all and the others fitted per data
    set. ``starts`` (one parameter dict per data set, from fitting each on its own) seeds the fit."""

    model = model_of(model) if isinstance(model, str) else model
    fixed = _clean_fixed(model, fixed)
    free = [name for name in model.params if name not in fixed]
    shared = [name for name in free if name in shared]
    own = [name for name in free if name not in shared]
    count = len(shared) + len(own) * len(data)
    total = sum(x.size for x, _y in data)
    if total <= count:
        raise StatisticsError(f"Fitting the data sets together needs more than {count} points (there are {total}).")
    if starts is None:
        pooled = fit_curve(model, np.concatenate([x for x, _y in data]), np.concatenate([y for _x, y in data]), fixed)
        starts = [pooled["params"]] * len(data)
    start = [float(np.median([item[name] for item in starts])) for name in shared]
    for item in starts:
        start += [item[name] for name in own]
    big = 1e6 * (max(float(np.abs(y).max()) + float(np.ptp(y)) for _x, y in data) + 1.0)

    def unpack(q):
        out = []
        for j in range(len(data)):
            p = {**fixed, **dict(zip(shared, q[: len(shared)])), **dict(zip(own, q[len(shared) + j * len(own): len(shared) + (j + 1) * len(own)]))}
            out.append(np.array([p[name] for name in model.params], float))
        return out

    def residual(q):
        r = np.concatenate([model.f(x, p) - y for (x, y), p in zip(data, unpack(q))])
        return np.where(np.isfinite(r), r, big)

    start = np.asarray(start, float)
    found = _least_squares(residual, _starts(start, model.linear))
    if len(starts) > 1 and shared and not model.linear:
        # The median of the separate fits can sit between very different data sets; also start the shared values at each
        # data set's own, and keep the best.
        for item in starts:
            other = start.copy()
            other[: len(shared)] = [item[name] for name in shared]
            candidate = _least_squares(residual, [other])
            if candidate is not None and (found is None or candidate.cost < found.cost * (1 - 1e-12)):
                found = candidate
    if found is None:
        raise StatisticsError(f"{model.name} could not be fitted to the data sets together (the fit did not converge).")
    with np.errstate(all="ignore"):
        ss = float((residual(found.x) ** 2).sum())
    return {"params": [dict(zip(model.params, map(float, p))) for p in unpack(found.x)], "ss_resid": ss, "df": total - count,
            "n": total, "k": count, "shared": shared, "aicc": aicc(ss, total, count), "converged": bool(found.status > 0),
            "ambiguous": _covariance(found.jac)[1]}


def _f_test(ss_simple: float, df_simple: int, ss_complex: float, df_complex: int) -> tuple[float, float, int, int]:
    dfn, dfd = df_simple - df_complex, df_complex
    if dfn <= 0 or dfd <= 0 or ss_complex <= 0:
        return float("nan"), float("nan"), dfn, dfd
    f = max(ss_simple - ss_complex, 0.0) / dfn / (ss_complex / dfd)
    return f, float(stats.f.sf(f, dfn, dfd)), dfn, dfd


def _aicc_choice(simple: float, complex_: float) -> tuple[float, float]:
    """(AICc of complex − AICc of simple, probability that the simpler model is correct)."""

    delta = complex_ - simple
    if not math.isfinite(delta):
        return float("nan"), float("nan")
    return delta, 1.0 / (1.0 + math.exp(-0.5 * delta)) if delta > -1400 else 0.0


def nested(simple: tuple[str, dict], complex_: tuple[str, dict]) -> bool:
    """Whether model ``simple`` (key, fixed) is ``complex_`` with some parameters set to constants."""

    (a, fixed_a), (b, fixed_b) = simple, complex_
    if a == b:
        return len(fixed_a) > len(fixed_b) and all(name in fixed_a and fixed_a[name] == value for name, value in fixed_b.items())
    return (a, b) in _NESTED and not fixed_a and not fixed_b


def compare_models(fit_a: dict, fit_b: dict, alpha: float = 0.05) -> dict:
    """Compare two fits of the same points: extra sum-of-squares F test (when nested) and AICc."""

    first, second = (fit_a, fit_b) if len(fit_a["free"]) <= len(fit_b["free"]) else (fit_b, fit_a)
    out = {"simple": first, "complex": second, "nested": nested((first["model"], first["fixed"]), (second["model"], second["fixed"]))}
    if out["nested"]:
        f, p, dfn, dfd = _f_test(first["ss_resid"], first["df"], second["ss_resid"], second["df"])
        out.update(f=f, p=p, dfn=dfn, dfd=dfd, f_prefers=None if not math.isfinite(p) else "complex" if p < alpha else "simple")
    delta, probability = _aicc_choice(first["aicc"], second["aicc"])
    out.update(aicc_delta=delta, aicc_simple=probability,
               aicc_prefers=None if not math.isfinite(delta) else "simple" if delta >= 0 else "complex")
    return out


def compare_data_sets(model: str | Model, data: dict[str, tuple[np.ndarray, np.ndarray]], fits: dict[str, dict], parameter: str | None,
                      fixed: dict | None = None, alpha: float = 0.05) -> dict:
    """Do the data sets differ? Null: ``parameter`` (or, when None, every parameter: one curve for all) is shared;
    alternative: each data set has its own fit."""

    model = model_of(model) if isinstance(model, str) else model
    names = list(fits)
    free = fits[names[0]]["free"]
    if parameter is not None and parameter not in free:
        raise StatisticsError(f"{parameter} is not a free parameter of the fit, so it cannot be compared.")
    shared = list(free) if parameter is None else [parameter]
    null = global_fit(model, [data[name] for name in names], shared, fixed, [fits[name]["params"] for name in names])
    if not null["converged"]:
        raise StatisticsError("The fit with " + (f"{parameter} shared" if parameter else "one curve for every data set")
                              + " did not converge, so the data sets are not compared.")
    # An ambiguous shared fit is still compared: when the data sets differ strongly, forcing them to share a value leaves the
    # other parameters poorly determined, but the F test and AICc use only its sum of squares, which has settled.
    ss_separate = sum(fits[name]["ss_resid"] for name in names)
    df_separate = sum(fits[name]["df"] for name in names)
    k_separate = sum(len(fits[name]["free"]) for name in names)
    f, p, dfn, dfd = _f_test(null["ss_resid"], null["df"], ss_separate, df_separate)
    delta, probability = _aicc_choice(null["aicc"], aicc(ss_separate, null["n"], k_separate))
    return {"parameter": parameter, "shared": null, "data_sets": names, "f": f, "p": p, "dfn": dfn, "dfd": dfd,
            "f_prefers": None if not math.isfinite(p) else "separate" if p < alpha else "shared",
            "aicc_delta": delta, "aicc_shared": probability,
            "aicc_prefers": None if not math.isfinite(delta) else "shared" if delta >= 0 else "separate"}


# --- The results sheet ----------------------------------------------------------------------------------------


def _derived(fit: dict, ci_of: Callable[[str], tuple[float, float]]) -> list[tuple[str, float, tuple[float, float] | None]]:
    """(name, value, CI or None) of values computed from the parameters: EC50, half-life, doubling time, span."""

    p, key = fit["params"], fit["model"]
    out = []
    if key == "log_dose_response":
        low, high = ci_of("LogEC50")
        out.append(("EC50", 10 ** p["LogEC50"], (10**low, 10**high) if math.isfinite(low) and abs(high) < 300 else None))
    if key in ("log_dose_response", "dose_response"):
        out.append(("Span", p["Top"] - p["Bottom"], None))
    if key == "one_phase":
        out.append(("Span", p["Y0"] - p["Plateau"], None))
    if key in ("one_phase", "exponential_growth"):
        low, high = ci_of("K")
        reach = (LN2 / high, LN2 / low) if math.isfinite(low) and low > 0 else None
        out.append(("Half-life" if key == "one_phase" else "Doubling time", LN2 / p["K"] if p["K"] > 0 else float("nan"),
                    tuple(sorted(reach)) if reach else None))
    if key == "gaussian":
        out.append(("Width at half height", 2 * math.sqrt(2 * LN2) * abs(p["SD"]), None))
    return out


def _fit_rows(fit: dict, model: Model, level: str, ci: float) -> dict:
    t = stats.t.ppf(0.5 + ci / 2, fit["df"])

    def ci_of(name):
        se = fit["se"].get(name, float("nan"))
        value = fit["params"][name]
        return (value - t * se, value + t * se) if math.isfinite(se) else (float("nan"), float("nan"))

    fit["ci"] = {name: ci_of(name) for name in fit["free"]}
    rows = {}
    for name in model.params:
        rows[("Best-fit values", name)] = f"{fmt(fit['params'][name])} (fixed)" if name in fit["fixed"] else fmt(fit["params"][name])
    derived = _derived(fit, ci_of)
    fit["derived"] = {name: value for name, value, _ci in derived}
    for name, value, _ci in derived:
        rows[("Best-fit values", name)] = fmt(value)
    for name in fit["free"]:
        rows[("Std. Error", name)] = fmt(fit["se"][name])
    for name in fit["free"]:
        rows[(level, name)] = fmt_ci(*fit["ci"][name])
    for name, _value, interval in derived:
        if interval is not None:
            rows[(level, name)] = fmt_ci(*interval)
    rows[("Goodness of fit", "Degrees of freedom")] = str(fit["df"])
    rows[("Goodness of fit", "R squared")] = fmt(fit["r2"])
    rows[("Goodness of fit", "Sum of squares")] = fmt(fit["ss_resid"])
    rows[("Goodness of fit", "Sy.x")] = fmt(fit["sy_x"])
    rows[("Goodness of fit", "AICc")] = fmt(fit["aicc"])
    rows[("Data", "Points analyzed")] = str(fit["n"])
    rows[("Data", "Converged")] = "Yes" if fit["converged"] else "No"
    rows[("Data", "Ambiguous")] = "Yes" if fit["ambiguous"] else "No"
    return rows


def _unknowns(table: XYTable, j: int) -> list[tuple[int, float]]:
    """(row, mean Y) of each row of data set ``j`` with Y but no X."""

    out = []
    for i in np.flatnonzero(~np.isfinite(table.x)):
        values = table.values[i, j]
        values = values[np.isfinite(values)]
        if values.size:
            out.append((int(i), float(values.mean())))
    return out


_CACHE: dict[tuple, AnalysisResult] = {}


def _cache_key(table: XYTable, options: dict) -> tuple:
    return (table.title, table.x_title, tuple(table.sets), table.x.tobytes(), table.values.shape, table.values.tobytes(), repr(sorted(
        (key, sorted(value.items()) if isinstance(value, dict) else value) for key, value in options.items())))


def nonlinear_regression(table: XYTable, **options) -> AnalysisResult:
    """Fit a model to each data set of an XY table (see ``_nonlinear_regression`` for the options). The same table and
    options give the same result object, so checking options in a dialog and then showing them fits only once."""

    key = _cache_key(table, options)
    if key not in _CACHE:
        if len(_CACHE) >= 32:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = _nonlinear_regression(table, **options)
    return _CACHE[key]


def _nonlinear_regression(table: XYTable, *, model: str = "log_dose_response", fixed: dict | None = None, replicates: str = "each",
                         ci: float = 0.95, alpha: float = 0.05, style: str = "classic", compare: str = "none",
                         compare_model: str | None = None, compare_fixed: dict | None = None, compare_parameter: str | None = None,
                         interpolate: bool = False) -> AnalysisResult:
    """Fit ``model`` to each data set of an XY table.

    ``compare``: "none"; "model" (each data set fitted by ``compare_model``/``compare_fixed`` too, and the two fits
    compared); or "datasets" (do the data sets differ in ``compare_parameter``, or, when it is None, at all?).
    ``interpolate``: read X off each curve for the rows that have Y but no X."""

    fitted_model = model_of(model)
    if not 0 < ci < 1:
        raise StatisticsError("The confidence level must be between 0 and 1 (for example 0.95).")
    if not 0 < alpha < 1:
        raise StatisticsError("alpha must be between 0 and 1 (for example 0.05).")
    if replicates not in ("each", "mean"):
        raise StatisticsError("replicates must be 'each' (every replicate a point) or 'mean' (each row's mean).")
    if compare not in ("none", "model", "datasets"):
        raise StatisticsError("compare must be 'none', 'model' or 'datasets'.")
    fixed = _clean_fixed(fitted_model, fixed)
    other = None
    if compare == "model":
        other = model_of(compare_model or "")
        compare_fixed = _clean_fixed(other, compare_fixed)
        if other.key == fitted_model.key and compare_fixed == fixed:
            raise StatisticsError("The second fit is the same as the first; choose another model or fix a parameter.")
    result = AnalysisResult("Nonlinear regression", table.title,
                            {"model": fitted_model.key, "fixed": fixed, "replicates": replicates, "ci": ci, "alpha": alpha, "style": style,
                             "compare": compare, "compare_model": other.key if other else None,
                             "compare_fixed": compare_fixed if other else None, "compare_parameter": compare_parameter,
                             "interpolate": interpolate})
    level = f"{100 * ci:g}% CI (asymptotic)"
    fits, data, out, comparisons, interpolated = {}, {}, {}, {}, {}
    for j, name in enumerate(table.sets):
        x, y = table.points(j, means=replicates == "mean")
        try:
            fit = fit_curve(fitted_model, x, y, fixed)
        except StatisticsError as exc:
            result.warnings.append(f"{name}: {exc}")
            continue
        fits[name], data[name] = fit, (x, y)
        fit["equation"] = fitted_model.equation
        rows = _fit_rows(fit, fitted_model, level, ci)
        if not fit["converged"]:
            result.warnings.append(f"{name}: the fit stopped before converging; treat its values with care.")
        if fit["ambiguous"]:
            result.warnings.append(f"{name}: the fit is ambiguous: the data do not pin down every parameter (no SE or CI). Fix a "
                                   "parameter (for example Bottom or Top) or add X values that cover the whole curve.")
        if other is not None:
            try:
                second = fit_curve(other, x, y, compare_fixed)
            except StatisticsError as exc:
                result.warnings.append(f"{name}, second model: {exc}")
                second = None
            else:
                second["equation"] = other.equation
                unsure = [label for label, item in (("the first fit", fit), ("the second fit", second))
                          if item["ambiguous"] or not item["converged"]]
                if unsure:
                    result.warnings.append(f"{name}: the two fits are not compared because {' and '.join(unsure)} "
                                           f"{'is' if len(unsure) == 1 else 'are'} ambiguous or did not converge.")
                    second = None
            if second is not None:
                item = compare_models(fit, second, alpha)
                comparisons[name] = item
                label = _fit_label(other, compare_fixed)
                for parameter in other.params:
                    value = second["params"][parameter]
                    rows[(f"Second fit ({label})", parameter)] = f"{fmt(value)} (fixed)" if parameter in compare_fixed else fmt(value)
                rows[(f"Second fit ({label})", "Sum of squares")] = fmt(second["ss_resid"])
                rows[(f"Second fit ({label})", "Degrees of freedom")] = str(second["df"])
                rows.update(_model_rows(item, fitted_model, fixed, other, compare_fixed, style))
        if interpolate:
            found = []
            for row, value in _unknowns(table, j):
                roots = interpolate_x(fit, value)
                found.append({"row": row, "y": value, "x": roots[0] if roots else float("nan"), "all": roots})
                text = fmt(roots[0]) if roots else "out of range"
                if roots and fitted_model.key == "log_dose_response":
                    text += f" (dose {fmt(10 ** roots[0])})"
                if len(roots) > 1:
                    text += f"; also {', '.join(fmt(item) for item in roots[1:])}"
                if roots and (fit["ambiguous"] or not fit["converged"]):
                    text += " (unreliable: the fit is ambiguous or did not converge)"
                rows[("Interpolated X (rows with Y but no X)", f"Row {row + 1}: Y = {fmt(value)}")] = text
            interpolated[name] = found
        out[name] = rows
        section = result.section(name)
        for (part, label), text in rows.items():
            section.add(f"{part}: {label}", text, text)
    if not fits:
        raise StatisticsError(f"No data set could be fitted with {fitted_model.name}: " + "; ".join(result.warnings))
    frame = pd.DataFrame(out)
    frame.index.names = ["section", "statistic"]
    datasets = None
    if compare == "datasets":
        unsure = [name for name, fit in fits.items() if fit["ambiguous"] or not fit["converged"]]
        if len(fits) < 2:
            result.warnings.append("Comparing data sets needs at least two fitted data sets.")
        elif unsure:
            result.warnings.append(f"The data sets are not compared: the fit of {', '.join(unsure)} is ambiguous or did not converge, "
                                   "so a shared fit would not mean anything. Fix a parameter or choose another model.")
        else:
            try:
                datasets = compare_data_sets(fitted_model, data, fits, compare_parameter or None, fixed, alpha)
            except StatisticsError as exc:
                result.warnings.append(str(exc))
        if datasets is not None and datasets["shared"]["ambiguous"]:
            result.warnings.append("The fit with " + (f"{compare_parameter} shared" if compare_parameter else "one curve for every data set")
                                   + " is ambiguous: its parameters are poorly determined, as happens when the data sets differ "
                                   "strongly. The comparison uses only how well it fits (its sum of squares), so it still holds.")
        if len(fits) >= 2:
            skipped = [name for name in table.sets if name not in fits]
            if datasets is not None:
                lines = _dataset_rows(datasets, style)
                section_title = next(iter(lines))[0]
            else:
                section_title = (f"Is {compare_parameter} the same for every data set?" if compare_parameter
                                 else "Does one curve fit every data set?")
                lines = {(section_title, "Result"): "Not compared (see the warnings)"}
            lines[(section_title, "Data sets compared" if datasets is not None else "Data sets")] = ", ".join(
                datasets["data_sets"] if datasets is not None else fits)
            if skipped:
                lines[(section_title, "Left out (no fit)")] = ", ".join(skipped)
            extra = pd.DataFrame({next(iter(fits)): lines})
            extra.index.names = ["section", "statistic"]
            frame = pd.concat([frame, extra])
            section = result.section("Comparing the data sets")
            for (part, label), text in lines.items():
                section.add(f"{part}: {label}", text, text)
    if interpolate and not any(interpolated.values()):
        result.warnings.append("No row has Y without X, so nothing was interpolated. Enter unknowns as rows with Y values and a blank X.")
    result.values.update(fits=fits, wide=frame, comparisons=comparisons, datasets=datasets, interpolated=interpolated)
    points = "each replicate as a point" if replicates == "each" else "the mean of each row's replicates as one point"
    held = f" with {', '.join(f'{name} = {fmt(value)}' for name, value in fixed.items())} fixed" if fixed else ""
    methods = (f"Least-squares nonlinear regression ({fitted_model.name}: {fitted_model.equation}{held}) of each data set ({points}); "
               f"asymptotic {100 * ci:g}% CIs of the parameters" + (" (the CIs of EC50, half-life and doubling time are transformed from "
                                                                     "those of LogEC50 and K)" if fitted_model.key in
                                                                     ("log_dose_response", "one_phase", "exponential_growth") else "")
               + ".")
    if other is not None:
        methods += (f" Each fit was compared with {_fit_label(other, compare_fixed)} by AICc and, where one model is a special case of "
                    f"the other, the extra sum-of-squares F test (alpha = {alpha:g}).")
    if datasets is not None:
        what = f"{compare_parameter} is the same for every data set" if compare_parameter else "one curve fits every data set"
        left_out = [name for name in table.sets if name not in fits]
        what += f" ({', '.join(datasets['data_sets'])}; {', '.join(left_out)} could not be fitted and {'was' if len(left_out) == 1 else 'were'} left out)" if left_out else ""
        methods += (f" The null hypothesis that {what} was tested against separate fits by the extra sum-of-squares F test "
                    f"(alpha = {alpha:g}) and AICc.")
    if interpolate:
        methods += (" Unknown X values were read off each fitted curve within the range of its X values (no extrapolation); they have "
                    "no confidence intervals.")
    if fitted_model.key == "dose_response":
        methods += (" The EC50's CI is symmetric and asymptotic, so it can reach below 0; fitting log(dose) gives an asymmetric CI "
                    "that cannot.")
    result.methods = methods
    if any(fit["n"] < 2 * len(fit["free"]) + 1 for fit in fits.values()):
        result.warnings.append("Few points per parameter in a data set: the fit, its CIs and R² are rough.")
    return result


def _fit_label(model: Model, fixed: dict) -> str:
    held = f", {', '.join(f'{name} = {fmt(value)}' for name, value in fixed.items())}" if fixed else ""
    return f"{model.name}{held}"


def _model_rows(item: dict, model: Model, fixed: dict, other: Model, other_fixed: dict, style: str) -> dict:
    first_is_main = item["simple"]["model"] == model.key and item["simple"]["fixed"] == fixed
    simple_label = _fit_label(model, fixed) if first_is_main else _fit_label(other, other_fixed)
    complex_label = _fit_label(other, other_fixed) if first_is_main else _fit_label(model, fixed)
    section = "Which fit is better?"
    ranked = item["nested"] or len(item["simple"]["free"]) < len(item["complex"]["free"])
    if not ranked:  # neither is simpler: name them as they were chosen
        simple_label, complex_label = _fit_label(model, fixed), _fit_label(other, other_fixed)
    rows = {(section, "Simpler model" if ranked else "Model A"): simple_label}
    if not ranked:
        rows[(section, "Model B")] = complex_label
    if item["nested"]:
        rows[(section, "F (extra sum-of-squares)")] = fmt(item["f"])
        rows[(section, "DFn, DFd")] = f"{item['dfn']}, {item['dfd']}"
        rows[(section, "P value")] = format_p(item["p"], style=style)
        if item["f_prefers"]:
            rows[(section, "Preferred (F test)")] = (f"{complex_label} ({p_summary(item['p'], style)})" if item["f_prefers"] == "complex"
                                                     else f"{simple_label} (the extra parameters do not help: {p_summary(item['p'], style)})")
    else:
        rows[(section, "F (extra sum-of-squares)")] = "not nested: compared by AICc only"
    rows[(section, "AICc difference (complex − simple)" if ranked else "AICc difference (B − A)")] = fmt(item["aicc_delta"])
    if math.isfinite(item["aicc_simple"]):
        rows[(section, "Probability the simpler model is correct" if ranked else "Probability model A is correct")] = (
            f"{100 * item['aicc_simple']:.3g}%")
    if item["aicc_prefers"]:
        rows[(section, "Preferred (AICc)")] = simple_label if item["aicc_prefers"] == "simple" else complex_label
    return rows


def _dataset_rows(item: dict, style: str) -> dict:
    parameter = item["parameter"]
    section = f"Is {parameter} the same for every data set?" if parameter else "Does one curve fit every data set?"
    shared = "one shared value" if parameter else "one curve"
    rows = {}
    if parameter:
        rows[(section, f"Shared {parameter}")] = fmt(item["shared"]["params"][0][parameter]) + (
            " (poorly determined: the data sets differ too much to share it)" if item["shared"]["ambiguous"] else "")
    rows[(section, "F (extra sum-of-squares)")] = fmt(item["f"])
    rows[(section, "DFn, DFd")] = f"{item['dfn']}, {item['dfd']}"
    rows[(section, "P value")] = format_p(item["p"], style=style)
    if item["f_prefers"]:
        rows[(section, "Preferred (F test)")] = (f"separate fits ({p_summary(item['p'], style)})" if item["f_prefers"] == "separate"
                                                 else f"{shared} ({p_summary(item['p'], style)})")
    rows[(section, "AICc difference (separate − shared)")] = fmt(item["aicc_delta"])
    if math.isfinite(item["aicc_shared"]):
        rows[(section, f"Probability {shared} is correct")] = f"{100 * item['aicc_shared']:.3g}%"
    if item["aicc_prefers"]:
        rows[(section, "Preferred (AICc)")] = "separate fits" if item["aicc_prefers"] == "separate" else shared
    return rows


__all__ = ["MODELS", "Model", "aicc", "compare_data_sets", "compare_models", "curve_band", "fit_curve", "global_fit", "interpolate_x",
           "model_of", "nested", "nonlinear_regression", "predict"]
