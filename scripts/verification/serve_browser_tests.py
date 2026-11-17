#!/usr/bin/env python3
"""Browser-only workspace: temporary database and explicit synthetic raster, no keys.

Never points at data/workspace.sqlite3. The normal crop algorithm produces actual
GeoTIFF/PNG/CSV artifacts from this small local fixture; no satellite downloads.
"""
import copy
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL_PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if LOCAL_PYTHON.is_file() and Path(sys.prefix) != ROOT / '.venv':
    os.execv(str(LOCAL_PYTHON), [str(LOCAL_PYTHON), __file__])
sys.path.insert(0, str(ROOT))

import numpy as np
import rasterio
from rasterio.transform import from_origin
from backend.server import Workspace, LocalServer, make_handler
from backend.agriculture.crop_reference_services import CropReferenceServices, SOURCE_ID


def main():
    with tempfile.TemporaryDirectory(prefix='geoai-browser-test-') as directory:
        root = Path(directory)
        workspace = Workspace(root / 'workspace.sqlite3', node_delay=0)
        grid = from_origin(113.3, 30.31, .0001796630568239043, .0001796630568239043)
        pixels = np.zeros((32, 32), dtype='uint8')
        pixels[8:11, 8:11] = 1
        pixels[12, 12] = 255
        source = root / 'synthetic-browser-fixture.tif'
        with rasterio.open(source, 'w', driver='GTiff', width=32, height=32,
                           count=1, dtype='uint8', crs=4326, transform=grid, nodata=255) as raster:
            raster.write(pixels, 1)
        metadata = {
            'item': {'id': 'global_2024_22', 'collection': 'Anthropogenic-V2',
                     'properties': {'sci:doi': '10.5281/zenodo.18168302', 'datetime': '2024-01-01',
                                    'end_datetime': '2024-12-31', 'proj:transform': list(grid)[:6]},
                     'assets': {'crop_type_rice_paddies': {
                         'href': 'https://s3.waw4-1.cloudferro.com/test/ECDC/Anthropogenic/Anthropogenic_V_global_20240101_20241231_crop_type_rice_paddies.tif',
                         'bands': [{'data_type': 'uint8', 'nodata': 255}],
                         'classification:classes': [{'value': 1, 'name': 'paddy_rice'}]}}},
            'collection': {'id': 'Anthropogenic-V2', 'license': 'CC-BY-4.0'}}
        workspace.crop_reference = CropReferenceServices(workspace, source_path=source,
                                                       metadata_provider=lambda: copy.deepcopy(metadata))
        product, _ = workspace.crop_reference.analyze({'source_id': SOURCE_ID, 'bbox': [113.3005, 30.305, 113.305, 30.3095]})
        result_file = workspace.agriculture_atlas.root / product['id'] / 'result.json'
        record = json.loads(result_file.read_text())
        record['title'] = '浏览器测试专用 · 合成格网面积核算'
        record['scope_label'] = '隔离测试数据，非仙桃实测或卫星影像'
        result_file.write_text(json.dumps(record, ensure_ascii=False))
        server = LocalServer(('127.0.0.1', 8096), make_handler(workspace))
        server.quiet = True
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        print('Isolated browser workspace http://127.0.0.1:8096', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            workspace.close()


if __name__ == '__main__':
    main()
