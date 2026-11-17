"""Verified regional data products: fixed local artifacts and explicit data lineage."""
import copy,hashlib,json,math,re,threading,uuid,zipfile
from pathlib import Path
from backend.core.geo_services import GeoError

VERSION='agriculture-atlas-1.0'
SLUG=re.compile(r'[a-z0-9][a-z0-9-]{2,95}')
FILE=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}')
MAX_BYTES=512*1024*1024

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode('utf-8')
def sha_file(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def bounds(value):
 if isinstance(value,list) and len(value)==2 and all(isinstance(v,list) and len(v)==2 for v in value):value=[value[0][1],value[0][0],value[1][1],value[1][0]]
 if not isinstance(value,list) or len(value)!=4 or not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in value):raise GeoError('ATLAS_BOUNDS','成果配准范围无效',422)
 w,s,e,n=value
 if not -180<=w<e<=180 or not -85<=s<n<=85:raise GeoError('ATLAS_BOUNDS','成果范围不是WGS84配准边界',422)
 return [[s,w],[n,e]]

def product_quality(raw):
 s=raw.get('summary') or {};p=raw.get('provenance') or {};selection=p.get('selection') or {}
 minimum=selection.get('minimum_city_qa_ratio');valid=s.get('valid_ratio',s.get('coverage_ratio'))
 numeric=lambda v:isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and 0<=v<=1
 minimum=minimum if numeric(minimum) and minimum>0 else None;valid=valid if numeric(valid) else None
 qa=[];scenes=p.get('scenes') or {}
 for epoch,item in scenes.items() if isinstance(scenes,dict) else []:
  scene=(item.get('scene') or {}) if isinstance(item,dict) else {};ident=scene.get('scene_id')
  for candidate in selection.get('candidates',[]):
   if isinstance(candidate,dict) and candidate.get('scene_id')==ident and numeric(candidate.get('qa_valid_ratio')):qa.append(candidate['qa_valid_ratio'])
 qa_ratio=min(qa) if qa else None;warnings=[]
 if isinstance(s.get('quality_warning'),str) and s['quality_warning'].strip():warnings.append(s['quality_warning'].strip())
 low=s.get('quality_target_met') is False or (minimum is not None and valid is not None and valid<minimum)
 if s.get('quality_target_met') is False and not warnings:warnings.append('选中观测未达到请求的市界QA覆盖门槛，仅作低覆盖参考。')
 if minimum is not None and valid is not None and valid<minimum:warnings.append('最终指数有效覆盖低于选景参考门槛；云质量检查与公式域有效范围不同，缺测不能外推。')
 status='below_target' if low else 'passed' if s.get('quality_target_met') is True else 'not_assessed'
 return {'status':status,'warning':' '.join(dict.fromkeys(warnings)),'minimum_city_qa_ratio':minimum,'actual_city_qa_ratio':qa_ratio,'valid_ratio':valid,'coverage_is_accuracy':False}

class AgricultureAtlas:
 def __init__(self,root):self.root=Path(root);self.lock=threading.RLock();self.verified={};self.fingerprints={}
 def _content_hash(self,path):
  stat=path.stat();key=(str(path),stat.st_mtime_ns,stat.st_size)
  if key not in self.fingerprints:self.fingerprints[key]=sha_file(path)
  return self.fingerprints[key]
 def _sources(self):
  out={}
  if not self.root.exists():return out
  for directory in sorted(self.root.iterdir()):
   if not directory.is_dir() or directory.is_symlink() or not SLUG.fullmatch(directory.name):continue
   status_path=directory/'status.json'
   if status_path.is_file():
    try:
     if json.loads(status_path.read_text()).get('status') in ('running','failed'):continue
    except (ValueError,OSError):continue
   path=directory/'result.json'
   if not path.is_file() or path.is_symlink() or path.stat().st_size>4*1024*1024:continue
   try:
    raw=json.loads(path.read_text('utf-8'))
    if not isinstance(raw,dict):continue
    ident=raw.get('id',directory.name)
    if ident!=directory.name or raw.get('status') not in ('ready','succeeded'):continue
    out[ident]=(directory,raw,ident)
    if ident=='xiantao-2024-classification':
     for alias in ('xiantao-2024-cropland','xiantao-2024-water','xiantao-2024-trees'):out[alias]=(directory,raw,alias)
   except (ValueError,OSError,TypeError):continue
  return out
 def _binding(self,ident):
  if not isinstance(ident,str) or not SLUG.fullmatch(ident):raise GeoError('INVALID_ATLAS_ID','成果ID无效',422)
  match=self._sources().get(ident)
  if not match:raise GeoError('ATLAS_NOT_FOUND','该数据成果尚未生产完成或不存在',404)
  return match
 def _files(self,directory,raw):
  files={'result.json'}
  for item in raw.get('artifacts',[]):
   name=item if isinstance(item,str) else item.get('file',item.get('name')) if isinstance(item,dict) else None
   if isinstance(name,str) and FILE.fullmatch(name) and '..' not in name:files.add(name)
  return files
 def _file(self,directory,raw,name):
  if name not in self._files(directory,raw):raise GeoError('ATLAS_FILE_NOT_FOUND','成果未声明该文件',404)
  p=directory/name
  if not p.is_file() or p.is_symlink() or p.resolve().parent!=directory.resolve():raise GeoError('ATLAS_FILE_UNAVAILABLE','成果文件不可用或不安全',422)
  expected=next((item.get('sha256') for item in raw.get('artifacts',[]) if isinstance(item,dict) and item.get('file',item.get('name'))==name),None)
  if name in ('analysis.tif','classification.tif'):expected=expected or raw.get('native_sha256')
  if expected:
   identity=(str(p),p.stat().st_mtime_ns,p.stat().st_size,expected)
   if identity not in self.verified:
    if sha_file(p)!=expected:raise GeoError('ATLAS_SOURCE_CHANGED','成果文件与生产清单哈希不一致，拒绝显示或下载',409)
    self.verified[identity]=True
  return p
 def _url(self,ident,name):return '/api/agriculture-atlas/'+ident+'/files/'+name
 def _normalize(self,ident,directory,raw):
  source_id=directory.name;s=raw.get('summary',{});image=raw.get('image',{});prov=copy.deepcopy(raw.get('provenance',{}));limitations=list(raw.get('limitations',[]));metric=lambda label,value,unit,precision=2:{'label':label,'value':value,'unit':unit,'precision':precision}
  if source_id=='xiantao-2024-classification':
   common={'id':ident,'title':'仙桃2024土地覆盖底数','scene_ids':['farmland','grain'],'status':'ready','product_type':'annual_classification','scope_label':'仙桃全市 · 2017公开参考市界','period_label':'2024年度产品','resolution_m':10,'limitations':limitations,'provenance':prov}
   summary_metrics=[metric('参考覆盖总面积',s.get('total_area_ha'),'公顷'),metric('分类有效覆盖',(s.get('coverage_ratio') or 0)*100,'%',1),metric('未知面积',s.get('unknown_area_ha'),'公顷')]
   filename='classes.png';legend=[{'color':c['color'],'label':c['name']} for c in raw.get('classes',[]) if c.get('pixel_count',0)>0]
   if ident.endswith('-cropland'):
    common.update(title='仙桃耕作地参考底数',scene_ids=['farmland','grain','sowing','high_standard']);filename='cropland.png';legend=[{'color':'#41d091','label':'2024 Crops类别（非确权地块）'}]
    summary_metrics=[metric('Crops类别参考面积',s.get('cropland_mu'),'亩',1),metric('Crops类别参考面积',s.get('cropland_ha'),'公顷'),metric('有效分类覆盖',(s.get('coverage_ratio') or 0)*100,'%',1)]
   elif ident.endswith('-water'):
    common.update(title='仙桃水域与养殖环境参考',scene_ids=['breeding','tailwater','fishing','rivers','flood']);filename='water.png';legend=[{'color':'#419bdf','label':'年度Water类别（非养殖塘认定）'}]
    summary_metrics=[metric('Water类别参考面积',s.get('water_ha'),'公顷'),metric('水域占参考范围比例',s.get('water_ha',0)/s['total_area_ha']*100 if s.get('total_area_ha') else None,'%',1)]
    common['limitations']=['水体类别不等同养殖塘、黄鳝养殖或禁捕执法对象；需主体和监管边界。']+limitations
   elif ident.endswith('-trees'):
    common.update(title='仙桃树木覆盖参考',scene_ids=['forest','forest_change']);filename='trees.png';legend=[{'color':'#397d49','label':'2024 Trees类别（非林权调查）'}];summary_metrics=[metric('Trees类别参考面积',s.get('tree_ha'),'公顷')]
   common['summary_metrics']=summary_metrics;common['layers']=[{'id':ident+'-raster','title':common['title'],'kind':'raster','image':{'url':self._url(ident,filename),'bounds':bounds(image.get('bounds'))},'legend':legend}]
   if raw.get('boundary'):common['layers'].append({'id':ident+'-boundary','title':'2017公开参考市界','kind':'geojson','geojson':raw['boundary'],'boundary':True})
   common['classes']=copy.deepcopy(raw.get('classes',[]));common['summary']=copy.deepcopy(s)
   prov.setdefault('sources',[])
   if not prov['sources']:
    source=prov.get('source_product',{});boundary=prov.get('boundary_source',{})
    prov['sources']=[{'title':source.get('name','IO Annual LULC 2024'),'url':source.get('source_url','https://registry.opendata.aws/io-lulc/'),'license':'CC BY 4.0'},{'title':'geoBoundaries 2017公开参考市界','url':boundary.get('metadata_url','https://www.geoboundaries.org/api/current/gbOpen/CHN/ADM2/')}]
  else:
   common={key:copy.deepcopy(raw[key]) for key in ('id','title','name','scene_ids','status','product_type','scope_label','period_label','resolution_m','summary_metrics','limitations','provenance','layers','summary','classes') if key in raw}
   common.update(id=ident,title=raw.get('title',raw.get('name',ident)),status='ready');common.setdefault('scene_ids',['farmland']);common.setdefault('scope_label','仙桃市公开参考范围');common.setdefault('period_label',str(s.get('reference_year','见实际观测记录')));common.setdefault('summary_metrics',[]);common.setdefault('layers',[])
   for layer in common['layers']:
    if layer.get('kind')=='raster':
     img=layer.get('image',{});name=img.get('url','overlay.png')
     if name.startswith('/'):name=name.rsplit('/',1)[-1]
     self._file(directory,raw,name);img['url']=self._url(ident,name);img['bounds']=bounds(img.get('bounds',image.get('bounds')));layer['image']=img
   if not common['layers'] and raw.get('image') and 'overlay.png' in self._files(directory,raw):
    common['layers']=[{'id':ident+'-raster','title':common['title'],'kind':'raster','image':{'url':self._url(ident,'overlay.png'),'bounds':bounds(image.get('bounds'))},'legend':image.get('legend',[])}]
  provenance=common.setdefault('provenance',{})
  boundary=provenance.get('boundary_source',provenance.get('boundary',{}))
  if isinstance(boundary,dict):
   year=boundary.get('boundary_year_represented',s.get('boundary_year'))
   if year:provenance.setdefault('boundary_year',year)
  if provenance.get('source_id')=='pc_landsat_c2_l2':
   provenance.setdefault('sources',[{'title':'Landsat Collection 2 L2 真实观测目录','url':'https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2'},{'title':'USGS 地表反射率定标说明','url':'https://www.usgs.gov/faqs/how-do-i-use-a-scale-factor-landsat-level-2-science-products'},{'title':'USGS 云、阴影及饱和质量位','url':'https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands'}])
   algorithm=provenance.get('algorithm',{})
   provenance.setdefault('method','实际场景逐波段定标，排除填充值、云、阴影、雪和饱和像元；'+str(algorithm.get('index_formula',''))+'。缺测保留未知，原生30米格网统计；2024作物类别仅作历史分层参考。')
   if isinstance(boundary,dict) and boundary.get('metadata_url'):provenance['sources'].append({'title':'公开参考市界及版本','url':boundary['metadata_url']})
  common['version']=raw.get('version',raw.get('schema_version',VERSION));common['created_at']=raw.get('created_at',prov.get('created_at'));common['source_product_id']=source_id;common['layer_count']=len(common['layers']);common['quality']=product_quality(raw)
  if (raw.get('validation') or {}).get('status')=='failed':raise GeoError('ATLAS_VALIDATION_FAILED','该成果的原生验证未通过，不能标为可用',409)
  common['artifact_sha256']={filename:self._content_hash(self._file(directory,raw,filename)) for filename in sorted(self._files(directory,raw))}
  for layer in common['layers']:
   if layer.get('kind')=='raster':self._file(directory,raw,layer['image']['url'].rsplit('/',1)[-1])
  common['downloads']=[{'label':'完整成果与来源包','url':'/api/agriculture-atlas/'+ident+'/bundle.zip','type':'zip'}]
  labels={'classification.tif':'原生分类GeoTIFF','analysis.tif':'分析GeoTIFF','classes.csv':'分类统计CSV','statistics.csv':'指标统计CSV','row_counts.csv':'逐行复核CSV','project.csv':'公告项目台账CSV','locations.csv':'地址线索CSV','locations.geojson':'地址线索GeoJSON','result.json':'完整结果JSON'}
  for name in sorted(self._files(directory,raw)):
   if name in labels:common['downloads'].append({'label':labels[name],'url':self._url(ident,name),'type':name.rsplit('.',1)[-1]})
  from backend.agriculture.regional_product_tools import product_fingerprint
  common['product_sha256']=product_fingerprint(common)
  return common
 def list(self):
  products=[];errors=[]
  for ident,(directory,raw,_) in self._sources().items():
   try:
    full=self._normalize(ident,directory,raw);products.append({key:full.get(key) for key in ('id','title','scene_ids','status','product_type','scope_label','period_label','resolution_m','summary_metrics','limitations','layer_count','created_at','source_product_id','product_sha256','quality')})
   except (GeoError,ValueError,TypeError,KeyError,OSError,AttributeError):errors.append(ident)
  priority={key:i for i,key in enumerate(['xiantao-2024-cropland','xiantao-2026-spectral-ndvi','xiantao-2026-spectral-change','xiantao-2024-water','xiantao-2026-spectral-ndmi','xiantao-2026-spectral-mndwi','xiantao-cropland-review','xiantao-high-standard-2026'])}
  products.sort(key=lambda p:(priority.get(p['id'],30),p['id']))
  return {'schema_version':VERSION,'region':None,'scope_mode':'per_product','products':products,'production_record_count':len({p['source_product_id'] for p in products}),'unavailable_product_ids':errors,'note':'按产品实际年度/观测日及公开参考市界生产；覆盖率不代表精度。'}
 def get(self,ident):
  directory,raw,_=self._binding(ident)
  try:return self._normalize(ident,directory,raw)
  except (TypeError,KeyError,ValueError,AttributeError):raise GeoError('ATLAS_SCHEMA_INVALID','成果结构不完整，未作为可用结果返回',422)
 def artifact(self,ident,name):
  directory,raw,_=self._binding(ident);return self._file(directory,raw,name)
 def require_version(self,ident,expected):
  product=self.get(ident)
  if not isinstance(expected,str) or not re.fullmatch(r'[a-f0-9]{64}',expected):raise GeoError('INVALID_ATLAS_VERSION','成果版本须为64位内容指纹',400)
  if product['product_sha256']!=expected:raise GeoError('ATLAS_VERSION_CHANGED','该成果来源或内容已更新，请重新查看当前版本；没有替换历史答案或下载',409)
  return product
 def bundle(self,ident):
  with self.lock:
   directory,raw,_=self._binding(ident);product=self._normalize(ident,directory,raw);files={name:self._file(directory,raw,name) for name in sorted(self._files(directory,raw))}
   if sum(p.stat().st_size for p in files.values())>MAX_BYTES:raise GeoError('ATLAS_BUNDLE_LIMIT','成果包超过本机512MB上限',422)
   manifest={'schema_version':VERSION,'id':ident,'entries':[{'path':name,'bytes':p.stat().st_size,'sha256':sha_file(p)} for name,p in files.items()]}
   generated={'product.json':canonical(product),'README.txt':('仙桃农业真实数据成果\n'+product['title']+'\n'+product['scope_label']+'\n'+product['period_label']+'\n\n'+'\n'.join(product.get('limitations',[]))+'\n\n统计以原生栅格及CSV为准，PNG仅为配准预览。manifest记录文件字节SHA256；完整来源在provenance中。\n').encode()}
   manifest['entries'] += [{'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in generated.items()]
   fingerprint=hashlib.sha256(canonical(manifest)).hexdigest();out=self.root/'.bundles';out.mkdir(exist_ok=True);path=out/(ident+'-'+fingerprint[:16]+'.zip')
   if path.exists():
    try:
     with zipfile.ZipFile(path) as z:
      if z.testzip() is None and json.loads(z.read('manifest.json'))==manifest and set(z.namelist())=={e['path'] for e in manifest['entries']}|{'manifest.json'} and all(hashlib.sha256(z.read(e['path'])).hexdigest()==e['sha256'] for e in manifest['entries']):return path
    except (OSError,ValueError,zipfile.BadZipFile):pass
   temp=out/(path.name+'.'+uuid.uuid4().hex+'.partial')
   try:
    with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
     for name,p in files.items():
      z.write(p,name)
      if sha_file(p)!=next(e['sha256'] for e in manifest['entries'] if e['path']==name):raise GeoError('ATLAS_SOURCE_CHANGED','打包期间来源文件改变，停止归档',409)
     for name,data in generated.items():z.writestr(name,data)
     z.writestr('manifest.json',canonical(manifest))
    with zipfile.ZipFile(temp) as z:
     if z.testzip() is not None or any(hashlib.sha256(z.read(e['path'])).hexdigest()!=e['sha256'] for e in manifest['entries']):raise GeoError('ATLAS_SOURCE_CHANGED','打包字节与来源清单不一致，未交付',409)
    temp.replace(path);return path
   finally:temp.unlink(missing_ok=True)
