"""Controlled native Atlas adapters; no user paths, resampling or global limit changes."""
import copy,math
from pathlib import Path
from backend.core.geo_services import GeoError

MAX_REGIONAL_CELLS=7_000_000

def descriptor(atlas,ident,for_analysis=True):
 product=atlas.get(ident);kind=product.get('product_type');s=copy.deepcopy(product.get('summary') or {});p=copy.deepcopy(product.get('provenance') or {})
 if kind=='annual_classification' and (p.get('source_product') or {}).get('id')=='io_lulc_2024':
  if for_analysis and ident not in (product['source_product_id'],product['source_product_id'].replace('-classification','-cropland')):raise GeoError('AGRICULTURE_SOURCE_INVALID','分类算子统计Crops类别，请选择耕作地参考或完整分类源，不用水域/树木视图代替',422)
  category='classification';filename='classification.tif';value='classification';inside='aoi_mask';valid=None;goal='classification';index=None;p['product']=copy.deepcopy(p['source_product']);p['source_type']='public_classification_product'
 elif kind=='city_spectral_reference' and p.get('source_id')=='pc_landsat_c2_l2':
  category='spectral';filename='analysis.tif';value='value';inside='city_mask';valid='valid_mask';index=s.get('index_id');goal='change' if s.get('operation')=='after_minus_before' else index
  if index not in ('ndvi','ndmi','mndwi'):raise GeoError('AGRICULTURE_SOURCE_INVALID','该区域指数尚未登记为原生农业输入',422)
  p['source_type']='public_satellite_pixels'
 elif kind=='cross_product_review' and not for_analysis:
  category='review';filename='analysis.tif';value='cross_product_review_class';inside='aoi_mask';valid='jointly_valid_mask';goal='review';index=None
 else:raise GeoError('AGRICULTURE_SOURCE_INVALID','该成果不是已登记的可分析原生分类/光谱栅格，地址与公告不作为像元输入',422)
 path=Path(atlas.artifact(ident,filename));boundary=p.get('boundary_source') or p.get('boundary') or {};bbox=boundary.get('bbox')
 if not isinstance(bbox,list) or len(bbox)!=4:
  raster=next((v for v in product.get('layers',[]) if v.get('kind')=='raster'),{});bounds=(raster.get('image') or {}).get('bounds')
  if bounds:bbox=[bounds[0][1],bounds[0][0],bounds[1][1],bounds[1][0]]
 if not bbox:raise GeoError('AGRICULTURE_SOURCE_INVALID','原生区域源缺少可复核的WGS84范围',422)
 dates=s.get('actual_dates') or p.get('actual_dates') or [];date_keys=['before','after'] if len(dates)==2 else ['after']
 p.update(bbox=bbox,native_crs=s.get('native_crs') or p.get('native_crs') or (p.get('grid') or {}).get('crs'),source_details={'scenes':{k:{'datetime':v} for k,v in zip(date_keys,dates)}},atlas_product_sha256=product['product_sha256'])
 if category=='classification':s.setdefault('reference_year',2024)
 plan={'goal':goal,'index_id':index,'aoi':{'bbox':bbox},'mode':'sentinel_cog','sampling':'native','atlas_product_sha256':product['product_sha256'],'native':{'category':category,'value_band':value,'inside_band':inside,'valid_band':valid,'resolution_m':product.get('resolution_m')}}
 raw={'id':ident,'status':'succeeded','name':product['title'],'plan':plan,'result':{'summary':s,'provenance':p,'bbox':bbox,'boundary':next((v['geojson'] for v in product.get('layers',[]) if v.get('boundary') and v.get('geojson')),None),'classes':copy.deepcopy(product.get('classes',[]))},'atlas_product_sha256':product['product_sha256'],'atlas_product':product}
 return raw,path

def validate_grid(ds,native):
 resolution=native.get('resolution_m');names=list(ds.descriptions)
 if ds.driver!='GTiff' or not ds.crs or not ds.crs.is_projected or ds.crs.linear_units not in ('metre','meter','metres','meters') or ds.transform.b!=0 or ds.transform.d!=0 or ds.transform.a<=0 or ds.transform.e>=0:raise GeoError('AGRICULTURE_GRID_UNSUPPORTED','区域源必须为已登记北向米制原生GeoTIFF',422)
 if not isinstance(resolution,(int,float)) or not all(math.isclose(v,resolution,abs_tol=1e-7) for v in ds.res):raise GeoError('AGRICULTURE_GRID_UNSUPPORTED','实际区域格网与生产分辨率不符，拒绝重采样代替',422)
 required=[native['value_band'],native['inside_band']]+([native['valid_band']] if native.get('valid_band') else [])
 if len(set(names))!=len(names) or any(k not in names for k in required):raise GeoError('AGRICULTURE_MASK_REQUIRED','原生区域源缺少明确主值/市界/有效掩膜',422)
 if native['category'] in ('classification','review') and any(t!='uint8' for t in ds.dtypes) or native['category']=='spectral' and any(t!='float32' for t in ds.dtypes):raise GeoError('AGRICULTURE_GRID_UNSUPPORTED','区域栅格类型与已生产契约不符',422)
 return names

def read_window(task,path,dataset_geo=None,check=lambda:None):
 import numpy as np
 import rasterio
 from rasterio.warp import transform_bounds
 from rasterio.windows import Window,from_bounds
 from backend.agriculture.agriculture_services import _bbox
 native=task['plan']['native']
 with rasterio.open(path) as ds:
  names=validate_grid(ds,native);window=Window(0,0,ds.width,ds.height)
  if dataset_geo:
   bbox=_bbox(dataset_geo);projected=transform_bounds('EPSG:4326',ds.crs,*bbox,densify_pts=21);candidate=from_bounds(*projected,transform=ds.transform)
   c0=max(0,math.floor(candidate.col_off)-1);r0=max(0,math.floor(candidate.row_off)-1);c1=min(ds.width,math.ceil(candidate.col_off+candidate.width)+1);r1=min(ds.height,math.ceil(candidate.row_off+candidate.height)+1)
   window=Window(c0,r0,c1-c0,r1-r0) if c1>c0 and r1>r0 else Window(0,0,1,1)
  width,height=int(window.width),int(window.height)
  if width*height>MAX_REGIONAL_CELLS:raise GeoError('AGRICULTURE_REGIONAL_WINDOW_LIMIT','区域原生计算窗口超过700万像元，请分批选择更集中的地块；没有缩小像元或降采样',422)
  data=np.empty((height,width),dtype='float64');inside=np.empty((height,width),dtype=bool);valid=np.empty((height,width),dtype=bool);crop=np.zeros((height,width),dtype=bool) if native['category']=='classification' else None
  for row in range(0,height,256):
   check();count=min(256,height-row);part=Window(window.col_off,window.row_off+row,width,count);v=ds.read(names.index(native['value_band'])+1,window=part);aoi=ds.read(names.index(native['inside_band'])+1,window=part)
   if not np.isin(aoi,[0,1]).all():raise GeoError('AGRICULTURE_MASK_REQUIRED','归档区域市界掩膜不是0/1',422)
   m=aoi==1;good=m&np.isfinite(v)
   if native.get('valid_band'):
    q=ds.read(names.index(native['valid_band'])+1,window=part)
    if not np.isin(q[m],[0,1]).all():raise GeoError('AGRICULTURE_MASK_REQUIRED','市界内归档有效掩膜不是0/1',422)
    good &= q==1
   else:good &= np.isin(v,[1,2,4,5,7,8,9,11])
   data[row:row+count]=v;inside[row:row+count]=m;valid[row:row+count]=good
   if crop is not None:crop[row:row+count]=good&(v==5)
  t=ds.window_transform(window)
  return {'data':data,'inside':inside,'valid':valid,'crop':crop,'row_areas':np.full(height,abs(t.a*t.e)),'area_method':'native_projected_grid','transform':t,'crs':ds.crs.to_string(),'height':height,'width':width,'nodata':ds.nodata,'source_window':{'row_offset':int(window.row_off),'column_offset':int(window.col_off),'height':height,'width':width},'source_full_shape':[ds.height,ds.width],'native_windowed':bool(dataset_geo),'resampled':False}
