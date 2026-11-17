"""Typed place, coordinate, dataset and persisted-result search.

Place points are reference navigation locations, never administrative masks.
Optional credentialed TianDiTu lookup is explicit; local search stays available.
"""
import copy
import json
import math
import re
import unicodedata
from functools import lru_cache
from backend.paths import PROJECT_ROOT
from pathlib import Path
from datetime import datetime
from backend.core.geo_services import GeoError

ROOT=PROJECT_ROOT
PLACE_FILE=ROOT/'map_sources/china_reference_places.json'
NUMBER=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'

def norm(text):
    value=unicodedata.normalize('NFKD',str(text)).casefold()
    return ''.join(c for c in value if not unicodedata.combining(c) and (c.isalnum() or '\u4e00'<=c<='\u9fff'))

def score_normalized(q,values):
    if not q:return 0
    best=0
    for text in values:
        if text==q:best=max(best,100)
        elif text.startswith(q):best=max(best,70)
        elif q in text:best=max(best,40)
    return best

def score(query,values):return score_normalized(norm(query),(norm(value) for value in values))

def bounds(value):
    if not isinstance(value,(list,tuple)) or len(value)!=4:return None
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):return None
    w,s,e,n=value
    return list(value) if -180<=w<=e<=180 and -85<=s<=n<=85 else None

def geo_bounds(geo):
    points=[]
    def visit(v):
        if isinstance(v,list):
            if len(v)>=2 and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in v[:2]):points.append(v[:2])
            else:
                for x in v:visit(x)
    if isinstance(geo,dict):
        if geo.get('type')=='FeatureCollection':
            for f in geo.get('features',[]):visit((f.get('geometry') or {}).get('coordinates',[]))
        elif geo.get('type')=='Feature':visit((geo.get('geometry') or {}).get('coordinates',[]))
        else:visit(geo.get('coordinates',[]))
    return bounds([min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]) if points else None

def locate_fields(box):
    box=bounds(box)
    if not box:return None
    fields={'center':[(box[0]+box[2])/2,(box[1]+box[3])/2]}
    if box[0]<box[2] or box[1]<box[3]:fields['bbox']=box
    else:fields['zoom']=15
    return fields

def atlas_dates(product):
    """Index observation/publication dates, never a fetch or export timestamp."""
    provenance=product.get('provenance') or {};summary=product.get('summary') or {};values=[]
    for record in (provenance,summary):
        actual=record.get('actual_dates',[])
        values.extend(actual.values() if isinstance(actual,dict) else actual if isinstance(actual,list) else [])
        values.extend(record[key] for key in ('reference_year','source_year','year') if key in record)
    for scenes in (provenance.get('scenes',{}),(provenance.get('source_details') or {}).get('scenes',{})):
        for item in scenes.values() if isinstance(scenes,dict) else scenes if isinstance(scenes,list) else []:
            if isinstance(item,dict):values.append((item.get('scene') or item).get('datetime'))
    for source in provenance.get('sources',[]):
        if isinstance(source,dict):values.append(source.get('published_at'))
    dates=[]
    for value in values:
        if isinstance(value,bool) or not isinstance(value,(str,int)):continue
        value=str(value)
        if re.fullmatch(r'[12]\d{3}',value):valid=value
        else:
            try:valid=datetime.fromisoformat(value.replace('Z','+00:00')).date().isoformat()
            except ValueError:continue
        if valid not in dates:dates.append(valid)
    return dates

def atlas_title_term(value):
    """Two local title equivalents; place navigation keeps its own norm."""
    return re.sub(r'(?<!\d)(\d{4})年',r'\1',value.replace('仙桃市','仙桃'))

def atlas_location(product):
    """Derive navigation solely from delivered layers, not a region label."""
    boxes=[]
    for layer in product.get('layers',[]):
        if not isinstance(layer,dict):continue
        box=None
        if layer.get('kind')=='raster':
            value=(layer.get('image') or {}).get('bounds')
            if isinstance(value,list) and len(value)==2 and all(isinstance(v,list) and len(v)==2 for v in value):value=[value[0][1],value[0][0],value[1][1],value[1][0]]
            box=bounds(value)
        elif layer.get('kind')=='geojson':
            try:box=geo_bounds(layer.get('geojson'))
            except (TypeError,AttributeError,KeyError):continue
        if box:boxes.append(box)
    return locate_fields([min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]) if boxes else None

def coordinates(text):
    """Return (point, looks_coordinate). Invalid numeric input stays numeric."""
    text=unicodedata.normalize('NFKC',text).strip();lat=None;lon=None
    if re.fullmatch(r'(?:19|20|21)\d{2}(?:-\d{2}){0,2}',text):return None,False
    labeled=[(r'(?:经度|longitude|lon|lng)\s*[:=：]?\s*('+NUMBER+r')', 'lon'),(r'(?:纬度|latitude|lat)\s*[:=：]?\s*('+NUMBER+r')','lat')]
    if any(re.search(pattern,text,re.I) for pattern,_ in labeled):
        matches={key:re.findall(pattern,text,re.I) for pattern,key in labeled}
        remainder=text
        for pattern,_ in labeled:remainder=re.sub(pattern,'',remainder,flags=re.I)
        if all(len(v)==1 for v in matches.values()) and not re.sub(r'[\s,，;；°]','',remainder):lon=float(matches['lon'][0]);lat=float(matches['lat'][0])
        else:return None,True
    else:
        pair=re.fullmatch(r'\s*('+NUMBER+r')\s*°?\s*([EW])\s*[,，;；\s]+\s*('+NUMBER+r')\s*°?\s*([NS])\s*',text,re.I)
        if pair:
            lon=abs(float(pair[1]))*(-1 if pair[2].upper()=='W' else 1);lat=abs(float(pair[3]))*(-1 if pair[4].upper()=='S' else 1)
        else:
            pair=re.fullmatch(r'\s*('+NUMBER+r')\s*[,，;；\s]+\s*('+NUMBER+r')\s*',text)
            if pair:lon=float(pair[1]);lat=float(pair[2])
            elif re.fullmatch(r'[\d\s.,，;；+\-°EeWwNnSs]+',text) or re.search(r'(?:^|[,，\s])(?:nan|inf|infinity)(?:$|[,，\s])',text,re.I):return None,True
            else:return None,False
    if lon is None or lat is None or not math.isfinite(lon) or not math.isfinite(lat) or not -180<=lon<=180 or not -85<=lat<=85:return None,True
    return [lon,lat],True

@lru_cache(maxsize=4)
def load_places(path,modified):
    doc=json.loads(Path(path).read_text('utf-8'));places=doc['places'];metadata=doc['metadata']
    terms=[(p,[norm(x) for x in [p.get('label',''),p.get('name',''),p.get('name_ascii','')]+p.get('aliases',[])]) for p in places]
    return places,metadata,terms

class SearchServices:
    def __init__(self,store,place_file=None,tianditu=None):
        self.store=store;self.place_file=Path(place_file) if place_file else PLACE_FILE;self.tianditu=tianditu
        try:self.places,self.place_metadata,self.place_terms=load_places(str(self.place_file),self.place_file.stat().st_mtime_ns)
        except (OSError,ValueError,KeyError):self.places=[];self.place_metadata={};self.place_terms=[]
    def atlas_hits(self,query):
        atlas=getattr(self.store,'agriculture_atlas',None)
        if atlas is None:return []
        try:products=atlas.list().get('products',[])
        except (GeoError,OSError,ValueError,TypeError,KeyError,AttributeError):return []
        if not isinstance(products,list):return []
        hits=[]
        for product in products:
            if not isinstance(product,dict) or product.get('status')!='ready':continue
            ident=product.get('id')
            if not isinstance(ident,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,95}',ident):continue
            try:
                full=atlas.get(ident)
                if not isinstance(full,dict) or full.get('id')!=ident or full.get('status')!='ready':continue
                title=full.get('title');period=full.get('period_label','')
                if not isinstance(title,str) or not title.strip() or not isinstance(period,str):continue
                summary=full.get('summary') or {};index=summary.get('index_id') if isinstance(summary,dict) and full.get('product_type')=='city_spectral_reference' else None
                index=index if isinstance(index,str) and index in ('ndvi','ndmi','mndwi') else None
                dates=atlas_dates(full);rank=max(score(query,[title,ident,period]+dates+([index] if index else [])),score(atlas_title_term(query),[atlas_title_term(title)]))
                # A real regional index product must survive many old task hits.
                # This boost applies only to an exact declared spectral index.
                if index and norm(query)==index:rank+=20
                if not rank:continue
                loc=atlas_location(full)
                if full.get('layers') and not loc:continue
                subtitle='已生产参考成果'+(' · '+period if period else ' · 时相见来源')
                if dates and not all(date in period for date in dates):subtitle+=' · 来源日期 '+' → '.join(dates)
                if not loc:subtitle+=' · 无空间边界，仅查看资料'
                ref={'type':'agriculture_atlas','task_id':ident,'url':'/api/agriculture-atlas/'+ident}
                hit={'id':'agriculture_atlas:'+ident,'kind':'result','label':title[:100],'subtitle':subtitle,'source':'农业真实成果目录，来源见成果报告','source_type':'public_reference','task_type':'agriculture_atlas','task_id':ident,'result_ref':ref,'aoi_eligible':False,**(loc or {})}
                # Exact reference city names keep their existing rank+5 priority.
                hits.append((rank,0,hit))
            except (GeoError,OSError,ValueError,TypeError,KeyError,AttributeError):continue
        return hits
    def search(self,query,limit=12,remote=True):
        if not isinstance(query,str) or len(query)>200 or any(ord(c)<32 for c in query):raise GeoError('INVALID_SEARCH','搜索内容须为不超过200字符的单行文本',400)
        query=query.strip()
        if isinstance(limit,bool) or not re.fullmatch(r'\d{1,2}',str(limit)) or not 1<=int(limit)<=20:raise GeoError('INVALID_SEARCH_LIMIT','搜索条数须为1–20',400)
        if not isinstance(remote,bool):raise GeoError('INVALID_REMOTE_SEARCH','联网检索开关须为布尔值',400)
        limit=int(limit);warnings=[];hits=[]
        if not query:return {'query':query,'results':[],'warnings':[],'provider':'workspace+reference_places'}
        point,is_coordinate=coordinates(query)
        if is_coordinate:
            if point:
                results=[{'id':'coordinate:'+','.join(format(x,'.8f').rstrip('0').rstrip('.') for x in point),'kind':'coordinate','label':', '.join(format(x,'.6f').rstrip('0').rstrip('.') for x in point),'subtitle':'WGS84坐标 · 经度在前、纬度在后 · 仅定位','center':point,'zoom':14,'source':'用户输入坐标','source_type':'user_coordinate','aoi_eligible':False}]
            else:
                results=[];warnings=[{'code':'INVALID_COORDINATES','message':'坐标格式无效：请输入“经度,纬度”（经度−180至180，纬度−85至85），例如114.2,30.5。'}]
            return {'query':query,'results':results,'warnings':warnings,'provider':'coordinate_parser'}
        qplaces=query
        for prefix in ('中华人民共和国','中国','湖北省'):qplaces=qplaces.replace(prefix,'')
        for p,terms in self.place_terms:
            names=[p.get('label',''),p.get('name',''),p.get('name_ascii','')]+p.get('aliases',[]);rank=max(score_normalized(norm(query),terms),score_normalized(norm(qplaces),terms) if qplaces and qplaces!=query else 0)
            if not rank:continue
            center=p.get('center');loc=locate_fields([*center,*center]) if isinstance(center,list) and len(center)==2 else None
            if not loc:continue
            label=p.get('label') or p['name'];exact=next((a for a in names if norm(a)==norm(qplaces) and re.search(r'[\u4e00-\u9fff]',a)),None)
            if exact:label=exact
            hit={'id':'place:'+str(p['id']),'kind':'place','label':label,'subtitle':p.get('region_label','中国')+' · 地名参考点，仅用于地图定位','center':list(center),'zoom':p.get('zoom',11),'source':'GeoNames 地名参考点','source_url':p['source_url'],'source_type':'public_reference','aoi_eligible':False,'place_id':str(p['id']),'reference_date':self.place_metadata.get('fetched_at')}
            hits.append((rank+5, p.get('population',0),hit))
        questions=self.store.list('assistant_question');by_task={}
        for q in questions:
            if q.get('status')=='answered' and q.get('binding'):
                b=q['binding'];by_task.setdefault((b.get('type'),b.get('task_id')),[]).append(q)
        for kind,record_kind in [('production','production_task'),('landcover','landcover_task'),('cropland','cropland_task'),('agriculture','agriculture_task'),('raster_processing','raster_processing_task'),('analysis','analysis')]:
            for task in self.store.list(record_kind):
                if task.get('status')!='succeeded':continue
                result=task.get('result',task) or {};p=result.get('provenance') or {};plan=task.get('plan') or {};summary=result.get('summary',{});summary=summary if isinstance(summary,dict) else {}
                if p.get('source_type')=='synthetic' or kind=='production' and plan.get('mode')=='sample_pixels':continue
                box=p.get('bbox') or p.get('source_bbox') or plan.get('aoi',{}).get('bbox') or p.get('boundary_source',{}).get('bbox') or (result.get('image') or {}).get('bounds') or geo_bounds(result.get('boundary') or result.get('geojson') or {})
                loc=locate_fields(box)
                if not loc:continue
                related=by_task.get((kind,task['id']),[]);app_names=[str((q.get('application_ref') or {}).get('name','')) for q in related]
                index=(summary.get('index') or {}).get('label') or summary.get('index_id','').upper();title=task.get('name') or plan.get('requirement') or ('土地覆盖分类成果' if kind=='landcover' else (index+'光谱分析' if index else p.get('dataset_name','空间分析成果')))
                if kind=='agriculture':index='FVC' if plan.get('algorithm_id')=='fvc' else (plan.get('source') or {}).get('index_label','')
                if kind=='raster_processing':index=summary.get('index_label') or summary.get('index_id',plan.get('index_id','')).upper() or (plan.get('source') or {}).get('index_label','')
                candidates=[q for q in related if (q.get('answer') or {}).get('map_effect')!='none' and q.get('capability_id') not in ('source_quality','download_results')]
                primary=max(candidates,key=lambda q:score(query,[q.get('question',''),(q.get('answer') or {}).get('title',''),(q.get('application_ref') or {}).get('name',''),(q.get('application_ref') or {}).get('id','')])) if candidates else None
                if primary and (primary.get('answer') or {}).get('title'):title=primary['answer']['title']
                matched_app=next((name for name in app_names if score(query,[name])),None)
                if matched_app:title=matched_app+(' · '+index if index else '')
                scenes=p.get('source_details',{}).get('scenes',{})
                dates=[str(v.get('datetime',''))[:10] for v in (scenes.values() if isinstance(scenes,dict) else scenes if isinstance(scenes,list) else []) if isinstance(v,dict)]
                if not dates and isinstance(p.get('actual_dates'),dict):dates=[str(v)[:10] for v in p['actual_dates'].values() if v]
                if not dates and isinstance(p.get('actual_dates'),list):dates=[str(v)[:10] for v in p['actual_dates'] if v]
                if not dates and isinstance(summary.get('actual_dates'),list):dates=[str(v)[:10] for v in summary['actual_dates'] if v]
                if not dates and p.get('year'):dates=[str(p['year'])]
                if not dates and p.get('source_year'):dates=[str(p['source_year'])]
                values=[title,task['id'],plan.get('region',''),index,(summary.get('index') or {}).get('name',''),'成果 结果 遥感 分析']+app_names+dates
                if kind=='agriculture':values.extend([plan.get('algorithm_id',''),(plan.get('dataset') or {}).get('name',''),'植被覆盖度 FVC NDVI端元' if plan.get('algorithm_id')=='fvc' else '逐地块 分区统计 地块统计 zonal statistics'])
                if kind=='raster_processing':values.extend([plan.get('algorithm_id',''),{'temporal_median':'时段 中位数 合成 多时相', 'temporal_mean':'时段 均值 合成 多时相', 'temporal_trend':'时序 趋势 线性 回归 每天变化率', 'otsu_segmentation':'Otsu 自动 自适应 阈值 分区 连通 图斑'}.get(plan.get('algorithm_id'),''),(plan.get('source') or {}).get('task_id','')])
                for q in related:values.extend([q.get('question',''),(q.get('answer') or {}).get('title',''),(q.get('application_ref') or {}).get('id','')])
                rank=score(query,values)
                if not rank:continue
                source_type=p.get('source_type','public_satellite_pixels' if kind=='landcover' else 'unknown')
                if kind=='production':ref={'type':kind,'task_id':task['id'],'url':'/api/production/tasks/'+task['id'],'image_kind':'change' if plan.get('goal')=='change' else summary.get('index_id','ndvi')}
                elif kind=='cropland':ref={'type':kind,'task_id':task['id'],'url':'/api/cropland/tasks/'+task['id'],'image_kind':'cropland'}
                elif kind=='agriculture':ref={'type':kind,'task_id':task['id'],'url':'/api/agriculture/tasks/'+task['id'],'image_kind':plan.get('algorithm_id')}
                elif kind=='raster_processing':ref={'type':kind,'task_id':task['id'],'url':'/api/raster-processing/tasks/'+task['id'],'image_kind':plan.get('algorithm_id')}
                elif kind=='landcover':
                    image_kind=(((primary or {}).get('answer') or {}).get('result_ref') or {}).get('image_kind','cropland')
                    ref={'type':kind,'task_id':task['id'],'url':'/api/landcover/tasks/'+task['id'],'image_kind':image_kind if image_kind in ('cropland','classes') else 'cropland'}
                else:ref={'type':kind,'task_id':task['id'],'url':'/api/analysis/'+task['id'],'image_kind':'geojson'}
                hit={'id':kind+':'+task['id'],'kind':'result','label':str(title)[:100],'subtitle':('合成示例分析' if source_type=='synthetic' else '已完成成果')+(' · '+index if index else '')+(' · '+' → '.join(dates) if dates else ''),'source':(p.get('source') if isinstance(p.get('source'),str) else p.get('provider') if isinstance(p.get('provider'),str) else None) or ('ESA WorldCover / geoBoundaries参考界' if kind=='landcover' else '来源见任务报告'),'source_type':source_type,'task_type':kind,'task_id':task['id'],'result_ref':ref,**loc}
                if primary:hit['question_id']=primary['id']
                hits.append((rank+10,0,hit))
        hits.extend(self.atlas_hits(query))
        for dataset in self.store.list('dataset'):
            if dataset.get('source_type')!='user':continue
            loc=locate_fields(dataset.get('bbox') or geo_bounds(dataset.get('geojson',{})))
            if not loc:continue
            name=dataset.get('name','用户数据');rank=score(query,[name,dataset['id'],'用户数据 导入 图层'])
            matches=[]
            for feature in dataset.get('geojson',{}).get('features',[]):
                props=feature.get('properties') or {};feature_name=props.get('name') or props.get('名称') or props.get('id') or feature.get('id')
                if feature_name and score(query,[feature_name]):matches.append(str(feature_name))
            if not rank and not matches:continue
            hit={'id':'dataset:'+dataset['id'],'kind':'dataset','label':name,'subtitle':'用户导入 · %s个要素'%dataset.get('feature_count',len(dataset.get('geojson',{}).get('features',[])))+(' · 匹配：'+'、'.join(matches[:3]) if matches else ''),'source':'用户导入；真实性与授权以提供方为准','source_url':'/api/datasets/'+dataset['id'],'source_type':'user','dataset_id':dataset['id'],**loc}
            hits.append((max(rank,40 if matches else 0)+5,0,hit))
        provider='workspace+reference_places';remote_state='not_requested'
        # Autocomplete passes remote=False. Explicit search uses the owned key;
        # exact workspace matches need no network delay or geocoder quota.
        strong_workspace=any(hit['kind'] in ('result','dataset') and rank>=100 for rank,_,hit in hits)
        if remote and self.tianditu is not None and self.tianditu.status().get('configured'):
            if strong_workspace:remote_state='workspace_exact_match'
            else:
                try:
                    external=self.tianditu.search(query,min(limit,8));remote_state='completed';provider+=' + tianditu'
                    for i,hit in enumerate(external):hits.append((110-i,0,hit))
                except GeoError as error:
                    remote_state='failed';warnings.append({'code':error.code,'message':error.message+'；已保留本地成果与地名参考检索。'})
        unique={}
        for rank,pop,hit in sorted(hits,key=lambda h:(-h[0],-h[1],h[2]['label'])):
            unique.setdefault(hit['id'],hit)
        results=list(unique.values())[:limit]
        if not self.places:warnings.append({'code':'PLACE_INDEX_UNAVAILABLE','message':'地名参考索引暂不可用，仍可搜索坐标、已有成果和用户数据。'})
        if not results:warnings.append({'code':'NO_MATCH','message':'没有匹配的地点、已完成成果或用户数据。可输入“经度,纬度”定位；未配置天地图时仅检索本地城镇参考库。'})
        return {'query':query,'results':results,'warnings':warnings,'provider':provider,'remote_status':remote_state,'place_coverage':self.place_metadata.get('coverage','中国主要城市参考点；不含全部地名/兴趣点')}
