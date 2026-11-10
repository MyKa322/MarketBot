"""PNG chart rendering.

Uses matplotlib's object-oriented API (Figure + Agg canvas) rather than pyplot: pyplot
keeps global state that is not safe to touch from several coroutines, and rendering
happens off the event loop in a worker thread so a slow draw never stalls the bot.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import datetime

import matplotlib
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from marketbot.constants import currency as currency_of
from marketbot.utils.money import to_major

matplotlib.use("Agg")
log = logging.getLogger(__name__)

# Telegram dark-theme palette; charts should look native inside the client.
BG = "#17212b"
PANEL = "#1d2733"
GRID = "#2b3a4a"
TEXT = "#c9d5e1"
MUTED = "#7d8b99"
UP = "#4ecb71"
DOWN = "#ef5350"
NEUTRAL = "#5aa9e6"
VOLUME = "#3f5a75"


@dataclass(frozen=True, slots=True)
class ChartPoint:
    ts: datetime
    price_minor: int
    volume: int | None = None


def _style_axes(ax, *, currency_code: str) -> None:
    ax.set_facecolor(PANEL)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    cur = currency_of(currency_code)
    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda v, _: f"{cur.symbol}{v:,.{min(cur.minor_units, 2)}f}")
    )


def _empty_figure(message: str) -> bytes:
    fig = Figure(figsize=(10, 4.2), dpi=110, facecolor=BG)
    ax = fig.add_subplot(111)
    ax.set_facecolor(PANEL)
    ax.axis("off")
    ax.text(
        0.5, 0.5, message, ha="center", va="center",
        color=MUTED, fontsize=13, transform=ax.transAxes,
    )
    return _to_png(fig)


def _to_png(fig: Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    buffer.seek(0)
    return buffer.getvalue()


def _render_price(
    points: list[ChartPoint],
    *,
    title: str,
    subtitle: str,
    currency_code: str,
    show_volume: bool = True,
    empty_message: str = "Not enough price history yet.",
) -> bytes:
    if len(points) < 2:
        return _empty_figure(empty_message)

    times = [p.ts for p in points]
    values = [float(to_major(p.price_minor, currency_code)) for p in points]
    volumes = [p.volume or 0 for p in points]
    has_volume = show_volume and any(volumes)

    trend = UP if values[-1] >= values[0] else DOWN

    fig = Figure(figsize=(10, 5.0), dpi=110, facecolor=BG)
    if has_volume:
        gs = fig.add_gridspec(2, 1, height_ratios=[3.2, 1], hspace=0.08)
        ax = fig.add_subplot(gs[0])
        ax_vol = fig.add_subplot(gs[1], sharex=ax)
    else:
        ax = fig.add_subplot(111)
        ax_vol = None

    ax.plot(times, values, color=trend, linewidth=2.0, solid_capstyle="round")
    ax.fill_between(times, values, min(values), color=trend, alpha=0.12)

    # Mark the extremes: the two numbers everyone actually looks for.
    hi_i = max(range(len(values)), key=lambda i: values[i])
    lo_i = min(range(len(values)), key=lambda i: values[i])
    cur = currency_of(currency_code)
    for idx, color, va in ((hi_i, UP, "bottom"), (lo_i, DOWN, "top")):
        ax.scatter([times[idx]], [values[idx]], color=color, s=26, zorder=5)
        ax.annotate(
            f"{cur.symbol}{values[idx]:,.2f}",
            (times[idx], values[idx]),
            textcoords="offset points",
            xytext=(0, 9 if va == "bottom" else -16),
            ha="center", color=color, fontsize=9, fontweight="bold",
        )

    ax.scatter([times[-1]], [values[-1]], color=trend, s=44, zorder=6, edgecolors=BG, linewidths=1.4)
    ax.axhline(values[-1], color=trend, linewidth=0.8, linestyle=":", alpha=0.5)

    ax.set_xlim(times[0], times[-1])
    _style_axes(ax, currency_code=currency_code)
    ax.set_title(title, color=TEXT, fontsize=13, fontweight="bold", loc="left", pad=14)
    ax.text(
        0, 1.012, subtitle, transform=ax.transAxes,
        color=MUTED, fontsize=9.5, va="bottom",
    )

    if ax_vol is not None:
        width = _bar_width(times)
        ax_vol.bar(times, volumes, width=width, color=VOLUME, alpha=0.9)
        ax_vol.set_facecolor(PANEL)
        ax_vol.grid(True, color=GRID, linewidth=0.5, alpha=0.5)
        ax_vol.set_axisbelow(True)
        for spine in ax_vol.spines.values():
            spine.set_color(GRID)
        ax_vol.tick_params(colors=MUTED, labelsize=8)
        ax_vol.set_ylabel("vol", color=MUTED, fontsize=8)
        ax.tick_params(labelbottom=False)
        axis_for_dates = ax_vol
    else:
        axis_for_dates = ax

    locator = AutoDateLocator()
    axis_for_dates.xaxis.set_major_locator(locator)
    axis_for_dates.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    return _to_png(fig)


def _bar_width(times: list[datetime]) -> float:
    """Bar width in days, sized from the median sample gap so bars never overlap."""
    if len(times) < 2:
        return 0.01
    gaps = sorted(
        (times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)
    )
    median_gap = gaps[len(gaps) // 2]
    return max(median_gap / 86400.0 * 0.8, 0.0005)


class ChartService:
    """Async facade: all rendering is pushed to a worker thread."""

    def __init__(self, max_points: int = 400) -> None:
        self._max_points = max_points

    @staticmethod
    def _downsample(points: list[ChartPoint], target: int) -> list[ChartPoint]:
        """Keep charts readable and fast by thinning long series.

        The last point is always preserved -- it is the current price, and quietly
        dropping it would show a stale figure next to a fresh label.
        """
        if len(points) <= target:
            return points
        step = len(points) / target
        thinned = [points[int(i * step)] for i in range(target)]
        if thinned[-1] is not points[-1]:
            thinned.append(points[-1])
        return thinned

    async def price_chart(
        self,
        points: list[ChartPoint],
        *,
        title: str,
        subtitle: str,
        currency_code: str,
        show_volume: bool = True,
        empty_message: str = "Not enough price history yet.",
    ) -> bytes:
        data = self._downsample(points, self._max_points)
        return await asyncio.to_thread(
            _render_price,
            data,
            title=title,
            subtitle=subtitle,
            currency_code=currency_code,
            show_volume=show_volume,
            empty_message=empty_message,
        )

    async def empty(self, message: str) -> bytes:
        return await asyncio.to_thread(_empty_figure, message)
