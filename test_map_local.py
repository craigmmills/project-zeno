"""Quick local test to debug map rendering with basemap + data tiles."""
import asyncio
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.local", override=True)

from unittest.mock import AsyncMock, patch  # noqa: E402

from src.api.config import APISettings  # noqa: E402

TREE_COVER_LOSS_TILES = (
    "https://tiles.globalforestwatch.org/"
    "umd_tree_cover_loss/latest/dynamic/{z}/{x}/{y}.png"
    "?tree_cover_density_threshold=30&render_type=true_color"
)

SPAIN_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [-9.3, 36.0],
            [3.3, 36.0],
            [3.3, 43.8],
            [-9.3, 43.8],
            [-9.3, 36.0],
        ]
    ],
}

BRAZIL_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [-73.99, -33.77],
            [-34.79, -33.77],
            [-34.79, 5.27],
            [-73.99, 5.27],
            [-73.99, -33.77],
        ]
    ],
}


async def render_test(name, geometry, aoi, dataset, style):
    from src.api.map_renderer import render_map_png

    APISettings.mapbox_style_id = style

    with patch(
        "src.api.map_renderer.get_geometry_data",
        new=AsyncMock(
            return_value={
                "name": name,
                "geometry": geometry,
            }
        ),
    ):
        png_bytes = await render_map_png(
            aoi=aoi,
            dataset=dataset,
            width_px=800,
            height_px=600,
            dpi=120,
        )

    safe_name = name.lower().replace(" ", "_")
    safe_style = style.split("/")[-1]
    out = f"/tmp/test_map_{safe_name}_{safe_style}.png"
    with open(out, "wb") as f:
        f.write(png_bytes)
    print(f"  {len(png_bytes)} bytes -> {out}")
    return out


async def main():
    styles = [
        "mapbox/dark-v11",
        "mapbox/satellite-streets-v12",
        "mapbox/outdoors-v12",
    ]

    outputs = []

    for style in styles:
        print(f"\n=== Style: {style} ===")

        print(f"  Spain + tree cover loss ({style})")
        out = await render_test(
            name="Spain",
            geometry=SPAIN_GEOMETRY,
            aoi={"source": "gadm", "src_id": "ESP"},
            dataset={
                "dataset_name": "Tree cover loss",
                "tile_url": TREE_COVER_LOSS_TILES,
            },
            style=style,
        )
        outputs.append(out)

        print(f"  Brazil + tree cover loss ({style})")
        out = await render_test(
            name="Brazil",
            geometry=BRAZIL_GEOMETRY,
            aoi={"source": "gadm", "src_id": "BRA"},
            dataset={
                "dataset_name": "Tree cover loss",
                "tile_url": TREE_COVER_LOSS_TILES,
            },
            style=style,
        )
        outputs.append(out)

    print("\n--- Opening all images ---")
    for out in outputs:
        print(f"  {out}")


if __name__ == "__main__":
    asyncio.run(main())
