import pytest

from src.api.chart_renderer import (
    ChartRenderError,
    ChartRenderUnsupported,
    render_chart_png,
)

WIDTH = 1200
HEIGHT = 750
DPI = 180


@pytest.mark.parametrize(
    "chart",
    [
        {
            "id": "line",
            "type": "line",
            "title": "Line chart",
            "insight": "line",
            "xAxis": "year",
            "yAxis": "value",
            "data": [{"year": 2020, "value": 1}, {"year": 2021, "value": 2}],
        },
        {
            "id": "bar",
            "type": "bar",
            "title": "Bar chart",
            "insight": "bar",
            "xAxis": "name",
            "yAxis": "value",
            "data": [{"name": "A", "value": 1}, {"name": "B", "value": 2}],
        },
        {
            "id": "stacked",
            "type": "stacked-bar",
            "title": "Stacked chart",
            "insight": "stacked",
            "xAxis": "year",
            "yAxis": "value",
            "seriesFields": ["fire", "logging"],
            "data": [
                {"year": "2020", "fire": 1, "logging": 2},
                {"year": "2021", "fire": 3, "logging": 1},
            ],
        },
        {
            "id": "grouped",
            "type": "grouped-bar",
            "title": "Grouped chart",
            "insight": "grouped",
            "xAxis": "country",
            "yAxis": "value",
            "groupField": "metric",
            "data": [
                {"country": "BR", "metric": "loss", "value": 3},
                {"country": "BR", "metric": "fire", "value": 1},
                {"country": "ID", "metric": "loss", "value": 2},
                {"country": "ID", "metric": "fire", "value": 2},
            ],
        },
        {
            "id": "pie",
            "type": "pie",
            "title": "Pie chart",
            "insight": "pie",
            "xAxis": "driver",
            "yAxis": "value",
            "data": [
                {"driver": "fire", "value": 5},
                {"driver": "logging", "value": 4},
                {"driver": "agri", "value": 3},
            ],
        },
        {
            "id": "area",
            "type": "area",
            "title": "Area chart",
            "insight": "area",
            "xAxis": "year",
            "yAxis": "value",
            "data": [{"year": 2020, "value": 2}, {"year": 2021, "value": 4}],
        },
        {
            "id": "scatter",
            "type": "scatter",
            "title": "Scatter chart",
            "insight": "scatter",
            "xAxis": "x",
            "yAxis": "y",
            "data": [{"x": 1, "y": 2}, {"x": 2, "y": 3}],
        },
    ],
)
def test_render_supported_types_returns_png(chart):
    png = render_chart_png(chart, width_px=WIDTH, height_px=HEIGHT, dpi=DPI)

    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG")


def test_render_unsupported_type_raises():
    chart = {
        "id": "table",
        "type": "table",
        "title": "Table",
        "insight": "unsupported",
        "data": [{"a": 1}],
    }

    with pytest.raises(ChartRenderUnsupported):
        render_chart_png(chart, width_px=WIDTH, height_px=HEIGHT, dpi=DPI)


def test_render_malformed_data_raises_controlled_error():
    chart = {
        "id": "main_chart",
        "type": "bar",
        "title": "Bar chart",
        "insight": "bad data",
        "xAxis": "name",
        "yAxis": "value",
        "data": [{"name": "A", "value": "not-number"}],
    }

    with pytest.raises(ChartRenderError):
        render_chart_png(chart, width_px=WIDTH, height_px=HEIGHT, dpi=DPI)


def test_long_unicode_labels_do_not_crash():
    chart = {
        "id": "main_chart",
        "type": "bar",
        "title": "🚀 Forest changes across long unicode labels",
        "insight": "unicode",
        "xAxis": "name",
        "yAxis": "value",
        "data": [
            {"name": "São Tomé and Príncipe — Región Norte", "value": 10},
            {"name": "Réserve de Biosphère 🌿 Très Très Longue", "value": 12},
        ],
    }

    png = render_chart_png(chart, width_px=WIDTH, height_px=HEIGHT, dpi=DPI)
    assert png.startswith(b"\x89PNG")
