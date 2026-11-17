"""Actual Landsat city spectra, separate from bounded interactive AOIs.

The complete public reference polygon is masked on a selected native 30m grid.
Unsigned COG identities are archived; short-lived access tokens stay in memory.
"""
import copy,csv,hashlib,json,math,time
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlencode
from backend.core.geo_services import GeoError
from backend.analysis.landcover_services import load_boundary
from backend.analysis.landsat_reader import LandsatReader,asset_url,QUALITY_POLICY
from backend.agriculture.city_classification import densify_geometry
from backend.analysis.spectral_indices import evaluate_index,specification

VERSION='city-landsat-discovery-1.2'
MAX_CELLS=7_000_000
ASSETS={'green':'green','red':'red','nir':'nir08','swir1':'swir16','qa_pixel':'qa_pixel','qa_radsat':'qa_radsat'}
PERIODS={'before':['2026-09-01','2026-09-30'],'after':['2026-10-01','2026-11-14']}
STAC_SEARCH='https://planetarycomputer.microsoft.com/api/stac/v1/search'
SCENES={'ndvi':['grain','sowing','disaster'],'ndmi':['grain','disaster'],'mndwi':['breeding','tailwater','fishing','rivers','flood'],'change':['sowing','grain','high_standard']}
TITLES={'ndvi':'仙桃2026植被光谱观察（NDVI）','ndmi':'仙桃2026植被水分线索（NDMI）','mndwi':'仙桃2026水域光谱线索（MNDWI）','change':'仙桃2026秋季植被变化线索（NDVI）'}
LEGENDS={
 'ndvi':[{'color':'#a98a70','label':'NDVI < 0'},{'color':'#d5c58c','label':'0 ≤ NDVI < 0.2'},{'color':'#9bbb85','label':'0.2 ≤ NDVI < 0.5'},{'color':'#347c56','label':'NDVI ≥ 0.5'}],
 'ndmi':[{'color':'#b28a65','label':'NDMI < -0.2'},{'color':'#d7ce9b','label':'-0.2 ≤ NDMI < 0'},{'color':'#82b8b1','label':'0 ≤ NDMI < 0.2'},{'color':'#337f9e','label':'NDMI ≥ 0.2'}],
 'mndwi':[{'color':'#9c9b83','label':'MNDWI ≤ 0'},{'color':'#3f91bf','label':'MNDWI > 0（光谱条件，非水体分类）'}],
 'change':[{'color':'#ba7467','label':'ΔNDVI ≤ -0.1'},{'color':'#d7ccaa','label':'-0.1 < ΔNDVI < 0.1'},{'color':'#438b68','label':'ΔNDVI ≥ 0.1'}]}
LIMITATIONS=[
 '范围采用2017年geoBoundaries公开参考仙桃市界，不替代当前权威行政界、确权地块或项目工程边界。',
 '2026实际单景观测不是2026年度分类或连续逐日监测；请求窗口与实际观测日期分别保留。',
 'NDVI表示植被光谱，NDMI为植被水分线索；不认定油菜/小麦品种、播种面积、土壤含水率或干旱等级。',
 'MNDWI阈值是光谱条件，不认定养殖塘、黄鳝物种、尾水达标、水质浓度或违法捕捞。',
 'NDVI变化始终后期减前期，可能受收割、耕整、积水、植被发育等影响，不直接认定播种或灾损。',
 'QA_PIXEL bits0–5及所有非零QA_RADSAT剔除；负反射率和非正分母未知，不补0。有效覆盖不等于分类准确率。',
 '原生30米投影格网计量，900平方米像元为0.09公顷；非10米独立观测，非地籍或作业面积。',
 '2024 Crops类别5仅作为历史参考分层，最近邻采样到30米；其参考像元数/面积不能替代2024原生10米类别面积或2026播种面积。',
 'Landsat8/9 C2 L2同系列反射率按实际资产定标，未额外进行OLI/OLI2跨传感器辐射归一化。',
]

def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def sha_file(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def write_json(path,value):
 path=Path(path);tmp=path.with_suffix(path.suffix+'.partial');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n');tmp.replace(path)
def quality_mask(qa,rad):
 import numpy as np
 qa=np.asarray(qa);rad=np.asarray(rad)
 finite=np.isfinite(qa)&np.isfinite(rad)&(qa>=0)&(qa<=65535)&(rad>=0)&(rad<=65535)&(qa==np.floor(qa))&(rad==np.floor(rad))
 q=np.where(finite,qa,65535).astype('uint16');r=np.where(finite,rad,65535).astype('uint16')
 return finite&((q&63)==0)&(r==0)
def select_after(candidates,minimum=.7):
 if not candidates:raise GeoError('CITY_NO_OBSERVATION','没有可用后期观测',422)
 accepted=[c for c in candidates if c['qa_valid_ratio']>=minimum]
 chosen=max(accepted,key=lambda c:(c['datetime'],c['scene_id'])) if accepted else max(candidates,key=lambda c:(c['qa_valid_ratio'],c['datetime']))
 return chosen,('latest_actual_city_qa_at_least_'+str(minimum) if accepted else 'highest_actual_city_qa_below_target'),not bool(accepted)
def index_array(index_id,bands,qa,rad,inside):
 import numpy as np
 required=specification(index_id)['band_keys'];base=np.asarray(inside,dtype=bool)&quality_mask(qa,rad)
 for key in required:base &= np.isfinite(bands[key])&(bands[key]>=0)
 value,domain=evaluate_index(index_id,bands);valid=base&domain['valid'];return np.where(valid,value,np.nan).astype('float32'),valid

def production_request(periods=None,cloud_max=80,max_candidates=3,max_catalog_items=60,minimum_qa=.7,today=None):
 periods=copy.deepcopy(PERIODS if periods is None else periods);latest=(today or datetime.now(timezone.utc).date())-timedelta(days=1)
 try:
  if set(periods)!={'before','after'}:raise ValueError()
  parsed={}
  for epoch,interval in periods.items():
   if not isinstance(interval,(list,tuple)) or len(interval)!=2 or any(not isinstance(v,str) or date.fromisoformat(v).isoformat()!=v for v in interval):raise ValueError()
   start,end=map(date.fromisoformat,interval)
   if start<date(2013,4,11) or start>end or end>latest or (end-start).days>61:raise ValueError()
   parsed[epoch]=(start,end)
  if parsed['before'][1]>=parsed['after'][0]:raise ValueError()
  if isinstance(cloud_max,bool) or not isinstance(cloud_max,(float,int)) or not math.isfinite(cloud_max) or not 0<=cloud_max<=100:raise ValueError()
  if isinstance(minimum_qa,bool) or not isinstance(minimum_qa,(float,int)) or not math.isfinite(minimum_qa) or not 0<minimum_qa<=1:raise ValueError()
  if type(max_candidates)!=int or not 1<=max_candidates<=4 or type(max_catalog_items)!=int or not max_candidates<=max_catalog_items<=120:raise ValueError()
 except (TypeError,ValueError,OverflowError):raise GeoError('CITY_REQUEST_INVALID','日期须为完整历史日且每窗最多62天、前期早于后期；候选1–4、目录上限120、云量0–100、QA门槛(0,1]',400)
 return {'schema_version':'city-spectral-request-1.0','periods':periods,'scene_cloud_max':cloud_max,'max_qa_candidates_per_period':max_candidates,'max_catalog_items_per_period':max_catalog_items,'minimum_city_qa_ratio':minimum_qa,'collection':'landsat-c2-l2','platforms':['landsat-8','landsat-9'],'scope':'fixed_public_xiantao_reference_boundary','complete_through':latest.isoformat(),'max_native_cells':MAX_CELLS,'max_asset_reads':4*max_candidates+6}

def _ring_contains(point,ring):
 x,y=point;inside=False
 for a,b in zip(ring,ring[1:]):
  ax,ay=a;bx,by=b;cross=(bx-ax)*(y-ay)-(by-ay)*(x-ax)
  if abs(cross)<=1e-10 and min(ax,bx)-1e-10<=x<=max(ax,bx)+1e-10 and min(ay,by)-1e-10<=y<=max(ay,by)+1e-10:return True
  if (ay>y)!=(by>y) and x<(bx-ax)*(y-ay)/(by-ay)+ax:inside=not inside
 return inside

def covers_city_geometry(geometry,boundary):
 """Conservative STAC footprint screen; actual native grid and QA remain final.

 Landsat single-ring Polygon footprints are supported. Unsupported multipart or
 holed footprints are rejected instead of treating their bbox as full coverage.
 """
 try:
  if geometry.get('type')!='Polygon' or len(geometry['coordinates'])!=1:return False
  ring=geometry['coordinates'][0]
  if not 4<=len(ring)<=1000 or ring[0]!=ring[-1] or any(len(p)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in p) for p in ring):return False
  def orientation(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
  def crosses(a,b,c,d):return orientation(a,b,c)*orientation(a,b,d)<-1e-20 and orientation(c,d,a)*orientation(c,d,b)<-1e-20
  for feature in boundary['features']:
   geom=densify_geometry(feature['geometry']);polygons=[geom['coordinates']] if geom['type']=='Polygon' else geom['coordinates'] if geom['type']=='MultiPolygon' else []
   if not polygons:return False
   for polygon in polygons:
    outer=polygon[0]
    if not all(_ring_contains(p,ring) for p in outer):return False
    if any(crosses(a,b,c,d) for a,b in zip(outer,outer[1:]) for c,d in zip(ring,ring[1:])):return False
  return bool(boundary['features'])
 except (TypeError,KeyError,IndexError):return False

def scene_contract(feature,period=None):
 if not isinstance(feature,dict) or not isinstance(feature.get('properties'),dict):raise GeoError('CITY_SOURCE_CONTRACT','条目属性格式无效',422)
 props=feature.get('properties',{});ident=feature.get('id');date=props.get('datetime','')
 try:
  observed=datetime.fromisoformat(date.replace('Z','+00:00'));day=observed.date();latest=datetime.now(timezone.utc).date()-timedelta(days=1)
  valid=observed.tzinfo is not None and '2013-04-11'<=day.isoformat()<=latest.isoformat() and (period is None or period[0]<=day.isoformat()<=period[1])
 except (TypeError,ValueError,AttributeError):valid=False
 if not isinstance(ident,str) or not ident.startswith(('LC08_','LC09_')) or not valid or feature.get('collection','landsat-c2-l2')!='landsat-c2-l2' or props.get('platform') not in (None,'landsat-8','landsat-9'):raise GeoError('CITY_SOURCE_CONTRACT','场景日期、集合或传感器不符合本次请求',422)
 assets={}
 for key,name in ASSETS.items():
  src=feature.get('assets',{}).get(name)
  if not isinstance(src,dict):raise GeoError('CITY_MISSING_BAND','缺少真实资产'+name,422)
  item={'href':asset_url(src.get('href')),'asset_key':name}
  if key not in ('qa_pixel','qa_radsat'):
   metadata=src.get('raster:bands',[])
   if not isinstance(metadata,list) or len(metadata)!=1 or not isinstance(metadata[0],dict):raise GeoError('CITY_CALIBRATION_MISSING','缺少逐资产定标参数',422)
   m=metadata[0];scale=m.get('scale');offset=m.get('offset')
   if not isinstance(scale,(float,int)) or not isinstance(offset,(float,int)) or not math.isclose(scale,.0000275,rel_tol=1e-9) or not math.isclose(offset,-.2,abs_tol=1e-12) or m.get('nodata')!=0 or m.get('spatial_resolution')!=30:raise GeoError('CITY_CALIBRATION_UNSUPPORTED','定标/分辨率不符合实际Landsat C2 L2 SR契约',422)
   item.update(scale=scale,offset=offset,nodata=0)
  assets[key]=item
 return {'scene_id':ident,'datetime':date,'platform':props.get('platform'),'scene_cloud_cover':props.get('eo:cloud_cover'),'assets':assets,'stac_item_sha256':hashlib.sha256(canonical(feature)).hexdigest()}

def prepare_catalogs(boundary,metadata,request,catalog_dir,mode='live',reader=None,frozen_dir=None):
 """Two bounded official searches, or immutable local snapshots; never follow links."""
 from rasterio.features import bounds
 if mode not in ('live','frozen'):raise GeoError('CITY_CATALOG_MODE','目录模式须为live或frozen',400)
 root=Path(catalog_dir)
 if (root/'catalog-manifest.json').exists():raise GeoError('CITY_CATALOG_EXISTS','该目录已有冻结检索证据，请使用新的运行目录，或以其作为frozen输入',409)
 root.mkdir(parents=True,exist_ok=True);reader=reader or LandsatReader();boxes=[bounds(f['geometry']) for f in boundary['features']];bbox=[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)];contracts={};records=[];screens={}
 request={**request,'bbox':bbox,'boundary_canonical_sha256':hashlib.sha256(canonical(boundary)).hexdigest()};write_json(root/'production-request.json',request)
 for epoch,period in request['periods'].items():
  params={'collections':'landsat-c2-l2','bbox':','.join(str(v) for v in bbox),'datetime':period[0]+'T00:00:00Z/'+period[1]+'T23:59:59Z','query':json.dumps({'platform':{'in':['landsat-8','landsat-9']},'eo:cloud_cover':{'lte':request['scene_cloud_max']}},separators=(',',':')),'limit':request['max_catalog_items_per_period'],'sortby':'-datetime'};url=STAC_SEARCH+'?'+urlencode(params)
  if mode=='live':doc=reader.fetch_json(url,lambda:None);input_hash=hashlib.sha256(canonical(doc)).hexdigest();input_file=None
  else:
   base=Path(frozen_dir or catalog_dir);source=base/('catalog-'+epoch+'.json')
   if not source.is_file():source=base/('landsat-'+period[0]+'.json')
   if not source.is_file():raise GeoError('CITY_FROZEN_CATALOG_MISSING','冻结目录缺少'+epoch+'时间窗的真实快照',422)
   if source.stat().st_size>4*1024*1024:raise GeoError('CITY_FROZEN_CATALOG_INVALID','冻结目录超过4MB受控上限',422)
   manifest=base/'catalog-manifest.json'
   if manifest.is_file():
    try:entries=json.loads(manifest.read_text())['catalogs'];record=next((item for item in entries if item.get('file')==source.name),None)
    except (ValueError,KeyError,TypeError,AttributeError):raise GeoError('CITY_FROZEN_CATALOG_INVALID','冻结清单格式无效',422)
    if not record or record.get('sha256')!=sha_file(source):raise GeoError('CITY_FROZEN_HASH_MISMATCH','冻结目录文件缺少清单记录或SHA与原始清单不一致',422)
   try:doc=json.loads(source.read_text(),parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
   except (ValueError,UnicodeError):raise GeoError('CITY_FROZEN_CATALOG_INVALID','冻结目录不是有效JSON',422)
   input_hash=sha_file(source);input_file=source.name
  if not isinstance(doc,dict) or not isinstance(doc.get('features'),list):raise GeoError('CITY_CATALOG_SCHEMA','真实目录没有features数组',502)
  accepted=[];rejected=[];seen=set();items=doc['features'][:request['max_catalog_items_per_period']]
  for feature in items:
   try:
    if not isinstance(feature,dict):raise GeoError('CITY_SOURCE_CONTRACT','条目非对象',422)
    if not isinstance(feature.get('properties'),dict):raise GeoError('CITY_SOURCE_CONTRACT','条目属性格式无效',422)
    cloud=feature.get('properties',{}).get('eo:cloud_cover')
    if isinstance(cloud,bool) or not isinstance(cloud,(float,int)) or not math.isfinite(cloud) or not 0<=cloud<=request['scene_cloud_max']:raise GeoError('CITY_CLOUD_FILTER','整景云率缺失或超限',422)
    if not covers_city_geometry(feature.get('geometry',{}),boundary):raise GeoError('CITY_FOOTPRINT_FILTER','实际STAC足迹未完整包含参考市界或几何不受支持',422)
    # Whitelist metadata and unsigned assets; provider links/tokens are never archived.
    compact={'type':'Feature','id':feature.get('id'),'collection':feature.get('collection','landsat-c2-l2'),'geometry':feature['geometry'],'properties':{k:feature.get('properties',{}).get(k) for k in ('datetime','platform','eo:cloud_cover')},'assets':{name:copy.deepcopy(feature.get('assets',{}).get(name)) for name in ASSETS.values()}}
    for asset in compact['assets'].values():
     if isinstance(asset,dict):
      for key in list(asset):
       if key not in ('href','raster:bands','proj:epsg','proj:shape','proj:transform'):asset.pop(key)
    scene=scene_contract(compact,period)
    if scene['scene_id'] in seen:continue
    seen.add(scene['scene_id']);accepted.append((scene,compact))
   except GeoError as error:rejected.append({'scene_id':feature.get('id') if isinstance(feature,dict) else None,'reason':error.code})
  accepted.sort(key=lambda pair:(pair[0]['datetime'],-pair[0]['scene_cloud_cover'],pair[0]['scene_id']),reverse=True);selected=accepted[:request['max_qa_candidates_per_period']]
  snapshot={'type':'FeatureCollection','features':[f for _,f in accepted],'links':[]};encoded=canonical(snapshot)
  if b'sig=' in encoded or b'?st=' in encoded:raise GeoError('CITY_SIGNATURE_PERSISTENCE','目录含临时访问签名，拒绝归档',500)
  file='catalog-'+epoch+'.json';write_json(root/file,snapshot);contracts[epoch]=[s for s,_ in selected];screens[epoch]={'catalog_items_returned':len(doc['features']),'catalog_items_inspected':len(items),'eligible_full_city_items':len(accepted),'qa_candidates':[s['scene_id'] for s,_ in selected],'rejected':rejected,'qa_candidate_cap':request['max_qa_candidates_per_period'],'bounded_candidate_scope':len(accepted)>len(selected),'catalog_pagination_not_followed':any(isinstance(link,dict) and link.get('rel')=='next' for link in doc.get('links',[])),'catalog_item_cap_reached':len(doc['features'])>=request['max_catalog_items_per_period']}
  records.append({'epoch':epoch,'mode':mode,'endpoint':STAC_SEARCH,'request_url':url,'retrieved_at':stamp() if mode=='live' else None,'frozen_input_file':input_file,'input_sha256':input_hash,'file':file,'sha256':sha_file(root/file),'unsigned_assets_only':True})
  if not selected:raise GeoError('CITY_NO_FULL_COVERAGE_CANDIDATE',epoch+'时间窗没有通过日期、云率、实际足迹和波段契约的全市候选；未换年或伪造结果',422)
 evidence={'request':request,'catalogs':records,'screening':screens,'selection_scope':'bounded candidates only; not claimed exhaustive across unqueried pages or excluded items'};write_json(root/'catalog-manifest.json',evidence);return contracts,evidence

def choose_observation(scenes,boundary,reader,minimum,epoch,progress=lambda v:None):
 best=None;assessments=[];failures=[]
 for scene in scenes:
  progress('QA '+epoch+' '+scene['scene_id'])
  try:candidate=quality_candidate(scene,boundary,reader)
  except GeoError as error:failures.append({'scene_id':scene['scene_id'],'reason':error.code});continue
  candidate['assessment']['epoch']=epoch;assessments.append(candidate['assessment'])
  selected,_,_=select_after(assessments,minimum)
  if selected['scene_id']==scene['scene_id']:best=candidate
 if best is None:raise GeoError('CITY_NO_OBSERVATION',epoch+'所有候选实际QA读取失败；未生成替代结果',502)
 _,policy,below=select_after(assessments,minimum);return best,assessments,{'policy':policy,'minimum_city_qa_ratio':minimum,'below_target':below,'read_failures':failures}

def grid_for_dataset(ds,boundary,max_cells=MAX_CELLS):
 from rasterio.warp import transform_geom
 from rasterio.features import bounds,geometry_mask
 from rasterio.windows import Window,from_bounds
 if ds.crs is None or not ds.crs.is_projected or ds.crs.linear_units not in ('metre','meter') or not math.isclose(ds.transform.a,30,abs_tol=1e-6) or not math.isclose(ds.transform.e,-30,abs_tol=1e-6) or ds.transform.b!=0 or ds.transform.d!=0:raise GeoError('CITY_GRID_UNSUPPORTED','要求米制北向30米原生格网',422)
 geoms=[transform_geom('EPSG:4326',ds.crs,densify_geometry(f['geometry'])) for f in boundary['features']]
 boxes=[bounds(g) for g in geoms];box=[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
 if not (ds.bounds.left<=box[0] and ds.bounds.bottom<=box[1] and ds.bounds.right>=box[2] and ds.bounds.top>=box[3]):raise GeoError('CITY_SCENE_INCOMPLETE','实际资产不完整覆盖参考市界',422)
 w=from_bounds(*box,ds.transform);c0=math.floor(w.col_off);r0=math.floor(w.row_off);c1=math.ceil(w.col_off+w.width);r1=math.ceil(w.row_off+w.height);window=Window(c0,r0,c1-c0,r1-r0)
 if window.width*window.height>max_cells:raise GeoError('CITY_GRID_LIMIT','原生市界窗口超过700万像元控制上限',422)
 transform=ds.window_transform(window);shape=(int(window.height),int(window.width));mask=geometry_mask(geoms,out_shape=shape,transform=transform,invert=True,all_touched=False)
 if not mask.any():raise GeoError('CITY_EMPTY_MASK','市界没有原生像元中心',422)
 return {'shape':shape,'transform':transform,'crs':ds.crs,'window':window,'inside':mask,'cell_area_m2':abs(transform.a*transform.e)}

def validate_native_asset(ds,band,asset,reference_grid=None):
 """Check the opened COG, rather than trusting STAC's nominal resolution.

 Each band's native grid must really be a north-up 30m metre grid. Origins may
 differ and are aligned explicitly below; a coarser source is never silently
 upsampled. QA_RADSAT zero means unsaturated and remains a valid quality value.
 """
 transform=ds.transform
 if ds.crs is None or not ds.crs.is_projected or ds.crs.linear_units not in ('metre','meter') or not all(math.isfinite(v) for v in tuple(transform)[:6]) or not math.isclose(transform.a,30,abs_tol=1e-6) or not math.isclose(transform.e,-30,abs_tol=1e-6) or transform.b!=0 or transform.d!=0:
  raise GeoError('CITY_ASSET_GRID_MISMATCH','实际Landsat资产不是米制北向30米原生格网，未重采样替代',422)
 if reference_grid is not None and ds.crs!=reference_grid['crs']:
  raise GeoError('CITY_ASSET_GRID_MISMATCH','同一场景波段实际投影不一致，未隐式跨投影替代',422)
 if ds.count!=1 or ds.dtypes[0]!='uint16':
  raise GeoError('CITY_ASSET_ENCODING_MISMATCH','实际Landsat资产必须为单波段uint16原始DN/质量位',422)
 if band not in ('qa_pixel','qa_radsat') and (asset.get('nodata')!=0 or ds.nodata!=asset['nodata']):
  raise GeoError('CITY_ASSET_ENCODING_MISMATCH','实际地表反射率资产的缺测值与定标契约不一致',422)
 return {'native_pixel_size_m':[float(transform.a),float(-transform.e)],'native_band_count':ds.count,'native_dtype':ds.dtypes[0],'native_nodata':ds.nodata,'native_grid_validated':True}

CATALOG_EVIDENCE_FILES=('production-request.json','catalog-before.json','catalog-after.json','catalog-manifest.json','candidate-selection.json')
CROP_REFERENCE_FILE='crop-reference-2024.tif'
CROP_REFERENCE_METADATA='crop-reference-2024.json'

def archive_crop_reference(source,directory,expected_sha):
 """Include the verified historical reference, so the ZIP verifies on its own."""
 source,directory=Path(source),Path(directory);metadata=source.parent/'result.json'
 if not source.is_file() or source.is_symlink() or not metadata.is_file() or sha_file(source)!=expected_sha:
  raise GeoError('CITY_CROP_REFERENCE_CHANGED','历史分类参考缺失或SHA不匹配，停止归档',409)
 try:original=json.loads(metadata.read_text());summary=original['summary'];product=original.get('provenance',{}).get('source_product',{})
 except (ValueError,KeyError,TypeError):raise GeoError('CITY_CROP_REFERENCE_INVALID','历史分类来源元数据无效',422)
 if summary.get('reference_year')!=2024 or product.get('id')!='io_lulc_2024' or original.get('native_sha256')!=expected_sha:
  raise GeoError('CITY_CROP_REFERENCE_INVALID','参考源不是已冻结的IO2024原生分类，不冒称2024作物范围',422)
 info={'schema_version':'city-crop-reference-1.0','file':CROP_REFERENCE_FILE,'sha256':expected_sha,'source_product_id':product['id'],'source_product_name':product.get('name'),'product_year':2024,'crop_class_code':5,'native_resolution_m':summary.get('grid_resolution_m'),'native_crops_area_ha':summary.get('cropland_ha'),'source_result_sha256':sha_file(metadata),'source_binding':copy.deepcopy(original.get('provenance',{}).get('source_binding',{})),'source_url':original.get('provenance',{}).get('source_url'),'license':product.get('license'),'boundary_year':summary.get('boundary_year'),'note':'本包复制已匹配SHA的2024分类参考供离线核对；它不是光谱观测年度的农田认定。'}
 data=source.read_bytes()
 if hashlib.sha256(data).hexdigest()!=expected_sha:raise GeoError('CITY_CROP_REFERENCE_CHANGED','复制历史分类参考时源发生改变',409)
 destination=directory/CROP_REFERENCE_FILE;partial=destination.with_suffix('.tif.partial');partial.write_bytes(data);partial.replace(destination);write_json(directory/CROP_REFERENCE_METADATA,info)
 return [{'file':name,'bytes':(directory/name).stat().st_size,'sha256':sha_file(directory/name)} for name in (CROP_REFERENCE_FILE,CROP_REFERENCE_METADATA)]

def archive_catalog_evidence(catalog_dir,directory):
 """Copy the bounded original request/selection evidence into each portable result."""
 catalog_dir,directory=Path(catalog_dir),Path(directory);prepared={}
 for name in CATALOG_EVIDENCE_FILES:
  source=catalog_dir/name
  if not source.is_file() or source.is_symlink() or source.stat().st_size>4*1024*1024:
   raise GeoError('CITY_CATALOG_EVIDENCE_MISSING','生产目录缺少受控原始请求或选景证据：'+name,422)
  data=source.read_bytes()
  try:doc=json.loads(data,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
  except (ValueError,UnicodeError):raise GeoError('CITY_CATALOG_EVIDENCE_INVALID','生产来源证据不是有效JSON：'+name,422)
  if not isinstance(doc,dict) or any(token in data for token in (b'sig=',b'?st=')):
   raise GeoError('CITY_CATALOG_EVIDENCE_INVALID','生产来源证据格式不完整或包含临时签名',422)
  prepared[name]=(data,doc)
 manifest=prepared['catalog-manifest.json'][1]
 try:
  for name in ('catalog-before.json','catalog-after.json'):
   declared=next(v for v in manifest['catalogs'] if v.get('file')==name)
   if declared.get('sha256')!=hashlib.sha256(prepared[name][0]).hexdigest():raise ValueError()
 except (KeyError,TypeError,AttributeError,StopIteration,ValueError):
  raise GeoError('CITY_CATALOG_EVIDENCE_MISMATCH','原始目录字节与冻结清单不一致，停止归档',409)
 entries=[]
 for name,(data,_) in prepared.items():
  destination=directory/name;partial=destination.with_suffix(destination.suffix+'.partial');partial.write_bytes(data);partial.replace(destination)
  entries.append({'file':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
 return {'status':'complete','original_request_available':True,'original_candidate_selection_available':True,'files':entries,'scope':'原生产请求、冻结候选目录及实际QA选择记录；归档反射率和质量位支持离线指数复算，不含远程原始DN资产副本。'}

def validate_catalog_evidence(directory,provenance):
 """Verify portable provenance bytes independently of the delivery ZIP manifest."""
 directory=Path(directory);archive=provenance.get('catalog_archive')
 if not archive:return {'status':'not_archived','files_checked':0}
 def require(condition):
  if not condition:raise GeoError('CITY_CATALOG_EVIDENCE_MISMATCH','包内来源证据缺失、路径无效或SHA与原生产记录不一致',409)
 entries=archive.get('files');require(isinstance(entries,list) and 1<=len(entries)<=8);declared=set()
 for item in entries:
  name=item.get('file') if isinstance(item,dict) else None
  allowed=name in CATALOG_EVIDENCE_FILES or isinstance(name,str) and name.startswith('landsat-') and len(name)==23 and name.endswith('.json') and all(c.isdigit() or c=='-' for c in name[8:-5])
  require(allowed and '/' not in name and '\\' not in name);path=directory/name;require(path.is_file() and not path.is_symlink() and path.stat().st_size<=4*1024*1024 and sha_file(path)==item.get('sha256'));declared.add(name)
 for item in provenance.get('source_catalogs',[]):
  require(isinstance(item,dict) and item.get('file') in declared and sha_file(directory/item['file'])==item.get('sha256'))
 if archive.get('original_request_available') or archive.get('original_candidate_selection_available'):require(set(CATALOG_EVIDENCE_FILES)<=declared)
 return {'status':archive.get('status','verified'),'files_checked':len(declared),'original_request_available':archive.get('original_request_available',False),'scope':archive.get('scope')}

class CityReader:
 def __init__(self,reader=None):self.reader=reader or LandsatReader()
 def read(self,scene,band,grid=None,boundary=None):
  import numpy as np
  import rasterio
  from rasterio.windows import from_bounds,Window
  from rasterio.transform import array_bounds
  from rasterio.warp import reproject,Resampling,transform_bounds
  asset=scene['assets'][band]
  for attempt in range(2):
   try:
    signed=self.reader.signed_url(asset['href'],lambda:None)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',GDAL_HTTP_TIMEOUT=45,GDAL_HTTP_MAX_RETRY=1,GDAL_HTTP_RETRY_DELAY=1):
     with rasterio.open(signed) as ds:
      contract=validate_native_asset(ds,band,asset,grid)
      own=grid_for_dataset(ds,boundary) if grid is None else grid
      if grid is None:win=own['window']
      else:
       bb=transform_bounds(grid['crs'],ds.crs,*array_bounds(*grid['shape'],grid['transform']),densify_pts=21);w=from_bounds(*bb,ds.transform);c0=math.floor(w.col_off)-1;r0=math.floor(w.row_off)-1;c1=math.ceil(w.col_off+w.width)+1;r1=math.ceil(w.row_off+w.height)+1;win=Window(c0,r0,c1-c0,r1-r0)
       if win.width*win.height>MAX_CELLS+40000:raise GeoError('CITY_GRID_LIMIT','读取格网超过控制上限',422)
      raw=ds.read(1,window=win,boundless=True,fill_value=65535 if band in ('qa_pixel','qa_radsat') else 0);source_transform=ds.window_transform(win)
      evidence={'href':asset['href'],'asset_key':asset['asset_key'],'raw_window_sha256':hashlib.sha256(raw.tobytes(order='C')).hexdigest(),'source_crs':ds.crs.to_string(),'source_transform':list(source_transform)[:6],'source_window':[int(win.col_off),int(win.row_off),int(win.width),int(win.height)],'source_dtype':str(raw.dtype),**contract}
      quality=band in ('qa_pixel','qa_radsat')
      value=raw if quality else np.where(raw!=asset['nodata'],raw.astype('float32')*asset['scale']+asset['offset'],np.nan).astype('float32')
      if raw.shape!=own['shape'] or source_transform!=own['transform'] or ds.crs!=own['crs']:
       target=np.full(own['shape'],65535 if quality else np.nan,dtype='uint16' if quality else 'float32')
       reproject(value,target,src_transform=source_transform,src_crs=ds.crs,src_nodata=None if quality else np.nan,dst_transform=own['transform'],dst_crs=own['crs'],dst_nodata=65535 if quality else np.nan,resampling=Resampling.nearest)
       value=target
      evidence.update(scale=asset.get('scale'),offset=asset.get('offset'),aligned_sha256=hashlib.sha256(value.tobytes(order='C')).hexdigest(),resampling='nearest to selected native30m grid; no upsampling claim')
      return value,own,evidence
   except GeoError:raise
   except Exception as error:
    if attempt==0:self.reader.tokens.clear();time.sleep(.4);continue
    # Signed URLs and provider exception messages must not enter archived logs.
    raise GeoError('CITY_SOURCE_READ_FAILED',scene['scene_id']+'/'+band+' remote read failed ('+error.__class__.__name__+')',502) from None

def quality_candidate(scene,boundary,reader):
 qa,grid,qe=reader.read(scene,'qa_pixel',boundary=boundary);rad,_,re=reader.read(scene,'qa_radsat',grid=grid)
 mask=quality_mask(qa,rad)&grid['inside'];count=int(grid['inside'].sum())
 return {'scene':scene,'grid':grid,'qa':qa,'rad':rad,'evidence':{'qa_pixel':qe,'qa_radsat':re},'assessment':{'scene_id':scene['scene_id'],'datetime':scene['datetime'],'scene_cloud_cover':scene['scene_cloud_cover'],'native_city_pixel_count':count,'qa_valid_pixel_count':int(mask.sum()),'qa_valid_ratio':float(mask.sum()/count),'qa_unknown_pixel_count':int(count-mask.sum())}}

def align(values,source_grid,target_grid,quality=False):
 import numpy as np
 from rasterio.warp import reproject,Resampling
 if values.shape==target_grid['shape'] and source_grid['transform']==target_grid['transform'] and source_grid['crs']==target_grid['crs']:return values
 result=np.full(target_grid['shape'],65535 if quality else np.nan,dtype='uint16' if quality else 'float32')
 reproject(values,result,src_transform=source_grid['transform'],src_crs=source_grid['crs'],src_nodata=None if quality else np.nan,dst_transform=target_grid['transform'],dst_crs=target_grid['crs'],dst_nodata=65535 if quality else np.nan,resampling=Resampling.nearest)
 return result

def stats(value,valid,inside,crop,cell_ha=.09):
 import numpy as np
 count=int(inside.sum());n=int(valid.sum());finite=value[valid]
 subset=inside&(crop==5);selected_valid=subset&valid;cn=int(subset.sum());cv=int(selected_valid.sum())
 return {'native_city_pixel_count':count,'valid_pixel_count':n,'unknown_pixel_count':count-n,'valid_ratio':n/count,'native_city_area_ha_30m':count*cell_ha,'valid_area_ha_30m':n*cell_ha,'unknown_area_ha_30m':(count-n)*cell_ha,'mean_index':float(finite.mean(dtype='float64')) if n else None,'min_index':float(finite.min()) if n else None,'max_index':float(finite.max()) if n else None,'median_index':float(np.median(finite)) if n else None,'crop2024_reference':{'source_year':2024,'source_class_code':5,'sampling_resolution_m':30,'reference_pixel_count_30m':cn,'valid_pixel_count_30m':cv,'valid_ratio':cv/cn if cn else None,'mean_index':float(value[selected_valid].mean(dtype='float64')) if cv else None,'note':'2024年度Crops最近邻采样参考分层；不是2026农田确认/播种面积，也不替代2024原生10米类别面积。'}}

def preview(value,valid,grid,index,directory):
 import numpy as np
 from rasterio.warp import reproject,transform_bounds,Resampling
 from rasterio.transform import from_bounds,array_bounds
 from PIL import Image
 bb=transform_bounds(grid['crs'],'EPSG:3857',*array_bounds(*grid['shape'],grid['transform']),densify_pts=21);w=1800;h=max(1,round(w*(bb[3]-bb[1])/(bb[2]-bb[0])))
 if h>1800:w=max(1,round(w*1800/h));h=1800
 target=np.full((h,w),np.nan,dtype='float32');reproject(np.where(valid,value,np.nan),target,src_transform=grid['transform'],src_crs=grid['crs'],src_nodata=np.nan,dst_transform=from_bounds(*bb,w,h),dst_crs='EPSG:3857',dst_nodata=np.nan,resampling=Resampling.nearest)
 rgba=np.zeros((h,w,4),dtype='uint8');usable=np.isfinite(target)
 if index=='ndvi':conditions=[target<0,(target>=0)&(target<.2),(target>=.2)&(target<.5),target>=.5]
 elif index=='ndmi':conditions=[target<-.2,(target>=-.2)&(target<0),(target>=0)&(target<.2),target>=.2]
 elif index=='mndwi':conditions=[target<=0,target>0]
 else:conditions=[target<=-.1,(target>-.1)&(target<.1),target>=.1]
 for condition,entry in zip(conditions,LEGENDS[index]):rgba[usable&condition]=[int(entry['color'][i:i+2],16) for i in (1,3,5)]+[225]
 Image.fromarray(rgba).save(Path(directory)/'overlay.png')
 bounds=list(transform_bounds('EPSG:3857','EPSG:4326',*bb,densify_pts=21));return {'bounds':[[bounds[1],bounds[0]],[bounds[3],bounds[2]]],'width':w,'height':h,'crs':'EPSG:3857','resampling':'nearest; preview only, statistics remain native30m'}

def validate_product(directory,result=None,crop_reference_path=None):
 """Reopen exported inputs, independently recompute every pixel and CSV total.

 This does not call the production index/quality functions.  The test therefore
 catches band-order mistakes, missing city/QA masks and change-sign mistakes in
 the actual delivered raster, rather than just rechecking a JSON calculation.
 """
 import numpy as np
 import rasterio
 from rasterio.features import geometry_mask
 from rasterio.warp import transform_geom,reproject,Resampling
 from rasterio.windows import Window
 folder=Path(directory);result=result or json.loads((folder/'result.json').read_text());summary=result['summary'];index=result['id'].rsplit('-',1)[1]
 def require(condition,message):
  if not condition:raise GeoError('CITY_VALIDATION_FAILED',message,500)
 require(sha_file(folder/'analysis.tif')==result['native_sha256'],'归档原生栅格SHA不一致')
 require(not any(t in canonical(result) for t in (b'sig=',b'?st=')),'归档含临时签名')
 catalog_validation=validate_catalog_evidence(folder,result['provenance'])
 boundary=json.loads((folder/'boundary.geojson').read_text());totals={'city':0,'valid':0,'crop':0,'crop_valid':0,'sum':0.,'crop_sum':0.,'positive':0,'decrease':0,'increase':0};hist=np.zeros(20,dtype='int64');edges=np.linspace(-2,2,21) if index=='change' else np.linspace(-1,1,21);maximum_error=0.
 with rasterio.open(folder/'analysis.tif') as src:
  require(src.crs.to_string()==summary['native_crs'] and src.res==(30.,30.) and abs(src.transform.a*src.transform.e)==900.,'导出不是登记的原生30米格网')
  bands={name:i+1 for i,name in enumerate(src.descriptions)};require(len(bands)==src.count,'导出波段描述缺失或重复')
  require(src.shape==tuple(result['provenance']['grid']['shape']),'原生格网维度不一致')
  geoms=[transform_geom('EPSG:4326',src.crs,densify_geometry(f['geometry'])) for f in boundary['features']]
  expected_city=geometry_mask(geoms,out_shape=src.shape,transform=src.transform,invert=True,all_touched=False)
  crop=np.zeros(src.shape,dtype='uint8');crop_path=Path(crop_reference_path) if crop_reference_path else folder/CROP_REFERENCE_FILE if (folder/CROP_REFERENCE_FILE).is_file() else folder.parent/'xiantao-2024-classification'/'classification.tif'
  require(crop_path.is_file() and sha_file(crop_path)==result['provenance']['crop_reference']['sha256'],'历史耕作参考源缺失或SHA不一致')
  with rasterio.open(crop_path) as ref:reproject(rasterio.band(ref,1),crop,src_transform=ref.transform,src_crs=ref.crs,src_nodata=0,dst_transform=src.transform,dst_crs=src.crs,dst_nodata=0,resampling=Resampling.nearest)
  def clear(qa,rad):return np.isfinite(qa)&np.isfinite(rad)&(qa==np.floor(qa))&(rad==np.floor(rad))&(qa>=0)&(qa<=65535)&(rad==0)&((np.nan_to_num(qa,nan=65535).astype('uint16')&63)==0)
  def ratio(a,b,qa,rad,city):
   good=city&clear(qa,rad)&np.isfinite(a)&np.isfinite(b)&(a>=0)&(b>=0)&((a+b)>0)
   output=np.full(a.shape,np.nan,dtype='float32')
   np.divide(a-b,a+b,out=output,where=good)
   return output,good
  for row in range(0,src.height,256):
   window=Window(0,row,src.width,min(256,src.height-row));data=src.read(window=window);get=lambda key:data[bands[key]-1];city=get('city_mask')==1;require(np.array_equal(city,expected_city[row:row+int(window.height)]),'市界掩膜不等于归档参考多边形')
   require(np.isin(get('city_mask'),[0,1]).all(),'市界掩膜不是二值')
   qa,rad=get('after_qa_pixel'),get('after_qa_radsat')
   a,b={'ndvi':('nir','red'),'ndmi':('nir','swir1'),'mndwi':('green','swir1'),'change':('nir','red')}[index]
   value,valid=ratio(get('after_'+a+'_reflectance'),get('after_'+b+'_reflectance'),qa,rad,city)
   if index=='change':
    previous,pvalid=ratio(get('before_nir_reflectance'),get('before_red_reflectance'),get('before_qa_pixel'),get('before_qa_radsat'),city)
    require(np.array_equal(np.isfinite(get('before_ndvi')),pvalid) and np.allclose(get('before_ndvi')[pvalid],previous[pvalid],atol=1e-7,rtol=0),'前期NDVI重算不一致')
    require(np.array_equal(np.isfinite(get('after_ndvi')),valid) and np.allclose(get('after_ndvi')[valid],value[valid],atol=1e-7,rtol=0),'后期NDVI重算不一致')
    require(np.array_equal(get('before_valid_mask')[city],pvalid[city]) and np.array_equal(get('after_valid_mask')[city],valid[city]),'双期有效掩膜不一致')
    valid &= pvalid;value=np.where(valid,value-previous,np.nan).astype('float32')
   observed=get('value');require(np.array_equal(np.isfinite(observed),valid),'有效像元未满足公式、QA或市界条件')
   require(np.array_equal(get('valid_mask')[city],valid[city]) and np.isnan(get('valid_mask')[~city]).all(),'有效掩膜与未知处理不一致')
   error=float(np.max(np.abs(observed[valid]-value[valid]))) if valid.any() else 0.;maximum_error=max(maximum_error,error);require(error<=1e-7,'指数公式或后减前方向错误')
   expected_crop=crop[row:row+int(window.height)]==5;cohort=get('crop2024_reference')==1;require(np.array_equal(cohort,expected_crop&city),'历史耕作参考采样不一致')
   totals['city']+=int(city.sum());totals['valid']+=int(valid.sum());totals['crop']+=int(cohort.sum());totals['crop_valid']+=int((cohort&valid).sum());totals['sum']+=float(observed[valid].sum(dtype='float64'));totals['crop_sum']+=float(observed[cohort&valid].sum(dtype='float64'));totals['positive']+=int((valid&(observed>0)).sum());totals['decrease']+=int((valid&(observed<=-.1)).sum());totals['increase']+=int((valid&(observed>=.1)).sum());hist+=np.histogram(observed[valid],bins=edges)[0]
 require(totals['city']==summary['native_city_pixel_count'] and totals['valid']==summary['valid_pixel_count'] and totals['city']-totals['valid']==summary['unknown_pixel_count'],'全市像元统计不一致')
 require(abs(totals['sum']/totals['valid']-summary['mean_index'])<1e-10,'全市均值与原生栅格不一致')
 cs=summary['crop2024_reference'];require(totals['crop']==cs['reference_pixel_count_30m'] and totals['crop_valid']==cs['valid_pixel_count_30m'],'历史耕作参考统计不一致')
 require(not totals['crop_valid'] or abs(totals['crop_sum']/totals['crop_valid']-cs['mean_index'])<1e-10,'历史耕作参考均值不一致')
 if index=='mndwi':require(totals['positive']==summary['positive_condition_pixel_count'],'水域光谱条件统计不一致')
 if index=='change':require(totals['decrease']==summary['decrease_condition_pixel_count'] and totals['increase']==summary['increase_condition_pixel_count'],'变化条件统计不一致')
 with (folder/'histogram.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream));archived=np.array([int(r['pixels']) for r in rows]);require(np.array_equal(hist,archived) and int(hist.sum())==totals['valid'],'直方图未覆盖全部有效原生像元')
 with (folder/'statistics.csv').open(encoding='utf-8-sig',newline='') as stream:
  rows=list(csv.DictReader(stream));require(len(rows)==2 and int(rows[0]['valid_pixels_30m'])==totals['valid'] and int(rows[1]['valid_pixels_30m'])==totals['crop_valid'],'CSV和原生像元数不一致');require(abs(float(rows[0]['mean_index'])-summary['mean_index'])<1e-10,'CSV全市均值不一致')
 return {'status':'passed','validated_at':stamp(),'method':'reopened_native_export_independent_formula_quality_city_boundary_crop_reference_and_csv','native_pixels_checked':totals['city'],'valid_pixels_recomputed':totals['valid'],'max_formula_absolute_error':maximum_error,'checks':['native30m_grid','raster_sha256','archived_polygon_mask','qa_bits0_to5_and_saturation','negative_reflectance_and_denominator','every_valid_and_unknown_pixel','index_formula_and_after_minus_before','2024crop_source_sha_and_nearest_stratum','native_mean_and_cohort_mean','all_valid_histogram_and_csv'],'catalog_evidence':catalog_validation,'portable_crop_reference_used':crop_path==folder/CROP_REFERENCE_FILE,'network_retrieved_again':False}

def produce(directory,catalog_dir,source_dir=None,progress=lambda v:None,request=None,mode='live',frozen_dir=None,crop_reference_path='data/agriculture-atlas/xiantao-2024-classification/classification.tif',catalog_reader=None,city_reader=None):
 import numpy as np
 import rasterio
 from rasterio.warp import reproject,Resampling
 root=Path(directory);catalog_dir=Path(catalog_dir);boundary,metadata=load_boundary(source_dir);reader=city_reader or CityReader()
 if request is not None and not isinstance(request,dict):raise GeoError('CITY_REQUEST_INVALID','生产请求须为结构化对象',400)
 if request is not None:request=production_request(request.get('periods'),request.get('scene_cloud_max',80),request.get('max_qa_candidates_per_period',3),request.get('max_catalog_items_per_period',60),request.get('minimum_city_qa_ratio',.7))
 else:request=production_request()
 periods=request['periods'];year=int(periods['after'][1][:4]);minimum=request['minimum_city_qa_ratio']
 if any((root/('xiantao-'+str(year)+'-spectral-'+index)/'result.json').exists() for index in ('ndvi','ndmi','mndwi','change')):raise GeoError('CITY_OUTPUT_EXISTS','输出目录已有生产结果，请选择新的运行目录以保留冻结成果',409)
 contracts,discovery=prepare_catalogs(boundary,metadata,request,catalog_dir,mode,catalog_reader,frozen_dir)
 after,after_assessments,after_policy=choose_observation(contracts['after'],boundary,reader,minimum,'after',progress);before,before_assessments,before_policy=choose_observation(contracts['before'],boundary,reader,minimum,'before',progress);assessments=after_assessments+before_assessments;grid=after['grid'];reason=after_policy['policy'];below=after_policy['below_target']
 write_json(catalog_dir/'candidate-selection.json',{'after':after_policy,'before':before_policy,'actual_qa_assessments':assessments,'selected_ids':{'before':before['scene']['scene_id'],'after':after['scene']['scene_id']}})
 progress('Selected after '+after['scene']['scene_id']+' QA '+str(after['assessment']['qa_valid_ratio']))
 for candidate,names in [(after,['green','red','nir','swir1']),(before,['red','nir'])]:
  candidate['bands']={}
  for name in names:
   progress('Read '+candidate['scene']['scene_id']+' '+name);value,_,evidence=reader.read(candidate['scene'],name,grid=candidate['grid']);candidate['bands'][name]=value;candidate['evidence'][name]=evidence
 before_original_grid=before['grid'];before['bands']={k:align(v,before_original_grid,grid) for k,v in before['bands'].items()};before['qa']=align(before['qa'],before_original_grid,grid,True);before['rad']=align(before['rad'],before_original_grid,grid,True)
 crop_path=Path(crop_reference_path)
 if not crop_path.is_file():raise GeoError('CITY_CROP_REFERENCE_MISSING','2024全市分类参考尚未生产',422)
 crop=np.zeros(grid['shape'],dtype='uint8')
 with rasterio.open(crop_path) as src:reproject(rasterio.band(src,1),crop,src_transform=src.transform,src_crs=src.crs,src_nodata=0,dst_transform=grid['transform'],dst_crs=grid['crs'],dst_nodata=0,resampling=Resampling.nearest)
 crop_sha=sha_file(crop_path);native_reference=json.loads((crop_path.parent/'result.json').read_text())['summary']
 values={};validity={}
 for index in ('ndvi','ndmi','mndwi'):values[index],validity[index]=index_array(index,after['bands'],after['qa'],after['rad'],grid['inside'])
 before_ndvi,before_valid=index_array('ndvi',before['bands'],before['qa'],before['rad'],grid['inside']);validity['change']=before_valid&validity['ndvi'];values['change']=np.where(validity['change'],values['ndvi']-before_ndvi,np.nan).astype('float32')
 results=[];created=stamp();city=grid['inside'];cell_ha=grid['cell_area_m2']/10000
 for index in ('ndvi','ndmi','mndwi','change'):
  ident='xiantao-'+str(year)+'-spectral-'+index;folder=root/ident;folder.mkdir(parents=True,exist_ok=True);progress('Write '+ident)
  value=values[index];valid=validity[index];summary=stats(value,valid,city,crop,cell_ha)
  if not summary['valid_pixel_count']:raise GeoError('CITY_NO_VALID_PIXELS',index+'全市没有有效像元，不生成伪结果',422)
  summary.update(index_id='ndvi' if index=='change' else index,operation='after_minus_before' if index=='change' else 'single_actual_observation',native_crs=grid['crs'].to_string(),resolution_m=30,cell_area_m2=grid['cell_area_m2'],statistics_resampled=False,quality_target_met=not below,quality_warning='后期实际市界QA覆盖未达到'+str(minimum*100)+'%，使用最高有效率观测并保留未知。' if below else '',requested_periods=periods if index=='change' else {'after':periods['after']},actual_dates=[before['scene']['datetime'],after['scene']['datetime']] if index=='change' else [after['scene']['datetime']],reference_year=year,boundary_year=metadata.get('boundary_year_represented',2017),before_quality_target_met=not before_policy['below_target'])
  summary['crop2024_reference']['note']=summary['crop2024_reference']['note'].replace('2026',str(year))
  if index=='change' and before_policy['below_target']:
   summary['quality_target_met']=False;summary['quality_warning']+='前期实际市界QA覆盖未达到'+str(minimum*100)+'%，使用最高有效率观测并保留未知。'
  if index=='change':summary.update(mean_delta=summary['mean_index'],change_formula='after_ndvi-before_ndvi',decrease_condition_pixel_count=int((valid&(value<=-.1)).sum()),increase_condition_pixel_count=int((valid&(value>=.1)).sum()),decrease_condition_area_ha=float((valid&(value<=-.1)).sum())*cell_ha,increase_condition_area_ha=float((valid&(value>=.1)).sum())*cell_ha)
  if index=='mndwi':summary.update(positive_condition='MNDWI > 0',positive_condition_pixel_count=int((valid&(value>0)).sum()),positive_condition_area_ha=float((valid&(value>0)).sum())*cell_ha)
  provenance={'algorithm_version':VERSION,'source':'USGS/NASA Landsat 8/9 Collection2 L2 SR / Microsoft Planetary Computer','source_type':'public','source_id':'pc_landsat_c2_l2','source_catalogs':discovery['catalogs'],'discovery':discovery,'actual_dates':summary['actual_dates'],'requested_periods':summary['requested_periods'],'selection':{'policy':reason,'minimum_city_qa_ratio':minimum,'candidates':assessments,'after':after_policy,'before':before_policy},'scenes':{epoch:{'scene':c['scene'],'evidence':c['evidence']} for epoch,c in [('after',after),('before',before)] if epoch=='after' or index=='change'},'boundary':metadata,'grid':{'crs':grid['crs'].to_string(),'transform':list(grid['transform'])[:6],'shape':list(grid['shape']),'cell_area_m2':grid['cell_area_m2'],'mask':'densified2017reference polygon; all_touched=False; pixel centers','before_resampling':'nearest to selected after native grid'},'quality_policy':copy.deepcopy(QUALITY_POLICY),'crop_reference':{'file':'xiantao-2024-classification/classification.tif','sha256':crop_sha,'product_year':2024,'crop_class':5,'native_area_ha_10m':native_reference['cropland_ha'],'resampling':'nearest to30m solely for reference stratification; no2026crop claim'},'created_at':created,'unsigned_source_urls_only':True}
  definition=specification('ndvi' if index=='change' else index)
  provenance['algorithm']={'index_id':definition['id'],'index_formula':definition['formula'],'operation':summary['operation'],'change_formula':'after_ndvi-before_ndvi' if index=='change' else None,'reference_url':definition['source_url'],'native_sensor_bands':{'green':'SR_B3','red':'SR_B4','nir':'SR_B5','swir1':'SR_B6'},'qa_excluded_bits':[0,1,2,3,4,5],'saturation_rule':'all nonzero QA_RADSAT excluded','reflectance_rule':'actual asset scale and offset; negative input or nonpositive ratio denominator remains unknown','condition_thresholds':{'increase':.1,'decrease':-.1} if index=='change' else ({'positive':0} if index=='mndwi' else {}),'thresholds_are_classification':False}
  arrays=[('value',value),('valid_mask',np.where(city,valid.astype('float32'),np.nan)),('city_mask',city.astype('float32')),('crop2024_reference',np.where(city,np.where(np.isin(crop,[1,2,4,5,7,8,9,11]),(crop==5).astype('float32'),np.nan),np.nan))]
  required={'ndvi':['red','nir'],'ndmi':['nir','swir1'],'mndwi':['green','swir1'],'change':['red','nir']}[index]
  for name in required:arrays.append(('after_'+name+'_reflectance',after['bands'][name]))
  arrays.extend([('after_qa_pixel',after['qa']),('after_qa_radsat',after['rad'])])
  if index=='change':arrays.extend([('before_ndvi',before_ndvi),('after_ndvi',values['ndvi']),('before_valid_mask',np.where(city,before_valid.astype('float32'),np.nan)),('after_valid_mask',np.where(city,validity['ndvi'].astype('float32'),np.nan)),('before_red_reflectance',before['bands']['red']),('before_nir_reflectance',before['bands']['nir']),('before_qa_pixel',before['qa']),('before_qa_radsat',before['rad'])])
  raster_partial=folder/'analysis.tif.partial'
  with rasterio.open(raster_partial,'w',driver='GTiff',height=grid['shape'][0],width=grid['shape'][1],count=len(arrays),dtype='float32',crs=grid['crs'],transform=grid['transform'],nodata=np.nan,compress='deflate',predictor=3,tiled=True,blockxsize=256,blockysize=256,BIGTIFF='IF_SAFER') as dst:
   for n,(name,array) in enumerate(arrays,1):dst.write(array.astype('float32'),n);dst.set_band_description(n,name)
   dst.update_tags(algorithm_version=VERSION,reference='selected after native30m grid',actual_dates=json.dumps(summary['actual_dates']),index_id=summary['index_id'],operation=summary['operation'])
  raster_partial.replace(folder/'analysis.tif')
  provenance['catalog_archive']=archive_catalog_evidence(catalog_dir,folder)
  crop_artifacts=archive_crop_reference(crop_path,folder,crop_sha);provenance['crop_reference'].update(archive_file=CROP_REFERENCE_FILE,archive_metadata=CROP_REFERENCE_METADATA)
  image=preview(value,valid,grid,index,folder);write_json(folder/'provenance.json',provenance);write_json(folder/'boundary.geojson',boundary)
  with (folder/'statistics.csv').open('w',newline='',encoding='utf-8-sig') as stream:
   writer=csv.writer(stream);writer.writerow(['cohort','total_reference_pixels_30m','valid_pixels_30m','valid_ratio','mean_index','unknown_pixels_30m']);writer.writerow(['full_reference_city',summary['native_city_pixel_count'],summary['valid_pixel_count'],summary['valid_ratio'],summary['mean_index'],summary['unknown_pixel_count']]);cs=summary['crop2024_reference'];writer.writerow(['2024Crops_reference_resampled30m',cs['reference_pixel_count_30m'],cs['valid_pixel_count_30m'],cs['valid_ratio'],cs['mean_index'],cs['reference_pixel_count_30m']-cs['valid_pixel_count_30m']])
  with (folder/'histogram.csv').open('w',newline='',encoding='utf-8-sig') as stream:
   hist,edges=np.histogram(value[valid],bins=np.linspace(-1,1,21) if index!='change' else np.linspace(-2,2,21));writer=csv.writer(stream);writer.writerow(['lower_inclusive','upper_exclusive_except_last','pixels']);writer.writerows(zip(edges[:-1],edges[1:],hist))
  metric_label='NDVI平均变化' if index=='change' else summary['index_id'].upper()+'全市有效均值'
  metrics=[{'label':metric_label,'value':summary['mean_index'],'unit':'指数'},{'label':'共同有效覆盖' if index=='change' else '实际有效覆盖','value':summary['valid_ratio']*100,'unit':'%'},{'label':'2024耕作参考内均值','value':summary['crop2024_reference']['mean_index'],'unit':'指数'}]
  result={'schema_version':'agriculture-atlas-1.0','id':ident,'title':TITLES[index].replace('2026',str(year)),'name':TITLES[index].replace('2026',str(year)),'scene_ids':SCENES[index],'status':'ready','product_type':'city_spectral_reference','scope_label':'仙桃全市2017公开参考市界内原生30米像元','period_label':'实际观测 '+ ' → '.join(v[:10] for v in summary['actual_dates']),'resolution_m':30,'summary_metrics':metrics,'summary':summary,'layers':[{'id':index,'title':TITLES[index].replace('2026',str(year)),'kind':'raster','image':{'url':'overlay.png','bounds':image['bounds']},'legend':LEGENDS[index]}],'image':image,'limitations':[text.replace('2026',str(year)) for text in LIMITATIONS],'provenance':provenance,'artifacts':['result.json','analysis.tif','overlay.png','statistics.csv','histogram.csv','provenance.json','boundary.geojson']+provenance['catalog_archive']['files']+crop_artifacts,'created_at':created,'native_sha256':sha_file(folder/'analysis.tif')}
  # Detect any accidental credential persistence before publishing ready JSON.
  encoded=canonical(result)
  if b'sig=' in encoded or b'?st=' in encoded:raise GeoError('CITY_SIGNATURE_PERSISTENCE','检测到临时访问签名，停止归档',500)
  result['validation']=validate_product(folder,result,crop_path);write_json(folder/'validation.json',result['validation']);result['artifacts'].append('validation.json');write_json(folder/'result.json',result);results.append(result)
 progress('Completed all four actual city products');return results
