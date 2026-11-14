"""Persisted real raster processing jobs: temporal collections and Otsu partitions."""
import copy,hashlib,json,re,threading,uuid
from pathlib import Path
from geo_services import GeoError,finite
from landcover_services import now
from agriculture_services import ready,_sha,_source_dates
from spectral_indices import specification
import adaptive_segmentation

VERSION='raster-processing-1.0'
TEMPORAL=('temporal_median','temporal_mean','temporal_trend')
NAMES={'temporal_median':'时段中位数合成','temporal_mean':'时段均值合成','temporal_trend':'指数时序趋势','otsu_segmentation':'自适应阈值分区'}

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def files(algorithm):return adaptive_segmentation.FILES if algorithm=='otsu_segmentation' else ('analysis.tif','overlay.png','result.geojson','series.csv','result.json')
def fingerprint(value):return hashlib.sha256(canonical(value)).hexdigest()
def source_fingerprint(task):return fingerprint({k:task.get(k) for k in ('plan','result')})
def capabilities():
 return {'version':VERSION,'available':ready(),'limits':{'max_bbox_span_deg':.05,'max_scenes':6,'max_period_days':183,'max_cells':1048576},'algorithms':[{'id':id,'name':name,'version':adaptive_segmentation.VERSION if id=='otsu_segmentation' else 'collection-native-1.0','requires':'成功单期原生光谱栅格，自动128分箱阈值及可选连通斑块筛选' if id=='otsu_segmentation' else '同一传感器、明确选区/日期、2至6个独立观测日期；趋势至少3日期','external_engine_call':False} for id,name in NAMES.items()],'limitations':['仅使用登记的真实数据与固定算法；不会执行生成的任意代码或URL。','多景是所选有效观测的统计，不是逐日实测、作物分类或灾损认定。','不足支持的像元保持未知，不用单景或样本替代多景。']}


class RasterProcessingServices:
 def __init__(self,store,output_dir=None):
  self.store=store;self.output_dir=Path(output_dir or Path(store.db_path).parent/'raster-processing');self.stopping=False;self.threads=[]
  for task in self.list():
   if task['status'] in ('queued','running'):
    task.update(status='failed',finished_at=now(),error={'code':'INTERRUPTED','message':'服务重启，影像处理已中断；可按冻结条件重试'})
    for node in task.get('nodes',[]):
     if node['status'] in ('pending','running'):node.update(status='failed',finished_at=now())
    store.save('raster_processing_task',task)
 def list(self):return self.store.list('raster_processing_task')
 def get(self,id):return self.store.get('raster_processing_task',id)
 def source(self,id):
  task,path=self.store.agriculture._source('production',id)
  if task['plan'].get('goal')!='ndvi':raise GeoError('PROCESSING_REQUIRES_SINGLE_INDEX','自动阈值分区只接受单期原生光谱结果，不能忽略两期共同有效掩膜',422)
  return task,path
 def prepare(self,body):
  if not isinstance(body,dict):raise GeoError('INVALID_PROCESSING_INPUT','影像处理输入须为对象',400)
  algorithm=body.get('algorithm_id')
  if algorithm not in NAMES:raise GeoError('PROCESSING_ALGORITHM_UNSUPPORTED','请选择已实现的合成、趋势或Otsu算法',422)
  if algorithm in TEMPORAL:
   allowed={'algorithm_id','source_id','index_id','bbox','start','end','scene_limit','cloud'}
   if set(body)-allowed:raise GeoError('INVALID_PROCESSING_INPUT','时序请求包含不支持字段',400)
   from collection_raster import make_plan
   request=copy.deepcopy(body);source=request.get('source_id','auto')
   if source=='auto':request['source_id']='pc_landsat_c2_l2' if str(request.get('start',''))<'2015-06-27' else 'pc_sentinel2_l2a'
   core=make_plan(request);request={key:core[key] for key in allowed if key in core};core.update(request=request,processing_service_version=VERSION)
   core['input_fingerprint']=fingerprint({'algorithm_version':VERSION,'request':request})
   return core
  allowed={'algorithm_id','source_type','source_task_id','direction','min_patch_pixels'}
  if set(body)-allowed or body.get('source_type','production')!='production':raise GeoError('INVALID_PROCESSING_INPUT','自动阈值仅接受明确的单期生产任务，不接URL、路径或其他字段',400)
  direction=body.get('direction','above');minimum=body.get('min_patch_pixels',1)
  if direction not in ('above','below'):raise GeoError('INVALID_PROCESSING_DIRECTION','分区方向须为above或below',422)
  if isinstance(minimum,bool) or not isinstance(minimum,int) or not 1<=minimum<=1000:raise GeoError('INVALID_PROCESSING_PATCH_SIZE','最小斑块须为1至1000的整数像元数',422)
  task,path=self.source(body.get('source_task_id'));p=task['result']['provenance'];s=task['result']['summary'];index=s.get('index_id') or task['plan'].get('index_id','ndvi');spec=specification(index)
  source={'type':'production','task_id':task['id'],'record_fingerprint':source_fingerprint(task),'artifact_name':'analysis.tif','artifact_sha256':_sha(path),'input_sha256':p.get('input_sha256'),'index_id':index,'index_label':spec['label'],'actual_dates':_source_dates(task),'bbox':p.get('bbox') or task['plan']['aoi']['bbox'],'source_id':p.get('source_id') or task['plan'].get('source_id'),'reference_resolution_m':(s.get('index') or spec).get('reference_resolution_m')}
  request={'algorithm_id':algorithm,'source_type':'production','source_task_id':task['id'],'direction':direction,'min_patch_pixels':minimum}
  plan={'algorithm_id':algorithm,'algorithm_version':VERSION,'request':request,'source':source,'direction':direction,'min_patch_pixels':minimum,'bins':128,'limitations':adaptive_segmentation.LIMITATIONS}
  plan['input_fingerprint']=fingerprint({k:v for k,v in plan.items() if k!='limitations'});return plan
 def create(self,body,*,frozen_collection=None):
  plan=self.prepare(body)
  if frozen_collection is not None:
   if plan['algorithm_id'] not in TEMPORAL:raise GeoError('PROCESSING_INPUT_CONFLICT','只有时序分析接收内部冻结选景',422)
   plan['collection']=copy.deepcopy(frozen_collection)
  if not ready():raise GeoError('PROCESSING_UNAVAILABLE','本机原生栅格环境未就绪',503)
  task={'id':'processing_'+uuid.uuid4().hex[:14],'name':NAMES[plan['algorithm_id']],'status':'queued','progress':0,'plan':plan,'nodes':[{'id':key,'name':name,'status':'pending'} for key,name in [('source','冻结真实输入与选景'),('read','读取并校验原生像元'),('compute','计算统计与空间分区'),('archive','保存地图、明细与来源')]],'logs':[{'time':now(),'message':'已记录明确输入，将调用本机登记的实际像元算法'}],'created_at':now(),'result':None}
  with self.store.lock:
   if self.stopping:raise GeoError('SERVICE_STOPPING','服务正在关闭',503)
   if sum(t['status'] in ('queued','running') for t in self.list())>=2:raise GeoError('PROCESSING_BUSY','本机已有两个影像处理任务，请稍后提交',429)
   self.store.save('raster_processing_task',task);thread=threading.Thread(target=self.execute,args=(task['id'],),daemon=True);self.threads.append(thread);thread.start()
  return task
 def change(self,id,fn):
  with self.store.lock:
   task=self.get(id)
   if task['status'] in ('cancelled','succeeded','failed'):return False
   fn(task);self.store.save('raster_processing_task',task);return True
 def check(self,id):
  if self.stopping:raise GeoError('INTERRUPTED','服务停止，影像处理已中断')
  if self.get(id)['status'] not in ('queued','running'):raise GeoError('PROCESSING_STOPPED','影像处理已经结束')
 def verify_source(self,plan,check):
  task,path=self.source(plan['source']['task_id']);check()
  if source_fingerprint(task)!=plan['source']['record_fingerprint'] or _sha(path,check)!=plan['source']['artifact_sha256']:raise GeoError('PROCESSING_SOURCE_CHANGED','归档源成果或方法已改变，不能按旧参数静默读取新版本',409)
  return path
 def execute(self,id):
  directory=self.output_dir/id;directory.mkdir(parents=True,exist_ok=True)
  try:
   self.check(id);task=self.get(id);plan=task['plan'];check=lambda:self.check(id)
   def phase(stage,message):
    number={'discover':0,'source':0,'read':1,'compute':2,'archive':3}.get(stage,1)
    def update(t):
     t.update(status='running',progress=[5,25,65,90][number]);t['logs'].append({'time':now(),'message':str(message)})
     for i,node in enumerate(t['nodes']):
      if i<number and node['status'] in ('pending','running'):node.update(status='succeeded',finished_at=now())
     t['nodes'][number].update(status='running',started_at=t['nodes'][number].get('started_at',now()))
    check();self.change(id,update)
   phase('source','正在复核真实源数据')
   if plan['algorithm_id'] in TEMPORAL:
    from collection_raster import discover_collection,run_collection
    reader=self.store.production.landsat_reader if plan['source_id']=='pc_landsat_c2_l2' else self.store.production.reader
    if 'collection' not in plan:
     plan['collection']=discover_collection(plan,reader,check)
     self.change(id,lambda t:t['plan'].update(collection=copy.deepcopy(plan['collection'])))
    check();result=run_collection(plan,reader,directory,check,phase)
   else:
    path=self.verify_source(plan,check);phase('read','读取原生指数与选区/有效掩膜');phase('compute','计算Otsu阈值与四邻域连通分区')
    result=adaptive_segmentation.run_segmentation(plan,path,directory,check);self.verify_source(plan,check)
   check();phase('archive','校验可下载文件与实际统计')
   summary=result['summary']
   summary.setdefault('valid_pixel_count',summary.get('valid_count'))
   summary.setdefault('min_observations',summary.get('minimum_observations',1))
   summary.setdefault('mean_value',summary.get('statistics',{}).get('mean',summary.get('mean_index')))
   if plan['algorithm_id']=='temporal_trend':summary['slope_unit']='index/day'
   else:summary.setdefault('index_unit','index')
   if 'series' in result:
    result['time_series']=[{**row,'mean_value':row.get('mean_index_all_valid'),'mean_common_support':row.get('mean_index_common_support'),'date':str(row.get('date',row.get('datetime','')))[:10]} for row in result['series']]
   result['provenance']['input_fingerprint']=plan['input_fingerprint'];result['provenance']['processing_service_version']=VERSION
   for name in files(plan['algorithm_id']):
    if name!='result.json' and not (directory/name).is_file():raise GeoError('PROCESSING_ARTIFACT_MISSING','处理产物不完整，不能发布成功',500)
   (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2),'utf-8');check()
   def complete(t):
    t.update(status='succeeded',progress=100,result=result,finished_at=now())
    for node in t['nodes']:node.update(status='succeeded',finished_at=now())
    t['logs'].append({'time':now(),'message':'真实统计、地图及来源文件已归档'})
   self.change(id,complete)
  except Exception as error:
   code=getattr(error,'code','RASTER_PROCESSING_FAILED');message=getattr(error,'message','影像处理未成功，未生成替代数据；请查看条件和重试')
   def fail(t):
    t.update(status='failed',finished_at=now(),error={'code':code,'message':message});t['logs'].append({'time':now(),'message':message})
    for node in t['nodes']:
     if node['status'] in ('pending','running'):node.update(status='failed',finished_at=now())
   self.change(id,fail)
 def artifact(self,id,name):
  task=self.get(id)
  if task['status']!='succeeded' or name not in files(task['plan']['algorithm_id']):raise GeoError('ARTIFACT_UNAVAILABLE','影像处理成果尚未成功或文件不存在',404)
  path=self.output_dir/id/name
  if not path.is_file() or not path.resolve().is_relative_to(self.output_dir.resolve()) or path.is_symlink():raise GeoError('ARTIFACT_UNAVAILABLE','成果文件不可用',404)
  return path
 def cancel(self,id):
  with self.store.lock:
   task=self.get(id)
   if task['status'] not in ('queued','running'):raise GeoError('INVALID_STATE','仅待处理或运行中的任务可取消',409)
   task.update(status='cancelled',finished_at=now());task['logs'].append({'time':now(),'message':'本任务已取消，原数据不受影响'})
   for node in task['nodes']:
    if node['status'] in ('pending','running'):node.update(status='cancelled',finished_at=now())
   return self.store.save('raster_processing_task',task)
 def retry(self,id):
  task=self.get(id)
  if task['status'] not in ('failed','cancelled'):raise GeoError('INVALID_STATE','只有失败或取消任务可重试',409)
  if task['plan']['algorithm_id']=='otsu_segmentation':self.verify_source(task['plan'],lambda:None)
  if 'collection' in task['plan'] and not isinstance(task['plan']['collection'],dict):raise GeoError('PROCESSING_FROZEN_SOURCE_INVALID','冻结选景损坏，不能重新检索并冒充原任务重试',409)
  return self.create(copy.deepcopy(task['plan']['request']),frozen_collection=task['plan'].get('collection'))
 def close(self):
  self.stopping=True
  for thread in self.threads:thread.join(timeout=.5)
