"""Run genuine public scenes and independently recompute exported reducers.

Run with the project raster Python. Does not instantiate Workspace or write the
formal database. Selected coordinates are an algorithm test AOI, not field data.
"""
import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
import rasterio
from backend.analysis.collection_raster import make_plan, discover_collection, run_collection
from backend.analysis.production_services import SentinelReader
from backend.analysis.landsat_reader import LandsatReader


def independently_verify(directory):
    result = json.loads((directory / 'result.json').read_text());s = result['summary'];algorithm = s['algorithm_id']
    dates = [datetime.fromisoformat(v.replace('Z', '+00:00')) for v in s['actual_dates']]
    times = np.array([(v - dates[0]).total_seconds() / 86400 for v in dates])
    with rasterio.open(directory / 'analysis.tif') as ds:
        names = ds.descriptions;stack = np.stack([ds.read(names.index('scene_%02d_index' % i) + 1).astype('float64') for i in range(len(dates))]);inside = ds.read(names.index('aoi_mask') + 1) == 1
        expected = np.full(inside.shape, np.nan);r_squared = np.full(inside.shape, np.nan);counts = np.isfinite(stack).sum(axis=0)
        for row, col in zip(*np.where(inside)):
            mask = np.isfinite(stack[:, row, col]);y = stack[:, row, col][mask];x = times[mask]
            if len(y) < s['minimum_observations']:
                continue
            if algorithm == 'temporal_median':
                expected[row, col] = float(np.median(y))
            elif algorithm == 'temporal_mean':
                expected[row, col] = math.fsum(float(v) for v in y) / len(y)
            else:
                mx = math.fsum(float(v) for v in x) / len(x);my = math.fsum(float(v) for v in y) / len(y);dx, dy = x - mx, y - my
                slope = math.fsum(float(a * b) for a, b in zip(dx, dy)) / math.fsum(float(a * a) for a in dx)
                expected[row, col] = slope;total = math.fsum(float(v * v) for v in dy)
                if total > 1e-12:
                    r_squared[row, col] = 1 - math.fsum(float(v * v) for v in y - (my + slope * dx)) / total
        valid = np.isfinite(expected);actual = ds.read(1);expected32 = expected.astype('float32')
        np.testing.assert_allclose(actual, expected32, rtol=0, atol=1e-7, equal_nan=True)
        np.testing.assert_array_equal(ds.read(names.index('valid_observation_count') + 1)[inside], counts[inside])
        np.testing.assert_array_equal(ds.read(names.index('valid_mask') + 1)[inside], valid[inside])
        assert sum(item['pixels'] for item in s['observation_count_histogram']) == int(inside.sum())
        r2_error = None
        if algorithm == 'temporal_trend':
            actual_r2 = ds.read(names.index('r_squared') + 1);r2_valid = np.isfinite(r_squared)
            np.testing.assert_allclose(actual_r2, r_squared.astype('float32'), rtol=0, atol=1e-6, equal_nan=True)
            r2_error = float(np.max(np.abs(actual_r2[r2_valid] - r_squared.astype('float32')[r2_valid]))) if r2_valid.any() else None
        return {'algorithm_id': algorithm, 'source_id': result['provenance']['source_id'], 'index_id': s['index_id'], 'actual_dates': s['actual_dates'], 'all_valid_cells_checked': int(valid.sum()), 'max_abs_value_error': float(np.max(np.abs(actual[valid] - expected32[valid]))) if valid.any() else None, 'max_abs_r_squared_error': r2_error, 'counts_and_masks_equal': True, 'support_histogram_balanced': True, 'grid': [ds.height, ds.width], 'crs': str(ds.crs), 'resolution': list(ds.res), 'summary_mean': s['statistics']['mean'], 'geotiff_sha256': hashlib.sha256((directory / 'analysis.tif').read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser();parser.add_argument('--output', type=Path, default=ROOT / 'output/v18-collection-rerun');parser.add_argument('--verify-only', action='store_true', help='Recompute existing output only, without provider requests')
    args = parser.parse_args();args.output.mkdir(parents=True, exist_ok=True)
    cases = [('s2_ndvi_median', 'pc_sentinel2_l2a', 'ndvi', 'temporal_median', 4), ('s2_ndvi_mean', 'pc_sentinel2_l2a', 'ndvi', 'temporal_mean', 4), ('s2_ndvi_trend', 'pc_sentinel2_l2a', 'ndvi', 'temporal_trend', 4), ('landsat_evi2_mean', 'pc_landsat_c2_l2', 'evi2', 'temporal_mean', 3), ('s2_ndre_mean', 'pc_sentinel2_l2a', 'ndre', 'temporal_mean', 2)]
    readers = {'pc_sentinel2_l2a': SentinelReader(None), 'pc_landsat_c2_l2': LandsatReader()};proof = []
    for case, source, index, algorithm, count in cases:
        directory = args.output / case;started = time.monotonic()
        if not args.verify_only:
            plan = make_plan({'algorithm_id': algorithm, 'source_id': source, 'index_id': index, 'bbox': [114.2, 30.5, 114.21, 30.51], 'start': '2025-10-01', 'end': '2025-11-30', 'scene_limit': count, 'cloud': 30})
            plan['collection'] = discover_collection(plan, readers[source]);run_collection(plan, readers[source], directory)
        item = independently_verify(directory);item.update(case=case, seconds=round(time.monotonic() - started, 3));proof.append(item)
        (args.output / 'independent-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2));print(case, item['all_valid_cells_checked'], item['max_abs_value_error'], flush=True)


if __name__ == '__main__':
    main()
