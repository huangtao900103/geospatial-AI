"""Reproducible agricultural derivatives from existing native GIS results.

No remote data, user path or arbitrary URL is accepted. FVC is an explicit
NDVI two-endmember estimate; zonal statistics describe only covered native
pixel centres, never infer the unobserved portion of a parcel.
"""
import copy
import csv
import hashlib
import importlib.util
import json
import math
import re
import threading
import uuid
from pathlib import Path

from backend.core.geo_services import GeoError, finite, digest, validate_geojson
from backend.analysis.landcover_services import ellipsoid_row_pixel_area, now

VERSION='agriculture-1.1'
MAX_CELLS=1_048_576
MAX_ZONES=200
ID_PATTERN=r'[A-Za-z0-9_-]{1,100}'
FILES={
 'fvc':('analysis.tif','overlay.png','result.geojson','result.json','summary.csv'),
 'zonal_statistics':('result.geojson','zones.csv','result.json'),
}
LIMITATIONS=[
 '结果是对归档原生像元的分析，不是作物类型、产量、灾损或法定耕地认定。',
 '像元中心纳入地块；10/20/30米等源格网不能表达所有田埂或确权边界。',
 '数据有效覆盖不等于分类精度；缺测与未覆盖区域不当作零值或非农田。',
]
FVC_LIMITATIONS=[
 'FVC采用显式NDVI土壤/全植被端元的线性像元二分模型，不是独立训练的作物或叶面积模型。',
 '端元由用户提供，未做本地校准；端元和土壤背景、季节、混合像元会影响估计。',
 '输出FVC裁至[0,1]；同时保留未裁剪FVC与低/高裁剪像元数，不将裁剪后边界值视为地面实测。',
]
ZONAL_LIMITATIONS=[
 '统计只覆盖地块与源AOI的交集；partial时不报告虚假的全地块覆盖率，未覆盖部分不外推。',
 '逐地块面积合计可重复计入重叠地块；唯一覆盖面积另按像元并集统计，不能混用。',
 '均值和分位数仅使用有效像元；无有效像元时数值为null，不填0。',
]


def ready():
 return all(importlib.util.find_spec(m) is not None for m in ('numpy','rasterio','PIL'))


def capabilities():
 return {'version':VERSION,'available':ready(),'max_cells':MAX_CELLS,'max_zones':MAX_ZONES,'regional_native_sources':{'max_window_cells':7_000_000,'block_rows':256,'resampling':False,'classification_scope':'uploaded_polygon_window; split dispersed large requests','fvc_scope':'registered_single_date_ndvi'},
  'algorithms':[{'id':'fvc','name':'植被覆盖度估算','version':VERSION,
   'formula':'FVC_raw=(NDVI-NDVI_soil)/(NDVI_vegetation-NDVI_soil); FVC=clip(FVC_raw,0,1)',
   'source_types':['production','agriculture_atlas'],'requires':'成功单期原生NDVI成果与显式soil_ndvi/vegetation_ndvi端元',
   'limitations':LIMITATIONS+FVC_LIMITATIONS},
   {'id':'zonal_statistics','name':'逐地块遥感分区统计','version':VERSION,
   'source_types':['production','cropland','agriculture_atlas'],'requires':'成功原生栅格与明确用户Polygon/MultiPolygon数据版本',
   'outputs':['有效像元分布统计','逐地块阈值/分类面积','源覆盖范围与未知区域','独立像元并集汇总'],
   'limitations':LIMITATIONS+ZONAL_LIMITATIONS}]}


def _canonical(value):
 return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _sha(path,check=lambda:None):
 value=hashlib.sha256()
 with Path(path).open('rb') as stream:
  for chunk in iter(lambda:stream.read(1024*1024),b''):
   check();value.update(chunk)
 return value.hexdigest()


def _bbox(geo):
 points=[]
 def collect(value):
  if isinstance(value,(list,tuple)) and len(value)>=2 and all(finite(v) for v in value[:2]):points.append(value[:2])
  elif isinstance(value,(list,tuple)):
   for child in value:collect(child)
 for f in geo.get('features',[]):collect((f.get('geometry') or {}).get('coordinates',[]))
 if not points:return None
 return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]


def _source_dates(task):
 result=task.get('result') or {};p=result.get('provenance') or {}
 return {key:str(value.get('datetime','')) for key,value in p.get('source_details',{}).get('scenes',{}).items() if isinstance(value,dict) and value.get('datetime')}


def _row_areas(ds):
 import numpy as np
 if ds.transform.b!=0 or ds.transform.d!=0 or ds.transform.a<=0 or ds.transform.e>=0:
  raise GeoError('AGRICULTURE_GRID_UNSUPPORTED','农业统计仅支持已归档北向上原生格网',422)
 if ds.crs and ds.crs.to_epsg()==4326:
  t=ds.transform
  return np.asarray([ellipsoid_row_pixel_area(t.f+r*t.e,t.f+(r+1)*t.e,t.a) for r in range(ds.height)]),'wgs84_ellipsoid_latitude_band'
 if ds.crs and ds.crs.is_projected and ds.crs.linear_units in ('metre','meter','metres','meters'):
  return np.full(ds.height,abs(ds.transform.a*ds.transform.e)),'native_projected_grid'
 raise GeoError('AGRICULTURE_GRID_UNSUPPORTED','源格网必须为WGS84经纬度或明确米制投影，不能猜测面积单位',422)


def _area(mask,row_areas):
 import numpy as np
 return float(np.dot(mask.sum(axis=1,dtype='int64'),row_areas))


def _distribution(values):
 import numpy as np
 if not values.size:return {key:None for key in ('mean','median','p10','p90','min','max','std')}
 return {'mean':float(values.mean()),'median':float(np.median(values)),
  'p10':float(np.percentile(values,10)),'p90':float(np.percentile(values,90)),
  'min':float(values.min()),'max':float(values.max()),'std':float(values.std())}


class AgricultureServices:
 def __init__(self,store,output_dir=None):
  self.store=store;self.output_dir=Path(output_dir or Path(store.db_path).parent/'agriculture')
  self.stopping=False;self.threads=[]
  for task in self.list():
   if task['status'] in ('queued','running'):
    task.update(status='failed',error={'code':'INTERRUPTED','message':'服务重启，农业分析中断；请重试'},finished_at=now())
    for node in task.get('nodes',[]):
     if node['status'] in ('pending','running'):node.update(status='failed',message='服务重启中断',finished_at=now())
    store.save('agriculture_task',task)
 def capabilities(self):return capabilities()
 def get(self,id):return self.store.get('agriculture_task',id)
 def list(self):return self.store.list('agriculture_task')
 def _source(self,kind,id):
  if kind=='agriculture_atlas':
   from backend.analysis.regional_raster import descriptor
   return descriptor(self.store.agriculture_atlas,id)
  if kind not in ('production','cropland') or not isinstance(id,str) or not re.fullmatch(ID_PATTERN,id):
   raise GeoError('AGRICULTURE_SOURCE_INVALID','请选择已有production或cropland成功任务，不接受URL或路径',422)
  service=getattr(self.store,kind);task=service.get(id);p=(task.get('result') or {}).get('provenance') or {};plan=task.get('plan') or {}
  if task.get('status')!='succeeded' or not isinstance(task.get('result'),dict):raise GeoError('AGRICULTURE_SOURCE_NOT_READY','源遥感任务尚未成功，不能使用预览或失败产物',422)
  if kind=='production' and (plan.get('mode')!='sentinel_cog' or plan.get('sampling')!='native' or p.get('source_type')!='public_satellite_pixels'):
   raise GeoError('AGRICULTURE_REQUIRES_NATIVE','农业分析只接受真实原生影像成果，不接受教学样本或代表网格',422)
  if kind=='cropland' and (p.get('source_type')!='public_classification_product' or p.get('operation')!='published_classification_extraction'):
   raise GeoError('AGRICULTURE_SOURCE_INVALID','分类来源必须为已归档真实年度分类产品',422)
  name='analysis.tif' if kind=='production' else 'classification.tif';path=Path(service.artifact(id,name));root=Path(getattr(service,'output_dir',Path(self.store.db_path).parent/kind))
  if path.absolute()!=(root/id/name).absolute() or not path.resolve().is_relative_to(root.resolve()):raise GeoError('AGRICULTURE_SOURCE_INVALID','源文件不属于该任务的归档目录',422)
  current=path
  while current.absolute()!=root.absolute():
   if current.is_symlink():raise GeoError('AGRICULTURE_SOURCE_INVALID','不能通过文件链接读取其他位置',422)
   current=current.parent
  if not path.is_file():raise GeoError('AGRICULTURE_SOURCE_UNAVAILABLE','原生源文件不可用',404)
  return task,path
 def _dataset(self,id):
  if not isinstance(id,str) or not re.fullmatch(ID_PATTERN,id):raise GeoError('AGRICULTURE_DATASET_REQUIRED','请选择已导入的用户地块图层',422)
  value=self.store.get_dataset(id)
  if value.get('source_type')!='user':raise GeoError('AGRICULTURE_REQUIRES_USER_POLYGONS','逐地块统计只接受明确用户图层，合成示例不作为业务输入',422)
  geo,_=validate_geojson(value.get('geojson'))
  if any(f['geometry']['type'] not in ('Polygon','MultiPolygon') for f in geo['features']):raise GeoError('AGRICULTURE_POLYGONS_REQUIRED','地块图层须全部为Polygon/MultiPolygon，点线不能代替地块',422)
  if len(geo['features'])>MAX_ZONES:raise GeoError('AGRICULTURE_ZONE_LIMIT','本机一次最多统计200个地块，请分批处理',422)
  if value.get('sha256')!=digest(geo):raise GeoError('AGRICULTURE_DATASET_CHANGED','用户图层内容与归档版本指纹不一致',409)
  return value,geo
 def prepare(self,body):
  allowed={'algorithm_id','source_type','source_task_id','dataset_id','field','threshold','soil_ndvi','vegetation_ndvi'}
  if not isinstance(body,dict) or set(body)-allowed:raise GeoError('INVALID_AGRICULTURE_REQUEST','农业分析请求包含不支持字段',400)
  algorithm=body.get('algorithm_id')
  if algorithm not in FILES:raise GeoError('AGRICULTURE_ALGORITHM_UNSUPPORTED','农业算子仅支持fvc或zonal_statistics',422)
  task,path=self._source(body.get('source_type'),body.get('source_task_id'));kind=body['source_type'];source_plan=task['plan'];result=task['result'];p=result['provenance'];s=result['summary']
  spectral=kind=='production' or kind=='agriculture_atlas' and source_plan['native']['category']=='spectral'
  index_id=(s.get('index_id') or p.get('index_id') or source_plan.get('index_id','ndvi')) if spectral else None
  source={'type':kind,'task_id':task['id'],'artifact_name':path.name,'artifact_sha256':_sha(path),
   'input_sha256':p.get('input_sha256',p.get('classified_window_sha256')),'product':copy.deepcopy(p.get('product')),
   'reference_year':s.get('reference_year',p.get('year')),'actual_dates':_source_dates(task),
   'bbox':copy.deepcopy(p.get('selected_bbox') or p.get('bbox') or source_plan.get('aoi',{}).get('bbox') or source_plan.get('bbox') or result.get('bbox')),
   'index_id':index_id,'index_label':(s.get('index') or p.get('algorithm') or {}).get('label',str(index_id).upper() if index_id else '分类类别'),
   'crs':p.get('native_crs') or p.get('crs') or (p.get('grid') or {}).get('crs'),
   'source_type':p.get('source_type'),'kind':'spectral' if spectral else 'classification'}
  if kind=='agriculture_atlas':source.update(product_sha256=task['atlas_product_sha256'],native=copy.deepcopy(source_plan['native']),regional_window_limit=7_000_000,quality=copy.deepcopy(task['atlas_product'].get('quality')),scope_label=task['atlas_product']['scope_label'],boundary_year=p.get('boundary_year',2017))
  if not isinstance(source['bbox'],list) or len(source['bbox'])!=4 or not all(finite(v) for v in source['bbox']):raise GeoError('AGRICULTURE_SOURCE_INVALID','源成果缺少可冻结的WGS84分析范围',422)
  source['record_fingerprint']=hashlib.sha256(_canonical({'plan':source_plan,'provenance':p,'summary':s})).hexdigest()
  plan={'algorithm_id':algorithm,'algorithm_version':VERSION,'request':copy.deepcopy(body),'source':source,'limitations':LIMITATIONS+(FVC_LIMITATIONS if algorithm=='fvc' else ZONAL_LIMITATIONS)}
  if kind=='agriculture_atlas':plan['limitations']=plan['limitations']+task['atlas_product'].get('limitations',[])+([source['quality']['warning']] if (source.get('quality') or {}).get('warning') else [])
  if algorithm=='fvc':
   if not spectral or index_id!='ndvi' or source_plan.get('goal')!='ndvi':raise GeoError('FVC_REQUIRES_SINGLE_NDVI','植被覆盖度仅接受单期真实原生NDVI任务，不能使用两期共同有效掩膜或其他指数',422)
   if any(k in body for k in ('dataset_id','field','threshold')):raise GeoError('FVC_INPUT_CONFLICT','FVC不接地块、字段或阈值；请明确NDVI端元',422)
   soil,vegetation=body.get('soil_ndvi'),body.get('vegetation_ndvi')
   if not all(finite(v) for v in (soil,vegetation)) or not -1<=soil<vegetation<=1 or vegetation-soil<=1e-6:
    raise GeoError('FVC_ENDMEMBERS_REQUIRED','请明确提供NDVI土壤/全植被端元，须满足-1≤土壤<植被≤1且间隔大于1e-6',422)
   plan.update(soil_ndvi=float(soil),vegetation_ndvi=float(vegetation),field='after_ndvi',formula='clip((NDVI-NDVI_soil)/(NDVI_vegetation-NDVI_soil),0,1)',endmember_source='explicit_user_values_not_locally_calibrated')
  else:
   if any(k in body for k in ('soil_ndvi','vegetation_ndvi')):raise GeoError('AGRICULTURE_INPUT_CONFLICT','逐地块统计不接FVC端元',422)
   dataset,geo=self._dataset(body.get('dataset_id'));field=body.get('field','delta_index' if kind=='agriculture_atlas' and source_plan.get('goal')=='change' else 'after_index' if spectral else 'classification')
   if spectral:
    if field not in ('after_index','delta_index') or field=='delta_index' and source_plan.get('goal')!='change':raise GeoError('AGRICULTURE_FIELD_INVALID','请选择源任务实际具备的单期指数或两期差值',422)
    value_band=('after_' if field=='after_index' else 'delta_')+index_id
    if kind=='agriculture_atlas':
     expected='delta_index' if source_plan.get('goal')=='change' else 'after_index'
     if field!=expected:raise GeoError('AGRICULTURE_FIELD_INVALID','请使用所选区域产品实际归档的单期指数或双期差值字段',422)
     value_band=source_plan['native']['value_band']
   else:
    if field!='classification':raise GeoError('AGRICULTURE_FIELD_INVALID','年度分类仅接受classification字段，不能继承光谱字段',422)
    if 'threshold' in body:raise GeoError('AGRICULTURE_INPUT_CONFLICT','分类地块统计按源产品耕作地类别，不接光谱阈值',422)
    value_band='classification'
   if 'threshold' in body:
    limits=(s.get('index') or p.get('algorithm') or {}).get('threshold_range')
    if not limits:
     from backend.analysis.spectral_indices import specification
     limits=specification(index_id).get('threshold_range',[-1,1])
    if isinstance(limits,dict):limits=[limits.get('min',-1),limits.get('max',1)]
    if field=='delta_index':limits=[limits[0]-limits[1],limits[1]-limits[0]]
    threshold=body['threshold']
    if not finite(threshold) or not limits[0]<=threshold<=limits[1]:raise GeoError('AGRICULTURE_THRESHOLD_INVALID','分区筛选阈值须在源指数声明范围内',422)
    plan['threshold']=float(threshold)
   plan.update(field=field,value_band=value_band,dataset={'id':dataset['id'],'name':dataset['name'],'sha256':dataset['sha256'],'version':dataset.get('version'),'feature_count':len(geo['features'])},overlap_policy='per_zone_may_repeat; union_reported_separately')
  plan['input_fingerprint']=hashlib.sha256(_canonical({k:v for k,v in plan.items() if k!='limitations'})).hexdigest()
  return plan
 def create(self,body):
  plan=self.prepare(body)
  if not ready():raise GeoError('AGRICULTURE_RUNTIME_UNAVAILABLE','本机原生栅格环境未就绪',503)
  steps=[('validate','复核已归档源与输入版本'),('read','读取原生统计及质量掩膜'),('compute','执行植被覆盖估算' if plan['algorithm_id']=='fvc' else '逐地块分区统计'),('archive','归档GIS、明细与来源')]
  task={'id':'agriculture_'+uuid.uuid4().hex[:14],'name':'植被覆盖度估算' if plan['algorithm_id']=='fvc' else '逐地块遥感统计','status':'queued','progress':0,'plan':plan,'snapshot':copy.deepcopy(plan),'nodes':[{'id':k,'name':v,'status':'pending'} for k,v in steps],'logs':[{'time':now(),'message':'已冻结源成果、方法与输入；未调用外部URL或临时样本'}],'result':None,'created_at':now()}
  with self.store.lock:
   if self.stopping:raise GeoError('SERVICE_STOPPING','服务正在关闭',503)
   if sum(t['status'] in ('queued','running') for t in self.list())>=2:raise GeoError('AGRICULTURE_BUSY','本机农业分析已有两个任务运行，请稍后重试',429)
   self.store.save('agriculture_task',task);thread=threading.Thread(target=self.execute,args=(task['id'],),daemon=True);self.threads.append(thread);thread.start()
  return task
 def check(self,id):
  if self.stopping:raise GeoError('INTERRUPTED','服务关闭，停止农业分析')
  if self.get(id)['status']=='cancelled':raise GeoError('CANCELLED','用户取消农业分析')
 def change(self,id,fn):
  with self.store.lock:
   t=self.get(id)
   if t['status'] in ('succeeded','failed','cancelled'):return False
   fn(t);self.store.save('agriculture_task',t);return True
 def _verify(self,plan,check=lambda:None):
  task,path=self._source(plan['source']['type'],plan['source']['task_id'])
  if _sha(path,check)!=plan['source']['artifact_sha256']:raise GeoError('AGRICULTURE_SOURCE_CHANGED','源原生栅格内容改变，不能沿用冻结输入',409)
  current=hashlib.sha256(_canonical({'plan':task['plan'],'provenance':task['result']['provenance'],'summary':task['result']['summary']})).hexdigest()
  if plan['source'].get('record_fingerprint') and current!=plan['source']['record_fingerprint']:raise GeoError('AGRICULTURE_SOURCE_CHANGED','源任务方法、来源或统计版本改变，不能沿用冻结输入',409)
  if plan.get('dataset'):
   dataset,_=self._dataset(plan['dataset']['id'])
   if dataset['sha256']!=plan['dataset']['sha256'] or dataset.get('version')!=plan['dataset']['version']:raise GeoError('AGRICULTURE_DATASET_CHANGED','用户地块版本改变，不能沿用冻结输入',409)
  return task,path
 def _read(self,task,path,plan,check=lambda:None):
  if plan['source']['type']=='agriculture_atlas':
   from backend.analysis.regional_raster import read_window
   geo=self._dataset(plan['dataset']['id'])[1] if plan.get('dataset') else None
   return read_window(task,path,geo,check)
  import numpy as np
  import rasterio
  from rasterio.features import geometry_mask
  with rasterio.open(path) as ds:
   if ds.driver!='GTiff' or ds.width*ds.height>MAX_CELLS:raise GeoError('AGRICULTURE_GRID_LIMIT','源不是本机上限内的原生GeoTIFF，不能降采样替代',422)
   names=list(ds.descriptions);kind=plan['source']['type'];result=task['result'];p=result['provenance']
   if kind=='production':
    band=plan.get('value_band',plan.get('field','after_ndvi'))
    if band not in names or 'aoi_mask' not in names or 'common_valid_mask' not in names:raise GeoError('AGRICULTURE_MASK_REQUIRED','源缺少指定指数和原生AOI/有效掩膜，不能从PNG推算',422)
    data=ds.read(names.index(band)+1).astype('float64');inside=ds.read(names.index('aoi_mask')+1)==1;valid=inside&(ds.read(names.index('common_valid_mask')+1)==1)&np.isfinite(data)
    crop=None
   else:
    data=ds.read(1).astype('float64');product=(p.get('product') or {}).get('id')
    if product=='io_lulc_2024':
     if names!=['classification','aoi_mask']:raise GeoError('AGRICULTURE_MASK_REQUIRED','IO源缺少明确选区掩膜',422)
     inside=ds.read(2)==1;known=[1,2,4,5,7,8,9,11];code=5
    elif product=='worldcover_2021_v200':
     boundary=result.get('boundary') or {};geoms=[f['geometry'] for f in boundary.get('features',[]) if f.get('geometry')]
     if not geoms or ds.crs.to_epsg()!=4326:raise GeoError('AGRICULTURE_MASK_REQUIRED','WorldCover源缺少归档选区或原生经纬度格网',422)
     inside=geometry_mask(geoms,out_shape=data.shape,transform=ds.transform,invert=True,all_touched=False);known=[10,20,30,40,50,60,70,80,90,95,100];code=40
    else:raise GeoError('AGRICULTURE_SOURCE_INVALID','尚未支持该分类产品的类别与面积契约',422)
    valid=inside&np.isin(data,known);crop=inside&(data==code)
   areas,method=_row_areas(ds)
   return {'data':data,'inside':inside,'valid':valid,'crop':crop,'row_areas':areas,'area_method':method,'transform':ds.transform,'crs':ds.crs.to_string(),'height':ds.height,'width':ds.width,'nodata':ds.nodata}
 def _fvc(self,plan,grid,directory,check):
  import numpy as np
  import rasterio
  from rasterio.transform import array_bounds,from_bounds
  from rasterio.warp import transform_bounds,reproject,Resampling
  from PIL import Image
  data=grid['data'];valid=grid['valid'];inside=grid['inside'];raw=(data-plan['soil_ndvi'])/(plan['vegetation_ndvi']-plan['soil_ndvi']);fvc=np.where(valid,np.clip(raw,0,1),np.nan)
  raw=np.where(valid,raw,np.nan);values=fvc[valid];counts=int(valid.sum());inside_count=int(inside.sum());distribution=_distribution(values)
  summary={'algorithm_id':'fvc','formula':plan['formula'],'unit':'fraction','mean_fvc':distribution['mean'],'median_fvc':distribution['median'],'p10_fvc':distribution['p10'],'p90_fvc':distribution['p90'],'min_fvc':distribution['min'],'max_fvc':distribution['max'],'std_fvc':distribution['std'],'native_pixel_count':inside_count,'valid_pixel_count':counts,'invalid_pixel_count':inside_count-counts,'valid_ratio':counts/inside_count if inside_count else 0.,'clamped_low_count':int((valid&(raw<0)).sum()),'clamped_high_count':int((valid&(raw>1)).sum()),'unclamped_min':float(raw[valid].min()) if counts else None,'unclamped_max':float(raw[valid].max()) if counts else None,'soil_ndvi':plan['soil_ndvi'],'vegetation_ndvi':plan['vegetation_ndvi'],'endmember_source':plan['endmember_source'],'valid_area_ha':_area(valid,grid['row_areas'])/10000,'unknown_area_ha':_area(inside&~valid,grid['row_areas'])/10000,'area_method':grid['area_method'],'native_crs':grid['crs'],'raster_dimensions':[grid['height'],grid['width']],'mean_area_weighted_fvc':float(np.sum(np.where(valid,fvc,0)*grid['row_areas'][:,None])/_area(valid,grid['row_areas'])) if counts else None,'estimated_vegetation_area_ha':float(np.sum(np.where(valid,fvc,0)*grid['row_areas'][:,None]))/10000 if counts else None}
  with rasterio.open(directory/'analysis.tif','w',driver='GTiff',height=grid['height'],width=grid['width'],count=5,dtype='float32',crs=grid['crs'],transform=grid['transform'],nodata=float('nan'),compress='deflate') as ds:
   for i,(name,array) in enumerate([('fvc',fvc),('fvc_unclamped',raw),('common_valid_mask',valid.astype('float32')),('aoi_mask',inside.astype('float32')),('after_ndvi',data)],1):ds.write(array.astype('float32'),i);ds.set_band_description(i,name)
   ds.update_tags(algorithm_id='fvc',algorithm_version=VERSION,source_task_id=plan['source']['task_id'],source_sha256=plan['source']['artifact_sha256'],soil_ndvi=str(plan['soil_ndvi']),vegetation_ndvi=str(plan['vegetation_ndvi']),formula=plan['formula'],endmember_source=plan['endmember_source'])
  check();bounds=array_bounds(grid['height'],grid['width'],grid['transform']);merc=transform_bounds(grid['crs'],'EPSG:3857',*bounds,densify_pts=21);width=min(1536,grid['width']);height=max(1,round(width*(merc[3]-merc[1])/(merc[2]-merc[0])))
  if height>1536:width=max(1,round(width*1536/height));height=1536
  target=np.full((height,width),np.nan,dtype='float32');reproject(fvc.astype('float32'),target,src_transform=grid['transform'],src_crs=grid['crs'],src_nodata=np.nan,dst_transform=from_bounds(*merc,width,height),dst_crs='EPSG:3857',dst_nodata=np.nan,resampling=Resampling.nearest)
  colors=['#e6ddb4','#bad8a2','#7dbd86','#378c65'];legend=[];rgba=np.zeros((height,width,4),dtype='uint8')
  for i,color in enumerate(colors):
   selected=np.isfinite(target)&(target>=i/4)&((target<(i+1)/4) if i<3 else target<=1);rgba[selected]=[int(color[j:j+2],16) for j in (1,3,5)]+[215];legend.append({'label':str(i*25)+'–'+str((i+1)*25)+'%','color':color})
  Image.fromarray(rgba).save(directory/'overlay.png')
  bbox=plan['source']['bbox'];w,s,e,n=bbox
  geo={'type':'FeatureCollection','features':[{'type':'Feature','id':'fvc-aoi','geometry':{'type':'Polygon','coordinates':[[[w,s],[e,s],[e,n],[w,n],[w,s]]]},'properties':{'name':'FVC实际分析范围','algorithm_id':'fvc','mean_fvc':summary['mean_fvc'],'source_task_id':plan['source']['task_id']}}]}
  with (directory/'summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
   writer=csv.writer(f);writer.writerow(['metric','value']);writer.writerows(summary.items())
  return summary,geo,{'bounds':list(transform_bounds('EPSG:3857','EPSG:4326',*merc,densify_pts=21)),'width':width,'height':height,'crs':'EPSG:3857','legend':legend,'statistics_from_preview':False}
 def _zonal(self,plan,grid,geo,check):
  import numpy as np
  from rasterio.features import geometry_mask
  from rasterio.warp import transform_geom
  features=[];rows=[];union=np.zeros(grid['data'].shape,dtype='bool');source_box=plan['source']['bbox'];production=plan['source']['type']=='production' or plan['source'].get('kind')=='spectral'
  for index,f in enumerate(geo['features']):
   check();geometry=f['geometry'];projected=transform_geom('EPSG:4326',grid['crs'],geometry)
   mask=geometry_mask([projected],out_shape=grid['data'].shape,transform=grid['transform'],invert=True,all_touched=False)
   covered=mask&grid['inside'];valid=covered&grid['valid'];union|=covered;count=int(covered.sum());valid_count=int(valid.sum());box=_bbox({'features':[f]})
   contained=bool(source_box and box and source_box[0]<=box[0] and source_box[1]<=box[1] and box[2]<=source_box[2] and box[3]<=source_box[3])
   disjoint=bool(source_box and box and (box[2]<=source_box[0] or box[0]>=source_box[2] or box[3]<=source_box[1] or box[1]>=source_box[3]))
   status='full' if contained and count>0 and count==int(mask.sum()) else 'outside' if not count and disjoint else 'partial';unknown=covered&~grid['valid']
   props=f.get('properties') or {};identity=f.get('id',props.get('id',props.get('编号',index+1)));name=props.get('name',props.get('名称',str(identity)))
   stats={'zone_id':str(identity),'zone_name':str(name),'coverage_status':status,'source_coverage_ratio':0. if status=='outside' else 1. if status=='full' else None,'covered_pixel_count':count,'valid_pixel_count':valid_count,'unknown_pixel_count':count-valid_count,'valid_ratio_covered':valid_count/count if count else None,'covered_area_ha':_area(covered,grid['row_areas'])/10000,'valid_area_ha':_area(valid,grid['row_areas'])/10000,'unknown_area_ha':_area(unknown,grid['row_areas'])/10000,'area_method':grid['area_method'],'source_field':plan['value_band'],'source_task_id':plan['source']['task_id']}
   stats.update(pixel_center_hit=bool(count),coverage_note='无原生像元中心命中，统计值为空；不从底图或未覆盖区域补值。' if not count else '仅统计地块与源AOI的原生像元中心交集。')
   if production:
    stats.update(_distribution(grid['data'][valid]))
    if 'threshold' in plan:
     selected=valid&(grid['data']>=plan['threshold']);stats.update(threshold=plan['threshold'],threshold_operator='gte',selected_pixel_count=int(selected.sum()),selected_area_ha=_area(selected,grid['row_areas'])/10000 if valid_count else None)
   else:
    crop=covered&grid['crop'];stats.update(cropland_pixel_count=int(crop.sum()),cropland_area_ha=_area(crop,grid['row_areas'])/10000 if valid_count else None,cropland_share_of_valid=int(crop.sum())/valid_count if valid_count else None)
   rows.append(stats);properties=copy.deepcopy(props);properties.update({'agri_'+key:value for key,value in stats.items()})
   features.append({'type':'Feature','id':f.get('id','zone-'+str(index+1)),'geometry':copy.deepcopy(geometry),'properties':properties})
  union_valid=union&grid['valid'];summary={'algorithm_id':'zonal_statistics','source_field':plan['value_band'],'zone_count':len(rows),'full_zone_count':sum(r['coverage_status']=='full' for r in rows),'partial_zone_count':sum(r['coverage_status']=='partial' for r in rows),'outside_zone_count':sum(r['coverage_status']=='outside' for r in rows),'no_valid_zone_count':sum(r['valid_pixel_count']==0 for r in rows),'no_pixel_hit_zone_count':sum(r['covered_pixel_count']==0 for r in rows),'sum_zone_covered_area_ha':sum(r['covered_area_ha'] for r in rows),'sum_zone_valid_area_ha':sum(r['valid_area_ha'] for r in rows),'unique_covered_pixel_count':int(union.sum()),'unique_valid_pixel_count':int(union_valid.sum()),'unique_covered_area_ha':_area(union,grid['row_areas'])/10000,'unique_valid_area_ha':_area(union_valid,grid['row_areas'])/10000,'unique_unknown_area_ha':_area(union&~grid['valid'],grid['row_areas'])/10000,'unique_valid_ratio_covered':int(union_valid.sum())/int(union.sum()) if union.any() else None,'area_method':grid['area_method'],'native_crs':grid['crs'],'overlap_policy':plan['overlap_policy'],'source_coverage_note':'partial地块只统计源AOI交集；valid_ratio_covered不代表全地块覆盖；未覆盖部分不外推。'}
  if production:
   summary['unique_distribution']=_distribution(grid['data'][union_valid])
   if 'threshold' in plan:
    selected=union_valid&(grid['data']>=plan['threshold']);summary.update(threshold=plan['threshold'],threshold_operator='gte',unique_selected_area_ha=_area(selected,grid['row_areas'])/10000 if union_valid.any() else None,unique_selected_pixel_count=int(selected.sum()))
  else:
   crop=union&grid['crop'];summary.update(sum_zone_cropland_area_ha=sum(r['cropland_area_ha'] or 0 for r in rows) if any(r['valid_pixel_count'] for r in rows) else None,unique_cropland_area_ha=_area(crop,grid['row_areas'])/10000 if union_valid.any() else None,unique_cropland_pixel_count=int(crop.sum()))
  return summary,{'type':'FeatureCollection','features':features},rows
 def execute(self,id):
  directory=self.output_dir/id;directory.mkdir(parents=True,exist_ok=True)
  try:
   task=self.get(id);plan=task['plan'];grid=None;source_task=None;path=None;summary=geo=image=rows=None
   for step,node in enumerate(task['nodes']):
    self.check(id)
    def start(t):t.update(status='running',progress=[2,15,35,90][step]);t['nodes'][step].update(status='running',started_at=now());t['logs'].append({'time':now(),'message':'开始：'+node['name']})
    if not self.change(id,start):return
    if node['id']=='validate':source_task,path=self._verify(plan,lambda:self.check(id))
    elif node['id']=='read':grid=self._read(source_task,path,plan,lambda:self.check(id))
    elif node['id']=='compute':
     if plan['algorithm_id']=='fvc':summary,geo,image=self._fvc(plan,grid,directory,lambda:self.check(id))
     else:
      dataset,input_geo=self._dataset(plan['dataset']['id']);summary,geo,rows=self._zonal(plan,grid,input_geo,lambda:self.check(id))
      fields=list(dict.fromkeys(key for row in rows for key in row))
      with (directory/'zones.csv').open('w',encoding='utf-8-sig',newline='') as f:
       writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    else:
     self._verify(plan,lambda:self.check(id));base='/api/agriculture/tasks/'+id+'/'
     provenance={'algorithm_id':plan['algorithm_id'],'algorithm_version':VERSION,'source_type':'derived_public_satellite_pixels' if plan['source'].get('kind','spectral' if plan['source']['type']=='production' else 'classification')=='spectral' else 'derived_public_classification','source_ref':copy.deepcopy(plan['source']),'dataset_ref':copy.deepcopy(plan.get('dataset')),'input_fingerprint':plan['input_fingerprint'],'area_method':grid['area_method'],'native_crs':grid['crs'],'source_year':plan['source']['reference_year'],'actual_dates':copy.deepcopy(plan['source']['actual_dates']),'source_bbox':plan['source']['bbox'],'method':plan.get('formula','原生像元中心地块分区统计'),'overlap_policy':plan.get('overlap_policy'),'endmember_source':plan.get('endmember_source')}
     if plan['source']['type']=='agriculture_atlas':provenance.update(source_window=grid['source_window'],source_full_shape=grid['source_full_shape'],statistics_resampled=False,regional_window_limit=7_000_000)
     if image:image['url']=base+'overlay.png'
     artifacts=[{'name':{'analysis.tif':'FVC完整原生格网GeoTIFF','overlay.png':'配准FVC预览PNG','result.geojson':'地理结果GeoJSON','result.json':'完整统计与来源报告','summary.csv':'FVC统计CSV','zones.csv':'逐地块统计CSV'}[name],'url':base+name,'type':{'tif':'geotiff','png':'png','geojson':'geojson','json':'json','csv':'csv'}[name.rsplit('.',1)[-1]]} for name in FILES[plan['algorithm_id']]]
     report={'title':'植被覆盖度估算报告' if plan['algorithm_id']=='fvc' else '逐地块遥感分区统计报告','findings':['结果直接来自所绑定的成功原生栅格，不从底图或PNG估计。','FVC端元为用户明确提供，未做本地校准。' if plan['algorithm_id']=='fvc' else '逐地块结果与像元并集结果分别统计，重叠不作为唯一面积重复计算。'],'limitations':copy.deepcopy(plan['limitations']),'input':copy.deepcopy(plan),'steps':copy.deepcopy(self.get(id)['nodes'])}
     result={'summary':summary,'geojson':geo,'image':image,'artifacts':artifacts,'provenance':provenance,'report':report}
     (directory/'result.geojson').write_text(json.dumps(geo,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
     (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
     def finish(t):t.update(status='succeeded',progress=100,result=result,finished_at=now());t['nodes'][step].update(status='succeeded',finished_at=now());t['logs'].append({'time':now(),'message':'GIS、明细和冻结来源已归档'})
     self.check(id);self.change(id,finish);return
    def complete(t):t['nodes'][step].update(status='succeeded',finished_at=now())
    self.change(id,complete)
  except Exception as error:
   code=error.code if isinstance(error,GeoError) else 'AGRICULTURE_FAILED';message=error.message if isinstance(error,GeoError) else '农业分析未成功；未填充模拟统计或替代数据'
   def fail(t):
    t.update(status='failed',error={'code':code,'message':message},finished_at=now());t['logs'].append({'time':now(),'message':message})
    for n in t['nodes']:
     if n['status'] in ('pending','running'):n.update(status='failed',message=message,finished_at=now())
   self.change(id,fail)
 def artifact(self,id,name):
  task=self.get(id)
  if task['status']!='succeeded' or name not in FILES[task['plan']['algorithm_id']]:raise GeoError('ARTIFACT_UNAVAILABLE','农业成果尚未成功或文件不存在',404)
  path=self.output_dir/id/name
  if not path.is_file():raise GeoError('ARTIFACT_UNAVAILABLE','农业成果文件不可用',404)
  return path
 def cancel(self,id):
  with self.store.lock:
   t=self.get(id)
   if t['status'] not in ('queued','running'):raise GeoError('INVALID_STATE','只有待处理或运行中的农业任务可取消',409)
   t.update(status='cancelled',finished_at=now());t['logs'].append({'time':now(),'message':'本农业任务取消，原始源成果不受影响'})
   for n in t['nodes']:
    if n['status'] in ('pending','running'):n.update(status='cancelled',finished_at=now())
   return self.store.save('agriculture_task',t)
 def retry(self,id):
  old=self.get(id)
  if old['status'] not in ('failed','cancelled'):raise GeoError('INVALID_STATE','只有失败或取消农业任务可重试',409)
  self._verify(old['plan']);return self.create(copy.deepcopy(old['plan']['request']))
 def close(self):
  self.stopping=True
  for thread in self.threads:thread.join(timeout=.5)
