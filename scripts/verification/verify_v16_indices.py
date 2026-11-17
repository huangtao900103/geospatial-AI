"""Opt-in live public-pixel verification, isolated from the formal database.

Run from the repository root with .venv/bin/python scripts/verification/verify_v16_indices.py.
No SAS token is persisted; source scenes, unsigned assets, calibration hashes,
whole-window statistics and independent export checks are recorded in output.
"""
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from backend.analysis.production_services import make_plan,SentinelReader,ProductionServices
from backend.analysis.landsat_reader import LandsatReader
from backend.catalog.catalog_adapters import normalize_item
from backend.catalog.algorithm_catalog import match_observation
from backend.analysis.native_raster import compute_native

BOX=[114.2,30.5,114.21,30.51]
PERIOD={'start':'2025-11-01','end':'2025-11-30'}
OUT=ROOT/'output/v16-agriculture-indices'


def independent_export(path,index,summary):
    import numpy as np
    import rasterio
    from rasterio.warp import transform
    with rasterio.open(path) as ds:
        arrays={name:ds.read(i+1) for i,name in enumerate(ds.descriptions)}
        value=arrays['after_'+index];aoi=arrays['aoi_mask']==1
        inputs={name[len('after_'):-len('_reflectance')]:arr for name,arr in arrays.items() if name.startswith('after_') and name.endswith('_reflectance')}
        # Recreate the centre-inside-rectangle AOI independently in WGS84.
        rows,cols=np.indices(value.shape)
        xs=ds.transform.c+(cols+.5)*ds.transform.a+(rows+.5)*ds.transform.b
        ys=ds.transform.f+(cols+.5)*ds.transform.d+(rows+.5)*ds.transform.e
        lon,lat=transform(ds.crs,'EPSG:4326',xs.ravel().tolist(),ys.ravel().tolist())
        lon=np.array(lon).reshape(value.shape);lat=np.array(lat).reshape(value.shape)
        independent_aoi=(lon>=BOX[0])&(lon<=BOX[2])&(lat>=BOX[1])&(lat<=BOX[3])
        np.testing.assert_array_equal(aoi,independent_aoi)
        finite=np.logical_and.reduce([np.isfinite(arr) for arr in inputs.values()])
        positive=np.logical_and.reduce([arr>=0 for arr in inputs.values()])
        if 'after_scl' in arrays:
            scl=arrays['after_scl'];quality=np.isfinite(scl)&np.isin(scl,[4,5,6,7])
        else:
            qa,rad=arrays['after_qa_pixel'],arrays['after_qa_radsat']
            integer=np.isfinite(qa)&np.isfinite(rad)&(qa>=0)&(qa<=65535)&(rad>=0)&(rad<=65535)&(qa==np.floor(qa))&(rad==np.floor(rad))
            q=np.where(integer,qa,65535).astype('uint16');r=np.where(integer,rad,65535).astype('uint16')
            quality=integer&((q&63)==0)&(r==0)
        v={key:arr.astype('float64') for key,arr in inputs.items()}
        if index=='evi2':
            denominator=v['nir']+2.4*v['red']+1;domain=denominator>1e-12
            expected=(2.5*(v['nir']-v['red'])/denominator).astype('float32')
        elif index=='ndre':
            a,b=inputs['nir_narrow'],inputs['rededge'];denominator=a+b;domain=denominator>0
            expected=((a-b)/denominator).astype('float32')
        elif index=='bsi':
            a=v['swir1']+v['red'];b=v['nir']+v['blue'];denominator=a+b;domain=denominator>1e-12
            expected=((a-b)/denominator).astype('float32')
        else:raise AssertionError(index)
        valid=aoi&finite&positive&quality&domain&np.isfinite(expected)
        np.testing.assert_array_equal(valid,arrays['common_valid_mask']==1)
        np.testing.assert_array_equal(valid,np.isfinite(value))
        np.testing.assert_allclose(value[valid],expected[valid],rtol=0,atol=0)
        np.testing.assert_allclose(summary['mean_index'],float(expected[valid].mean(dtype='float64')),rtol=0,atol=0)
        selected=valid&(expected>0)
        assert summary['selected_count']==int(selected.sum())
        assert math.isclose(summary['selected_grid_area_ha'],int(selected.sum())*abs(ds.transform.a*ds.transform.e-ds.transform.b*ds.transform.d)/10000,rel_tol=0,abs_tol=1e-12)
        assert sum(row['count'] for row in summary['index_histogram'])==int(valid.sum())
        return {'formula_and_mask_equal':True,'aoi_centres_independently_checked':True,'all_valid_cells_checked':int(valid.sum()),'max_abs_formula_error':float(np.abs(value[valid]-expected[valid]).max()),'selected_count_equal':True,'histogram_balanced':True,'geotiff_bands':list(ds.descriptions),'grid':[ds.height,ds.width],'crs':ds.crs.to_string(),'resolution_m':list(ds.res),'geotiff_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def run(source,index,reader):
    requirement='分析所选区域2025年11月'+index.upper()+'大于0的范围'
    plan=make_plan({'requirement':requirement,'source_id':source,'mode':'sentinel_cog','sampling':'native','index_id':index,'bbox':BOX,'threshold':0})
    first=reader.discover(plan,lambda:None)
    doc=reader.fetch_json(first['after']['catalog_url'],lambda:None)
    raw=next(feature for feature in doc['features'] if feature['id']==first['after']['id'])
    obs=normalize_item(source,raw,BOX,PERIOD)
    obs.update(source_id=source,item_id=obs['id'])
    fingerprint=hashlib.sha256(json.dumps(obs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    obs.update(id='isolated_'+fingerprint[:24],metadata_sha256=fingerprint,fingerprint=fingerprint)
    match=match_observation(obs,index)
    assert match['ready_count']==1,match
    plan['scene_id']=obs['item_id'];plan['observation_ref']={'id':obs['id'],'metadata_sha256':fingerprint}
    scenes=reader.discover(plan,lambda:None)
    service=ProductionServices.__new__(ProductionServices)
    service.store=SimpleNamespace(observations=SimpleNamespace(get=lambda key:copy.deepcopy(obs)))
    service.verify_bound_scene(plan,scenes)
    bands=reader.read_native(plan,scenes,lambda:None)
    folder=OUT/(source+'-'+index)
    product=compute_native(plan,bands,'v16-'+source+'-'+index,folder,lambda:None)
    proof=independent_export(folder/'analysis.tif',index,product['summary'])
    evidence={'source_id':source,'index_id':index,'scene_id':scenes['after']['id'],'datetime':scenes['after']['datetime'],'observation_ref':plan['observation_ref'],'metadata_match':match,'bound_scene_verified':True,'source_assets':[a for a in obs['assets'] if a['key'] in match['matches'][0]['required_bands']+match['matches'][0]['required_quality_assets']],'source_calibration':scenes['after']['calibration'],'summary':product['summary'],'verification':proof,'artifact_folder':str(folder.relative_to(ROOT))}
    (folder/'verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
    print(json.dumps({'source':source,'index':index,'scene':evidence['scene_id'],'valid':product['summary']['valid_count'],'mean':product['summary']['mean_index'],'grid':proof['grid'],'resolution':proof['resolution_m'],'independent_error':proof['max_abs_formula_error']},ensure_ascii=False),flush=True)
    return evidence


if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    s2=SentinelReader(None);ls=LandsatReader();results=[]
    for source,index,reader in [('pc_sentinel2_l2a','evi2',s2),('pc_sentinel2_l2a','ndre',s2),('pc_sentinel2_l2a','bsi',s2),('pc_landsat_c2_l2','evi2',ls),('pc_landsat_c2_l2','bsi',ls)]:results.append(run(source,index,reader))
    (OUT/'verification-summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
