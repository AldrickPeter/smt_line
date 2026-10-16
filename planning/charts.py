"""Comparison charts (static PNGs for a report). Only this module imports matplotlib.

Style (from the dataviz reference palette, light mode):
  - products get categorical slots 1-3 (blue, orange, aqua), in fixed order;
  - per-method KPI charts put methods on an axis and use ONE series colour,
    so a colour never means both "a product" and "a method";
  - thin marks, solid hairline grid, recessive axes, text in ink colours.
"""

from collections import defaultdict

import matplotlib

matplotlib.use("Agg")   # file output only, no window
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from planning.evaluate import RunResult, mean_ci95  # noqa: E402
from planning.model import PlanningModel  # noqa: E402
from planning.plan import Plan, Prediction, timeline  # noqa: E402
from smt_sim.orders import Order  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]   # categorical slots 1-4, fixed order
SETUP_FILL = "#e1e0d9"

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.titlecolor": INK,
    "axes.labelcolor": INK_2,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "savefig.dpi": 160,
})


def _style(ax, grid_axis: str = "x") -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(0.8)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def _hours(s: float) -> float:
    return s / 3600.0


# ---- 1. Gantt of each method's plan -------------------------------------------


def gantt(plans: list[Plan], predictions: dict[str, Prediction], orders: list[Order],
          model: PlanningModel, title: str, path: str) -> None:
    """One panel per method: each line's lots as bars, changeovers in grey, due dates as ticks."""
    products = list(model.products)
    colour = {p: SERIES[i] for i, p in enumerate(products)}
    due = {o.id: o.due_s for o in orders}
    lines = list(model.lines)
    makespan_h = max(_hours(p.makespan_s) for p in predictions.values())

    fig, axes = plt.subplots(len(plans), 1, figsize=(10, 1.25 * len(plans) + 1.2), sharex=True)
    for ax, plan in zip(np.atleast_1d(axes), plans):
        pred = predictions[plan.method]
        for row, line in enumerate(lines):
            y = len(lines) - 1 - row
            for s in timeline(plan, model)[line]:
                if s.start_s > s.setup_start_s:
                    ax.barh(y, _hours(s.start_s - s.setup_start_s), left=_hours(s.setup_start_s), height=0.55,
                            color=SETUP_FILL, edgecolor=SURFACE, linewidth=2)
                width = _hours(s.end_s - s.start_s)
                ax.barh(y, width, left=_hours(s.start_s), height=0.55, color=colour[s.lot.product],
                        edgecolor=SURFACE, linewidth=2)
                label = s.lot.order_id.replace("SO-", "") + _late_text(s.end_s - due[s.lot.order_id])
                if width > 0.04 * makespan_h:
                    ax.text(_hours(s.start_s) + 0.05, y, label, va="center", ha="left", fontsize=7, color=INK)
                ax.plot([_hours(due[s.lot.order_id])] * 2, [y - 0.38, y - 0.30], color=INK_2, linewidth=1.2)
        ax.set_yticks(range(len(lines)), list(reversed(lines)))
        ax.set_title(f"{plan.method}   weighted lateness {pred.weighted_tardiness_h:.1f} h · "
                     f"makespan {_hours(pred.makespan_s):.1f} h · {pred.changeovers} changeovers",
                     fontsize=9)
        _style(ax)
    np.atleast_1d(axes)[-1].set_xlabel("Hours from start (planning model)")
    handles = [Patch(color=colour[p], label=p) for p in products] + [
        Patch(color=SETUP_FILL, label="changeover"),
        Line2D([], [], color=INK_2, linewidth=1.2, label="due date (tick under the bar)")]
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False, fontsize=8)
    fig.suptitle(title, x=0.01, ha="left", fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    fig.savefig(path)
    plt.close(fig)


def _late_text(late_s: float) -> str:
    if late_s <= 30:
        return ""
    return f"  late {late_s / 60:.0f} min" if late_s < 3600 else f"  late {late_s / 3600:.1f} h"


# ---- 2. KPI comparison: simulated mean ± 95 % CI vs model prediction ------------


KPIS = [
    ("weighted_tardiness_h", "Weighted lateness (h)"),
    ("makespan_h", "Makespan (h)"),
    ("late_orders", "Late orders"),
    ("changeovers", "Changeovers"),
]


def kpi_comparison(results: dict[str, list[RunResult]], predictions: dict[str, Prediction],
                   title: str, path: str) -> None:
    methods = list(results)
    predicted = {
        "weighted_tardiness_h": lambda p: p.weighted_tardiness_h,
        "makespan_h": lambda p: _hours(p.makespan_s),
        "late_orders": lambda p: p.late_orders,
        "changeovers": lambda p: p.changeovers,
    }
    fig, axes = plt.subplots(2, 2, figsize=(10, 6.2))
    for ax, (key, label) in zip(axes.flat, KPIS):
        y = np.arange(len(methods))[::-1]
        for yi, m in zip(y, methods):
            mean, half = mean_ci95([getattr(r, key) for r in results[m]])
            ax.plot([mean - half, mean + half], [yi, yi], color=SERIES[0], linewidth=2, solid_capstyle="round")
            ax.plot(mean, yi, "o", color=SERIES[0], markersize=7, markeredgecolor=SURFACE, markeredgewidth=1.5)
            ax.plot(predicted[key](predictions[m]), yi, "D", markersize=6, markerfacecolor="none",
                    markeredgecolor=INK_2, markeredgewidth=1.2)
        ax.set_yticks(y, methods)
        ax.set_title(label)
        ax.set_xlim(left=0)
        _style(ax)
    handles = [Line2D([], [], color=SERIES[0], marker="o", linewidth=2, label="simulated mean ± 95 % CI"),
               Line2D([], [], color=INK_2, marker="D", markerfacecolor="none", linestyle="none",
                      label="planning model prediction")]
    fig.legend(handles=handles, loc="upper right", ncol=2, frameon=False)
    fig.suptitle(title, x=0.01, ha="left", fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path)
    plt.close(fig)


# ---- 3. Distribution over seeds -------------------------------------------------


def distribution(results: dict[str, list[RunResult]], key: str, label: str, title: str, path: str) -> None:
    methods = list(results)
    data = [[getattr(r, key) for r in results[m]] for m in methods][::-1]
    fig, ax = plt.subplots(figsize=(8, 0.55 * len(methods) + 1.4))
    ax.boxplot(data, vert=False, widths=0.5, showfliers=False,
               boxprops={"color": AXIS}, whiskerprops={"color": AXIS}, capprops={"color": AXIS},
               medianprops={"color": INK, "linewidth": 1.5})
    rng = np.random.default_rng(0)   # jitter only; fixed so the picture is reproducible
    for i, values in enumerate(data, start=1):
        ax.scatter(values, i + rng.uniform(-0.15, 0.15, len(values)), s=14, color=SERIES[0], alpha=0.75,
                   linewidths=0)
    ax.set_yticks(range(1, len(methods) + 1), methods[::-1])
    ax.set_xlabel(label)
    ax.set_title(title)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ---- 4. Paired difference vs a baseline (same seeds) ------------------------------


def paired_vs_baseline(results: dict[str, list[RunResult]], baseline: str, key: str, label: str,
                       title: str, path: str) -> None:
    base = {r.seed: getattr(r, key) for r in results[baseline]}
    methods = [m for m in results if m != baseline]
    diffs = [[getattr(r, key) - base[r.seed] for r in results[m]] for m in methods][::-1]
    fig, ax = plt.subplots(figsize=(8, 0.55 * len(methods) + 1.4))
    ax.axvline(0, color=AXIS, linewidth=1)
    ax.boxplot(diffs, vert=False, widths=0.5, showfliers=False,
               boxprops={"color": AXIS}, whiskerprops={"color": AXIS}, capprops={"color": AXIS},
               medianprops={"color": INK, "linewidth": 1.5})
    rng = np.random.default_rng(0)
    for i, values in enumerate(diffs, start=1):
        ax.scatter(values, i + rng.uniform(-0.15, 0.15, len(values)), s=14, color=SERIES[0], alpha=0.75,
                   linewidths=0)
        better = sum(v < 0 for v in values)
        ax.text(1.01, i, f"better on {better}/{len(values)} seeds", transform=ax.get_yaxis_transform(),
                va="center", fontsize=8, color=INK_2)
    ax.set_yticks(range(1, len(methods) + 1), methods[::-1])
    ax.set_xlabel(label)
    ax.set_title(title)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ---- 5. Planned vs simulated completion ------------------------------------------


def planned_vs_simulated(panels: list[tuple[str, dict[str, Prediction], dict[str, list[RunResult]]]],
                         path: str) -> None:
    """One dot per (method, order): x = model's completion time, y = simulated mean (± range)."""
    fig, axes = plt.subplots(1, len(panels), figsize=(4.2 * len(panels), 4.2))
    for ax, (name, predictions, results) in zip(np.atleast_1d(axes), panels):
        xs, ys, lo, hi = [], [], [], []
        for m, runs in results.items():
            for order_id, planned_s in predictions[m].completion_s.items():
                actual = [r.completion_s[order_id] / 3600 for r in runs]
                xs.append(planned_s / 3600)
                ys.append(np.mean(actual))
                lo.append(np.mean(actual) - np.min(actual))
                hi.append(np.max(actual) - np.mean(actual))
        top = max(max(xs), max(np.array(ys) + np.array(hi))) * 1.05
        ax.plot([0, top], [0, top], color=AXIS, linewidth=1)
        ax.errorbar(xs, ys, yerr=[lo, hi], fmt="o", color=SERIES[0], ecolor=GRID, elinewidth=1.5,
                    markersize=5, markeredgecolor=SURFACE, markeredgewidth=1)
        ax.set_xlim(0, top)
        ax.set_ylim(0, top)
        ax.set_aspect("equal")
        ax.set_xlabel("Planned completion (h)")
        ax.set_ylabel("Simulated completion (h): mean, min-max")
        late_share = np.mean(np.array(ys) > np.array(xs))
        ax.set_title(f"{name}: {late_share:.0%} of orders finish later than planned", pad=10)
        _style(ax, grid_axis="both")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path)
    plt.close(fig)


# ---- 6. Solver scaling ----------------------------------------------------------------


def solver_scaling(rows: list[dict], time_limit_s: float, path: str) -> None:
    """Solve time and solution quality vs number of orders, for the exact methods."""
    methods = ["CP-SAT", "MILP", "BRUTE_FORCE"]
    colour = dict(zip(methods + ["EDD"], SERIES))
    by = defaultdict(list)
    for r in rows:
        by[(r["method"], r["n"])].append(r)

    fig, (ax_t, ax_q) = plt.subplots(1, 2, figsize=(11, 4.2))
    for m in methods:
        ns = sorted(n for (mm, n) in by if mm == m)
        if not ns:
            continue
        times = [np.mean([r["solve_time_s"] for r in by[(m, n)]]) for n in ns]
        ax_t.plot(ns, times, color=colour[m], linewidth=2, marker="o", markersize=6, label=m)
        hit = [n for n in ns if any(r["hit_limit"] for r in by[(m, n)])]
        ax_t.plot(hit, [np.mean([r["solve_time_s"] for r in by[(m, n)]]) for n in hit], "o", markersize=11,
                  markerfacecolor="none", markeredgecolor=colour[m], markeredgewidth=1.2)
    ax_t.axhline(time_limit_s, color=AXIS, linewidth=1)
    ax_t.text(ax_t.get_xlim()[0], time_limit_s * 1.15, f"time limit {time_limit_s:g} s", fontsize=8, color=INK_2)
    ax_t.set_yscale("log")
    ax_t.set_xlabel("Number of orders")
    ax_t.set_ylabel("Solve time (s, log scale)")
    ax_t.set_title("Solve time (ringed = stopped at the time limit)")
    ax_t.legend(frameon=False)
    _style(ax_t, grid_axis="y")

    for m in ["CP-SAT", "MILP", "EDD"]:
        ns = sorted(n for (mm, n) in by if mm == m)
        vals = [np.mean([r["weighted_tardiness_h"] for r in by[(m, n)]]) for n in ns]
        ax_q.plot(ns, vals, color=colour[m], linewidth=2, marker="o", markersize=6, label=m)
    ax_q.set_xlabel("Number of orders")
    ax_q.set_ylabel("Weighted lateness of the plan (h, model)")
    ax_q.set_title("Plan quality within the time limit")
    ax_q.legend(frameon=False)
    _style(ax_q, grid_axis="y")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
