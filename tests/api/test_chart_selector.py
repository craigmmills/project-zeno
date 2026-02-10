from src.api.chart_selector import select_best_chart


def test_select_best_chart_by_query_relevance():
    charts_data = [
        {
            "id": "main_chart",
            "type": "line",
            "title": "Forest loss trend over time",
            "insight": "Loss increased in 2023",
            "data": [{"year": 2022, "value": 10}],
        },
        {
            "id": "secondary",
            "type": "pie",
            "title": "Forest loss by driver",
            "insight": "Fire share rose",
            "data": [{"driver": "fire", "value": 10}],
        },
    ]

    selected = select_best_chart(
        query="show the forest loss trend over time",
        charts_data=charts_data,
    )

    assert selected is not None
    assert selected["id"] == "main_chart"


def test_select_best_chart_tie_prefers_main_chart():
    charts_data = [
        {
            "id": "other_chart",
            "type": "bar",
            "title": "Forest loss comparison",
            "insight": "A simple compare chart",
            "data": [{"name": "A", "value": 1}],
        },
        {
            "id": "main_chart",
            "type": "bar",
            "title": "Forest loss comparison",
            "insight": "A simple compare chart",
            "data": [{"name": "A", "value": 1}],
        },
    ]

    selected = select_best_chart(
        query="compare forest loss",
        charts_data=charts_data,
    )

    assert selected is not None
    assert selected["id"] == "main_chart"


def test_select_best_chart_filters_invalid_candidates():
    charts_data = [
        {
            "id": "bad",
            "type": "table",
            "title": "t",
            "insight": "i",
            "data": [],
        },
        "not-a-dict",
        {"id": "missing", "type": "bar"},
    ]

    selected = select_best_chart(query="anything", charts_data=charts_data)

    assert selected is None
