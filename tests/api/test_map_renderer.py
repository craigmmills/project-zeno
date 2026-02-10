from unittest.mock import AsyncMock, patch

import pytest

from src.api.map_renderer import (
    MapRenderError,
    MapRenderUnsupported,
    render_map_png,
)

pytestmark = pytest.mark.asyncio


async def test_render_map_png_with_polygon_returns_png_bytes():
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [-55.0, -10.0],
                [-54.0, -10.0],
                [-54.0, -9.0],
                [-55.0, -9.0],
                [-55.0, -10.0],
            ]
        ],
    }

    with patch(
        "src.api.map_renderer.get_geometry_data",
        new=AsyncMock(
            return_value={
                "name": "Test AOI",
                "geometry": geometry,
            }
        ),
    ):
        png = await render_map_png(
            aoi={"source": "gadm", "src_id": "BRA"},
            dataset={"dataset_name": "Dataset"},
            width_px=800,
            height_px=500,
            dpi=120,
        )

    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG")


async def test_render_map_png_invalid_geometry_raises_error():
    with patch(
        "src.api.map_renderer.get_geometry_data",
        new=AsyncMock(
            return_value={
                "name": "Bad AOI",
                "geometry": {"type": "Polygon"},
            }
        ),
    ):
        with pytest.raises(MapRenderError):
            await render_map_png(
                aoi={"source": "gadm", "src_id": "BRA"},
                dataset=None,
                width_px=800,
                height_px=500,
                dpi=120,
            )


async def test_render_map_png_empty_geometry_raises_unsupported():
    with patch(
        "src.api.map_renderer.get_geometry_data",
        new=AsyncMock(
            return_value={
                "name": "Empty AOI",
                "geometry": {"type": "FeatureCollection", "features": []},
            }
        ),
    ):
        with pytest.raises(MapRenderUnsupported):
            await render_map_png(
                aoi={"source": "gadm", "src_id": "BRA"},
                dataset=None,
                width_px=800,
                height_px=500,
                dpi=120,
            )
