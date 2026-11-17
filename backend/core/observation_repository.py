"""Versioned observation/asset library, additive to existing workspace records."""
import copy,hashlib,json,math,re
from datetime import datetime,timezone,date
from urllib.parse import quote
from backend.core.geo_services import GeoError,finite
from backend.catalog.catalog_adapters import PROVIDERS

def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

class ObservationRepository:
 def __init__(self,store):
  self.store=store
  with store.connection() as c:
   c.executescript('''CREATE TABLE IF NOT EXISTS eo_observations(id TEXT PRIMARY KEY,source_id TEXT NOT NULL,collection TEXT NOT NULL,item_id TEXT NOT NULL,observed_at TEXT,cloud REAL,metadata_sha256 TEXT NOT NULL,metadata_json TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,UNIQUE(source_id,collection,item_id,metadata_sha256));
CREATE INDEX IF NOT EXISTS eo_observations_time ON eo_observations(source_id,observed_at);
CREATE INDEX IF NOT EXISTS eo_observations_item ON eo_observations(collection,item_id);
CREATE TABLE IF NOT EXISTS eo_assets(observation_id TEXT NOT NULL,asset_key TEXT NOT NULL,common_name TEXT,metadata_json TEXT NOT NULL,PRIMARY KEY(observation_id,asset_key));
CREATE INDEX IF NOT EXISTS eo_assets_band ON eo_assets(common_name,observation_id);
CREATE TABLE IF NOT EXISTS eo_extents(observation_id TEXT PRIMARY KEY,west REAL NOT NULL,south REAL NOT NULL,east REAL NOT NULL,north REAL NOT NULL);
CREATE INDEX IF NOT EXISTS eo_extents_west_east ON eo_extents(west,east);
CREATE TABLE IF NOT EXISTS eo_discovery_members(search_id TEXT NOT NULL,observation_id TEXT NOT NULL,PRIMARY KEY(search_id,observation_id));
CREATE TABLE IF NOT EXISTS eo_pixel_uses(observation_id TEXT NOT NULL,task_id TEXT NOT NULL,input_sha256 TEXT NOT NULL,used_at TEXT NOT NULL,PRIMARY KEY(observation_id,task_id));
PRAGMA user_version=13;''')
   for key,metadata in c.execute('SELECT id,metadata_json FROM eo_observations WHERE id NOT IN (SELECT observation_id FROM eo_extents)').fetchall():
    b=json.loads(metadata).get('bbox')
    if isinstance(b,list) and len(b)==4:c.execute('INSERT OR IGNORE INTO eo_extents VALUES(?,?,?,?,?)',(key,*b))
 def _view(self,row,c):
  from backend.catalog.algorithm_catalog import match_observation
  doc=json.loads(row['metadata_json']);doc.update(id=row['id'],metadata_sha256=row['metadata_sha256'],fingerprint=row['metadata_sha256'],ingested_at=row['first_seen_at'],last_seen_at=row['last_seen_at'])
  doc['search_ids']=[r[0] for r in c.execute('SELECT search_id FROM eo_discovery_members WHERE observation_id=?',(row['id'],))]
  uses=[dict(zip(('task_id','input_sha256','used_at'),r)) for r in c.execute('SELECT task_id,input_sha256,used_at FROM eo_pixel_uses WHERE observation_id=?',(row['id'],))]
  matches=match_observation(doc)['matches'];ready=any(m.get('ready') for m in matches)
  doc['stages']={'catalogued':True,'pixel_verified':bool(uses),'analysis_ready':ready};doc['pixel_uses']=uses
  doc['compatible_algorithms']=[m['id'] for m in matches if m.get('ready')]
  doc['analysis_context']={'observation_id':doc['id'],'source_id':doc['source_id'],'period':{'start':str(doc.get('datetime',''))[:10],'end':str(doc.get('datetime',''))[:10]},'bbox_is_scene_coverage':True,'requires_user_aoi':True} if ready else None
  return doc
 @staticmethod
 def _rows(c,sql,args=()):
  cursor=c.execute(sql,args);names=[v[0] for v in cursor.description]
  return [dict(zip(names,r)) for r in cursor.fetchall()]
 def ingest(self,body):
  if not isinstance(body,dict) or set(body)-{'search_id','source_id','item_ids'}:raise GeoError('INVALID_OBSERVATION_INGEST','入库仅接受目录快照ID、来源与条目ID',400)
  search_id=body.get('search_id')
  if not isinstance(search_id,str) or not 1<=len(search_id)<=100:raise GeoError('INVALID_OBSERVATION_INGEST','请提供真实目录检索ID',400)
  search=self.store.data_catalog.get_search(search_id);source=body.get('source_id');ids=body.get('item_ids')
  if source is not None and source not in PROVIDERS:raise GeoError('INVALID_SOURCE_SELECTION','来源未登记',400)
  if ids is not None and (not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(v,str) or not v or len(v)>300 for v in ids) or len(set(ids))!=len(ids)):raise GeoError('INVALID_OBSERVATION_INGEST','条目ID须为1–100项唯一ID',400)
  selected=[]
  for result in search.get('results',[]):
   if result.get('status') not in ('succeeded','empty') or source and result['source_id']!=source:continue
   for item in result.get('items',[]):
    if ids is None or item['id'] in ids:selected.append((result,item))
  if ids is not None and set(ids)-{item['id'] for _,item in selected}:raise GeoError('OBSERVATION_NOT_IN_SEARCH','所选条目不属于该次成功目录快照',422)
  if not selected:raise GeoError('NO_OBSERVATIONS_TO_INGEST','目录没有可入库的真实候选',422)
  inserted=updated=0;keys=[];timestamp=now()
  with self.store.lock,self.store.connection() as c:
   for result,item in selected:
    src=result['source_id'];doc={k:copy.deepcopy(item.get(k)) for k in ('datetime','bbox','cloud_cover','platform','assets','geometry','temporal_role')}
    doc.update(source_id=src,provider=PROVIDERS[src]['provider'],collection=item.get('collection') or PROVIDERS[src]['collection'],item_id=item['id'],assets=doc.get('assets') or [],source_type='public_catalog_observation',provenance={'source_url':PROVIDERS[src]['root']+'collections/'+PROVIDERS[src]['collection']+'/items/'+quote(item['id'],safe=''),'collection_url':PROVIDERS[src]['root']+'collections/'+PROVIDERS[src]['collection'],'catalog_only':True})
    # Search time and credentials do not participate in immutable metadata version.
    fingerprint=digest(doc);key='observation_'+digest([src,doc['collection'],doc['item_id'],fingerprint])[:24]
    existing=c.execute('SELECT 1 FROM eo_observations WHERE id=?',(key,)).fetchone()
    if existing:c.execute('UPDATE eo_observations SET last_seen_at=? WHERE id=?',(timestamp,key));updated+=1
    else:
     c.execute('INSERT INTO eo_observations VALUES(?,?,?,?,?,?,?,?,?,?)',(key,src,doc['collection'],doc['item_id'],doc.get('datetime'),doc.get('cloud_cover'),fingerprint,json.dumps(doc,ensure_ascii=False,allow_nan=False),timestamp,timestamp));inserted+=1
     for asset in doc['assets']:
      c.execute('INSERT INTO eo_assets VALUES(?,?,?,?)',(key,asset['key'],asset.get('common_name'),json.dumps(asset,ensure_ascii=False,allow_nan=False)))
    c.execute('INSERT OR IGNORE INTO eo_extents VALUES(?,?,?,?,?)',(key,*doc['bbox']))
    c.execute('INSERT OR IGNORE INTO eo_discovery_members VALUES(?,?)',(search_id,key));keys.append(key)
  return {'search_id':search_id,'inserted':inserted,'updated':updated,'total':len(keys),'items':[self.get(k) for k in keys],'message':'已入库观测与无凭据资产元数据；尚未因此读取像元'}
 def get(self,key):
  if not isinstance(key,str) or not re.fullmatch(r'observation_[a-f0-9]{24}',key):raise GeoError('OBSERVATION_NOT_FOUND','未找到此影像资源版本',404)
  with self.store.connection() as c:
   rows=self._rows(c,'SELECT * FROM eo_observations WHERE id=?',(key,))
   if not rows:raise GeoError('OBSERVATION_NOT_FOUND','未找到此影像资源版本',404)
   return self._view(rows[0],c)
 def list(self,query=None):
  q=query or {};allowed={'source_id','collection','start','end','cloud_max','q','bbox','limit','offset'}
  if set(q)-allowed:raise GeoError('INVALID_OBSERVATION_FILTER','资源筛选包含不支持字段',400)
  def val(k,default=None):v=q.get(k,default);return v[0] if isinstance(v,list) else v
  def integer(k,default,maximum):
   v=val(k,default)
   if isinstance(v,bool) or not re.fullmatch(r'\d{1,7}',str(v)) or not 0<=int(v)<=maximum:raise GeoError('INVALID_OBSERVATION_FILTER','分页参数无效',400)
   return int(v)
  limit=integer('limit',20,100);offset=integer('offset',0,1000000)
  if limit<1:raise GeoError('INVALID_OBSERVATION_FILTER','每页至少一条',400)
  clauses=[];args=[]
  for key,column in (('source_id','source_id'),('collection','collection')):
   value=val(key)
   if value:
    if not isinstance(value,str) or len(value)>150:raise GeoError('INVALID_OBSERVATION_FILTER','来源或集合无效',400)
    clauses.append(column+'=?');args.append(value)
  dates={}
  for key,op in (('start','>='),('end','<=')):
   value=val(key)
   if value:
    try:
     if not isinstance(value,str) or len(value)!=10:raise ValueError()
     date.fromisoformat(value)
    except ValueError:raise GeoError('INVALID_OBSERVATION_FILTER','日期须为有效YYYY-MM-DD',400)
    dates[key]=value;clauses.append('substr(observed_at,1,10)'+op+'?');args.append(value)
  if dates.get('start','')>dates.get('end','9999'):raise GeoError('INVALID_OBSERVATION_FILTER','日期顺序无效',400)
  cloud=val('cloud_max')
  if cloud not in (None,''):
   try:cloud=float(cloud)
   except (ValueError,TypeError):raise GeoError('INVALID_OBSERVATION_FILTER','云量无效',400)
   if not math.isfinite(cloud) or not 0<=cloud<=100:raise GeoError('INVALID_OBSERVATION_FILTER','云量限0–100',400)
   clauses.append('cloud<=?');args.append(cloud)
  text=val('q')
  if text:
   if not isinstance(text,str) or len(text)>200:raise GeoError('INVALID_OBSERVATION_FILTER','关键词限200字',400)
   clauses.append('instr(lower(metadata_json),lower(?))>0');args.append(text)
  box=q.get('bbox')
  if box is not None:
   if isinstance(box,list) and len(box)==1:box=box[0]
   if isinstance(box,str):
    try:box=[float(v) for v in re.split(r'[,，\s]+',box.strip())]
    except ValueError:raise GeoError('INVALID_OBSERVATION_FILTER','空间范围无效',400)
   if not isinstance(box,list) or len(box)!=4 or not all(finite(v) for v in box) or not -180<=box[0]<box[2]<=180 or not -90<=box[1]<box[3]<=90:raise GeoError('INVALID_OBSERVATION_FILTER','bbox须为西南东北四个有效经纬度',400)
   clauses.append('id IN (SELECT observation_id FROM eo_extents WHERE west<=? AND east>=? AND south<=? AND north>=?)');args.extend([box[2],box[0],box[3],box[1]])
  where=' WHERE '+' AND '.join(clauses) if clauses else ''
  with self.store.connection() as c:
   total=c.execute('SELECT count(*) FROM eo_observations'+where,args).fetchone()[0]
   rows=self._rows(c,'SELECT * FROM eo_observations'+where+' ORDER BY observed_at DESC,id LIMIT ? OFFSET ?',args+[limit,offset]);items=[self._view(row,c) for row in rows]
   stats={'observations':c.execute('SELECT count(*) FROM eo_observations').fetchone()[0],'assets':c.execute('SELECT count(*) FROM eo_assets').fetchone()[0],'sources':c.execute('SELECT count(DISTINCT source_id) FROM eo_observations').fetchone()[0],'pixel_verified':c.execute('SELECT count(DISTINCT observation_id) FROM eo_pixel_uses').fetchone()[0]}
  return {'items':items,'total':total,'limit':limit,'offset':offset,'stats':stats,'schema_version':'1.0','message':'资源库保存观测/资产版本；已入库、可调算法、实际读取分别计量'}
 def record_pixel_use(self,observation_id,task_id,input_sha256):
  if not observation_id:return
  observation=self.get(observation_id)
  task=self.store.get('production_task',task_id)
  if task.get('status')!='succeeded' or (task.get('result') or {}).get('provenance',{}).get('input_sha256')!=input_sha256:raise GeoError('PIXEL_USE_NOT_VERIFIED','仅已成功且输入哈希一致的原生任务可登记像元读取',422)
  reference=task['result']['provenance'].get('observation_ref') or {}
  if reference.get('id')!=observation_id or reference.get('metadata_sha256')!=observation['metadata_sha256']:raise GeoError('PIXEL_USE_NOT_VERIFIED','任务实际绑定的观测版本不一致',422)
  with self.store.lock,self.store.connection() as c:c.execute('INSERT OR IGNORE INTO eo_pixel_uses VALUES(?,?,?,?)',(observation_id,task_id,input_sha256,now()))
