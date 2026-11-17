"""Native IO2024 classification for an entire bounded city reference polygon.

Independent city workflow: fixed official catalog, one complete native UTM grid,
strip processing, no AOI-size relaxation in public single-window APIs.
"""
import csv
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from backend.paths import PROJECT_ROOT
from pathlib import Path

from backend.core.geo_services import GeoError
from backend.analysis.io_lulc_reader import CLASSES, CLOUD_CLASS, PRODUCT, fetch_catalog, validate_item
from backend.analysis.landcover_services import load_boundary

VERSION='city-io2024-1.0'
MAX_CELLS=100_000_000
CLASS_CODES=[c[0] for c in CLASSES]
LIMITATIONS=[
 'IO2024年度土地覆盖分类参考，不是2026实况、官方耕地调查或作物品种识别。',
 '采用2017 geoBoundaries公开参考市界，原始边界约72顶点，不能替代现行权威行政界。',
 'Crops类别5不等于法定耕地、永久基本农田、高标准农田或本季播种面积。',
 '水体类别1不能直接认定养殖塘、黄鳝养殖主体或禁捕违法对象。',
 '0缺测、10云及未知类别保留未知，不能作为非耕作地或非水域证据。',
 '全量指参考市界内产品原生10米像元中心；投影格网面积100平方米/像元，未作面积误差校正或本地精度验证。',
 '边界投影前按每边不超过0.005度加密；边缘像元按中心纳入，保留完整像元。',
 'PNG为Web Mercator显示重投影和最近邻降采样，定量统计仅来自原生格网；不同年度/产品不能直接作耕地增减。',
]

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def file_sha(path):
 digest=hashlib.sha256()
 with Path(path).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
 return digest.hexdigest()
def densify_geometry(geom,max_step=.005):
 def ring(points):
  out=[]
  for a,b in zip(points,points[1:]):
   steps=max(1,math.ceil(max(abs(a[0]-b[0]),abs(a[1]-b[1]))/max_step))
   for k in range(steps):out.append([a[0]+(b[0]-a[0])*k/steps,a[1]+(b[1]-a[1])*k/steps])
  return out+[list(points[-1])]
 if geom['type']=='Polygon':return {'type':'Polygon','coordinates':[ring(r) for r in geom['coordinates']]}
 if geom['type']=='MultiPolygon':return {'type':'MultiPolygon','coordinates':[[ring(r) for r in p] for p in geom['coordinates']]}
 raise GeoError('CITY_BOUNDARY_INVALID','市界必须为Polygon/MultiPolygon',422)

def select_binding(metadata,payload):
 """Official supercells overlap at edges; select the city's own UTM zone only."""
 bbox=metadata['bbox'];zone=int((sum((bbox[0],bbox[2]))/2+180)//6)+1;expected=32600+zone if bbox[1]>=0 else 32700+zone
 if int((bbox[0]+180)//6)+1!=int((bbox[2]+180)//6)+1:raise GeoError('CITY_MULTI_UTM_UNSUPPORTED','市界跨UTM区，不自动拼接投影',422)
 if any(l.get('rel')=='next' for l in payload.get('links',[]) if isinstance(l,dict)):raise GeoError('CITY_CATALOG_TRUNCATED','市级目录存在未遍历页，停止而非猜选',502)
 candidates=[]
 for item in payload.get('features',[]):
  binding=validate_item({'features':[item],'links':[]})
  if binding['epsg']==expected:candidates.append(binding)
 if len(candidates)!=1:raise GeoError('CITY_SINGLE_GRID_REQUIRED','没有唯一对应UTM区的2024官方超级瓦片',422)
 chosen=candidates[0];chosen['selection_reason']='市界完整处于UTM'+str(zone)+'；相邻官方超级瓦片边缘重叠，选择本区原生格网，后续验证完整覆盖。';chosen['catalog_item_ids']=[i.get('id') for i in payload.get('features',[])];return chosen

def preview(native_path,directory):
 import numpy as np
 import rasterio
 from rasterio.warp import reproject,transform_bounds,Resampling
 from rasterio.transform import from_bounds
 from PIL import Image
 directory=Path(directory)
 with rasterio.open(native_path) as src:
  bb=transform_bounds(src.crs,'EPSG:3857',*src.bounds,densify_pts=21);width=min(1800,src.width);height=max(1,round(width*(bb[3]-bb[1])/(bb[2]-bb[0])))
  if height>1800:width=max(1,round(width*1800/height));height=1800
  dest=np.zeros((height,width),dtype='uint8');inside=np.zeros((height,width),dtype='uint8')
  reproject(source=rasterio.band(src,1),destination=dest,src_transform=src.transform,src_crs=src.crs,dst_transform=from_bounds(*bb,width,height),dst_crs='EPSG:3857',src_nodata=0,dst_nodata=0,resampling=Resampling.nearest)
  reproject(source=rasterio.band(src,2),destination=inside,src_transform=src.transform,src_crs=src.crs,dst_transform=from_bounds(*bb,width,height),dst_crs='EPSG:3857',src_nodata=0,dst_nodata=0,resampling=Resampling.nearest)
  classes=np.zeros((height,width,4),dtype='uint8')
  for code,name,color in CLASSES+[CLOUD_CLASS]:classes[dest==code]=[int(color[i:i+2],16) for i in (1,3,5)]+[210]
  Image.fromarray(classes).save(directory/'classes.png')
  for code,name,color in [(5,'cropland',[65,208,145,220]),(1,'water',[65,155,223,220]),(2,'trees',[57,125,73,220])]:
   image=np.zeros_like(classes);image[dest==code]=color;Image.fromarray(image).save(directory/(name+'.png'))
  image=np.zeros_like(classes);image[(inside==1)&~np.isin(dest,CLASS_CODES)]=[150,150,150,150];Image.fromarray(image).save(directory/'unknown.png')
  return {'bounds':list(transform_bounds('EPSG:3857','EPSG:4326',*bb,densify_pts=21)),'width':width,'height':height,'display_crs':'EPSG:3857','display_resampling':'nearest; preview only','statistics_resampled':False,'attribution':PRODUCT['attribution']}

def account_city(boundary,metadata,directory,binding,source_path=None,progress=lambda value:None,max_cells=MAX_CELLS):
 import numpy as np
 import rasterio
 from rasterio.features import geometry_mask
 from rasterio.warp import transform_geom
 from rasterio.windows import Window
 directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
 geoms=[transform_geom('EPSG:4326','EPSG:'+str(binding['epsg']),densify_geometry(f['geometry'])) for f in boundary['features']]
 points=[]
 def walk(value):
  if len(value)==2 and all(isinstance(v,(int,float)) for v in value):points.append(value)
  else:
   for child in value:walk(child)
 for geom in geoms:walk(geom['coordinates'])
 left,bottom,right,top=min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)
 env={'GDAL_DISABLE_READDIR_ON_OPEN':'EMPTY_DIR','CPL_VSIL_CURL_ALLOWED_EXTENSIONS':'.tif','GDAL_HTTP_TIMEOUT':'45','GDAL_HTTP_CONNECTTIMEOUT':'15','GDAL_HTTP_MAX_RETRY':'2','GDAL_HTTP_RETRY_DELAY':'1','GDAL_CACHEMAX':96*1024*1024,'CPL_VSIL_CURL_USE_HEAD':'YES'}
 counts={code:0 for code in CLASS_CODES};unknown_count=cloud_count=inside_count=processed=0;row_records=[];histogram={};strips=0
 native=directory/'classification.tif';partial=directory/'classification.partial.tif'
 with rasterio.Env(**env),rasterio.open(source_path or binding['href']) as src:
  t=src.transform
  if src.crs.to_epsg()!=binding['epsg'] or src.count!=1 or src.dtypes[0]!='uint8' or src.nodata!=0 or abs(t.a-10)>1e-8 or abs(t.e+10)>1e-8 or t.b or t.d:raise GeoError('CITY_GRID_MISMATCH','IO源不是声明的北向上10米原生分类格网',502)
  if left<src.bounds.left or right>src.bounds.right or bottom<src.bounds.bottom or top>src.bounds.top:raise GeoError('CITY_SOURCE_COVERAGE_GAP','官方原生瓦片未完整覆盖参考市界，不报告全市结果',422)
  c0=math.floor((left-t.c)/10);c1=math.ceil((right-t.c)/10);r0=math.floor((t.f-top)/10);r1=math.ceil((t.f-bottom)/10);width,height=c1-c0,r1-r0
  if width<=0 or height<=0 or width*height>max_cells:raise GeoError('CITY_GRID_LIMIT','市界包络原生格网超过处理上限，不能降采样代替统计',422)
  full_window=Window(c0,r0,width,height);transform=src.window_transform(full_window)
  profile={'driver':'GTiff','width':width,'height':height,'count':2,'dtype':'uint8','crs':src.crs,'transform':transform,'nodata':0,'compress':'deflate','tiled':True,'blockxsize':512,'blockysize':512}
  progress({'stage':'read','shape':[height,width],'total_bbox_pixels':width*height,'strips':math.ceil(height/512),'source':binding['item_id']})
  with rasterio.open(partial,'w',**profile) as dst:
   for offset in range(0,height,512):
    rows=min(512,height-offset);window=Window(c0,r0+offset,width,rows);data=src.read(1,window=window,masked=False)
    if data.shape!=(rows,width):raise GeoError('CITY_SOURCE_READ_INCOMPLETE','市级原生条带读取不完整',502)
    inside=geometry_mask(geoms,out_shape=data.shape,transform=src.window_transform(window),invert=True,all_touched=False)
    strip_counts=[]
    for code in CLASS_CODES:
     per_row=((data==code)&inside).sum(axis=1,dtype='int64');counts[code]+=int(per_row.sum());strip_counts.append(per_row)
    known=inside&np.isin(data,CLASS_CODES);unknown=inside&~known;cloud=inside&(data==10);per_inside=inside.sum(axis=1,dtype='int64');per_unknown=unknown.sum(axis=1,dtype='int64');per_cloud=cloud.sum(axis=1,dtype='int64')
    inside_count+=int(per_inside.sum());unknown_count+=int(per_unknown.sum());cloud_count+=int(per_cloud.sum());strips+=1;processed+=rows*width
    clipped=np.where(inside,data,0).astype('uint8');dst.write(clipped,1,window=Window(0,offset,width,rows));dst.write(inside.astype('uint8'),2,window=Window(0,offset,width,rows))
    values,numbers=np.unique(data[inside],return_counts=True)
    for code,n in zip(values,numbers):histogram[str(int(code))]=histogram.get(str(int(code)),0)+int(n)
    for j in range(rows):row_records.append([offset+j,transform.f-(offset+j)*10,transform.f-(offset+j+1)*10,100,int(per_inside[j]),int(per_unknown[j]),int(per_cloud[j])]+[int(v[j]) for v in strip_counts])
    progress({'stage':'read','processed_bbox_pixels':processed,'total_bbox_pixels':width*height,'inside_pixel_count':inside_count,'crop_pixel_count':counts[5],'strip':strips})
   dst.set_band_description(1,'classification');dst.set_band_description(2,'aoi_mask')
   dst.update_tags(product_year='2024',source_product=PRODUCT['name'],source_item=binding['item_id'],boundary_sha256=metadata.get('sha256',''),boundary_year=str(metadata.get('boundary_year_represented','unknown')),mask_rule='pixel center in projected densified city polygon; all_touched=False',area_method='native UTM projected grid 100 m2 per pixel',license=PRODUCT['license'],attribution=PRODUCT['attribution'])
  native_bounds=list(rasterio.windows.bounds(full_window,src.transform));native_crs=src.crs.to_string();source_shape=[src.height,src.width]
 partial.replace(native)
 if processed!=width*height or inside_count==0 or sum(counts.values())+unknown_count!=inside_count:raise GeoError('CITY_AREA_BALANCE_ERROR','市级全量像元或面积未守恒',502)
 with (directory/'row_counts.csv').open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.writer(f);writer.writerow(['row','northing','southing','pixel_area_m2','inside_pixels','unknown_pixels','cloud_pixels']+['class_'+str(c) for c in CLASS_CODES]);writer.writerows(row_records)
 classes=[{'code':code,'name':name,'color':color,'pixel_count':counts[code],'area_ha':counts[code]*.01,'area_mu':counts[code]*.15,'share':counts[code]/inside_count} for code,name,color in CLASSES]
 summary={'reference_year':2024,'boundary_year':metadata.get('boundary_year_represented'),'inside_pixel_count':inside_count,'valid_pixel_count':sum(counts.values()),'unknown_pixel_count':unknown_count,'cloud_pixel_count':cloud_count,'cropland_pixel_count':counts[5],'cropland_ha':counts[5]*.01,'cropland_mu':counts[5]*.15,'water_ha':counts[1]*.01,'tree_ha':counts[2]*.01,'total_area_ha':inside_count*.01,'valid_area_ha':sum(counts.values())*.01,'unknown_area_ha':unknown_count*.01,'nodata_area_ha':unknown_count*.01,'cloud_area_ha':cloud_count*.01,'coverage_ratio':sum(counts.values())/inside_count,'cropland_share':counts[5]/inside_count,'native_crs':native_crs,'grid_resolution_m':10,'pixel_area_m2':100,'area_method':'native_utm_projected_grid','mask_rule':'pixel center; all_touched=False; boundary densified <=0.005 degrees','raster_dimensions':[height,width],'processed_bbox_pixel_count':processed,'strips_read':strips,'source_class_histogram':histogram,'is_official':False,'accuracy_note':'未做仙桃本地独立精度或面积误差校正','method':'IO2024原生10米分类、2017公开参考市界中心掩膜、100平方米/像元投影面积'}
 return {'summary':summary,'classes':classes,'bounds':native_bounds,'native_path':str(native),'native_window':[c0,r0,width,height],'native_transform':list(transform)[:6],'source_shape':source_shape,'native_sha256':file_sha(native)}

def produce(directory=None,progress=lambda value:None):
 directory=Path(directory or PROJECT_ROOT/'data/agriculture-atlas/xiantao-2024-classification');directory.mkdir(parents=True,exist_ok=True)
 started=stamp();(directory/'status.json').write_text(json.dumps({'status':'running','started_at':started,'version':VERSION},ensure_ascii=False,indent=2))
 try:
  boundary,metadata=load_boundary();payload=fetch_catalog(metadata['bbox']);binding=select_binding(metadata,payload)
  (directory/'source_catalog.json').write_bytes(canonical(payload));(directory/'boundary.geojson').write_bytes(canonical(boundary));(directory/'boundary_metadata.json').write_bytes(canonical(metadata))
  result=account_city(boundary,metadata,directory,binding,progress=progress);image=preview(directory/'classification.tif',directory)
  with (directory/'classes.csv').open('w',encoding='utf-8-sig',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=['code','name','pixel_count','area_ha','area_mu','share','color']);writer.writeheader();writer.writerows(result['classes'])
  result.update({'id':'xiantao-2024-classification','name':'仙桃市2024土地覆盖与耕作地参考底数','status':'succeeded','created_at':stamp(),'started_at':started,'version':VERSION,'boundary':boundary,'image':image,'provenance':{'source_type':'public','source_product':PRODUCT,'source_binding':binding,'boundary_source':metadata,'source_url':binding['href'],'native_sha256':result['native_sha256'],'algorithm_version':VERSION,'attribution':PRODUCT['attribution'],'limitations':LIMITATIONS,'source_catalog_sha256':file_sha(directory/'source_catalog.json'),'native_statistics_resampled':False,'external_calls':'fixed official IO STAC and public COG only'},'limitations':LIMITATIONS})
  result['artifacts']=[{'file':p.name,'bytes':p.stat().st_size,'sha256':file_sha(p)} for p in sorted(directory.iterdir()) if p.is_file() and p.name not in ('result.json','status.json') and not p.name.endswith('.partial.tif')]
  (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n');(directory/'status.json').write_text(json.dumps({'status':'succeeded','started_at':started,'finished_at':stamp(),'result_sha256':file_sha(directory/'result.json')},ensure_ascii=False,indent=2)+'\n');return result
 except Exception as error:
  (directory/'status.json').write_text(json.dumps({'status':'failed','started_at':started,'finished_at':stamp(),'error_type':type(error).__name__,'error':str(error),'no_substitute_data':True},ensure_ascii=False,indent=2)+'\n');raise
