# Sample terrain

## Synthetic (committed, and regenerable)

```bash
python scripts/generate_terrain.py --size 512
```

The three tiles here are committed so the repo is demoable immediately; the
command above regenerates them byte-for-byte. They are chosen to exercise each branch of the classifier:

| File | Character | Expected classification |
|---|---|---|
| `synthetic_sandy_plain_512.png` | Low relief, sparse obstacles | `sandy_plain` |
| `synthetic_rocky_highland_512.png` | High relief, dense small rocks | `rocky_highland` |
| `synthetic_crater_field_512.png` | Few large impact craters | `crater_field` |

Fractal heightfields (diamond-square) with stamped craters and rock fields.
Reproducible from `--seed`, so a given seed always yields the same tile.

Craters are stamped with a **raised rim**, not just a bowl — the rim is the
high-gradient ring Canny actually detects. A rimless depression produces almost
no edge response and the obstacle detector finds nothing.

## Real USGS Mars terrain

```bash
python scripts/prepare_usgs_terrain.py --source <global-dem>.tif --lat -5.4 --lon 137.8
```

> **Mars MGS MOLA — MEX HRSC Blended DEM Global 200m v2**
> USGS Astrogeology Science Center, 2018. 200 m/pixel, 16-bit, simple cylindrical.
> https://astrogeology.usgs.gov/search/map/mars_mgs_mola_mex_hrsc_blended_dem_global_200m
>
> Fergason, R. L., Hare, T. M., & Laura, J. (2018). *HRSC and MOLA Blended
> Digital Elevation Model at 200m v2.* Astrogeology PDS Annex, U.S. Geological Survey.

The global product is ~11 GB, so it is neither committed here nor downloaded
automatically. Download it once from the catalogue page, then point the script
at it — it reads a window rather than the whole raster.

The script writes a sidecar JSON with the tile's true elevation range. Set
`ELEVATION_RANGE_M` in `backend/.env` from it: intensity is interpreted as a
linear elevation proxy, so a wrong range makes every slope angle downstream
wrong by a constant factor.

USGS Astrogeology data is public domain; the citation above is required by the
dataset's access constraints.
