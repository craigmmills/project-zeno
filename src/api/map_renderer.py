import asyncio
import io
import math
import time
from typing import Any, Optional
from urllib.parse import urlparse

import geopandas as gpd
import httpx
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw

from src.api.config import APISettings
from src.shared.geocoding_helpers import get_geometry_data
from src.shared.logging_config import get_logger

logger = get_logger(__name__)

TILE_SIZE = 256
MAX_TILE_REQUESTS = 64
DEFAULT_MAX_ZOOM = 12
MERCATOR_MAX_LAT = 85.05112878

MAPBOX_STATIC_BASE_URL = "https://api.mapbox.com/styles/v1"
MAPBOX_MAX_DIMENSION = 1280
MAPBOX_DEFAULT_STYLE = "mapbox/dark-v11"


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


def _normalize_xyz_template(url: str) -> str:
    return (
        url.replace("{{z}}", "{z}")
        .replace("{{x}}", "{x}")
        .replace("{{y}}", "{y}")
    )


def _resolve_dataset_tile_url_details(
    dataset: Optional[dict[str, Any]],
) -> tuple[Optional[str], str, dict[str, Any]]:
    details = {
        "template_has_z": False,
        "template_has_x": False,
        "template_has_y": False,
        "template_normalized": False,
    }
    if not isinstance(dataset, dict):
        return None, "missing_tile_url", details

    raw = dataset.get("tile_url")
    if not isinstance(raw, str):
        return None, "missing_tile_url", details

    tile_url = raw.strip()
    if not tile_url:
        return None, "missing_tile_url", details

    normalized = _normalize_xyz_template(tile_url)
    details["template_normalized"] = normalized != tile_url
    details["template_has_z"] = "{z}" in normalized
    details["template_has_x"] = "{x}" in normalized
    details["template_has_y"] = "{y}" in normalized

    if not (
        details["template_has_z"]
        and details["template_has_x"]
        and details["template_has_y"]
    ):
        return None, "unresolved_xyz_placeholders", details

    if "{" in normalized or "}" in normalized:
        leftovers = ["{z}", "{x}", "{y}"]
        stripped = normalized
        for part in leftovers:
            stripped = stripped.replace(part, "")
        if "{" in stripped or "}" in stripped:
            return None, "malformed_template", details

    return normalized, "ok", details


def _resolve_dataset_tile_url(
    dataset: Optional[dict[str, Any]],
) -> Optional[str]:
    tile_url, _, _ = _resolve_dataset_tile_url_details(dataset)
    return tile_url


def _extract_tile_host(url_template: Optional[str]) -> Optional[str]:
    if not isinstance(url_template, str) or not url_template:
        return None
    try:
        return urlparse(url_template).netloc or None
    except Exception:
        return None


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


def _resolve_mapbox_token() -> Optional[str]:
    token = (APISettings.resolved_mapbox_token or "").strip()
    if not token:
        return None
    return token


def _resolve_mapbox_style() -> str:
    style = (APISettings.mapbox_style_id or "").strip()
    if not style:
        return MAPBOX_DEFAULT_STYLE
    return style


def _normalize_static_dims(
    width_px: int,
    height_px: int,
    scale: int,
) -> tuple[int, int, int]:
    target_w = max(width_px, 1)
    target_h = max(height_px, 1)
    resolved_scale = 2 if int(scale) == 2 else 1

    request_w = max(1, math.ceil(target_w / resolved_scale))
    request_h = max(1, math.ceil(target_h / resolved_scale))

    ratio = min(
        1.0,
        MAPBOX_MAX_DIMENSION / max(request_w, 1),
        MAPBOX_MAX_DIMENSION / max(request_h, 1),
    )
    request_w = max(1, int(math.floor(request_w * ratio)))
    request_h = max(1, int(math.floor(request_h * ratio)))

    return request_w, request_h, resolved_scale


def _build_mapbox_static_url(
    bounds4326: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    style_id: str,
    token: str,
    scale: int,
) -> str:
    min_lon, min_lat, max_lon, max_lat = bounds4326
    w, h, resolved_scale = _normalize_static_dims(width_px, height_px, scale)
    scale_suffix = "@2x" if resolved_scale == 2 else ""

    bbox = f"[{min_lon:.6f},{min_lat:.6f},{max_lon:.6f},{max_lat:.6f}]"
    base = f"{MAPBOX_STATIC_BASE_URL}/{style_id}/static/{bbox}/{w}x{h}{scale_suffix}"
    query = f"logo=false&attribution=false&access_token={token}"
    return f"{base}?{query}"


async def _download_mapbox_basemap(
    bounds4326: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
) -> Optional[Image.Image]:
    token = _resolve_mapbox_token()
    if not token:
        return None

    style_id = _resolve_mapbox_style()
    timeout_s = max(float(APISettings.mapbox_static_timeout_seconds), 0.1)
    scale = APISettings.mapbox_static_scale

    request_w, request_h, resolved_scale = _normalize_static_dims(
        width_px=width_px,
        height_px=height_px,
        scale=scale,
    )
    url = _build_mapbox_static_url(
        bounds4326=bounds4326,
        width_px=width_px,
        height_px=height_px,
        style_id=style_id,
        token=token,
        scale=resolved_scale,
    )

    timeout = httpx.Timeout(timeout_s)
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True
        ) as client:
            response = await client.get(url)
        response.raise_for_status()
    except Exception:
        return None

    try:
        basemap = Image.open(io.BytesIO(response.content)).convert("RGBA")
    except Exception:
        return None

    expected_w = request_w * resolved_scale
    expected_h = request_h * resolved_scale
    if basemap.size != (expected_w, expected_h):
        basemap = basemap.resize(
            (expected_w, expected_h),
            Image.Resampling.BILINEAR,
        )

    target_w = max(width_px, 1)
    target_h = max(height_px, 1)
    if basemap.size != (target_w, target_h):
        basemap = basemap.resize(
            (target_w, target_h),
            Image.Resampling.BILINEAR,
        )

    return basemap


def _create_blank_canvas(width_px: int, height_px: int) -> Image.Image:
    return Image.new(
        "RGBA",
        (max(width_px, 1), max(height_px, 1)),
        (0, 0, 0, 0),
    )


def _is_retryable_tile_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code >= 500
    return False


async def _download_tiles(
    url_template: str,
    z: int,
    x_range: range,
    y_range: range,
    timeout_s: float,
    concurrency: int,
    retries: int = 0,
) -> tuple[dict[tuple[int, int], Image.Image], dict[str, int]]:
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    timeout = httpx.Timeout(timeout_s)

    async def _fetch_tile(
        client: httpx.AsyncClient,
        x: int,
        y: int,
    ) -> tuple[tuple[int, int], Optional[Image.Image], int]:
        url = _tile_url(url_template, z=z, x=x, y=y)
        attempts = 0
        max_attempts = max(1, retries + 1)
        while attempts < max_attempts:
            attempts += 1
            try:
                async with semaphore:
                    response = await client.get(url)
                response.raise_for_status()
                image = Image.open(io.BytesIO(response.content)).convert(
                    "RGBA"
                )
                return (x, y), image, attempts
            except Exception as exc:
                if attempts >= max_attempts or not _is_retryable_tile_error(
                    exc
                ):
                    return (x, y), None, attempts

        return (x, y), None, attempts

    tiles: dict[tuple[int, int], Image.Image] = {}
    retry_attempts = 0
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True
    ) as client:
        tasks = [
            asyncio.create_task(_fetch_tile(client=client, x=x, y=y))
            for y in y_range
            for x in x_range
        ]
        for key, image, attempts in await asyncio.gather(*tasks):
            retry_attempts += max(0, attempts - 1)
            if image is not None:
                tiles[key] = image

    return tiles, {
        "retry_attempts": retry_attempts,
    }


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
    tile_size: int = TILE_SIZE,
) -> Image.Image:
    min_lon, min_lat, max_lon, max_lat = bounds4326

    tx_w, ty_n = _lonlat_to_tile_xy(min_lon, max_lat, z)
    tx_e, ty_s = _lonlat_to_tile_xy(max_lon, min_lat, z)

    left = int((tx_w - x_min) * tile_size)
    right = int((tx_e - x_min) * tile_size)
    top = int((ty_n - y_min) * tile_size)
    bottom = int((ty_s - y_min) * tile_size)

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


async def _render_data_overlay_image(
    dataset: Optional[dict[str, Any]],
    bounds: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    zoom: Optional[int] = None,
    tile_grid_result: Optional[tuple[int, int, int, int]] = None,
) -> tuple[Optional[Image.Image], dict[str, Any]]:
    started = time.perf_counter()
    overlay_timeout_s = max(
        float(APISettings.map_overlay_tile_timeout_seconds),
        0.1,
    )
    overlay_concurrency = max(int(APISettings.map_overlay_max_concurrency), 1)
    overlay_retries = max(int(APISettings.map_overlay_tile_retries), 0)

    dataset_name = ""
    dataset_id: str | int | None = None
    if isinstance(dataset, dict):
        dataset_name = str(dataset.get("dataset_name") or "").strip()
        raw_dataset_id = dataset.get("dataset_id")
        if isinstance(raw_dataset_id, (int, str)):
            dataset_id = raw_dataset_id

    metrics: dict[str, Any] = {
        "attempted": False,
        "used": False,
        "reason": None,
        "zoom": None,
        "zoom_selected": None,
        "zoom_fallback_used": False,
        "tile_count_requested": 0,
        "tile_count_success": 0,
        "tile_host": None,
        "dataset_name": dataset_name,
        "dataset_id": dataset_id,
        "template_has_z": False,
        "template_has_x": False,
        "template_has_y": False,
        "template_normalized": False,
        "overlay_timeout_s": overlay_timeout_s,
        "overlay_retries_configured": overlay_retries,
        "overlay_retries_attempted": 0,
        "overlay_max_concurrency": overlay_concurrency,
    }

    tile_template, resolve_reason, resolve_details = (
        _resolve_dataset_tile_url_details(dataset)
    )
    metrics.update(resolve_details)
    metrics["tile_host"] = _extract_tile_host(tile_template)

    if not tile_template:
        metrics["reason"] = resolve_reason
        metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return None, metrics

    try:
        metrics["attempted"] = True
        if zoom is None:
            zoom = _pick_zoom(
                bounds4326=bounds,
                width_px=width_px,
                height_px=height_px,
                max_zoom=DEFAULT_MAX_ZOOM,
            )

        selected_zoom = zoom
        metrics["zoom_selected"] = selected_zoom
        metrics["zoom"] = selected_zoom

        if tile_grid_result is not None:
            x_min, x_max, y_min, y_max = tile_grid_result
        else:
            x_min, x_max, y_min, y_max = _tile_grid(bounds, selected_zoom)
        requested_tiles = (x_max - x_min + 1) * (y_max - y_min + 1)

        download_result = await _download_tiles(
            url_template=tile_template,
            z=selected_zoom,
            x_range=range(x_min, x_max + 1),
            y_range=range(y_min, y_max + 1),
            timeout_s=overlay_timeout_s,
            concurrency=overlay_concurrency,
            retries=overlay_retries,
        )
        if isinstance(download_result, tuple):
            tiles, download_metrics = download_result
        else:
            tiles, download_metrics = download_result, {}

        metrics["tile_count_requested"] = requested_tiles
        metrics["tile_count_success"] = len(tiles)
        metrics["overlay_retries_attempted"] += int(
            download_metrics.get("retry_attempts", 0)
        )

        if metrics["tile_count_success"] == 0 and selected_zoom > 0:
            fallback_zoom = selected_zoom - 1
            x_min, x_max, y_min, y_max = _tile_grid(bounds, fallback_zoom)
            fallback_requested = (x_max - x_min + 1) * (y_max - y_min + 1)
            fallback_result = await _download_tiles(
                url_template=tile_template,
                z=fallback_zoom,
                x_range=range(x_min, x_max + 1),
                y_range=range(y_min, y_max + 1),
                timeout_s=overlay_timeout_s,
                concurrency=overlay_concurrency,
                retries=overlay_retries,
            )
            if isinstance(fallback_result, tuple):
                fallback_tiles, fallback_metrics = fallback_result
            else:
                fallback_tiles, fallback_metrics = fallback_result, {}

            metrics["overlay_retries_attempted"] += int(
                fallback_metrics.get("retry_attempts", 0)
            )
            metrics["zoom_fallback_used"] = True
            metrics["tile_count_requested"] += fallback_requested

            if fallback_tiles:
                tiles = fallback_tiles
                metrics["tile_count_success"] = len(fallback_tiles)
                metrics["zoom"] = fallback_zoom
            else:
                metrics["reason"] = "tile_download_total_failure"
                metrics["duration_ms"] = int(
                    (time.perf_counter() - started) * 1000
                )
                return None, metrics

        if metrics["tile_count_success"] == 0:
            metrics["reason"] = "tile_download_total_failure"
            metrics["duration_ms"] = int(
                (time.perf_counter() - started) * 1000
            )
            return None, metrics

        used_zoom = int(metrics.get("zoom") or selected_zoom)
        used_grid = (x_min, x_max, y_min, y_max)
        mosaic = _compose_mosaic(
            tiles=tiles,
            x_min=used_grid[0],
            x_max=used_grid[1],
            y_min=used_grid[2],
            y_max=used_grid[3],
            tile_size=TILE_SIZE,
        )
        cropped = _crop_mosaic_to_bounds(
            mosaic=mosaic,
            bounds4326=bounds,
            z=used_zoom,
            x_min=used_grid[0],
            y_min=used_grid[2],
        )
        overlay = cropped.resize(
            (max(width_px, 1), max(height_px, 1)),
            Image.Resampling.BILINEAR,
        )

        metrics["used"] = True
        if metrics["tile_count_success"] < metrics["tile_count_requested"]:
            metrics["reason"] = "tile_download_partial_failure"
        metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return overlay, metrics
    except Exception as exc:
        logger.warning(
            "Map overlay generation failed",
            error=str(exc),
            error_type=type(exc).__name__,
            dataset_name=dataset_name,
            dataset_id=dataset_id,
            tile_host=metrics.get("tile_host"),
        )
        metrics["reason"] = "overlay_exception"
        metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return None, metrics


def _build_mapbox_tile_template(
    style_id: str, token: str, scale: int
) -> str:
    scale_suffix = "@2x" if scale == 2 else ""
    return (
        f"https://api.mapbox.com/styles/v1/{style_id}"
        f"/tiles/256/{{z}}/{{x}}/{{y}}{scale_suffix}"
        f"?access_token={token}"
    )


async def _render_basemap_image(
    bounds: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    zoom: Optional[int] = None,
    tile_grid_result: Optional[tuple[int, int, int, int]] = None,
) -> tuple[Optional[Image.Image], dict[str, Any]]:
    started = time.perf_counter()
    token = _resolve_mapbox_token()
    style_id = _resolve_mapbox_style()
    timeout_s = max(float(APISettings.mapbox_static_timeout_seconds), 0.1)
    scale = APISettings.mapbox_static_scale

    metrics: dict[str, Any] = {
        "attempted": bool(token),
        "used": False,
        "reason": None,
        "mapbox_style_id": style_id,
        "mapbox_timeout_s": timeout_s,
    }

    if not token:
        metrics["reason"] = "missing_mapbox_token"
        metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return None, metrics

    try:
        if zoom is None:
            zoom = _pick_zoom(
                bounds4326=bounds,
                width_px=width_px,
                height_px=height_px,
                max_zoom=DEFAULT_MAX_ZOOM,
            )

        if tile_grid_result is None:
            tile_grid_result = _tile_grid(bounds, zoom)

        x_min, x_max, y_min, y_max = tile_grid_result
        tile_template = _build_mapbox_tile_template(
            style_id=style_id,
            token=token,
            scale=max(1, min(scale, 2)),
        )

        download_result = await _download_tiles(
            url_template=tile_template,
            z=zoom,
            x_range=range(x_min, x_max + 1),
            y_range=range(y_min, y_max + 1),
            timeout_s=timeout_s,
            concurrency=max(int(APISettings.map_overlay_max_concurrency), 1),
            retries=0,
        )
        if isinstance(download_result, tuple):
            tiles, _ = download_result
        else:
            tiles = download_result

        if not tiles:
            metrics["reason"] = "mapbox_tile_fetch_failed"
            metrics["duration_ms"] = int(
                (time.perf_counter() - started) * 1000
            )
            return None, metrics

        resolved_tile_size = TILE_SIZE * max(1, min(scale, 2))
        mosaic = _compose_mosaic(
            tiles=tiles,
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
            tile_size=resolved_tile_size,
        )
        cropped = _crop_mosaic_to_bounds(
            mosaic=mosaic,
            bounds4326=bounds,
            z=zoom,
            x_min=x_min,
            y_min=y_min,
            tile_size=resolved_tile_size,
        )
        basemap = cropped.resize(
            (max(width_px, 1), max(height_px, 1)),
            Image.Resampling.BILINEAR,
        )

        metrics["used"] = True
        metrics["duration_ms"] = int(
            (time.perf_counter() - started) * 1000
        )
        return basemap, metrics
    except Exception as exc:
        logger.warning(
            "Basemap tile fetch failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        metrics["reason"] = "mapbox_exception"
        metrics["duration_ms"] = int(
            (time.perf_counter() - started) * 1000
        )
        return None, metrics


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

    render_started = time.perf_counter()
    basemap_metrics: dict[str, Any] = {}
    overlay_metrics: dict[str, Any] = {}
    render_path = "aoi_only_matplotlib"

    zoom = _pick_zoom(
        bounds4326=bounds,
        width_px=width_px,
        height_px=height_px,
        max_zoom=DEFAULT_MAX_ZOOM,
    )
    grid = _tile_grid(bounds, zoom)

    try:
        (basemap_result, overlay_result) = await asyncio.gather(
            _render_basemap_image(
                bounds=bounds,
                width_px=width_px,
                height_px=height_px,
                zoom=zoom,
                tile_grid_result=grid,
            ),
            _render_data_overlay_image(
                dataset=dataset,
                bounds=bounds,
                width_px=width_px,
                height_px=height_px,
                zoom=zoom,
                tile_grid_result=grid,
            ),
        )
        basemap_image, basemap_metrics = basemap_result
        overlay_image, overlay_metrics = overlay_result

        if basemap_image is None and overlay_image is None:
            render_path = "aoi_only_matplotlib"
            return _render_aoi_only_png(
                gdf=gdf,
                bounds=bounds,
                dataset_name=dataset_name,
                aoi_name=aoi_name,
                width_px=width_px,
                height_px=height_px,
                dpi=max(dpi, 1),
            )

        if basemap_image is not None:
            canvas = basemap_image.copy()
        else:
            canvas = _create_blank_canvas(
                width_px=width_px, height_px=height_px
            )

        if overlay_image is not None:
            canvas.alpha_composite(overlay_image)

        _draw_aoi_boundary_on_image(
            image=canvas,
            gdf=gdf,
            bounds4326=bounds,
        )

        if basemap_image is not None and overlay_image is not None:
            render_path = "basemap_overlay_boundary"
        elif basemap_image is not None:
            render_path = "basemap_boundary"
        else:
            render_path = "overlay_boundary"

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()
    except MapRenderUnsupported:
        raise
    except Exception as exc:
        logger.exception(
            "Map render failed before fallback",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        try:
            render_path = "aoi_only_matplotlib"
            return _render_aoi_only_png(
                gdf=gdf,
                bounds=bounds,
                dataset_name=dataset_name,
                aoi_name=aoi_name,
                width_px=width_px,
                height_px=height_px,
                dpi=max(dpi, 1),
            )
        except Exception as fallback_exc:
            raise MapRenderError("map rendering failed") from fallback_exc
    finally:
        duration_ms = int((time.perf_counter() - render_started) * 1000)
        logger.info(
            "Map render completed",
            basemap_attempted=basemap_metrics.get("attempted", False),
            basemap_used=basemap_metrics.get("used", False),
            basemap_reason=basemap_metrics.get("reason"),
            mapbox_style_id=basemap_metrics.get(
                "mapbox_style_id", _resolve_mapbox_style()
            ),
            mapbox_timeout_s=basemap_metrics.get(
                "mapbox_timeout_s",
                max(float(APISettings.mapbox_static_timeout_seconds), 0.1),
            ),
            overlay_attempted=overlay_metrics.get("attempted", False),
            overlay_used=overlay_metrics.get("used", False),
            overlay_reason=overlay_metrics.get("reason"),
            overlay_dataset_name=overlay_metrics.get("dataset_name"),
            overlay_dataset_id=overlay_metrics.get("dataset_id"),
            overlay_tile_host=overlay_metrics.get("tile_host"),
            overlay_template_has_z=overlay_metrics.get("template_has_z"),
            overlay_template_has_x=overlay_metrics.get("template_has_x"),
            overlay_template_has_y=overlay_metrics.get("template_has_y"),
            overlay_template_normalized=overlay_metrics.get(
                "template_normalized"
            ),
            overlay_zoom_selected=overlay_metrics.get("zoom_selected"),
            overlay_zoom_used=overlay_metrics.get("zoom"),
            overlay_zoom_fallback_used=overlay_metrics.get(
                "zoom_fallback_used",
                False,
            ),
            overlay_timeout_s=overlay_metrics.get("overlay_timeout_s"),
            overlay_retries_configured=overlay_metrics.get(
                "overlay_retries_configured"
            ),
            overlay_retries_attempted=overlay_metrics.get(
                "overlay_retries_attempted"
            ),
            overlay_max_concurrency=overlay_metrics.get(
                "overlay_max_concurrency"
            ),
            tile_count_requested=overlay_metrics.get(
                "tile_count_requested", 0
            ),
            tile_count_success=overlay_metrics.get("tile_count_success", 0),
            basemap_duration_ms=basemap_metrics.get("duration_ms"),
            overlay_duration_ms=overlay_metrics.get("duration_ms"),
            render_duration_ms=duration_ms,
            render_path=render_path,
        )
