import io
from typing import Any, Optional

import geopandas as gpd
import matplotlib.pyplot as plt

from src.shared.geocoding_helpers import get_geometry_data


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

    return minx - x_pad, maxx + x_pad, miny - y_pad, maxy + y_pad


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
    minx, maxx, miny, maxy = _compute_plot_extent(gdf)

    fig = None
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

        aoi_name = str(geometry_data.get("name") or aoi.get("name") or "AOI")
        dataset_name = ""
        if isinstance(dataset, dict):
            dataset_name = str(dataset.get("dataset_name") or "").strip()

        title = aoi_name
        if dataset_name:
            title = f"{aoi_name} — {dataset_name}"

        ax.set_title(title, fontsize=11)

        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", dpi=dpi)
        return buf.getvalue()
    except MapRenderUnsupported:
        raise
    except Exception as exc:
        raise MapRenderError("map rendering failed") from exc
    finally:
        if fig is not None:
            plt.close(fig)
