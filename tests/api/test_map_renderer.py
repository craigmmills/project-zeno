from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from src.api import map_renderer as mr
from src.api.config import APISettings
from src.api.map_renderer import (
    MAX_TILE_REQUESTS,
    MapRenderError,
    MapRenderUnsupported,
    _build_mapbox_tile_template,
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
    old_overlay_timeout = APISettings.map_overlay_tile_timeout_seconds
    old_overlay_retries = APISettings.map_overlay_tile_retries
    old_overlay_concurrency = APISettings.map_overlay_max_concurrency

    APISettings.mapbox_access_token = "test-mapbox-token"
    APISettings.mapbox_api_token = ""
    APISettings.mapbox_style_id = "mapbox/dark-v11"
    APISettings.mapbox_static_timeout_seconds = 2.5
    APISettings.mapbox_static_scale = 2
    APISettings.map_overlay_tile_timeout_seconds = 4.0
    APISettings.map_overlay_tile_retries = 1
    APISettings.map_overlay_max_concurrency = 8

    yield

    APISettings.mapbox_access_token = old_access
    APISettings.mapbox_api_token = old_api
    APISettings.mapbox_style_id = old_style
    APISettings.mapbox_static_timeout_seconds = old_timeout
    APISettings.mapbox_static_scale = old_scale
    APISettings.map_overlay_tile_timeout_seconds = old_overlay_timeout
    APISettings.map_overlay_tile_retries = old_overlay_retries
    APISettings.map_overlay_max_concurrency = old_overlay_concurrency


def _mock_geometry():
    return patch(
        "src.api.map_renderer.get_geometry_data",
        new=AsyncMock(
            return_value={
                "name": "Test AOI",
                "geometry": GEOMETRY,
            }
        ),
    )


# ── Unit tests: tile template construction ──


def test_build_mapbox_tile_template_2x_scale():
    url = _build_mapbox_tile_template(
        style_id="mapbox/dark-v11",
        token="tok_abc",
        scale=2,
    )
    assert "mapbox/dark-v11" in url
    assert "@2x" in url
    assert "access_token=tok_abc" in url
    assert "{z}" in url and "{x}" in url and "{y}" in url


def test_build_mapbox_tile_template_1x_scale():
    url = _build_mapbox_tile_template(
        style_id="mapbox/outdoors-v12",
        token="tok_xyz",
        scale=1,
    )
    assert "mapbox/outdoors-v12" in url
    assert "@2x" not in url
    assert "access_token=tok_xyz" in url


def test_resolve_dataset_tile_url_accepts_standard_xyz_template():
    tile_url = mr._resolve_dataset_tile_url(
        {"tile_url": "https://tiles.example.com/{z}/{x}/{y}.png"}
    )

    assert tile_url == "https://tiles.example.com/{z}/{x}/{y}.png"


def test_resolve_dataset_tile_url_accepts_double_brace_template():
    tile_url = mr._resolve_dataset_tile_url(
        {"tile_url": "https://tiles.example.com/{{z}}/{{x}}/{{y}}.png"}
    )

    assert tile_url == "https://tiles.example.com/{z}/{x}/{y}.png"


def test_resolve_dataset_tile_url_rejects_missing_xyz_placeholders():
    tile_url = mr._resolve_dataset_tile_url(
        {"tile_url": "https://tiles.example.com/no-xyz.png"}
    )

    assert tile_url is None


def test_resolve_dataset_tile_url_accepts_grasslands_pattern():
    tile_url = mr._resolve_dataset_tile_url(
        {
            "tile_url": (
                "https://tiles.globalforestwatch.org/"
                "v1/grasslands/2020/{z}/{x}/{y}.png"
            )
        }
    )

    assert tile_url is not None


# ── Unit tests: zoom and tile grid ──


def test_pick_zoom_caps_tile_requests():
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


# ── Integration tests: render_map_png composition ──


async def test_render_map_png_with_polygon_returns_png_bytes():
    with _mock_geometry():
        png = await render_map_png(
            aoi={"source": "gadm", "src_id": "BRA"},
            dataset={"dataset_name": "Dataset"},
            width_px=800,
            height_px=500,
            dpi=120,
        )

    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG")


async def test_render_map_png_uses_basemap_and_overlay():
    fake_basemap = Image.new("RGBA", (800, 500), (30, 80, 120, 255))
    fake_overlay = Image.new("RGBA", (800, 500), (255, 10, 10, 100))

    with (
        _mock_geometry(),
        patch(
            "src.api.map_renderer._render_basemap_image",
            new=AsyncMock(
                return_value=(
                    fake_basemap,
                    {"attempted": True, "used": True},
                )
            ),
        ),
        patch(
            "src.api.map_renderer._render_data_overlay_image",
            new=AsyncMock(
                return_value=(
                    fake_overlay,
                    {"attempted": True, "used": True},
                )
            ),
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
    render_aoi_only.assert_not_called()


async def test_render_map_png_basemap_failure_uses_overlay_only():
    fake_overlay = Image.new("RGBA", (800, 500), (10, 40, 200, 100))

    with (
        _mock_geometry(),
        patch(
            "src.api.map_renderer._render_basemap_image",
            new=AsyncMock(
                return_value=(
                    None,
                    {
                        "attempted": True,
                        "used": False,
                        "reason": "mapbox_tile_fetch_failed",
                    },
                )
            ),
        ),
        patch(
            "src.api.map_renderer._render_data_overlay_image",
            new=AsyncMock(
                return_value=(
                    fake_overlay,
                    {"attempted": True, "used": True},
                )
            ),
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
    render_aoi_only.assert_not_called()


async def test_render_map_png_overlay_failure_uses_basemap_only():
    fake_basemap = Image.new("RGBA", (800, 500), (30, 80, 120, 255))

    with (
        _mock_geometry(),
        patch(
            "src.api.map_renderer._render_basemap_image",
            new=AsyncMock(
                return_value=(
                    fake_basemap,
                    {"attempted": True, "used": True},
                )
            ),
        ),
        patch(
            "src.api.map_renderer._render_data_overlay_image",
            new=AsyncMock(
                return_value=(
                    None,
                    {
                        "attempted": True,
                        "used": False,
                        "reason": "tile_download_total_failure",
                    },
                )
            ),
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
    render_aoi_only.assert_not_called()


async def test_overlay_timeout_retry_success_uses_overlay():
    fake_tile = Image.new("RGBA", (256, 256), (10, 120, 10, 180))

    with patch(
        "src.api.map_renderer._download_tiles",
        new=AsyncMock(
            return_value=({(0, 0): fake_tile}, {"retry_attempts": 1})
        ),
    ):
        overlay, metrics = await mr._render_data_overlay_image(
            dataset={
                "dataset_name": "Grasslands",
                "tile_url": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
            bounds=(-55.0, -10.0, -54.0, -9.0),
            width_px=300,
            height_px=200,
            zoom=0,
            tile_grid_result=(0, 0, 0, 0),
        )

    assert overlay is not None
    assert metrics["used"] is True
    assert metrics["overlay_retries_attempted"] == 1


async def test_overlay_zero_success_falls_back_to_lower_zoom():
    fake_tile = Image.new("RGBA", (256, 256), (10, 120, 10, 180))

    with patch(
        "src.api.map_renderer._download_tiles",
        new=AsyncMock(
            side_effect=[
                ({}, {"retry_attempts": 0}),
                ({(0, 0): fake_tile}, {"retry_attempts": 0}),
            ]
        ),
    ) as mock_download:
        overlay, metrics = await mr._render_data_overlay_image(
            dataset={
                "dataset_name": "Grasslands",
                "tile_url": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
            bounds=(-55.0, -10.0, -54.0, -9.0),
            width_px=300,
            height_px=200,
            zoom=2,
            tile_grid_result=(0, 0, 0, 0),
        )

    assert overlay is not None
    assert metrics["used"] is True
    assert metrics["zoom_fallback_used"] is True
    assert metrics["zoom"] == 1
    assert mock_download.await_count == 2

    first_call = mock_download.await_args_list[0].kwargs
    second_call = mock_download.await_args_list[1].kwargs
    assert first_call["z"] == 2
    assert second_call["z"] == 1


async def test_render_map_png_overlay_persistent_failure_falls_back_to_aoi_png():
    old_access = APISettings.mapbox_access_token
    old_api = APISettings.mapbox_api_token
    APISettings.mapbox_access_token = ""
    APISettings.mapbox_api_token = ""

    try:
        with (
            _mock_geometry(),
            patch(
                "src.api.map_renderer._download_tiles",
                new=AsyncMock(return_value=({}, {"retry_attempts": 1})),
            ),
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
    finally:
        APISettings.mapbox_access_token = old_access
        APISettings.mapbox_api_token = old_api

    assert png.startswith(b"\x89PNG")


async def test_render_map_png_both_fail_falls_back_to_matplotlib():
    with (
        _mock_geometry(),
        patch(
            "src.api.map_renderer._render_basemap_image",
            new=AsyncMock(
                return_value=(
                    None,
                    {"attempted": True, "used": False},
                )
            ),
        ),
        patch(
            "src.api.map_renderer._render_data_overlay_image",
            new=AsyncMock(
                return_value=(
                    None,
                    {"attempted": True, "used": False},
                )
            ),
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

    fake_overlay = Image.new("RGBA", (512, 512), (10, 40, 200, 100))

    with (
        _mock_geometry(),
        patch(
            "src.api.map_renderer._render_data_overlay_image",
            new=AsyncMock(
                return_value=(
                    fake_overlay,
                    {"attempted": True, "used": True},
                )
            ),
        ),
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


# ── Error handling ──


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
                "geometry": {
                    "type": "FeatureCollection",
                    "features": [],
                },
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


# ── Alignment: basemap and overlay share the same tile grid ──


async def test_basemap_and_overlay_share_zoom_and_grid():
    """render_map_png computes zoom and tile grid once and passes them
    to both _render_basemap_image and _render_data_overlay_image,
    guaranteeing pixel-perfect alignment."""
    captured = []

    async def _capture_basemap(
        bounds, width_px, height_px, zoom=None, tile_grid_result=None
    ):
        captured.append(
            (
                "basemap",
                {
                    "bounds": bounds,
                    "zoom": zoom,
                    "tile_grid_result": tile_grid_result,
                },
            )
        )
        img = Image.new("RGBA", (width_px, height_px), (30, 80, 120, 255))
        return img, {"attempted": True, "used": True}

    async def _capture_overlay(
        dataset,
        bounds,
        width_px,
        height_px,
        zoom=None,
        tile_grid_result=None,
    ):
        captured.append(
            (
                "overlay",
                {
                    "bounds": bounds,
                    "zoom": zoom,
                    "tile_grid_result": tile_grid_result,
                },
            )
        )
        img = Image.new("RGBA", (width_px, height_px), (255, 10, 10, 100))
        return img, {"attempted": True, "used": True}

    with (
        _mock_geometry(),
        patch(
            "src.api.map_renderer._render_basemap_image",
            side_effect=_capture_basemap,
        ),
        patch(
            "src.api.map_renderer._render_data_overlay_image",
            side_effect=_capture_overlay,
        ),
    ):
        await render_map_png(
            aoi={"source": "gadm", "src_id": "BRA"},
            dataset={
                "dataset_name": "Dataset",
                "tile_url": "https://tiles.example.com/{z}/{x}/{y}.png",
            },
            width_px=800,
            height_px=500,
            dpi=120,
        )

    assert len(captured) == 2
    basemap_args = captured[0][1]
    overlay_args = captured[1][1]

    # Both receive the same pre-computed zoom
    assert basemap_args["zoom"] is not None
    assert basemap_args["zoom"] == overlay_args["zoom"]

    # Both receive the same pre-computed tile grid
    assert basemap_args["tile_grid_result"] is not None
    assert (
        basemap_args["tile_grid_result"]
        == overlay_args["tile_grid_result"]
    )

    # Both receive the same bounds
    assert basemap_args["bounds"] == overlay_args["bounds"]


async def test_render_basemap_missing_token_returns_none():
    """_render_basemap_image returns None immediately when no Mapbox
    token is configured."""
    APISettings.mapbox_access_token = ""
    APISettings.mapbox_api_token = ""

    result_image, metrics = await mr._render_basemap_image(
        bounds=(-55.0, -10.0, -54.0, -9.0),
        width_px=800,
        height_px=500,
    )

    assert result_image is None
    assert metrics["attempted"] is False
    assert metrics["reason"] == "missing_mapbox_token"


async def test_render_basemap_uses_mapbox_tile_pipeline():
    """_render_basemap_image fetches tiles through _download_tiles
    using a Mapbox raster tile URL template."""
    fake_tile = Image.new("RGBA", (512, 512), (40, 40, 60, 255))

    with patch(
        "src.api.map_renderer._download_tiles",
        new=AsyncMock(return_value={(0, 0): fake_tile}),
    ) as mock_download:
        result_image, metrics = await mr._render_basemap_image(
            bounds=(-55.0, -10.0, -54.0, -9.0),
            width_px=800,
            height_px=500,
        )

    assert result_image is not None
    assert metrics["used"] is True

    mock_download.assert_awaited_once()
    call_kwargs = mock_download.call_args
    url_template = call_kwargs.kwargs.get(
        "url_template"
    ) or call_kwargs.args[0]
    assert "api.mapbox.com" in url_template
    assert "dark-v11" in url_template
    assert "@2x" in url_template
