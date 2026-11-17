"""Exercise real archived Xiantao data through applications in an isolated QA store."""
import hashlib,json,shutil,sys,time,uuid
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import numpy as np
import rasterio
from rasterio.warp import transform_geom
from rasterio.features import geometry_mask
from backend.server import Workspace

root=Path.cwd();out=root/'output/review-rounds'/('actual-reuse-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6]);out.mkdir(parents=True)
for ident in ['xiantao-2026-spectral-ndvi','xiantao-2024-classification']:
 shutil.copytree(root/'data/agriculture-atlas'/ident,out/'agriculture-atlas'/ident)
w=Workspace(out/'workspace.sqlite3');proof={'scope':'actual data and derived analysis in isolated QA database; source production files untouched','output':str(out),'endmembers':{'soil_ndvi':.2,'vegetation_ndvi':.8,'purpose':'explicit verification assumptions, not locally calibrated'}}
def run(app,context,parameters):
 q=w.applications.run({'application_id':app,'context':context,'parameters':parameters,'request_id':'review-isolated-'+uuid.uuid4().hex})
 for _ in range(1200):
  q=w.assistant.get(q['id'])
  if q['status']!='processing':break
  time.sleep(.1)
 if q['status']!='answered':raise AssertionError((app,q['status'],q.get('message')))
 t=w.agriculture.get(q['binding']['task_id']);assert t['status']=='succeeded';return q,t
try:
 q,fvc=run('vegetation_cover',{}, {'source_ref':'agriculture_atlas:xiantao-2026-spectral-ndvi','soil_ndvi':.2,'vegetation_ndvi':.8})
 with rasterio.open(out/'agriculture-atlas/xiantao-2026-spectral-ndvi/analysis.tif') as src:
  ndvi=src.read(1).astype('float64');valid=(src.read(2)==1)&(src.read(3)==1)&np.isfinite(ndvi);crop=src.read(4)==1;expected=np.clip((ndvi-.2)/.6,0,1);transform=src.transform;crs=src.crs;choices=np.flatnonzero(valid&crop);row,col=np.unravel_index(choices[len(choices)//2],valid.shape);x,y=transform*(col+.5,row+.5)
 assert abs(fvc['result']['summary']['mean_fvc']-float(expected[valid].mean()))<1e-12
 with rasterio.open(w.agriculture.artifact(fvc['id'],'analysis.tif')) as ds:
  delivered=ds.read(1);assert np.array_equal(np.isfinite(delivered),valid);maximum=float(np.abs(delivered[valid]-expected[valid]).max());assert maximum<1e-7;assert ds.crs==crs and ds.transform==transform
 proof['fvc']={'question_id':q['id'],'task_id':fvc['id'],'native_city_pixels':fvc['result']['summary']['native_pixel_count'],'valid_pixels':int(valid.sum()),'mean':fvc['result']['summary']['mean_fvc'],'all_export_pixels_independently_checked':int(valid.sum()),'max_float32_export_error':maximum,'source_dates':fvc['plan']['source']['actual_dates'],'product_sha256':fvc['plan']['source']['product_sha256']}
 geom=json.loads(json.dumps(transform_geom(crs,'EPSG:4326',{'type':'Polygon','coordinates':[[[x-90,y-90],[x+90,y-90],[x+90,y+90],[x-90,y+90],[x-90,y-90]]]})))
 dataset=w.create_dataset({'name':'真实影像上的验证框选范围（非确权地块）','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','properties':{'purpose':'explicit QA analysis region, not an agricultural business parcel'},'geometry':geom}]}})
 q,zonal=run('field_zonal_statistics',{'dataset_id':dataset['id']},{'source_ref':'agriculture_atlas:xiantao-2024-cropland','field':'auto'})
 with rasterio.open(out/'agriculture-atlas/xiantao-2024-classification/classification.tif') as src:
  projected=transform_geom('EPSG:4326',src.crs,geom);mask=geometry_mask([projected],src.shape,src.transform,invert=True,all_touched=False)&(src.read(2)==1);data=src.read(1);known=mask&np.isin(data,[1,2,4,5,7,8,9,11]);n=int((known&(data==5)).sum());area=n*.01
 stats=zonal['result']['summary'];assert stats['unique_cropland_pixel_count']==n and abs(stats['unique_cropland_area_ha']-area)<1e-10;assert zonal['result']['provenance']['source_window']['height']*zonal['result']['provenance']['source_window']['width']<1000
 proof['zonal']={'question_id':q['id'],'task_id':zonal['id'],'crop_pixels_independently_counted':n,'crop_area_ha':area,'native_window':zonal['result']['provenance']['source_window'],'source_full_shape':zonal['result']['provenance']['source_full_shape'],'statistics_resampled':zonal['result']['provenance']['statistics_resampled'],'region':geom,'product_sha256':zonal['plan']['source']['product_sha256']}
 for task in [fvc,zonal]:
  p=w.result_bundles.artifact('agriculture',task['id']);assert p.is_file()
 proof['passed']=True;print(json.dumps({'passed':True,'output':str(out),'fvc':proof['fvc'],'zonal':{k:v for k,v in proof['zonal'].items() if k!='region'}},ensure_ascii=False),flush=True)
finally:
 (out/'proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n');w.close()
 (root/'output/review-rounds/actual-native-reuse-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
