"""Published crop classifications: fixed official COG, native area and map outputs."""
import copy,csv,hashlib,json,math,re,threading,uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlsplit
from backend.core.geo_services import GeoError
from backend.analysis.landcover_services import load_boundary,ellipsoid_row_pixel_area

VERSION='crop-reference-1.0'
SOURCE_ID='globalrice20_2024_esa'
ITEM_URL='https://catalogue.weed.apex.esa.int/collections/Anthropogenic-V2/items/global_2024_22'
COLLECTION_URL='https://catalogue.weed.apex.esa.int/collections/Anthropogenic-V2'
DOI='10.5281/zenodo.18168302'
LIMITATIONS=['GlobalRice20 2024年度水稻模型产品参考，不是2026现状、播种台账或官方调查。','面积按EPSG:4326原生像元逐行WGS84椭球积分，不将名义20米统一乘400平方米。','0为产品非水稻类别，255为未知；覆盖率不代表本地分类精度。','产品经过ESA ECDC再分发，保留原作者DOI与镜像元数据；不声称与Zenodo原文件字节相同。','年度水稻分布不区分早晚稻，不作为独立地面训练/验证样本；连通区域不是确权地块。']
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def write(path,v):Path(path).write_bytes(canonical(v))

def region_mentions(question):
 """Only explicit administrative names; never infer a city from a map center."""
 words=re.split(r'统计|查询|查看|计算|核算|读取|提取|分析|显示|展示|下载|导出|比较|对比|请|帮我|我要|要把|按|在|与|和|及|省|[0-9\s，,。；;：:的]',question)
 names=[]
 for word in words:
  for match in re.finditer(r'([\u4e00-\u9fff]{2,16}?)(全市|全县|全域|自治州|市|县|区)',word):
   name,suffix=match.groups()
   if name in ('全','本','我','该','选','所选','地图选','当前选','仙桃全'):continue
   value=name+({'全市':'市','全县':'县','全域':'市'}.get(suffix,suffix))
   if not any(w in value for w in ('选','区域','范围','行政')) and value not in names:names.append(value)
 return names

def crop_scope_error(product,question,context):
 """Reference follow-ups stay pinned to the original region or exact AOI."""
 p=product.get('provenance',{});region=p.get('region')
 if not region and product['id'].startswith('xiantao-'):region={'name':'仙桃市'}
 names=region_mentions(question)
 if context.get('region'):names.append(context['region'])
 if names and (not region or any(name not in (region.get('name'),str(region.get('name','')).removesuffix('市'),region.get('code')) for name in names)):return '所请求地区与这份作物成果不一致，请对目标地区运行分析。'
 if any(w in question for w in ('全省','全国','省域','各市','各县','各镇','各村')):return '这份区域成果不能替代省域或其他行政层级统计。'
 if region and any(w in question for w in ('乡镇','村级','分镇','沿岸','周边','东部','西部','南部','北部','城区')):return '请求的是行政子域，请明确对应边界后另行分析。'
 if not region and any(w in question for w in ('全市','全县','全域')):return '地图矩形成果不能替代行政区全域统计。'
 if 'bbox' in context:
  if region or context['bbox']!=p.get('analysis_bbox'):return '当前选区与原成果范围不同，请重新运行分析。'
 if region and any(w in question for w in ('选区','框选','所选区域','这块地','该地块')):return '原成果为行政参考范围，不能替代新的地图选区。'
 if 'dataset_id' in context:return '用户地块范围与作物成果不能自动互换，请明确分析范围。'
 return None

def prepare_question(store,question,context):
 if not getattr(store,'crop_reference',None) or not ('水稻' in question or 'globalrice20' in question.lower()) or not any(w in question for w in ('面积','分布','上图','提取','统计','核算','标注','显示')):return None
 blocked=lambda m:{'intent':'crop_reference','status':'needs_data','missing_fields':[],'blocking_gaps':[m],'clarification_message':m,'executable':False}
 from backend.analysis.cropland_services import requests_subannual_period
 from backend.analysis.spectral_indices import detect_indices,unsupported_index_requested
 semantic=re.sub(r'当前(?:地图)?(?:选定的?|选中的?|所选的?)?(?:框选范围|地图范围|地图选区|范围|选区|区域|窗口)','',question)
 if detect_indices(question) or unsupported_index_requested(question) or any(k in context for k in ('threshold','field','processing_algorithm','index_id','scene_limit','cloud','direction','min_patch_pixels')):return blocked('年度水稻类别不含反射率或指数，不能替代NDVI、阈值、分区统计等其他分析请求。')
 if requests_subannual_period(question) or any(w in semantic for w in ('今年','当前','最新','现状','实时','播种','收获期','确权','法定','官方','准确率','精度','产量','灾损','变化','增减','2015','2023','EARice','田埂','地块','每块','田块','图斑','数量','几块','占比','比例','百分比','小麦','玉米','油菜','棉花','大豆','灌溉','需水','蒸散','施肥','长势','病虫害')):return blocked('已接通的是GlobalRice20的2024年度水稻分类参考面积与分布，不支持指定月份、当期、官方面积、地块边界、其他作物或衍生农情替代。')
 years=set(re.findall(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)',question))
 if years!={'2024'}:return blocked('请明确2024年产品参考口径；其他年度来源尚未接通，不自动换年。')
 if context.get('source_id') not in (None,SOURCE_ID) or context.get('source_type') or any(k in context for k in ('period','periods','dataset_id','observation_id','cropland_product')):return blocked('所选来源、时间或图层与2024水稻产品不一致，请选择正确专题条件。')
 if any(w in question for w in ('全省','全国','省域','各市','各县','乡镇','分镇','村级','沿岸','周边','东部','西部','南部','北部','城区')):return blocked('请指定一个行政区或地图选区；省域、多地区和未明确边界的子域不作为整市统计。')
 inputs={'source_id':SOURCE_ID,'force':any(w in question for w in ('重算','重新计算','重新统计','重新核算'))}
 names=region_mentions(question)
 if len(names)>1:return blocked('本次请明确一个行政区；多地比较需分别分析，不合并为同一地区结果。')
 if names or any(w in question for w in ('全市','全县','全域')) or (context.get('region') and not context.get('bbox')):
  if not any(w in question for w in ('选区','所选','选中','框选')):
   region=names[0] if names else context.get('region')
   if not region:return blocked('请明确行政区全名或在地图框选，不能由默认位置推测全市范围。')
   if context.get('region') and context['region'] not in (region,region.removesuffix('市')):return blocked('问题地区与所选地区不一致，请明确目标地区。')
   inputs.update(scope='xiantao_city') if region in ('仙桃','仙桃市') else inputs.update(scope='administrative',region=region)
  elif context.get('bbox'):inputs['bbox']=copy.deepcopy(context['bbox'])
  else:return blocked('请在地图明确框选范围，未用行政整域替代选区。')
 elif context.get('bbox'):
  if re.search(r'[\u4e00-\u9fff]{2,}(?:市|县|省)',question) and not any(w in question for w in ('选区','所选','选中','框选')):return blocked('请明确按地图选区分析，或明确仙桃全市；不能用当前矩形替代问题中的行政范围。')
  inputs['bbox']=copy.deepcopy(context['bbox'])
 else:return blocked('请在地图明确框选，或指定市县全名查询2024年水稻参考面积。')
 try:store.crop_reference.prepare(inputs)
 except GeoError as e:return blocked(e.message)
 return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':'crop_reference','status':'ready','missing_fields':[],'blocking_gaps':[],'executable':True,'force':inputs['force'],'tool':{'id':'crop_reference_analysis','version':VERSION,'inputs':inputs},'plan_ref':None,'source_snapshot':{'source_id':SOURCE_ID,'product_year':2024,'operation':'native_published_crop_classification'},'planner':'bounded_rules_no_general_llm'}
def validate_metadata(item,collection):
 asset=(item.get('assets') or {}).get('crop_type_rice_paddies') or {};p=item.get('properties') or {};url=asset.get('href','');parts=urlsplit(url)
 if item.get('id')!='global_2024_22' or item.get('collection')!='Anthropogenic-V2' or p.get('sci:doi')!=DOI or not str(p.get('datetime','')).startswith('2024-01-01') or not str(p.get('end_datetime','')).startswith('2024-12-31'):raise GeoError('CROP_SOURCE_VERSION','公开作物产品年度/DOI与登记契约不符',409)
 if collection.get('id')!='Anthropogenic-V2' or collection.get('license')!='CC-BY-4.0':raise GeoError('CROP_SOURCE_LICENSE','数据集合许可未满足登记条件',422)
 if parts.scheme!='https' or parts.hostname!='s3.waw4-1.cloudferro.com' or not parts.path.endswith('/ECDC/Anthropogenic/Anthropogenic_V_global_20240101_20241231_crop_type_rice_paddies.tif') or parts.query or parts.fragment:raise GeoError('CROP_ASSET_DENIED','数据资产不是已登记ESA公开水稻COG',422)
 bands=asset.get('bands') or []
 if len(bands)!=1 or bands[0].get('data_type')!='uint8' or bands[0].get('nodata')!=255 or not any(c.get('value')==1 and c.get('name')=='paddy_rice' for c in asset.get('classification:classes',[])):raise GeoError('CROP_CLASS_CONTRACT','作物类别或未知值声明不符',422)
 return asset

class CropReferenceServices:
 def __init__(self,store,source_path=None,metadata_provider=None):
  self.store=store;self.root=Path(store.db_path).parent/'crop-reference';self.lock=threading.RLock();self.source_path=source_path;self.metadata_provider=metadata_provider
 def administrative_boundary(self,region):
  with self.lock:return self._administrative_boundary(region)
 def _administrative_boundary(self,region):
  if not isinstance(region,str) or not re.fullmatch(r'(?:[\u4e00-\u9fff]{2,24}(?:市|县|区|自治州)|\d{6}|\d{9})',region):raise GeoError('CROP_REGION_REQUIRED','请输入市县完整名称或行政编码；不自动选择相似地名',422)
  path=self.root/'boundaries'/(digest(region)+'.json')
  if path.is_file():
   cached=json.loads(path.read_text());item=cached['item']
   if cached.get('sha256')!=digest(item):raise GeoError('CROP_BOUNDARY_CHANGED','行政参考范围缓存校验失败',409)
  else:
   provider=getattr(self.store,'tianditu',None)
   if not provider:raise GeoError('CROP_BOUNDARY_UNAVAILABLE','行政范围服务未配置，请使用地图选区',422)
   candidates=provider.administrative(region)['results']
   matches=[x for x in candidates if region in (x.get('name'),x.get('code')) or (region.isdigit() and str(x.get('code',''))=='156'+region)]
   if len(matches)!=1:raise GeoError('CROP_REGION_AMBIGUOUS','行政区无唯一精确匹配，请提供完整名称或行政编码',422)
   item=matches[0]
   if item.get('boundary_status')!='available' or not item.get('geojson'):raise GeoError('CROP_BOUNDARY_UNAVAILABLE','该行政区没有可读取参考边界，未使用中心点或矩形代替',422)
   path.parent.mkdir(parents=True,exist_ok=True);write(path,{'item':item,'sha256':digest(item)})
  from backend.core.search_services import geo_bounds
  geo=item.get('geojson');bbox=geo_bounds(geo)
  if not bbox or geo.get('type')!='FeatureCollection' or not geo.get('features') or any(f.get('geometry',{}).get('type') not in ('Polygon','MultiPolygon') for f in geo['features']):raise GeoError('CROP_BOUNDARY_INVALID','行政参考范围不是可用面几何',422)
  meta={'bbox':bbox,'provider':item['source'],'source_url':item['source_url'],'boundary_year_represented':None,'data_version':item.get('data_version'),'fetched_at':item['fetched_at'],'sha256':digest(geo),'name':item['name'],'code':item['code'],'coordinate_reference':'天地图返回经纬度；未核定法定界线或现状面积'}
  return geo,meta
 def metadata(self):
  if self.metadata_provider:
   record=self.metadata_provider();return record,validate_metadata(record['item'],record['collection'])
  directory=self.root/'sources'/SOURCE_ID;directory.mkdir(parents=True,exist_ok=True)
  saved=directory/'metadata.json'
  if saved.is_file():record=json.loads(saved.read_text())
  else:
   try:
    values=[]
    for url in (ITEM_URL,COLLECTION_URL):
     with urlopen(url,timeout=20) as response:
      raw=response.read(2_000_001)
      if len(raw)>2_000_000:raise ValueError()
      values.append(json.loads(raw))
    record={'item':values[0],'collection':values[1]};validate_metadata(*values);write(saved,record)
   except GeoError:raise
   except Exception:raise GeoError('CROP_CATALOG_UNAVAILABLE','ESA作物目录暂不可读取，未使用模拟分类',502)
  asset=validate_metadata(record['item'],record['collection']);return record,asset
 def prepare(self,body):
  if not isinstance(body,dict) or set(body)-{'source_id','bbox','scope','region','force'} or body.get('source_id')!=SOURCE_ID:raise GeoError('CROP_REFERENCE_INPUT','请选择已接通的2024年GlobalRice20来源',422)
  if type(body.get('force',False))is not bool:raise GeoError('CROP_REFERENCE_INPUT','重算条件须为布尔值',400)
  scope=body.get('scope','selected_aoi')
  if scope!='administrative' and 'region' in body:raise GeoError('CROP_SCOPE_CONFLICT','行政区名称仅用于行政范围分析，不与框选或固定仙桃参考界混用',422)
  if scope=='xiantao_city':
   if 'bbox' in body:raise GeoError('CROP_SCOPE_CONFLICT','全市参考范围不能同时提交框选范围',422)
   geo,meta=load_boundary();bbox=meta['bbox']
  elif scope=='administrative':
   if 'bbox' in body:raise GeoError('CROP_SCOPE_CONFLICT','行政全域分析不能同时提交地图矩形',422)
   geo,meta=self.administrative_boundary(body.get('region'));bbox=meta['bbox']
   if not -135<=bbox[0]<bbox[2]<=180 or not -80<=bbox[1]<bbox[3]<=80:raise GeoError('CROP_COVERAGE_GAP','该行政区不在已登记作物产品范围内',422)
   cell=.0001796630568239043
   if (math.ceil((bbox[2]-bbox[0])/cell)+2)*(math.ceil((bbox[3]-bbox[1])/cell)+2)>20_000_000:raise GeoError('CROP_PIXEL_LIMIT','该行政全域超过本机2000万原生像元上限，请框选子区；不降采样冒充全域统计',422)
  elif scope=='selected_aoi':
   bbox=body.get('bbox')
   if not isinstance(bbox,list) or len(bbox)!=4 or any(type(v) not in (float,int) or not math.isfinite(v) for v in bbox):raise GeoError('CROP_AOI_REQUIRED','请明确选择分析范围',422)
   w,s,e,n=bbox
   if not -135<=w<e<=180 or not -80<=s<n<=80 or e-w>.050001 or n-s>.050001:raise GeoError('CROP_AOI_LIMIT','选区须为合法WGS84矩形且单边不超过0.05度',422)
   geo={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'name':'用户分析选区'},'geometry':{'type':'Polygon','coordinates':[[[w,s],[e,s],[e,n],[w,n],[w,s]]]}}]};meta={'bbox':bbox,'provider':'用户框选','boundary_year_represented':None}
  else:raise GeoError('CROP_SCOPE_UNSUPPORTED','请选择地图选区或明确行政参考范围',422)
  return {'source_id':SOURCE_ID,'scope':scope,'bbox':bbox,'boundary':geo,'boundary_source':meta,'force':body.get('force',False)}
 def analyze(self,body,check=lambda:None):
  with self.lock:return self._analyze(self.prepare(body),check)
 def _analyze(self,plan,check):
  import numpy as np
  import rasterio
  from rasterio.features import geometry_mask
  from rasterio.windows import Window
  from rasterio.transform import from_bounds,array_bounds
  from rasterio.warp import reproject,transform_bounds,Resampling
  from PIL import Image
  check();record,asset=self.metadata();token=digest({'version':VERSION,'metadata':record,'plan':{k:v for k,v in plan.items() if k!='force'}})
  base='xiantao-rice-2024-globalrice20' if plan['scope']=='xiantao_city' else 'crop-rice-2024-'+token[:20]
  atlas=self.store.agriculture_atlas;directory=atlas.root/base
  if (directory/'result.json').is_file() and not plan['force']:
   old=json.loads((directory/'result.json').read_text())
   if old.get('input_sha256')==token:
    product=atlas.get(base)
    if hasattr(self.store,'save'):
     checked=datetime.now(timezone.utc).isoformat()
     self.store.save('data_source_check',{'id':'cropcheck_'+uuid.uuid4().hex[:14],'source_id':SOURCE_ID,'created_at':checked,'verification':{'status':'succeeded','operation':'verified_native_crop_archive_read','checked_at':checked,'query_scope':{'bbox':plan['bbox'],'year':2024},'detail':'已读取并校验此前真实生产的原生分类归档；本次未重新联网读取像元。'}})
    return product,True
  if directory.exists():base=base+'-'+uuid.uuid4().hex[:8];directory=atlas.root/base
  temp=atlas.root/('.crop-'+uuid.uuid4().hex);temp.mkdir(parents=True)
  try:
   env={'GDAL_DISABLE_READDIR_ON_OPEN':'EMPTY_DIR','CPL_VSIL_CURL_ALLOWED_EXTENSIONS':'.tif','GDAL_HTTP_TIMEOUT':'25','GDAL_HTTP_CONNECTTIMEOUT':'10','GDAL_HTTP_MAX_RETRY':'0','GDAL_CACHEMAX':64*1024*1024}
   with rasterio.Env(**env),rasterio.open(self.source_path or asset['href']) as ds:
    t=ds.transform;expected=record['item']['properties']['proj:transform']
    if ds.crs.to_epsg()!=4326 or ds.count!=1 or ds.dtypes!=('uint8',) or ds.nodata!=255 or any(abs(a-b)>1e-9 for a,b in zip(list(t)[:6],expected)):raise GeoError('CROP_NATIVE_CONTRACT','实际COG格网/类型与发布元数据不符',409)
    w,s,e,n=plan['bbox'];c0=math.floor((w-t.c)/t.a);c1=math.ceil((e-t.c)/t.a);r0=math.floor((n-t.f)/t.e);r1=math.ceil((s-t.f)/t.e)
    if min(c0,r0)<0 or c1>ds.width or r1>ds.height:raise GeoError('CROP_COVERAGE_GAP','数据源未覆盖完整分析范围',422)
    if (c1-c0)*(r1-r0)>20_000_000:raise GeoError('CROP_PIXEL_LIMIT','超过作物参考读取上限，未降采样代替统计',422)
    window=Window(c0,r0,c1-c0,r1-r0);native_transform=ds.window_transform(window);check();data=ds.read(1,window=window)
   inside=geometry_mask([f['geometry'] for f in plan['boundary']['features']],out_shape=data.shape,transform=native_transform,invert=True,all_touched=False)
   if not inside.any() or not np.isin(data[inside],[0,1,255]).all():raise GeoError('CROP_CLASS_CONTRACT','选区为空或出现未登记类别，未计作非水稻',422)
   valid=inside&(data!=255);rice=inside&(data==1)
   weights=np.array([ellipsoid_row_pixel_area(native_transform.f+i*native_transform.e,native_transform.f+(i+1)*native_transform.e,native_transform.a)/10000 for i in range(data.shape[0])])
   area=lambda mask:float(np.dot(mask.sum(axis=1,dtype='int64'),weights))
   summary={'reference_year':2024,'crop':'rice','class_code':1,'rice_area_ha':area(rice) if valid.any() else None,'rice_area_mu':area(rice)*15 if valid.any() else None,'valid_area_ha':area(valid),'unknown_area_ha':area(inside&~valid),'reference_area_ha':area(inside),'rice_pixels':int(rice.sum()),'valid_pixels':int(valid.sum()),'inside_pixels':int(inside.sum()),'valid_ratio':float(valid.sum()/inside.sum()),'native_crs':'EPSG:4326','native_resolution_degrees':[native_transform.a,-native_transform.e],'area_method':'WGS84 ellipsoid latitude-band integral; native pixel centers; no resampling'}
   with rasterio.open(temp/'classification.tif','w',driver='GTiff',width=data.shape[1],height=data.shape[0],count=2,dtype='uint8',crs='EPSG:4326',transform=native_transform,nodata=255,compress='deflate') as dst:
    dst.write(np.where(inside,data,255).astype('uint8'),1);dst.set_band_description(1,'classification');dst.write(inside.astype('uint8'),2);dst.set_band_description(2,'aoi_mask')
   # Reopen output and independently verify counts, mask and row-weighted area.
   with rasterio.open(temp/'classification.tif') as verify:
    a=verify.read(1);m=verify.read(2)==1
    if not np.array_equal(m,inside) or not np.array_equal(a[m],data[m]) or int(((a==1)&m).sum())!=summary['rice_pixels']:raise GeoError('CROP_OUTPUT_INVALID','导出像元与统计不一致',409)
   with (temp/'statistics.csv').open('w',newline='') as f:
    writer=csv.writer(f);writer.writerow(['class','pixel_count','area_ha'])
    for code,label in [(1,'rice'),(0,'not_rice'),(255,'unknown')]:writer.writerow([label,int((inside&(data==code)).sum()),area(inside&(data==code))])
   write(temp/'source.json',record);write(temp/'boundary.geojson',plan['boundary'])
   bb=transform_bounds('EPSG:4326','EPSG:3857',*array_bounds(*data.shape,native_transform));pw=1600;ph=max(1,round(pw*(bb[3]-bb[1])/(bb[2]-bb[0])));pw=max(1,round(pw*1600/ph)) if ph>1600 else pw;ph=min(ph,1600);target=np.full((ph,pw),255,dtype='uint8')
   reproject(np.where(inside,data,255),target,src_transform=native_transform,src_crs='EPSG:4326',src_nodata=255,dst_transform=from_bounds(*bb,pw,ph),dst_crs='EPSG:3857',dst_nodata=255,resampling=Resampling.nearest)
   rgba=np.zeros((ph,pw,4),dtype='uint8');rgba[target==1]=[68,203,153,230];rgba[target==0]=[209,220,208,55]
   Image.fromarray(rgba).save(temp/'overlay.png');bounds=transform_bounds('EPSG:3857','EPSG:4326',*bb)
   region=({'name':'仙桃市'} if plan['scope']=='xiantao_city' else {k:plan['boundary_source'][k] for k in ('name','code')} if plan['scope']=='administrative' else None)
   title=('仙桃全市' if plan['scope']=='xiantao_city' else region['name'] if region else '选区')+'2024水稻分布参考'
   scope_label='仙桃全市 · 2017公开参考市界' if plan['scope']=='xiantao_city' else region['name']+' · 天地图公开参考界（年份未声明）' if region else '用户明确框选范围'
   result={'id':base,'status':'ready','product_type':'crop_reference','version':VERSION,'created_at':datetime.now(timezone.utc).isoformat(),'title':title,'scene_ids':['grain','sowing','crop_research'],'scope_label':scope_label,'period_label':'2024年度分类产品','resolution_m':20,'input_sha256':token,'summary':summary,'summary_metrics':[{'label':'水稻类别参考面积','value':summary['rice_area_ha'],'unit':'公顷','precision':4},{'label':'折合面积','value':summary['rice_area_mu'],'unit':'亩','precision':3},{'label':'分类有效覆盖','value':summary['valid_ratio']*100,'unit':'%','precision':2}],'limitations':LIMITATIONS+(['2017概化公开参考市界，不是现行权威市界。'] if plan['scope']=='xiantao_city' else ['天地图参考界年份未声明；仅按此次归档轮廓统计，不作为现行法定界线。'] if region else []),'layers':[{'id':base+'-raster','kind':'raster','title':title,'image':{'url':'overlay.png','bounds':[[bounds[1],bounds[0]],[bounds[3],bounds[2]]]},'legend':[{'color':'#44cb99','label':'2024水稻类别1'},{'color':'#d1dcd0','label':'产品非水稻类别0；透明处为范围外或未知'}]},{'id':base+'-boundary','kind':'geojson','title':'分析范围','boundary':True,'geojson':plan['boundary']}],'provenance':{'source_id':SOURCE_ID,'original_doi':DOI,'redistributor':'ESA ECDC / WEED','metadata_sha256':digest(record),'source_asset':asset['href'],'source_product':{'id':SOURCE_ID,'name':'GlobalRice20 2024 / ESA ECDC COG','year':2024,'class_code':1},'boundary_source':plan['boundary_source'],'region':region,'analysis_bbox':plan['bbox'],'sources':[{'title':'ESA公开STAC来源','url':ITEM_URL},{'title':'原始GlobalRice20 DOI','url':'https://doi.org/'+DOI}],'license':'CC-BY-4.0 (ESA collection declaration)','local_accuracy_validated':False,'native_window':[c0,r0,c1-c0,r1-r0]},'validation':{'status':'passed','method':'reopened native cells/mask/count validation; not local classification accuracy'},'native_sha256':sha(temp/'classification.tif')}
   result['artifacts']=[{'file':name,'sha256':sha(temp/name),'bytes':(temp/name).stat().st_size} for name in ('classification.tif','statistics.csv','source.json','boundary.geojson','overlay.png')]
   write(temp/'result.json',result);check();temp.replace(directory)
   if hasattr(self.store,'save'):
    self.store.save('data_source_check',{'id':'cropcheck_'+uuid.uuid4().hex[:14],'source_id':SOURCE_ID,'created_at':result['created_at'],'verification':{'status':'succeeded','operation':'native_crop_read','checked_at':result['created_at'],'query_scope':{'bbox':plan['bbox'],'year':2024},'detail':'实际读取公开COG并归档原生分类；文件SHA已核验，非本地地面精度验证。'}})
   return atlas.get(base),False
  except rasterio.errors.RasterioIOError:
   raise GeoError('CROP_PIXELS_UNAVAILABLE','公开水稻COG读取失败或超时，未生成替代数据；可重试同一来源',502)
  finally:
   if temp.exists():
    import shutil;shutil.rmtree(temp)
