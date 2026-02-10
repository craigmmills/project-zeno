from __future__ import annotations

import io
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.use("Agg")

SUPPORTED_RENDER_TYPES = {
    "line",
    "bar",
    "stacked-bar",
    "grouped-bar",
    "pie",
    "area",
    "scatter",
}


class ChartRenderUnsupported(Exception):
    """Raised when chart type is not supported for rendering."""


class ChartRenderError(Exception):
    """Raised for malformed or unrenderable chart payloads."""


def _coerce_numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _validate_chart(chart: dict[str, Any]) -> tuple[str, pd.DataFrame]:
    chart_type = chart.get("type")
    if chart_type not in SUPPORTED_RENDER_TYPES:
        raise ChartRenderUnsupported(f"Unsupported chart type: {chart_type}")

    data = chart.get("data")
    if not isinstance(data, list) or len(data) == 0:
        raise ChartRenderError("Chart data must be a non-empty list")

    df = pd.DataFrame(data)
    if df.empty:
        raise ChartRenderError("Chart data is empty after conversion")

    return chart_type, df


def _extract_series_fields(
    chart: dict[str, Any], df: pd.DataFrame
) -> list[str]:
    series_fields = chart.get("seriesFields") or []
    if isinstance(series_fields, list) and series_fields:
        return [field for field in series_fields if field in df.columns]

    x_axis = chart.get("xAxis")
    y_axis = chart.get("yAxis")
    excluded = {x_axis, y_axis, None, ""}

    numeric_candidates: list[str] = []
    for column in df.columns:
        if column in excluded:
            continue
        coerced = pd.to_numeric(df[column], errors="coerce")
        if coerced.notna().any():
            numeric_candidates.append(column)

    return numeric_candidates


def _style_axes(
    ax: Any, chart: dict[str, Any], rotate_x: bool = False
) -> None:
    x_axis = chart.get("xAxis")
    y_axis = chart.get("yAxis")
    if x_axis:
        ax.set_xlabel(str(x_axis))
    if y_axis:
        ax.set_ylabel(str(y_axis))

    title = chart.get("title") or "Chart"
    ax.set_title(str(title), fontsize=13)

    if rotate_x:
        ax.tick_params(axis="x", labelrotation=35)


def _render_line_or_area(
    chart_type: str,
    chart: dict[str, Any],
    df: pd.DataFrame,
    ax: Any,
) -> None:
    x_axis = chart.get("xAxis")
    y_axis = chart.get("yAxis")

    if not x_axis or x_axis not in df.columns:
        x_axis = df.columns[0]

    series_fields = _extract_series_fields(chart, df)
    metric_fields = series_fields or ([y_axis] if y_axis in df.columns else [])
    if not metric_fields:
        metric_fields = [col for col in df.columns if col != x_axis][:1]

    if not metric_fields:
        raise ChartRenderError("No numeric metric columns available")

    df = _coerce_numeric(df, metric_fields)
    df = df.dropna(subset=metric_fields)
    if df.empty:
        raise ChartRenderError("No valid rows for line/area chart")

    for metric in metric_fields:
        if chart_type == "area":
            ax.fill_between(
                range(len(df)),
                df[metric].tolist(),
                alpha=0.25,
                label=str(metric),
            )
            ax.plot(range(len(df)), df[metric].tolist(), linewidth=2)
        else:
            ax.plot(df[x_axis], df[metric], marker="o", label=str(metric))

    if chart_type == "area":
        ax.set_xticks(range(len(df)))
        ax.set_xticklabels(df[x_axis].astype(str).tolist())

    if len(metric_fields) > 1:
        ax.legend(loc="best", fontsize=9)

    _style_axes(ax, chart, rotate_x=len(df[x_axis].unique()) > 8)
    ax.grid(axis="y", linestyle="--", alpha=0.3)


def _render_bar(chart: dict[str, Any], df: pd.DataFrame, ax: Any) -> None:
    x_axis = chart.get("xAxis")
    y_axis = chart.get("yAxis")

    if not x_axis or x_axis not in df.columns:
        x_axis = df.columns[0]
    if not y_axis or y_axis not in df.columns:
        numeric_cols = [
            col
            for col in df.columns
            if pd.to_numeric(df[col], errors="coerce").notna().any()
            and col != x_axis
        ]
        if not numeric_cols:
            raise ChartRenderError("No y-axis metric for bar chart")
        y_axis = numeric_cols[0]

    df = _coerce_numeric(df, [y_axis])
    df = df.dropna(subset=[x_axis, y_axis])
    if df.empty:
        raise ChartRenderError("No valid rows for bar chart")

    ax.bar(df[x_axis].astype(str), df[y_axis])
    _style_axes(ax, chart, rotate_x=len(df[x_axis].unique()) > 8)
    ax.grid(axis="y", linestyle="--", alpha=0.3)


def _render_stacked_bar(
    chart: dict[str, Any], df: pd.DataFrame, ax: Any
) -> None:
    x_axis = chart.get("xAxis")
    if not x_axis or x_axis not in df.columns:
        x_axis = df.columns[0]

    series_fields = _extract_series_fields(chart, df)
    if not series_fields:
        series_fields = [
            col
            for col in df.columns
            if col != x_axis
            and pd.to_numeric(df[col], errors="coerce").notna().any()
        ]

    if not series_fields:
        raise ChartRenderError("No series fields for stacked bar chart")

    df = _coerce_numeric(df, series_fields)
    df = df.dropna(subset=[x_axis])
    df = df.dropna(subset=series_fields, how="all")
    if df.empty:
        raise ChartRenderError("No valid rows for stacked bar chart")

    bottom = pd.Series([0] * len(df), dtype=float)
    for field in series_fields:
        values = df[field].fillna(0)
        ax.bar(df[x_axis].astype(str), values, bottom=bottom, label=str(field))
        bottom = bottom + values

    ax.legend(loc="best", fontsize=9)
    _style_axes(ax, chart, rotate_x=len(df[x_axis].unique()) > 8)
    ax.grid(axis="y", linestyle="--", alpha=0.3)


def _render_grouped_bar(
    chart: dict[str, Any], df: pd.DataFrame, ax: Any
) -> None:
    x_axis = chart.get("xAxis")
    y_axis = chart.get("yAxis")
    group_field = chart.get("groupField")

    if not x_axis or x_axis not in df.columns:
        raise ChartRenderError("Grouped bar chart requires xAxis")
    if not y_axis or y_axis not in df.columns:
        raise ChartRenderError("Grouped bar chart requires yAxis")
    if not group_field or group_field not in df.columns:
        raise ChartRenderError("Grouped bar chart requires groupField")

    df = _coerce_numeric(df, [y_axis])
    df = df.dropna(subset=[x_axis, y_axis, group_field])
    if df.empty:
        raise ChartRenderError("No valid rows for grouped bar chart")

    pivot = df.pivot_table(
        index=x_axis,
        columns=group_field,
        values=y_axis,
        aggfunc="sum",
        fill_value=0,
    )
    if pivot.empty:
        raise ChartRenderError("No grouped data to plot")

    categories = pivot.index.astype(str).tolist()
    groups = [str(col) for col in pivot.columns]
    num_groups = max(1, len(groups))
    bar_width = 0.8 / num_groups
    x_positions = list(range(len(categories)))

    for idx, group in enumerate(groups):
        values = pivot.iloc[:, idx].tolist()
        offsets = [pos + (idx * bar_width) for pos in x_positions]
        ax.bar(offsets, values, width=bar_width, label=group)

    center_offset = (num_groups - 1) * bar_width / 2
    ax.set_xticks([pos + center_offset for pos in x_positions])
    ax.set_xticklabels(categories)
    ax.legend(loc="best", fontsize=9)

    _style_axes(ax, chart, rotate_x=len(categories) > 8)
    ax.grid(axis="y", linestyle="--", alpha=0.3)


def _render_pie(chart: dict[str, Any], df: pd.DataFrame, ax: Any) -> None:
    x_axis = chart.get("xAxis")
    y_axis = chart.get("yAxis")

    if not x_axis or x_axis not in df.columns:
        x_axis = df.columns[0]
    if not y_axis or y_axis not in df.columns:
        numeric_cols = [
            col
            for col in df.columns
            if pd.to_numeric(df[col], errors="coerce").notna().any()
            and col != x_axis
        ]
        if not numeric_cols:
            raise ChartRenderError("No numeric value field for pie chart")
        y_axis = numeric_cols[0]

    df = _coerce_numeric(df, [y_axis])
    df = df.dropna(subset=[x_axis, y_axis])
    df = df[df[y_axis] > 0]
    if df.empty:
        raise ChartRenderError("No valid rows for pie chart")

    grouped = (
        df.groupby(x_axis, as_index=False)[y_axis]
        .sum()
        .sort_values(y_axis, ascending=False)
    )

    if len(grouped) > 8:
        top = grouped.iloc[:7].copy()
        other_sum = grouped.iloc[7:][y_axis].sum()
        other_row = pd.DataFrame([{x_axis: "Other", y_axis: other_sum}])
        grouped = pd.concat([top, other_row], ignore_index=True)

    ax.pie(
        grouped[y_axis],
        labels=grouped[x_axis].astype(str),
        autopct="%1.1f%%",
        startangle=90,
    )
    ax.axis("equal")
    ax.set_title(str(chart.get("title") or "Chart"), fontsize=13)


def _render_scatter(chart: dict[str, Any], df: pd.DataFrame, ax: Any) -> None:
    x_axis = chart.get("xAxis")
    y_axis = chart.get("yAxis")

    if not x_axis or x_axis not in df.columns:
        raise ChartRenderError("Scatter chart requires xAxis")
    if not y_axis or y_axis not in df.columns:
        raise ChartRenderError("Scatter chart requires yAxis")

    df = _coerce_numeric(df, [x_axis, y_axis])
    df = df.dropna(subset=[x_axis, y_axis])
    if df.empty:
        raise ChartRenderError("No valid rows for scatter chart")

    ax.scatter(df[x_axis], df[y_axis], alpha=0.75)
    _style_axes(ax, chart, rotate_x=False)


def render_chart_png(
    chart: dict,
    width_px: int,
    height_px: int,
    dpi: int,
) -> bytes:
    chart_type, df = _validate_chart(chart)

    fig_width = width_px / dpi
    fig_height = height_px / dpi

    fig, ax = plt.subplots(figsize=(fig_width, fig_height), dpi=dpi)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    try:
        if chart_type in {"line", "area"}:
            _render_line_or_area(chart_type, chart, df, ax)
        elif chart_type == "bar":
            _render_bar(chart, df, ax)
        elif chart_type == "stacked-bar":
            _render_stacked_bar(chart, df, ax)
        elif chart_type == "grouped-bar":
            _render_grouped_bar(chart, df, ax)
        elif chart_type == "pie":
            _render_pie(chart, df, ax)
        elif chart_type == "scatter":
            _render_scatter(chart, df, ax)

        plt.tight_layout()

        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=dpi)
        buffer.seek(0)
        return buffer.getvalue()
    except ChartRenderUnsupported:
        raise
    except ChartRenderError:
        raise
    except Exception as exc:
        raise ChartRenderError("Failed to render chart") from exc
    finally:
        plt.close(fig)
