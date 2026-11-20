"""Indexed business registries and a local incident -> verification -> archive loop.

No business seeds, sensor values or official warnings are manufactured. Geometry
intersection locates objects to CHECK; it never certifies agricultural damage.
"""
import copy
import hashlib
import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from backend.core.geo_services import GeoError

KINDS = {'farmland':'种植地块','livestock':'畜牧主体','aquaculture':'水产养殖',
         'high_standard':'高标准农田','demonstration':'示范创建','machinery':'农机资源','coldchain':'冷库资源'}
HAZARDS = {'flood':'洪涝','drought':'干旱','freeze':'冰冻雨雪','wind':'大风','typhoon':'台风','other':'其他核查'}
STATES = {'assigned':{'processing'},'processing':{'submitted'},'returned':{'processing'},'submitted':{'closed','returned'},'closed':set()}
MAX_IMPACT = 5000


def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def ident(prefix):return prefix+'_'+uuid.uuid4().hex[:18]
def encode(value):
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))
    except (TypeError,ValueError):raise GeoError('INVALID_OPERATIONS_INPUT','内容须为有效JSON，不接受非有限数值',422)
def text(value,label,maximum=200,optional=False):
    if not isinstance(value,str) or len(value)>maximum or (not optional and not value.strip()):
        raise GeoError('INVALID_OPERATIONS_INPUT',label+'未填写或长度超限',422)
    return value.strip()
def fields(body,allowed,required=()):
    if not isinstance(body,dict) or set(body)-set(allowed) or set(required)-set(body):
        raise GeoError('INVALID_OPERATIONS_INPUT','请求字段不完整或包含不支持字段',422)
def revision(body):
    value=body.get('expected_revision')
    if isinstance(value,bool) or not isinstance(value,int) or value<1:raise GeoError('REVISION_REQUIRED','请先刷新记录，再按当前版本提交',409)
    return value
def number(value,label,minimum,maximum):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not minimum<=value<=maximum:
        raise GeoError('INVALID_OPERATIONS_INPUT',label+'超出有效范围',422)
    return value
def geometry(value,area=False):
    try:from shapely.geometry import shape
    except ImportError:raise GeoError('GEOMETRY_ENGINE_UNAVAILABLE','请安装requirements-raster.txt中的空间计算依赖',503)
    if not isinstance(value,dict) or value.get('type') not in (('Polygon','MultiPolygon') if area else ('Point','LineString','Polygon','MultiPolygon')):
        raise GeoError('INVALID_BUSINESS_GEOMETRY','请选择WGS84点、线或面；范围须为面数据',422)
    positions=[]
    def walk(v):
        if not isinstance(v,list) or not v:raise ValueError()
        if isinstance(v[0],(int,float)):
            if len(v)!=2 or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in v) or not -180<=v[0]<=180 or not -85<=v[1]<=85:raise ValueError()
            positions.append(v)
            if len(positions)>20000:raise ValueError()
        else:
            for child in v:walk(child)
    try:
        walk(value.get('coordinates'))
        polygons=[value['coordinates']] if value['type']=='Polygon' else value['coordinates'] if value['type']=='MultiPolygon' else []
        for polygon in polygons:
            if any(len(ring)<4 or ring[0]!=ring[-1] for ring in polygon):raise ValueError()
        g=shape(value)
        if g.is_empty or not g.is_valid:raise ValueError()
        if g.bounds[2]-g.bounds[0]>30 or g.bounds[3]-g.bounds[1]>30:raise ValueError()
        return g
    except (ValueError,TypeError,KeyError,RecursionError):raise GeoError('INVALID_BUSINESS_GEOMETRY','几何无效、坐标超限或单对象超过20000坐标点；未自动修补边界',422)


class OperationsServices:
    def __init__(self,store):
        self.store=store
        with store.lock,store.connection() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS agri_registries(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS agri_objects(rowid INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,registry_id TEXT NOT NULL,code TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,UNIQUE(registry_id,code));
              CREATE INDEX IF NOT EXISTS agri_object_registry ON agri_objects(registry_id,rowid);
              CREATE INDEX IF NOT EXISTS agri_object_status ON agri_objects(registry_id,status);
              CREATE VIRTUAL TABLE IF NOT EXISTS agri_object_bounds USING rtree(rowid,minx,maxx,miny,maxy);
              CREATE TABLE IF NOT EXISTS agri_imports(registry_id TEXT,batch_id TEXT,digest TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(registry_id,batch_id));
              CREATE TABLE IF NOT EXISTS agri_documents(kind TEXT NOT NULL,id TEXT PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS agri_document_kind ON agri_documents(kind,created_at);
              CREATE TABLE IF NOT EXISTS agri_audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,entity_id TEXT NOT NULL,action TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,data TEXT NOT NULL);
            ''')
    def audit(self,c,item,action,actor):
        c.execute('INSERT INTO agri_audit(entity_id,action,actor,created_at,data) VALUES(?,?,?,?,?)',(item['id'],action,actor,now(),encode(item)))
    def _get(self,c,kind,id):
        row=c.execute('SELECT data FROM agri_documents WHERE kind=? AND id=?',(kind,id)).fetchone()
        if not row:raise GeoError('OPERATIONS_NOT_FOUND','未找到业务记录',404)
        return json.loads(row[0])
    def _put(self,c,kind,item):
        c.execute('INSERT OR REPLACE INTO agri_documents VALUES(?,?,?,?,?)',(kind,item['id'],item['revision'],encode(item),item['created_at']))
    def _registry(self,c,id):
        row=c.execute('SELECT data FROM agri_registries WHERE id=?',(id,)).fetchone()
        if not row:raise GeoError('REGISTRY_NOT_FOUND','未找到台账',404)
        return json.loads(row[0])
    def _version(self,item,expected):
        if item['revision']!=expected:raise GeoError('OPERATIONS_VERSION_CHANGED','记录已更新，请刷新后再操作；未覆盖其他修改',409)
    def catalog(self):
        return {'schema_version':'agriculture-operations-1.0','storage':'sqlite_rtree','authentication':'local_workspace_only',
          'kinds':[{'id':k,'name':v} for k,v in KINDS.items()], 'hazards':[{'id':k,'name':v} for k,v in HAZARDS.items()],
          'modules':[{'id':k,'name':name,'status':status,'note':note} for k,name,status,note in [
          ('supply','稳产保供','registry','种植、畜牧、水产主体台账；遥感参考另见专题分析。'),
          ('high_standard','高标准农田','registry','项目边界、状态、进度及业务属性；监控待授权接入。'),
          ('services','社会化服务','awaiting_integration','可登记农机；实时北斗、排班和道路服务圈待接入。'),
          ('traceability','农产品溯源','reserved','预留；暂未建设批次和消费扫码流程。'),
          ('coldchain','冷链物流','awaiting_integration','可登记冷库；动态库存及出入库订单待建设。'),
          ('drone','禁捕巡河','awaiting_integration','无人机设备、直播及作业接口待接入。'),
          ('disaster','防灾减灾','workflow','人工录入或业务转录的范围核查、派单和归档；不是官方预警发布系统。'),
          ('demonstration','示范创建','registry','示范区、村镇与项目台账，进度和报批属性由业务提供。'),
          ('guidance','生产指导','workflow','管理人员编制、审核发布与执行回执；不自动生成施肥剂量。')]],
          'limits':{'batch_features':500,'page_size':200,'impact_objects':MAX_IMPACT},
          'connections':[{'id':k,'status':'not_connected','name':n} for k,n in [('weather_station','气象站与权威预警'),('soil','土壤墒情'),('pond','塘口监测'),('video','现场视频'),('beidou','北斗农机'),('warehouse','冷库库存')]]}
    def registries(self):
        with self.store.connection() as c:
            return [dict(json.loads(r[0]),object_count=r[1]) for r in c.execute('SELECT r.data,(SELECT count(*) FROM agri_objects o WHERE o.registry_id=r.id) FROM agri_registries r ORDER BY r.rowid DESC')]
    def create_registry(self,b):
        fields(b,{'name','kind','source','year','actor'},{'name','kind','source','actor'})
        if not isinstance(b['kind'],str) or b['kind'] not in KINDS:raise GeoError('INVALID_REGISTRY_KIND','请选择支持的业务台账类型',422)
        year=b.get('year')
        if year is not None:
            number(year,'业务年度',1900,2200)
            if not isinstance(year,int):raise GeoError('INVALID_OPERATIONS_INPUT','业务年度须为整数',422)
        item={'id':ident('registry'),'name':text(b['name'],'台账名称'),'kind':b['kind'],'source':text(b['source'],'数据来源',500),'year':year,'revision':1,'created_at':now()}
        actor=text(b['actor'],'操作人',100)
        with self.store.lock,self.store.connection() as c:
            c.execute('INSERT INTO agri_registries VALUES(?,?,?,?,?)',(item['id'],item['name'],item['kind'],1,encode(item)));self.audit(c,item,'registry_created',actor)
        return dict(item,object_count=0)
    def import_batch(self,id,b):
        fields(b,{'dataset_id','geojson','code_field','name_field','status_field','batch_id','expected_revision','actor'}, {'code_field','batch_id','expected_revision','actor'})
        if ('dataset_id' in b)==('geojson' in b):raise GeoError('INVALID_IMPORT_SOURCE','请选择上传图层或分批GeoJSON一种输入',422)
        expected=revision(b);actor=text(b['actor'],'操作人',100);batch=text(b['batch_id'],'批次编号',100)
        source={}
        if 'dataset_id' in b:
            d=self.store.get_dataset(b['dataset_id'])
            if d.get('source_type')!='user':raise GeoError('BUSINESS_DATA_REQUIRED','须使用实际上传台账，不使用遥感结果冒充业务档案',422)
            geo=d['geojson'];source={k:d[k] for k in ('id','version','sha256')}
        else:geo=b['geojson']
        if isinstance(geo,dict) and geo.get('crs') is not None:raise GeoError('INVALID_BUSINESS_CRS','请转换为WGS84 GeoJSON后入库，不接受其他坐标系声明',422)
        if not isinstance(geo,dict) or geo.get('type')!='FeatureCollection' or not isinstance(geo.get('features'),list) or not 1<=len(geo['features'])<=500:raise GeoError('INVALID_BUSINESS_BATCH','每批须为1至500对象的GeoJSON FeatureCollection',422)
        code_field=text(b['code_field'],'业务编码字段',100);name_field=b.get('name_field','');status_field=b.get('status_field','')
        text(name_field,'名称字段',100,True);text(status_field,'状态字段',100,True)
        digest=hashlib.sha256(encode({'geojson':geo,'source':source,'mapping':[code_field,name_field,status_field]}).encode()).hexdigest()
        decoded=[];seen=set()
        for f in geo['features']:
            if not isinstance(f,dict) or f.get('type')!='Feature' or not isinstance(f.get('properties'),dict):raise GeoError('INVALID_BUSINESS_BATCH','对象须包含属性和几何',422)
            p=f['properties'];code=text(p.get(code_field),'每个对象的业务编码',100)
            if code in seen:raise GeoError('DUPLICATE_BUSINESS_CODE','同批业务编码重复：'+code,422)
            seen.add(code);g=geometry(f.get('geometry'));name=text(p.get(name_field,code) if name_field else code,'对象名称');status=text(p.get(status_field,'未填报') if status_field else '未填报','业务状态',100)
            if len(encode(p))>30000:raise GeoError('BUSINESS_PROPERTIES_LIMIT','单对象属性过大',422)
            decoded.append((code,name,status,f['geometry'],p,g.bounds))
        with self.store.lock,self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE');reg=self._registry(c,id)
            old=c.execute('SELECT digest,data FROM agri_imports WHERE registry_id=? AND batch_id=?',(id,batch)).fetchone()
            if old:
                if old[0]!=digest:raise GeoError('IMPORT_BATCH_CONFLICT','同批次编号已用于其他内容，未覆盖原批次',409)
                return dict(json.loads(old[1]),registry_revision=reg['revision'],reused=True)
            self._version(reg,expected)
            if reg['kind'] in ('farmland','high_standard','demonstration') and any(v[3]['type'] not in ('Polygon','MultiPolygon') for v in decoded):raise GeoError('REGISTRY_POLYGONS_REQUIRED','地块、工程和示范区须提供真实面边界',422)
            items=[]
            for code,name,status,geo,p,bounds in decoded:
                item={'id':ident('object'),'registry_id':id,'kind':reg['kind'],'code':code,'name':name,'status':status,'revision':1,'geometry':geo,'properties':p,'source':source,'created_at':now()}
                try:cur=c.execute('INSERT INTO agri_objects(id,registry_id,code,name,status,revision,data) VALUES(?,?,?,?,?,?,?)',(item['id'],id,code,name,status,1,encode(item)))
                except sqlite3.IntegrityError:raise GeoError('DUPLICATE_BUSINESS_CODE','台账已存在编码：'+code+'；未重复导入或部分写入',409)
                w,s,e,n=bounds;c.execute('INSERT INTO agri_object_bounds VALUES(?,?,?,?,?)',(cur.lastrowid,w,e,s,n));items.append(item['id'])
            reg['revision']+=1;c.execute('UPDATE agri_registries SET revision=?,data=? WHERE id=?',(reg['revision'],encode(reg),id))
            result={'id':batch,'batch_id':batch,'registry_id':id,'registry_revision':reg['revision'],'imported_count':len(items),'object_ids':items,'source':source,'created_at':now(),'reused':False}
            c.execute('INSERT INTO agri_imports VALUES(?,?,?,?)',(id,batch,digest,encode(result)));self.audit(c,dict(result,id=id),'objects_imported',actor)
            return result
    def objects(self,query):
        if set(query)-{'registry_id','bbox','q','status','limit','offset'} or any(len(v)!=1 for v in query.values()):raise GeoError('INVALID_OBJECT_QUERY','查询参数不支持或重复',422)
        get=lambda key,default='':query.get(key,[default])[0]
        try:
            limit=int(get('limit','100'));offset=int(get('offset','0'))
            if not 1<=limit<=200 or offset<0:raise ValueError()
        except (TypeError,ValueError):raise GeoError('INVALID_OBJECT_QUERY','分页须为有效整数，每页最多200对象',422)
        where=['1=1'];args=[];join=''
        for key,col in [('registry_id','o.registry_id'),('status','o.status')]:
            if get(key):where.append(col+'=?');args.append(get(key))
        if get('q'):
            where.append('(instr(o.name,?)>0 OR instr(o.code,?)>0)');args.extend([text(get('q'),'搜索词',100)]*2)
        if get('bbox'):
            try:
                bb=[float(v) for v in get('bbox').split(',')]
                if len(bb)!=4 or not all(math.isfinite(v) for v in bb) or not -180<=bb[0]<bb[2]<=180 or not -85<=bb[1]<bb[3]<=85:raise ValueError()
            except (ValueError,TypeError):raise GeoError('INVALID_OBJECT_QUERY','地图范围无效',422)
            join=' JOIN agri_object_bounds r ON r.rowid=o.rowid';where+=['r.minx<=? AND r.maxx>=? AND r.miny<=? AND r.maxy>=?'];args.extend([bb[2],bb[0],bb[3],bb[1]])
        sql=' FROM agri_objects o'+join+' WHERE '+' AND '.join(where)
        with self.store.connection() as c:
            total=c.execute('SELECT count(*)'+sql,args).fetchone()[0]
            items=[json.loads(r[0]) for r in c.execute('SELECT o.data'+sql+' ORDER BY o.rowid LIMIT ? OFFSET ?',args+[limit,offset])]
        return {'items':items,'total':total,'limit':limit,'offset':offset,'has_more':offset+len(items)<total,'spatial_filter':'bbox_candidates' if join else None}
    def update_object(self,id,b):
        fields(b,{'expected_revision','actor','status','progress','note'}, {'expected_revision','actor','status','note'})
        expected=revision(b);actor=text(b['actor'],'操作人',100);status=text(b['status'],'业务状态',100);note=text(b['note'],'更新说明',2000)
        if 'progress' in b:number(b['progress'],'进度百分比',0,100)
        with self.store.lock,self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT data FROM agri_objects WHERE id=?',(id,)).fetchone()
            if not row:raise GeoError('OPERATIONS_NOT_FOUND','未找到业务对象',404)
            item=json.loads(row[0]);self._version(item,expected);item.update(status=status,note=note,revision=expected+1,updated_at=now())
            if 'progress' in b:item['progress']=b['progress']
            c.execute('UPDATE agri_objects SET status=?,revision=?,data=? WHERE id=?',(status,item['revision'],encode(item),id))
            reg=self._registry(c,item['registry_id']);reg['revision']+=1;c.execute('UPDATE agri_registries SET revision=?,data=? WHERE id=?',(reg['revision'],encode(reg),reg['id']));self.audit(c,item,'object_updated',actor)
        return item
    def documents(self,kind):
        with self.store.connection() as c:return [json.loads(r[0]) for r in c.execute('SELECT data FROM agri_documents WHERE kind=? ORDER BY created_at DESC,id',(kind,))]
    def document(self,kind,id):
        with self.store.connection() as c:return self._get(c,kind,id)
    def create_event(self,b):
        fields(b,{'title','hazard','geometry','source_name','source_url','source_kind','issued_at','actor'}, {'title','hazard','geometry','source_name','source_kind','issued_at','actor'})
        if not isinstance(b['hazard'],str) or b['hazard'] not in HAZARDS or b['source_kind'] not in ('manual','transcribed'):raise GeoError('INVALID_EVENT_SOURCE','事件类型或来源类型不支持；尚未接入权威预警推送',422)
        geometry(b['geometry'],True);date=text(b['issued_at'],'来源时间',40)
        try:datetime.fromisoformat(date.replace('Z','+00:00'))
        except ValueError:raise GeoError('INVALID_EVENT_TIME','请输入有效来源时间',422)
        url=b.get('source_url','');text(url,'来源链接',1000,True)
        if url and not url.startswith('https://'):raise GeoError('INVALID_SOURCE_URL','来源链接须为HTTPS，不由服务端代取网页',422)
        item={'id':ident('event'),'title':text(b['title'],'事件名称'),'hazard':b['hazard'],'geometry':b['geometry'],'source_name':text(b['source_name'],'来源名称',500),'source_url':url,'source_kind':b['source_kind'],'issued_at':date,'status':'recorded','revision':1,'created_at':now(),'notice':'人工录入或业务转录的待核查事件，不代表平台发布官方预警。'}
        with self.store.lock,self.store.connection() as c:self._put(c,'event',item);self.audit(c,item,'event_recorded',text(b['actor'],'操作人',100))
        return item
    def analyze_impact(self,id,b):
        fields(b,{'registry_ids','actor'},{'registry_ids','actor'})
        ids=b['registry_ids']
        if not isinstance(ids,list) or not 1<=len(ids)<=30 or not all(isinstance(v,str) for v in ids) or len(ids)!=len(set(ids)):raise GeoError('INVALID_IMPACT_SCOPE','请选择1至30个不同台账',422)
        actor=text(b['actor'],'操作人',100)
        with self.store.lock,self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE');event=self._get(c,'event',id);shape=geometry(event['geometry'],True);w,s,e,n=shape.bounds
            registry_versions={i:self._registry(c,i)['revision'] for i in ids};marks=','.join('?' for _ in ids)
            rows=c.execute('SELECT o.data FROM agri_objects o JOIN agri_object_bounds r ON r.rowid=o.rowid WHERE o.registry_id IN ('+marks+') AND r.minx<=? AND r.maxx>=? AND r.miny<=? AND r.maxy>=? ORDER BY o.rowid LIMIT ?',ids+[e,w,n,s,MAX_IMPACT+1]).fetchall()
            if len(rows)>MAX_IMPACT:raise GeoError('IMPACT_SCOPE_LIMIT','候选对象超过5000，请缩小事件范围或分区核查；未截断结果',422)
            matches=[];counts={};contacts=0
            for row in rows:
                obj=json.loads(row[0]);g=geometry(obj['geometry'])
                if not shape.intersects(g):continue
                # Boundary-only contact is reported separately, never counted as exposed area.
                if g.geom_type in ('Polygon','MultiPolygon') and shape.touches(g):contacts+=1;continue
                matches.append(obj);counts[obj['kind']]=counts.get(obj['kind'],0)+1
            item={'id':ident('impact'),'event_id':id,'event_revision':event['revision'],'registry_versions':registry_versions,'objects':matches,'counts':counts,'object_count':len(matches),'bbox_candidate_count':len(rows),'boundary_only_contacts':contacts,'method':'rtree_candidates_then_shapely_intersection_v1','revision':1,'created_at':now(),'notice':'空间相交待核查对象，不是实际受灾对象或受灾面积；未按外包矩形直接认定。'}
            self._put(c,'impact',item);self.audit(c,item,'impact_analyzed',actor)
        return item
    def dispatch(self,id,b):
        fields(b,{'object_ids','assignee','due_date','actor'},{'object_ids','assignee','due_date','actor'})
        wanted=b['object_ids'];owner=text(b['assignee'],'责任单位或人员',100);due=text(b['due_date'],'完成日期',10)
        try:datetime.strptime(due,'%Y-%m-%d')
        except ValueError:raise GeoError('INVALID_DUE_DATE','完成日期须为YYYY-MM-DD',422)
        if not isinstance(wanted,list) or not wanted or len(wanted)>200 or not all(isinstance(v,str) for v in wanted) or len(wanted)!=len(set(wanted)):raise GeoError('INVALID_DISPATCH','每次请选择1至200个不同对象',422)
        with self.store.lock,self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE');impact=self._get(c,'impact',id);lookup={o['id']:o for o in impact['objects']}
            if any(i not in lookup for i in wanted):raise GeoError('INVALID_DISPATCH_OBJECT','所选对象不属于本次空间核查结果',422)
            for rid,version in impact['registry_versions'].items():self._version(self._registry(c,rid),version)
            created=[]
            for oid in wanted:
                tid='task_'+hashlib.sha256((id+':'+oid).encode()).hexdigest()[:24]
                row=c.execute('SELECT data FROM agri_documents WHERE id=?',(tid,)).fetchone()
                if row:
                    old=json.loads(row[0])
                    if old['assignee']!=owner or old['due_date']!=due:raise GeoError('DISPATCH_ALREADY_EXISTS','该对象已派单，责任人与期限不一致；未重派或覆盖',409)
                    created.append(old);continue
                task={'id':tid,'title':'核查 · '+lookup[oid]['name'],'kind':'verification','event_id':impact['event_id'],'impact_id':id,'object':lookup[oid],'assignee':owner,'due_date':due,'status':'assigned','revision':1,'created_at':now()}
                self._put(c,'task',task);self.audit(c,task,'task_assigned',text(b['actor'],'操作人',100));created.append(task)
            return {'tasks':created,'count':len(created)}
    def update_task(self,id,b):
        fields(b,{'expected_revision','actor','status','note','finding','evidence_urls'},{'expected_revision','actor','status','note'})
        expected=revision(b);actor=text(b['actor'],'操作人',100);note=text(b['note'],'处理或审核说明',4000)
        text(b['status'],'任务状态',40)
        with self.store.lock,self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE');item=self._get(c,'task',id);self._version(item,expected)
            if b['status'] not in STATES[item['status']]:raise GeoError('INVALID_TASK_TRANSITION','须按接单、提交反馈、审核归档顺序处理',409)
            if b['status']=='submitted':
                if b.get('finding') not in ('normal','affected','needs_support','unknown','completed'):raise GeoError('FINDING_REQUIRED','请选择现场核查或执行结果',422)
                urls=b.get('evidence_urls',[])
                if not isinstance(urls,list) or len(urls)>8 or any(not isinstance(u,str) or not u.startswith('https://') or len(u)>1000 for u in urls):raise GeoError('INVALID_EVIDENCE','最多8个HTTPS证据链接；链接未自动核验',422)
                item['feedback']={'finding':b['finding'],'note':note,'evidence_urls':urls,'submitted_by':actor,'submitted_at':now()}
            elif 'finding' in b or 'evidence_urls' in b:raise GeoError('INVALID_TASK_FEEDBACK','仅在提交反馈时填写核查结果',422)
            if b['status']=='closed':item['review']={'actor':actor,'note':note,'reviewed_at':now()}
            item.update(status=b['status'],revision=expected+1,updated_at=now(),last_note=note)
            self._put(c,'task',item);self.audit(c,item,'task_'+item['status'],actor)
        return item
    def create_guidance(self,b):
        fields(b,{'title','content','registry_id','actor'},{'title','content','registry_id','actor'})
        with self.store.lock,self.store.connection() as c:
            reg=self._registry(c,b['registry_id']);item={'id':ident('guidance'),'title':text(b['title'],'指导标题'),'content':text(b['content'],'指导内容',4000),'registry_id':reg['id'],'registry_revision':reg['revision'],'status':'draft','revision':1,'created_at':now()}
            self._put(c,'guidance',item);self.audit(c,item,'guidance_drafted',text(b['actor'],'编制人',100))
        return item
    def publish_guidance(self,id,b):
        fields(b,{'expected_revision','actor','assignee','due_date'},{'expected_revision','actor','assignee','due_date'})
        expected=revision(b);actor=text(b['actor'],'审核人',100);owner=text(b['assignee'],'执行单位',100);due=text(b['due_date'],'完成日期',10)
        try:datetime.strptime(due,'%Y-%m-%d')
        except ValueError:raise GeoError('INVALID_DUE_DATE','完成日期须为YYYY-MM-DD',422)
        with self.store.lock,self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE');item=self._get(c,'guidance',id);self._version(item,expected)
            if item['status']!='draft':raise GeoError('GUIDANCE_ALREADY_PUBLISHED','指导已发布，原内容和任务保留',409)
            reg=self._registry(c,item['registry_id']);self._version(reg,item['registry_revision'])
            count=c.execute('SELECT count(*) FROM agri_objects WHERE registry_id=?',(reg['id'],)).fetchone()[0]
            if not 1<=count<=200:raise GeoError('GUIDANCE_SCOPE_LIMIT','请使用1至200个对象的明确台账发布指导，空台账或大范围须先分组',422)
            for row in c.execute('SELECT data FROM agri_objects WHERE registry_id=? ORDER BY rowid',(reg['id'],)).fetchall():
                obj=json.loads(row[0]);task={'id':ident('task'),'title':item['title']+' · '+obj['name'],'kind':'guidance','guidance_id':id,'instruction':item['content'],'object':obj,'assignee':owner,'due_date':due,'status':'assigned','revision':1,'created_at':now()};self._put(c,'task',task);self.audit(c,task,'guidance_assigned',actor)
            item.update(status='published',revision=expected+1,published_by=actor,published_at=now(),task_count=count)
            self._put(c,'guidance',item);self.audit(c,item,'guidance_published',actor)
        return item
    def audit_log(self,id):
        with self.store.connection() as c:return [{'seq':r[0],'action':r[1],'actor':r[2],'created_at':r[3],'snapshot':json.loads(r[4])} for r in c.execute('SELECT seq,action,actor,created_at,data FROM agri_audit WHERE entity_id=? ORDER BY seq',(id,))]
    def impact_geojson(self,id):
        item=self.document('impact',id)
        return {'type':'FeatureCollection','features':[{'type':'Feature','id':o['id'],'geometry':o['geometry'],'properties':{**o['properties'],'business_code':o['code'],'business_name':o['name'],'registry_id':o['registry_id'],'object_revision':o['revision'],'business_status':o['status']}} for o in item['objects']],
                'metadata':{'impact_id':id,'event_id':item['event_id'],'registry_versions':item['registry_versions'],'method':item['method'],'notice':item['notice'],'crs':'EPSG:4326'}}
    def export_event(self,id):
        with self.store.connection() as c:
            event=self._get(c,'event',id);impacts=[json.loads(r[0]) for r in c.execute("SELECT data FROM agri_documents WHERE kind='impact'")];impacts=[i for i in impacts if i['event_id']==id]
            tasks=[json.loads(r[0]) for r in c.execute("SELECT data FROM agri_documents WHERE kind='task'")];tasks=[i for i in tasks if i.get('event_id')==id]
        return {'schema_version':'agriculture-case-1.0','event':event,'impacts':impacts,'tasks':tasks,'audit':self.audit_log(id)+[a for t in tasks for a in self.audit_log(t['id'])],'exported_at':now(),'deployment_scope':'本机工作空间；操作人字段用于留痕，不构成组织身份认证。'}
    def route(self,method,parts,query,b=None):
        if method=='GET':
            if not parts or parts==['catalog']:return self.catalog()
            if parts==['registries']:return {'registries':self.registries()}
            if parts==['objects']:return self.objects(query)
            if len(parts)==1 and parts[0] in ('events','tasks','guidance'):return {'items':self.documents({'events':'event','tasks':'task','guidance':'guidance'}[parts[0]])}
            if len(parts)==2 and parts[0] in ('events','impacts','tasks','guidance'):return self.document({'events':'event','impacts':'impact','tasks':'task','guidance':'guidance'}[parts[0]],parts[1])
            if len(parts)==2 and parts[0]=='audit':return {'items':self.audit_log(parts[1])}
            if len(parts)==3 and parts[0]=='impacts' and parts[2]=='geojson':return self.impact_geojson(parts[1])
            if len(parts)==3 and parts[0]=='events' and parts[2]=='export':return self.export_event(parts[1])
        if method=='POST':
            if parts==['registries']:return self.create_registry(b)
            if len(parts)==3 and parts[0]=='registries' and parts[2]=='import':return self.import_batch(parts[1],b)
            if parts==['events']:return self.create_event(b)
            if len(parts)==3 and parts[0]=='events' and parts[2]=='analyze':return self.analyze_impact(parts[1],b)
            if len(parts)==3 and parts[0]=='impacts' and parts[2]=='dispatch':return self.dispatch(parts[1],b)
            if len(parts)==3 and parts[0]=='tasks' and parts[2]=='transition':return self.update_task(parts[1],b)
            if parts==['guidance']:return self.create_guidance(b)
            if len(parts)==3 and parts[0]=='guidance' and parts[2]=='publish':return self.publish_guidance(parts[1],b)
        if method=='PATCH' and len(parts)==2 and parts[0]=='objects':return self.update_object(parts[1],b)
        raise GeoError('OPERATIONS_NOT_FOUND','未找到农业业务接口',404)
