from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from src.api import map_renderer as mr
from src.api.config import APISettings
from src.api.map_renderer import (
    MAX_TILE_REQUESTS,
    MapRenderError,
    MapRenderUnsupported,
    _pick_zoom,
    _tile_grid,
    render_map_png,
)

pytestmark = pytest.mark.asyncio


GEOMETRY = {
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


@pytest.fixture(autouse=True)
def reset_mapbox_settings():
    old_access = APISettings.mapbox_access_token
    old_api = APISettings.mapbox_api_token
    old_style = APISettings.mapbox_style_id
    old_timeout = APISettings.mapbox_static_timeout_seconds
    old_scale = APISettings.mapbox_static_scale

    APISettings.mapbox_access_token = "test-mapbox-token"
    APISettings.mapbox_api_token = ""
    APISettings.mapbox_style_id = "mapbox/outdoors-v12"
    APISettings.mapbox_static_timeout_seconds = 2.5
    APISettings.mapbox_static_scale = 2

    yield

    APISettings.mapbox_access_token = old_access
    APISettings.mapbox_api_token = old_api
    APISettings.mapbox_style_id = old_style
    APISettings.mapbox_static_timeout_seconds = old_timeout
    APISettings.mapbox_static_scale = old_scale


async def test_render_map_png_with_polygon_returns_png_bytes():
    with patch(
        "src.api.map_renderer.get_geometry_data",
        new=AsyncMock(
            return_value={
                "name": "Test AOI",
                "geometry": GEOMETRY,
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


async def test_render_map_png_uses_basemap_and_overlay_when_both_available():
    fake_tile = Image.new("RGBA", (256, 256), (255, 10, 10, 180))
    fake_basemap = Image.new("RGBA", (512, 512), (30, 80, 120, 255))

    with (
        patch(
            "src.api.map_renderer.get_geometry_data",
            new=AsyncMock(
                return_value={
                    "name": "Test AOI",
                    "geometry": GEOMETRY,
                }
            ),
        ),
        patch(
            "src.api.map_renderer._download_mapbox_basemap",
            new=AsyncMock(return_value=fake_basemap),
        ) as download_basemap,
        patch(
            "src.api.map_renderer._download_tiles",
            new=AsyncMock(return_value={(0, 0): fake_tile}),
        ) as download_tiles,
        patch(
            "src.api.map_renderer._render_aoi_only_png",
            wraps=mr._render_aoi_only_png,
        ) as render_aoi_only,
    ):
        png = await render_map_png(
            aoi={"source": "gadm", "src_id": "BRA"},
            dataset={
                "dataset_name": "Dataset",
                "tile_url": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
            width_px=512,
            height_px=512,
            dpi=120,
        )

    assert png.startswith(b"\x89PNG")
    download_basemap.assert_awaited_once()
    download_tiles.assert_awaited_once()
    render_aoi_only.assert_not_called()


async def test_render_map_png_mapbox_failure_uses_overlay_only_path():
    fake_tile = Image.new("RGBA", (256, 256), (10, 40, 200, 180))

    with (
        patch(
            "src.api.map_renderer.get_geometry_data",
            new=AsyncMock(
                return_value={
                    "name": "Test AOI",
                    "geometry": GEOMETRY,
                }
            ),
        ),
        patch(
            "src.api.map_renderer._download_mapbox_basemap",
            new=AsyncMock(return_value=None),
        ) as download_basemap,
        patch(
            "src.api.map_renderer._download_tiles",
            new=AsyncMock(return_value={(0, 0): fake_tile}),
        ) as download_tiles,
        patch(
            "src.api.map_renderer._render_aoi_only_png",
            wraps=mr._render_aoi_only_png,
        ) as render_aoi_only,
    ):
        png = await render_map_png(
            aoi={"source": "gadm", "src_id": "BRA"},
            dataset={
                "dataset_name": "Dataset",
                "tile_url": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
            width_px=800,
            height_px=500,
            dpi=120,
        )

    assert png.startswith(b"\x89PNG")
    download_basemap.assert_awaited_once()
    download_tiles.assert_awaited_once()
    render_aoi_only.assert_not_called()


async def test_render_map_png_overlay_failure_uses_basemap_only_path():
    fake_basemap = Image.new("RGBA", (800, 500), (30, 80, 120, 255))

    with (
        patch(
            "src.api.map_renderer.get_geometry_data",
            new=AsyncMock(
                return_value={
                    "name": "Test AOI",
                    "geometry": GEOMETRY,
                }
            ),
        ),
        patch(
            "src.api.map_renderer._download_mapbox_basemap",
            new=AsyncMock(return_value=fake_basemap),
        ) as download_basemap,
        patch(
            "src.api.map_renderer._download_tiles",
            new=AsyncMock(return_value={}),
        ) as download_tiles,
        patch(
            "src.api.map_renderer._render_aoi_only_png",
            wraps=mr._render_aoi_only_png,
        ) as render_aoi_only,
    ):
        png = await render_map_png(
            aoi={"source": "gadm", "src_id": "BRA"},
            dataset={
                "dataset_name": "Dataset",
                "tile_url": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
            width_px=800,
            height_px=500,
            dpi=120,
        )

    assert png.startswith(b"\x89PNG")
    download_basemap.assert_awaited_once()
    download_tiles.assert_awaited_once()
    render_aoi_only.assert_not_called()


async def test_render_map_png_both_fail_falls_back_to_matplotlib():
    with (
        patch(
            "src.api.map_renderer.get_geometry_data",
            new=AsyncMock(
                return_value={
                    "name": "Test AOI",
                    "geometry": GEOMETRY,
                }
            ),
        ),
        patch(
            "src.api.map_renderer._download_mapbox_basemap",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.api.map_renderer._download_tiles",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.api.map_renderer._render_aoi_only_png",
            wraps=mr._render_aoi_only_png,
        ) as render_aoi_only,
    ):
        png = await render_map_png(
            aoi={"source": "gadm", "src_id": "BRA"},
            dataset={
                "dataset_name": "Dataset",
                "tile_url": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
            width_px=800,
            height_px=500,
            dpi=120,
        )

    assert png.startswith(b"\x89PNG")
    assert render_aoi_only.call_count == 1


async def test_render_map_png_missing_mapbox_token_still_returns_image():
    APISettings.mapbox_access_token = ""
    APISettings.mapbox_api_token = ""

    fake_tile = Image.new("RGBA", (256, 256), (10, 40, 200, 180))
    with (
        patch(
            "src.api.map_renderer.get_geometry_data",
            new=AsyncMock(
                return_value={
                    "name": "Test AOI",
                    "geometry": GEOMETRY,
                }
            ),
        ),
        patch(
            "src.api.map_renderer._download_tiles",
            new=AsyncMock(return_value={(0, 0): fake_tile}),
        ) as download_tiles,
    ):
        png = await render_map_png(
            aoi={"source": "gadm", "src_id": "BRA"},
            dataset={
                "dataset_name": "Dataset",
                "tile_url": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
            width_px=512,
            height_px=512,
            dpi=120,
        )

    assert png.startswith(b"\x89PNG")
    download_tiles.assert_awaited_once()


def test_render_map_png_caps_tile_requests_by_reducing_zoom():
    world_bounds = (-170.0, -70.0, 170.0, 70.0)
    zoom = _pick_zoom(
        bounds4326=world_bounds,
        width_px=1200,
        height_px=800,
        max_zoom=12,
    )
    x_min, x_max, y_min, y_max = _tile_grid(world_bounds, zoom)
    tile_count = (x_max - x_min + 1) * (y_max - y_min + 1)

    assert tile_count <= MAX_TILE_REQUESTS


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
