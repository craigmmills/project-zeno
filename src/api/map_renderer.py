import asyncio
import io
import math
import time
from typing import Any, Optional

import geopandas as gpd
import httpx
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw

from src.shared.geocoding_helpers import get_geometry_data
from src.shared.logging_config import get_logger

logger = get_logger(__name__)

TILE_SIZE = 256
MAX_TILE_REQUESTS = 64
TILE_TIMEOUT_SECONDS = 1.5
MAX_TILE_CONCURRENCY = 8
DEFAULT_MAX_ZOOM = 12
MERCATOR_MAX_LAT = 85.05112878


class MapRenderError(Exception):
    """Raised when map rendering fails unexpectedly."""


class MapRenderUnsupported(Exception):
    """Raised when map rendering is unsupported for the provided AOI."""


def _geojson_to_geodataframe(geometry: dict) -> gpd.GeoDataFrame:
    if not isinstance(geometry, dict):
        raise MapRenderError("geometry must be a GeoJSON object")

    geometry_type = geometry.get("type")
    features: list[dict[str, Any]] = []

    if geometry_type == "FeatureCollection":
        features = geometry.get("features") or []
    elif geometry_type == "Feature":
        features = [geometry]
    elif geometry_type == "GeometryCollection":
        geoms = geometry.get("geometries") or []
        features = [
            {"type": "Feature", "geometry": item, "properties": {}}
            for item in geoms
        ]
    elif geometry_type:
        features = [
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {},
            }
        ]

    if not features:
        raise MapRenderUnsupported("empty geometry")

    try:
        gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    except Exception as exc:
        raise MapRenderError("invalid GeoJSON geometry") from exc

    if gdf.empty:
        raise MapRenderUnsupported("empty geometry")

    if gdf.geometry.is_empty.all():
        raise MapRenderUnsupported("empty geometry")

    return gdf


def _compute_plot_extent(
    gdf: gpd.GeoDataFrame,
    pad_ratio: float = 0.1,
) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = gdf.total_bounds

    if minx == maxx:
        minx -= 0.1
        maxx += 0.1
    if miny == maxy:
        miny -= 0.1
        maxy += 0.1

    width = maxx - minx
    height = maxy - miny

    x_pad = width * pad_ratio
    y_pad = height * pad_ratio

    return minx - x_pad, miny - y_pad, maxx + x_pad, maxy + y_pad


def _resolve_dataset_tile_url(
    dataset: Optional[dict[str, Any]],
) -> Optional[str]:
    if not isinstance(dataset, dict):
        return None

    raw = dataset.get("tile_url")
    if not isinstance(raw, str):
        return None

    tile_url = raw.strip()
    if not tile_url:
        return None

    if "{z}" in tile_url and "{x}" in tile_url and "{y}" in tile_url:
        return tile_url

    normalized = _normalize_xyz_template(tile_url)
    if "{z}" in normalized and "{x}" in normalized and "{y}" in normalized:
        return normalized

    return None


def _normalize_xyz_template(url: str) -> str:
    return (
        url.replace("{{z}}", "{z}")
        .replace("{{x}}", "{x}")
        .replace("{{y}}", "{y}")
    )


def _clamp_lat(lat: float) -> float:
    return max(-MERCATOR_MAX_LAT, min(MERCATOR_MAX_LAT, lat))


def _lonlat_to_tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    lat_clamped = _clamp_lat(lat)
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat_clamped)
    y = (
        (1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi)
        / 2.0
        * n
    )
    return x, y


def _tile_grid(
    bounds4326: tuple[float, float, float, float],
    z: int,
) -> tuple[int, int, int, int]:
    min_lon, min_lat, max_lon, max_lat = bounds4326

    tx0, ty0 = _lonlat_to_tile_xy(min_lon, max_lat, z)
    tx1, ty1 = _lonlat_to_tile_xy(max_lon, min_lat, z)

    n = 2**z
    x_min = max(0, min(n - 1, int(math.floor(min(tx0, tx1)))))
    x_max = max(0, min(n - 1, int(math.floor(max(tx0, tx1) - 1e-9))))
    y_min = max(0, min(n - 1, int(math.floor(min(ty0, ty1)))))
    y_max = max(0, min(n - 1, int(math.floor(max(ty0, ty1) - 1e-9))))

    x_max = max(x_max, x_min)
    y_max = max(y_max, y_min)
    return x_min, x_max, y_min, y_max


def _pick_zoom(
    bounds4326: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    max_zoom: int = DEFAULT_MAX_ZOOM,
) -> int:
    min_lon, min_lat, max_lon, max_lat = bounds4326
    lon_span = max(max_lon - min_lon, 1e-6)
    lat_span = max(max_lat - min_lat, 1e-6)
    target_deg_per_px = max(
        lon_span / max(width_px, 1),
        lat_span / max(height_px, 1),
    )

    candidate = 0
    for z in range(max_zoom, -1, -1):
        zoom_deg_per_px = 360.0 / (TILE_SIZE * (2**z))
        if zoom_deg_per_px <= target_deg_per_px:
            candidate = z
            break

    zoom = candidate
    while zoom > 0:
        x_min, x_max, y_min, y_max = _tile_grid(bounds4326, zoom)
        count = (x_max - x_min + 1) * (y_max - y_min + 1)
        if count <= MAX_TILE_REQUESTS:
            return zoom
        zoom -= 1

    return 0


def _tile_url(url_template: str, z: int, x: int, y: int) -> str:
    return url_template.format(z=z, x=x, y=y)


async def _download_tiles(
    url_template: str,
    z: int,
    x_range: range,
    y_range: range,
    timeout_s: float,
    concurrency: int,
) -> dict[tuple[int, int], Image.Image]:
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    timeout = httpx.Timeout(timeout_s)

    async def _fetch_tile(
        client: httpx.AsyncClient,
        x: int,
        y: int,
    ) -> tuple[tuple[int, int], Optional[Image.Image]]:
        url = _tile_url(url_template, z=z, x=x, y=y)
        try:
            async with semaphore:
                response = await client.get(url)
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content)).convert("RGBA")
            return (x, y), image
        except Exception:
            return (x, y), None

    tiles: dict[tuple[int, int], Image.Image] = {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            asyncio.create_task(_fetch_tile(client=client, x=x, y=y))
            for y in y_range
            for x in x_range
        ]
        for key, image in await asyncio.gather(*tasks):
            if image is not None:
                tiles[key] = image

    return tiles


def _compose_mosaic(
    tiles: dict[tuple[int, int], Image.Image],
    x_min: int,
    x_max: int,
    y_min: int,
    y_max: int,
    tile_size: int = TILE_SIZE,
) -> Image.Image:
    cols = x_max - x_min + 1
    rows = y_max - y_min + 1
    mosaic = Image.new(
        "RGBA", (cols * tile_size, rows * tile_size), (0, 0, 0, 0)
    )

    for (x, y), tile in tiles.items():
        paste_x = (x - x_min) * tile_size
        paste_y = (y - y_min) * tile_size
        if tile.size != (tile_size, tile_size):
            tile = tile.resize(
                (tile_size, tile_size), Image.Resampling.BILINEAR
            )
        mosaic.paste(tile, (paste_x, paste_y))

    return mosaic


def _crop_mosaic_to_bounds(
    mosaic: Image.Image,
    bounds4326: tuple[float, float, float, float],
    z: int,
    x_min: int,
    y_min: int,
) -> Image.Image:
    min_lon, min_lat, max_lon, max_lat = bounds4326

    tx_w, ty_n = _lonlat_to_tile_xy(min_lon, max_lat, z)
    tx_e, ty_s = _lonlat_to_tile_xy(max_lon, min_lat, z)

    left = int((tx_w - x_min) * TILE_SIZE)
    right = int((tx_e - x_min) * TILE_SIZE)
    top = int((ty_n - y_min) * TILE_SIZE)
    bottom = int((ty_s - y_min) * TILE_SIZE)

    left = max(0, min(left, mosaic.width - 1))
    right = max(left + 1, min(right, mosaic.width))
    top = max(0, min(top, mosaic.height - 1))
    bottom = max(top + 1, min(bottom, mosaic.height))

    return mosaic.crop((left, top, right, bottom))


def _draw_geometry_lines(
    draw: ImageDraw.ImageDraw,
    geometry: Any,
    bounds4326: tuple[float, float, float, float],
    image_w: int,
    image_h: int,
):
    min_lon, min_lat, max_lon, max_lat = bounds4326
    lon_span = max(max_lon - min_lon, 1e-9)
    lat_span = max(max_lat - min_lat, 1e-9)

    def _project(lon: float, lat: float) -> tuple[float, float]:
        px = (lon - min_lon) / lon_span * image_w
        py = (max_lat - lat) / lat_span * image_h
        return px, py

    geom_type = getattr(geometry, "geom_type", "")
    if geom_type == "LineString":
        points = [_project(lon, lat) for lon, lat in geometry.coords]
        if len(points) >= 2:
            draw.line(
                points, fill=(255, 255, 255, 235), width=5, joint="curve"
            )
            draw.line(points, fill=(27, 94, 32, 255), width=3, joint="curve")
        return

    if geom_type == "MultiLineString":
        for line in geometry.geoms:
            _draw_geometry_lines(draw, line, bounds4326, image_w, image_h)
        return

    if geom_type in {"Polygon", "MultiPolygon"}:
        _draw_geometry_lines(
            draw,
            geometry.boundary,
            bounds4326,
            image_w,
            image_h,
        )


def _draw_aoi_boundary_on_image(
    image: Image.Image,
    gdf: gpd.GeoDataFrame,
    bounds4326: tuple[float, float, float, float],
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        _draw_geometry_lines(
            draw=draw,
            geometry=geom,
            bounds4326=bounds4326,
            image_w=image.width,
            image_h=image.height,
        )


def _render_aoi_only_png(
    gdf: gpd.GeoDataFrame,
    bounds: tuple[float, float, float, float],
    dataset_name: str,
    aoi_name: str,
    width_px: int,
    height_px: int,
    dpi: int,
) -> bytes:
    fig = None
    minx, miny, maxx, maxy = bounds
    try:
        fig_w = max(width_px, 1) / max(dpi, 1)
        fig_h = max(height_px, 1) / max(dpi, 1)
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)

        gdf.plot(
            ax=ax,
            facecolor="#8FD19E",
            edgecolor="#1B5E20",
            linewidth=1.2,
            alpha=0.85,
        )

        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_xlabel("")
        ax.set_ylabel("")

        title = aoi_name
        if dataset_name:
            title = f"{aoi_name} — {dataset_name}"
        ax.set_title(title, fontsize=11)

        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", dpi=dpi)
        return buf.getvalue()
    finally:
        if fig is not None:
            plt.close(fig)


async def render_map_png(
    aoi: dict,
    dataset: Optional[dict],
    width_px: int,
    height_px: int,
    dpi: int,
) -> bytes:
    if not isinstance(aoi, dict):
        raise MapRenderUnsupported("missing AOI")

    source = aoi.get("source")
    src_id = aoi.get("src_id")
    if not source or not src_id:
        raise MapRenderUnsupported("AOI missing source/src_id")

    geometry_data = await get_geometry_data(source=source, src_id=src_id)
    if not geometry_data:
        raise MapRenderUnsupported("AOI geometry not found")

    geometry = geometry_data.get("geometry")
    if not geometry:
        raise MapRenderUnsupported("AOI geometry unavailable")

    gdf = _geojson_to_geodataframe(geometry)
    bounds = _compute_plot_extent(gdf)

    aoi_name = str(geometry_data.get("name") or aoi.get("name") or "AOI")
    dataset_name = ""
    if isinstance(dataset, dict):
        dataset_name = str(dataset.get("dataset_name") or "").strip()

    overlay_attempted = False
    overlay_used = False
    zoom: Optional[int] = None
    tile_count_requested = 0
    tile_count_success = 0
    fallback_reason: Optional[str] = None
    overlay_started = time.perf_counter()

    try:
        tile_template = _resolve_dataset_tile_url(dataset)
        if tile_template:
            overlay_attempted = True
            tile_template = _normalize_xyz_template(tile_template)
            zoom = _pick_zoom(
                bounds4326=bounds,
                width_px=width_px,
                height_px=height_px,
                max_zoom=DEFAULT_MAX_ZOOM,
            )
            x_min, x_max, y_min, y_max = _tile_grid(bounds, zoom)
            tile_count_requested = (x_max - x_min + 1) * (y_max - y_min + 1)

            tiles = await _download_tiles(
                url_template=tile_template,
                z=zoom,
                x_range=range(x_min, x_max + 1),
                y_range=range(y_min, y_max + 1),
                timeout_s=TILE_TIMEOUT_SECONDS,
                concurrency=MAX_TILE_CONCURRENCY,
            )
            tile_count_success = len(tiles)

            if tile_count_success == 0:
                fallback_reason = "tile_download_total_failure"
            else:
                mosaic = _compose_mosaic(
                    tiles=tiles,
                    x_min=x_min,
                    x_max=x_max,
                    y_min=y_min,
                    y_max=y_max,
                    tile_size=TILE_SIZE,
                )
                cropped = _crop_mosaic_to_bounds(
                    mosaic=mosaic,
                    bounds4326=bounds,
                    z=zoom,
                    x_min=x_min,
                    y_min=y_min,
                )
                resized = cropped.resize(
                    (max(width_px, 1), max(height_px, 1)),
                    Image.Resampling.BILINEAR,
                )
                _draw_aoi_boundary_on_image(
                    image=resized,
                    gdf=gdf,
                    bounds4326=bounds,
                )

                buf = io.BytesIO()
                resized.save(buf, format="PNG")
                overlay_used = True
                return buf.getvalue()
        else:
            fallback_reason = "missing_tile_url"

        if overlay_attempted and fallback_reason is None:
            fallback_reason = "overlay_unavailable"

        return _render_aoi_only_png(
            gdf=gdf,
            bounds=bounds,
            dataset_name=dataset_name,
            aoi_name=aoi_name,
            width_px=width_px,
            height_px=height_px,
            dpi=dpi,
        )
    except MapRenderUnsupported:
        raise
    except Exception as exc:
        logger.exception(
            "Map overlay render failed; falling back to AOI-only",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        try:
            fallback_reason = fallback_reason or "overlay_exception"
            return _render_aoi_only_png(
                gdf=gdf,
                bounds=bounds,
                dataset_name=dataset_name,
                aoi_name=aoi_name,
                width_px=width_px,
                height_px=height_px,
                dpi=dpi,
            )
        except Exception as fallback_exc:
            raise MapRenderError("map rendering failed") from fallback_exc
    finally:
        overlay_duration_ms = int(
            (time.perf_counter() - overlay_started) * 1000
        )
        logger.info(
            "Map render completed",
            overlay_attempted=overlay_attempted,
            overlay_used=overlay_used,
            zoom=zoom,
            tile_count_requested=tile_count_requested,
            tile_count_success=tile_count_success,
            overlay_duration_ms=overlay_duration_ms,
            fallback_reason=fallback_reason,
        )
