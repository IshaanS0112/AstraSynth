#!/usr/bin/env python3
"""Turn a real USGS Mars DEM into an AstraSynth-ready terrain tile.

Dataset
-------
Mars MGS MOLA - MEX HRSC Blended DEM Global 200m v2
USGS Astrogeology Science Center, published 2018-01-31.

  Catalogue page:
  https://astrogeology.usgs.gov/search/map/mars_mgs_mola_mex_hrsc_blended_dem_global_200m

  Citation (required by the dataset's access constraints):
  Fergason, R. L., Hare, T. M., & Laura, J. (2018). HRSC and MOLA Blended
  Digital Elevation Model at 200m v2. Astrogeology PDS Annex, U.S. Geological
  Survey.

  Grid: 106694 x 53347, 16-bit, 200 m/pixel, simple cylindrical,
  planetocentric latitude, positive-east longitude, -180..180 domain.

Why this script does not download the DEM for you
-------------------------------------------------
The global product is ~11 GB. Vendoring it, or silently pulling it on first
run, is not a reasonable thing for a repository to do. Download it once from
the catalogue page above, then point this script at the file: it crops a
region, rescales the 16-bit elevations to the 8-bit range the pipeline expects,
and writes both the tile and a sidecar JSON recording the true elevation range
so ``elevation_range_m`` is set from the data rather than guessed.

Usage
-----
    # Crop a 512x512 tile (about 102 km square at 200 m/px) centred on
    # Gale Crater, where Curiosity landed (-5.4 N, 137.8 E)
    python scripts/prepare_usgs_terrain.py \
        --source ~/Downloads/Mars_HRSC_MOLA_BlendDEM_Global_200mp_v2.tif \
        --lat -5.4 --lon 137.8 --size 512

    # Or crop by raw pixel offset if you already know where you are
    python scripts/prepare_usgs_terrain.py --source <tif> --px 62000 --py 26000

Reading an 11 GB GeoTIFF needs a windowed reader. ``rasterio`` is used if
present (``pip install rasterio``); otherwise the script falls back to GDAL,
and if neither is installed it explains what to install rather than dying with
an ImportError.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# Grid geometry of the 200 m/px global product, from the USGS catalogue entry.
DEM_COLUMNS = 106694
DEM_ROWS = 53347
DEM_METERS_PER_PIXEL = 200.0
CITATION = (
    "Fergason, R. L., Hare, T. M., & Laura, J. (2018). HRSC and MOLA Blended "
    "Digital Elevation Model at 200m v2. Astrogeology PDS Annex, "
    "U.S. Geological Survey."
)


def lonlat_to_pixel(lon: float, lat: float) -> tuple[int, int]:
    """Simple-cylindrical lon/lat -> pixel column/row on the global grid."""
    if not -180 <= lon <= 180:
        raise ValueError("Longitude must be in [-180, 180] (positive east)")
    if not -90 <= lat <= 90:
        raise ValueError("Latitude must be in [-90, 90] (planetocentric)")
    column = int(round((lon + 180.0) / 360.0 * DEM_COLUMNS))
    row = int(round((90.0 - lat) / 180.0 * DEM_ROWS))
    return column, row


def read_window(source: Path, px: int, py: int, size: int) -> np.ndarray:
    """Read a ``size`` x ``size`` window without loading the whole raster."""
    half = size // 2
    col_off = max(0, min(DEM_COLUMNS - size, px - half))
    row_off = max(0, min(DEM_ROWS - size, py - half))

    try:
        import rasterio
        from rasterio.windows import Window

        with rasterio.open(source) as dataset:
            return dataset.read(1, window=Window(col_off, row_off, size, size))
    except ImportError:
        pass

    try:
        from osgeo import gdal
    except ImportError:
        raise SystemExit(
            "Windowed GeoTIFF reading needs rasterio or GDAL.\n"
            "  pip install rasterio\n"
            "Both read only the requested window, which matters here: the "
            "source raster is ~11 GB."
        ) from None

    dataset = gdal.Open(str(source))
    if dataset is None:
        raise SystemExit(f"GDAL could not open {source}")
    return dataset.GetRasterBand(1).ReadAsArray(col_off, row_off, size, size)


def to_uint8(elevation: np.ndarray) -> tuple[np.ndarray, dict]:
    """Rescale real elevations to 0-255, recording the true range.

    The pipeline reads intensity as a linear proxy for elevation, so the
    ``elevation_range_m`` it is configured with must be the actual relief of
    this tile - otherwise every slope angle downstream is wrong by a constant
    factor. That number is written to the sidecar JSON.
    """
    values = elevation.astype(np.float64)
    # MOLA/HRSC no-data is a large negative sentinel; exclude it from the range.
    valid = values[values > -100000]
    if valid.size == 0:
        raise SystemExit("Window contains no valid elevation data - check lat/lon.")

    low, high = float(valid.min()), float(valid.max())
    relief = high - low
    if relief < 1e-6:
        raise SystemExit("Window is perfectly flat; pick a different region.")

    clipped = np.clip(values, low, high)
    scaled = ((clipped - low) / relief * 255.0).astype(np.uint8)

    return scaled, {
        "min_elevation_m": round(low, 2),
        "max_elevation_m": round(high, 2),
        "elevation_range_m": round(relief, 2),
        "meters_per_pixel": DEM_METERS_PER_PIXEL,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="Path to the downloaded global DEM GeoTIFF")
    parser.add_argument("--lat", type=float, help="Centre latitude (planetocentric)")
    parser.add_argument("--lon", type=float, help="Centre longitude (positive east, -180..180)")
    parser.add_argument("--px", type=int, help="Centre column, if specifying pixels directly")
    parser.add_argument("--py", type=int, help="Centre row, if specifying pixels directly")
    parser.add_argument("--size", type=int, default=512, help="Tile size in pixels")
    parser.add_argument("--name", default=None, help="Output basename")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "sample_terrain",
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(
            f"{args.source} not found.\nDownload the DEM from:\n"
            "  https://astrogeology.usgs.gov/search/map/"
            "mars_mgs_mola_mex_hrsc_blended_dem_global_200m"
        )

    if args.lat is not None and args.lon is not None:
        px, py = lonlat_to_pixel(args.lon, args.lat)
        label = args.name or f"mars_{args.lat:+.1f}_{args.lon:+.1f}"
    elif args.px is not None and args.py is not None:
        px, py = args.px, args.py
        label = args.name or f"mars_px{px}_py{py}"
    else:
        raise SystemExit("Provide either --lat/--lon or --px/--py")

    try:
        import cv2
    except ImportError:
        raise SystemExit("opencv-python-headless is required to write the tile") from None

    window = read_window(args.source, px, py, args.size)
    tile, elevation_metadata = to_uint8(window)

    args.out.mkdir(parents=True, exist_ok=True)
    image_path = args.out / f"{label}_{args.size}.png"
    sidecar_path = args.out / f"{label}_{args.size}.json"

    cv2.imwrite(str(image_path), tile)
    sidecar_path.write_text(
        json.dumps(
            {
                "source_dataset": "Mars MGS MOLA - MEX HRSC Blended DEM Global 200m v2",
                "source_url": (
                    "https://astrogeology.usgs.gov/search/map/"
                    "mars_mgs_mola_mex_hrsc_blended_dem_global_200m"
                ),
                "citation": CITATION,
                "centre_pixel": {"x": px, "y": py},
                "tile_size_px": args.size,
                "ground_coverage_km": round(args.size * DEM_METERS_PER_PIXEL / 1000.0, 2),
                **elevation_metadata,
            },
            indent=2,
        )
    )

    print(f"Wrote {image_path}")
    print(f"Wrote {sidecar_path}")
    print(
        "\nSet these in backend/.env so slope angles are computed against the "
        "real relief:\n"
        f"  METERS_PER_PIXEL={DEM_METERS_PER_PIXEL}\n"
        f"  ELEVATION_RANGE_M={elevation_metadata['elevation_range_m']}"
    )
    print(f"\nCite this data as:\n  {CITATION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
