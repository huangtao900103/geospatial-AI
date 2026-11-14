"""Typed real-tool execution jobs. Rules plan; registered services actually execute."""
import copy
import calendar
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import date,datetime,timedelta,timezone
from urllib.parse import urlsplit,parse_qs
from geo_services import GeoError,finite
from assistant_services import route as question_route
from production_services import make_plan,raster_available,SCL_RETAINED
from native_raster import VERSION as NATIVE_VERSION
from spectral_indices import detect_indices,choose_index,specification,available_indices,unsupported_index_requested
from landcover_services import PRODUCT,load_boundary,available as landcover_available
from data_catalog_services import period_value
from agriculture_services import VERSION as AGRICULTURE_VERSION
from raster_processing_services import VERSION as PROCESSING_VERSION

VERSION='1.0'
TERMINAL={'succeeded','needs_data','unsupported','failed','cancelled'}
TOOLS={
 'raster_processing':{'name':'真实多景与阈值影像处理','version':PROCESSING_VERSION,'actual_operation':'同一传感器多日期原生格网合成/OLS趋势，或明确源成果Otsu分区','limitations':['集合不是每日均值；至少2/3独立日期支持，未知不补0；指数分区不作地类认定。']},
 'agriculture_analysis':{'name':'已有栅格农业分析','version':AGRICULTURE_VERSION,'actual_operation':'对明确归档原生栅格执行端元FVC估算或用户地块分区统计','limitations':['继承源范围、日期与质量；端元需校准；部分覆盖不外推；不能作产量或灾损认定。']},
 'cropland_delineation':{'name':'选区年度耕作地参考标注','version':'cropland-products-native-patches-v2','actual_operation':'按明确年度产品类别读取原生选区、矢量化连通图斑并采用该产品的面积方法','limitations':['年度分类产品提取，不是当期影像新分类；连通图斑不是确权地块。']},
 'landcover_area':{'name':'真实全市分类面积','version':'worldcover-native-ellipsoid-v1','actual_operation':'WorldCover2021全原生格网×2017参考市界统计','limitations':['非当前官方耕地调查；固定2021分类基准。']},
 'sentinel_native':{'name':'真实多源原生像元分析','version':NATIVE_VERSION,'actual_operation':'PC S2或Landsat8/9原生窗口，按传感器适用性定标、质量检查及已注册光谱计算','limitations':['仅明确有界范围，不用代表网格或样本充当原生分析。']},
 'public_catalog_search':{'name':'真实公开影像目录查询','version':'pc-catalog-v1','actual_operation':'固定STAC集合真实HTTP目录检索','limitations':['目录候选不是像素读取或解译结果。']},
 'weather_forecast':{'name':'真实天气模型预报','version':'open-meteo-forecast-v1','actual_operation':'固定服务实际返回的当前及预报模型值','limitations':['不是田间实测或历史天气Archive。']},
 'result_reference':{'name':'读取原执行来源与产物','version':'execution-reference-v1','actual_operation':'只读取明确引用的真实成功执行，不启动新计算','limitations':['不改绑其他任务或年份；不把来源查看当重新计算。']},
 'vector_analysis':{'name':'用户GeoJSON实际算法','version':'geojson-algorithms-v1','actual_operation':'明确用户数据版本上的空间统计/最近邻/属性计算','limitations':['输入真实性与法定属性由提供方负责，不等于遥感像素解译。']},
}


def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def uid(prefix):return prefix+'_'+uuid.uuid4().hex[:14]
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def native_ready():
    try:
        p=make_plan({'requirement':'2025年10月NDVI','mode':'sentinel_cog','sampling':'native','bbox':[113.3,30.3,113.31,30.31]})
        return raster_available() and p.get('sampling')=='native'
    except (GeoError,ValueError,TypeError):return False


def capabilities():
    return {'tools':[{'id':id,**metadata,'limits':{'max_bbox_span_deg':.05} if id=='sentinel_native' else {},'available':landcover_available() if id=='landcover_area' else native_ready() if id=='sentinel_native' else True} for id,metadata in TOOLS.items()], 'indices':available_indices(raster_available()),'planner':'bounded_rules_no_general_llm','limitations':['仅调用已登记的真实服务函数，不执行任意URL、代码或自定义Shell。','缺少必要范围/时间时一次说明缺口；不会用默认样本、旧年度或目录命中替代分析。']}


def observation_period(context,question):
    if 'period' in context:return period_value(context['period'])
    dates=re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',question)
    if 1<=len(dates)<=2:return {'start':dates[0],'end':dates[-1]}
    m=re.search(r'((?:19|20|21)\d{2})年\s*(\d{1,2})月',question)
    if m and len(re.findall(r'\d{1,2}月',question))==1:
        import calendar
        y,month=map(int,m.groups())
        try:return {'start':date(y,month,1).isoformat(),'end':date(y,month,calendar.monthrange(y,month)[1]).isoformat()}
        except ValueError:raise GeoError('INVALID_PERIOD','月份无效')
    return None


def temporal_context_conflict(question,context,goal,actual):
    """Structured clarification may complete time, but cannot replace explicit text."""
    if not any(k in context for k in ('period','periods')):return None
    conflict='PERIOD_CONFLICT：补充时段与原文明示的日期、月份或年份不一致；请保留原时相或明确重提问题。'
    if 'period' in context:
        value=period_value(context['period'])
        expected=actual['after'] if goal=='ndvi' else [actual['before'][0],actual['after'][1]]
        if [value['start'],value['end']]!=expected:return conflict
    dates=re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',question)
    if dates:
        try:dates=[date.fromisoformat(d).isoformat() for d in dates]
        except ValueError:return conflict
        if len(dates)==4:
            expected={'before':dates[:2],'after':dates[2:]}
            if any(actual[k]!=expected[k] for k in (('before','after') if goal=='change' else ('after',))):return conflict
        elif len(dates)==2:
            if goal=='ndvi' and actual['after']!=dates:return conflict
            if goal=='change' and any(not actual[k][0]<=d<=actual[k][1] for k,d in zip(('before','after'),dates)):return conflict
        elif len(dates)==1:
            if goal=='ndvi' and actual['after']!=[dates[0],dates[0]]:return conflict
            if goal=='change' and not actual['after'][0]<=dates[0]<=actual['after'][1]:return conflict
        else:return conflict
        return None
    years=list(dict.fromkeys(re.findall(r'(?<!\d)((?:19|20|21)\d{2})(?!\d)',question)))
    months=re.findall(r'(?:(?:((?:19|20|21)\d{2}))年\s*)?(\d{1,2})月',question)
    if months and years:
        current=int(years[0]);windows=[]
        try:
            for y,m in months:
                if y:current=int(y)
                month=int(m);windows.append([date(current,month,1).isoformat(),date(current,month,calendar.monthrange(current,month)[1]).isoformat()])
        except ValueError:return conflict
        if len(windows)==1:
            if actual['after']!=windows[0]:return conflict
        elif len(windows)==2:
            if goal=='change' and (actual['before']!=windows[0] or actual['after']!=windows[1]):return conflict
            if goal=='ndvi' and actual['after']!=[windows[0][0],windows[1][1]]:return conflict
        else:return conflict
    elif years:
        if len(years)==1:
            if any(d[:4]!=years[0] for d in actual['after']):return conflict
        elif len(years)==2 and goal=='change':
            if any(d[:4]!=y for k,y in zip(('before','after'),years) for d in actual[k]):return conflict
        else:return conflict
    return None


class ExecutionServices:
    def __init__(self,store):
        self.store=store;self.assignment_lock=threading.RLock();self.stopping=False;self.threads=[]
        # Resume only jobs with a persisted child binding, or with no started tool call.
        for job in self.list():
            if job['status'] not in TERMINAL:
                if job.get('tool_calls') and not job.get('binding'):
                    job.update(status='failed',error={'code':'INTERRUPTED','message':'服务重启时同步工具调用中断；请显式重试，不静默重复执行'},finished_at=now())
                    for item in job.get('steps',[])+job.get('tool_calls',[]):
                        if item.get('status')=='running':item.update(status='failed',message='服务重启中断',finished_at=now())
                    self.store.save('execution',job)
                else:self.launch(job['id'])
    def get(self,id):return self.store.get('execution',id)
    def list(self):return self.store.list('execution')
    def change(self,id,fn):
        with self.store.lock:
            job=self.get(id)
            if job['status'] in TERMINAL:return False
            fn(job);job['updated_at']=now();self.store.save('execution',job);return True
    def check(self,id):
        if self.stopping:raise GeoError('INTERRUPTED','服务关闭，执行等待中断')
        if self.get(id)['status'] in TERMINAL:raise GeoError('EXECUTION_STOPPED','执行已经结束')
    def create(self,body,parent_id=None):
        if not isinstance(body,dict) or set(body)-{'question','context','request_id'}:raise GeoError('INVALID_EXECUTION','执行请求字段不支持',400)
        text=body.get('question');context=body.get('context',{});request_id=body.get('request_id')
        if not isinstance(text,str) or not text.strip() or len(text)>4000:raise GeoError('INVALID_EXECUTION','问题须为1–4000字符的文本',400)
        if not isinstance(context,dict) or set(context)-{'bbox','period','periods','dataset_id','region','reference_execution_id','source_id','observation_id','cropland_product','source_type','source_task_id','soil_ndvi','vegetation_ndvi','field','threshold','processing_algorithm','index_id','scene_limit','cloud','direction','min_patch_pixels'}:raise GeoError('INVALID_EXECUTION_CONTEXT','执行上下文只接受业务范围、时段、数据或历史执行引用',400)
        if request_id is not None and (not isinstance(request_id,str) or not request_id or len(request_id)>100):raise GeoError('INVALID_REQUEST_ID','request_id格式无效',400)
        try:request_hash=digest({'question':re.sub(r'\s+',' ',text.strip()),'context':context})
        except (ValueError,TypeError,OverflowError):raise GeoError('INVALID_EXECUTION_CONTEXT','执行上下文必须为有限JSON值',400)
        with self.store.lock:
            if self.stopping:raise GeoError('SERVICE_STOPPING','服务正在关闭',503)
            if request_id:
                old=next((j for j in self.list() if j.get('request_id')==request_id),None)
                if old:
                    if old['request_hash']!=request_hash:raise GeoError('IDEMPOTENCY_CONFLICT','同一request_id对应了不同业务输入',409)
                    return old
            if sum(j['status'] not in TERMINAL for j in self.list())>=12:raise GeoError('EXECUTION_BUSY','本机执行队列已满，请稍后重试',429)
            job={'id':uid('execution'),'question':text.strip(),'context':copy.deepcopy(context),'request_id':request_id,'request_hash':request_hash,'parent_execution_id':parent_id,'status':'queued','progress':0,'message':'已接收业务问题，等待规划真实工具','plan_ref':None,'plan_snapshot':None,'steps':[{'id':'plan','name':'理解需求并冻结执行输入','status':'pending'},{'id':'tool','name':'调用真实数据与算法工具','status':'pending'},{'id':'collect','name':'校验质量、GIS与来源归档','status':'pending'}],'tool_calls':[],'child_steps':[],'logs':[{'time':now(),'message':'执行请求已持久化；尚未运行任何工具'}],'binding':None,'result_ref':None,'result':None,'summary':None,'error':None,'created_at':now(),'updated_at':now()}
            self.store.save('execution',job)
        self.launch(job['id']);return job
    def launch(self,id):
        thread=threading.Thread(target=self.process,args=(id,),daemon=True);self.threads.append(thread);thread.start()
    def prepare_processing(self,question,context,intent):
        from raster_processing_services import TEMPORAL
        tool='raster_processing';missing=[];gaps=[];snapshot=None;algorithm=context.get('processing_algorithm')
        if any(word in question for word in ('全市','全省','全县','全国','省域','产量','灾损','法定','确权','作物分类','水体面积','水域面积','耕地面积','农田面积','水稻','小麦面积')):
            return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['本算子只处理明确选区的光谱集合或指数分区，不能替代全域、作物/水体面积、产量、灾损或法定认定。'],'executable':False}
        if intent=='temporal_analysis':
            requested=[]
            if '中位数' in question:requested.append('temporal_median')
            if '均值合成' in question or '平均合成' in question:requested.append('temporal_mean')
            if any(word in question for word in ('时序趋势','线性趋势')):requested.append('temporal_trend')
            if len(set(requested))>1:gaps.append('一次请选择一种时序方法，不能把多个方法静默合为一个结果。')
            if algorithm and algorithm not in TEMPORAL:gaps.append('所选方法不是时序集合算法。')
            elif algorithm and requested and algorithm!=requested[0]:gaps.append('问题中的方法与专题选择不一致。')
            elif not algorithm:algorithm=requested[0] if requested else None
            if not algorithm:missing.append('processing_algorithm')
            if 'bbox' not in context:missing.append('bbox')
            index=None
            try:index=choose_index(question,context.get('index_id'))['id']
            except GeoError as error:gaps.append(error.message)
            window=None
            try:
                if 'period' in context:window=period_value(context['period'])
                else:
                    from production_services import parse_periods
                    periods,defaulted=parse_periods(question,None,'ndvi')
                    if not defaulted:window={'start':periods['after'][0],'end':periods['after'][1]}
                if window:
                    conflict=temporal_context_conflict(question,{'period':window},'ndvi',{'after':[window['start'],window['end']]})
                    if conflict:gaps.append(conflict)
                else:missing.append('period')
            except GeoError as error:gaps.append(error.message)
            if any(key in context for key in ('source_task_id','source_type','observation_id','dataset_id','periods','threshold','direction','min_patch_pixels')):gaps.append('时段影像分析需要新选区和单一日期窗口，不能忽略其他专题或锁定单景的条件。')
            inputs={'algorithm_id':algorithm,'source_id':context.get('source_id','auto'),'index_id':index,'bbox':context.get('bbox'),'start':window['start'] if window else None,'end':window['end'] if window else None,'scene_limit':context.get('scene_limit',4),'cloud':context.get('cloud',40)}
            cloud_text=re.search(r'云量\s*(?:上限|不高于|低于|小于|≤|<=)?\s*(\d+(?:\.\d+)?)',question)
            if cloud_text:
                value=float(cloud_text.group(1))
                if 'cloud' in context and context['cloud']!=value:gaps.append('问题云量上限与专题参数不一致。')
                inputs['cloud']=value
            scene_text=re.search(r'(?:最多|至多|选取)\s*(\d+)\s*(?:景|个观测日期)',question)
            if scene_text:
                value=int(scene_text.group(1))
                if 'scene_limit' in context and context['scene_limit']!=value:gaps.append('问题中的最多观测次数与专题参数不一致。')
                inputs['scene_limit']=value

        else:
            if algorithm not in (None,'otsu_segmentation'):gaps.append('所选方法与自动阈值分区不一致。')
            inputs={'algorithm_id':'otsu_segmentation','source_task_id':context.get('source_task_id'),'direction':context.get('direction','above'),'min_patch_pixels':context.get('min_patch_pixels',1)}
            if not inputs['source_task_id']:missing.append('source_task_id')
            if context.get('source_type','production')!='production':gaps.append('Otsu仅接受真实成功的单期原生光谱成果。')
            if any(key in context for key in ('period','periods','dataset_id','source_id','observation_id','threshold','scene_limit','cloud')):gaps.append('自动阈值分区沿用源范围与观测，不能忽略新日期、别的图层或手动阈值。')
            if re.search(r'(?:阈值|大于|小于|高于|低于|>=|<=|>|<|≥|≤)\s*(?:为|是|=|：|:)?\s*-?\d',question):gaps.append('Otsu由实际分布自动确定阈值，不使用问题中手工指定的阈值。')
            if any(word in question for word in ('低于自动阈值','保留低值')):
                if context.get('direction') not in (None,'below'):gaps.append('问题中的低值方向与专题选择不一致。')
                inputs['direction']='below'
            if any(word in question for word in ('不低于自动阈值','保留高值')):
                if context.get('direction') not in (None,'above'):gaps.append('问题中的高值方向与专题选择不一致。')
                inputs['direction']='above'
        if not missing and not gaps:
            try:
                snapshot=self.store.raster_processing.prepare(inputs)
                if intent=='adaptive_segmentation':
                    source=snapshot['source'];indices=detect_indices(question)
                    if indices and (len(indices)!=1 or indices[0]!=source['index_id']):gaps.append('指定指数与所选源成果不一致，不能换指数代答。')
                    if 'bbox' in context and context['bbox']!=source['bbox']:gaps.append('所选地图范围不同于归档源成果；此运算不会用旧源代表新范围。')
                    years=set(re.findall(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)',question));actual={str(value)[:4] for value in source['actual_dates'].values()}
                    if years and not years.issubset(actual):gaps.append('问题年度与源实际日期不一致。')
                    days=[str(value)[:10] for value in source['actual_dates'].values()];months=re.findall(r'((?:19|20|21)\d{2})年\s*(\d{1,2})月',question)
                    if months and (not days or any(not any(day.startswith(year+'-'+month.zfill(2)) for year,month in months) for day in days)):gaps.append('问题月份与源实际观测日期不一致。')
                    explicit=re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',question)
                    try:
                        for value in explicit:date.fromisoformat(value)
                        if len(explicit)==2 and explicit[0]>explicit[1]:raise ValueError()
                    except ValueError:gaps.append('问题中的日期无效或顺序错误。')
                    if explicit and (not days or len(explicit) not in (1,2) or any(not (day==explicit[0] if len(explicit)==1 else explicit[0]<=day<=explicit[1]) for day in days)):gaps.append('问题指定日期与归档源实际观测不一致。')

                    if any(word in question for word in ('今年','去年','最新','实时','当期')):gaps.append('Otsu沿用归档源，不能把历史结果当成当前影像。')
            except GeoError as error:gaps.append(error.message)
            except Exception as error:
                if getattr(error,'status',None)!=404:raise
                gaps.append('所选源成果不存在，请选择可用的真实单期指数结果。')
        labels={'processing_algorithm':'选择中位数、均值合成或时序趋势','bbox':'在地图明确选择小范围','period':'明确观测开始和结束日期','source_task_id':'选择已有成功单期原生指数成果'}
        message='；'.join([labels[key] for key in missing]+gaps)
        return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':intent,'status':'ready' if not missing and not gaps else 'needs_data','missing_fields':missing,'blocking_gaps':gaps,'clarification_message':message,'executable':not missing and not gaps,'force':any(word in question for word in ('重跑','重算','重新计算','重新分析')),'tool':{'id':tool,'version':TOOLS[tool]['version'],'inputs':inputs},'plan_ref':None,'source_snapshot':snapshot,'planner':'bounded_rules_no_general_llm'}

    def prepare_agriculture(self,question,context,intent):
        algorithm='fvc' if intent=='vegetation_cover' else 'zonal_statistics'
        tool='agriculture_analysis';missing=[];gaps=[];snapshot=None
        if any(w in question for w in ('全省','全市','全县','全国','省域','产量','病虫害','施肥量','叶面积指数')) or re.search(r'(?<![a-z])lai(?![a-z])',question.lower()):
            return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['当前农业派生算子只处理明确源成果范围，不能替代全域分析或未接入的产量、病虫害、LAI模型。'],'executable':False}
        inputs={'algorithm_id':algorithm}
        required=['source_type','source_task_id']+(['soil_ndvi','vegetation_ndvi'] if algorithm=='fvc' else ['dataset_id'])
        for key in required:
            if context.get(key) in (None,''):missing.append(key)
            else:inputs[key]=copy.deepcopy(context[key])
        if algorithm=='zonal_statistics':
            if 'field' in context:inputs['field']=context['field']
            if 'threshold' in context:inputs['threshold']=context['threshold']
            comparisons=re.findall(r'(不低于|至少|>=|≥|大于|超过|高于|>|小于|低于|不高于|<=|≤|<)\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(%|％)?',question)
            if len(comparisons)>1:gaps.append('当前地块阈值算子一次只支持一个≥条件；区间或复合条件不能丢弃后半段执行。')
            if comparisons:
                operator,number,percent=comparisons[0];value=float(number)
                if percent:gaps.append('指数阈值使用绝对指数值；此算子未实现百分比条件，不能静默换算。')
                if operator not in ('不低于','至少','>=','≥'):gaps.append('分区阈值统计当前实现≥关系；本问题要求其他关系，不能静默换成≥。')
                elif 'threshold' in inputs and inputs['threshold']!=value:gaps.append('问题中的阈值与表单参数不一致，请统一后计算。')
                else:inputs['threshold']=value
            if any(w in question for w in ('差值','变化值','后减前')):
                if inputs.get('field') not in (None,'delta_index'):gaps.append('问题要求两期差值，但所选字段不是差值。')
                inputs['field']='delta_index'
            if any(k in context for k in ('soil_ndvi','vegetation_ndvi')):gaps.append('地块分区统计不接植被覆盖度端元，请移除其他专题参数。')
        elif any(k in context for k in ('dataset_id','field','threshold')) or re.search(r'不低于|不高于|大于|小于|高于|低于|>=|<=|>|<|≥|≤',question):gaps.append('此FVC运算覆盖所选源成果，不能忽略另选地块、字段或阈值；地块统计请使用对应专题。')
        if any(k in context for k in ('period','periods','observation_id','source_id','cropland_product')):gaps.append('农业派生分析沿用明确源成果的实际日期与数据，不能继承其他观测窗口或来源选择。')
        if any(w in question for w in ('今年','去年','最新','实时','当期','现状')):gaps.append('此能力沿用已有成果，不能把历史源解释为今年、最新或实时情况；请先按目标日期生成原生结果。')
        if any(w in question for w in ('月平均','月均','全年','年平均','年均','时序平均')):gaps.append('此能力按所选归档观测统计，未实现跨景月均、年均或时序合成，不能用单景代替。')
        if context.get('region') or re.search(r'[\u4e00-\u9fff]{2,10}(?:市|县|省)',question):gaps.append('请确认源成果的实际空间范围；农业派生运算不能用所选小窗替代问题中另一个行政区域。')
        if not missing and not gaps:
            try:
                snapshot=self.store.agriculture.prepare(inputs)
                if 'bbox' in context and context['bbox']!=snapshot['source']['bbox']:gaps.append('地图选区与源成果范围不一致；此运算沿用源成果范围，请明确选择相同范围的成果。')
                requested_years=set(re.findall(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)',question));actual_years={str(value)[:4] for value in snapshot['source']['actual_dates'].values()}
                if snapshot['source'].get('reference_year'):actual_years.add(str(snapshot['source']['reference_year']))
                if requested_years and not requested_years.issubset(actual_years):gaps.append('问题中的年度与源成果的实际观测/分类年度不一致，不会用其他年份代答。')
                indices=detect_indices(question)
                if indices and (len(indices)!=1 or snapshot['source']['index_id']!=indices[0]):gaps.append('问题指定的指数与所选源成果不一致；不会用另一指数或分类成果代答。')
                if algorithm=='zonal_statistics' and snapshot['source']['type']=='production' and any(w in question for w in ('农田面积','耕地面积','耕作地面积','农田多少','耕地多少')):gaps.append('光谱指数不能替代耕作地类别面积；请选择已归档年度分类成果。')
                actual_dates=[str(v)[:10] for v in snapshot['source']['actual_dates'].values()]
                if inputs.get('field')!='delta_index' and snapshot['source']['actual_dates'].get('after'):actual_dates=[snapshot['source']['actual_dates']['after'][:10]]
                months=re.findall(r'((?:19|20|21)\d{2})年\s*(\d{1,2})月',question)
                if months and any(not any(day.startswith(y+'-'+m.zfill(2)) for y,m in months) for day in actual_dates):gaps.append('问题中的观测月份与源成果实际日期不一致，请先获取相应月份的影像结果。')
                days=re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',question)
                if (months or days) and not actual_dates:gaps.append('年度分类源没有对应月日观测，不能将年度分类解释为该月份或当天结果。')
                try:
                    for day in days:date.fromisoformat(day)
                    if len(days)==2 and days[0]>days[1]:raise ValueError()
                except ValueError:gaps.append('问题中的日期无效或开始晚于结束，请使用有效观测日期。')
                if days and actual_dates and (len(days) not in (1,2) or any(not (day==days[0] if len(days)==1 else days[0]<=day<=days[1]) for day in actual_dates)):gaps.append('问题中的明确日期与归档源观测日期不一致，不会重新获取其他日期。')
            except GeoError as error:gaps.append(error.message)
            except Exception as error:
                if getattr(error,'status',None)!=404:raise
                gaps.append('所选源成果或用户地块图层不存在，请明确选择可用的归档输入。')
        labels={'source_type':'明确选择已有原生栅格来源','source_task_id':'明确选择成功栅格成果','soil_ndvi':'填写裸土NDVI端元','vegetation_ndvi':'填写高覆盖植被NDVI端元','dataset_id':'选择已导入的Polygon/MultiPolygon地块图层'}
        clarification='；'.join(list(dict.fromkeys(labels[k] for k in missing))+gaps)
        return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':intent,'status':'ready' if not missing and not gaps else 'needs_data','missing_fields':missing,'blocking_gaps':gaps,'clarification_message':clarification,'executable':not missing and not gaps,'force':any(w in question for w in ('重算','重跑','重新计算','重新统计')),'tool':{'id':tool,'version':TOOLS[tool]['version'],'inputs':inputs},'plan_ref':None,'source_snapshot':snapshot,'planner':'bounded_rules_no_general_llm'}

    def prepare(self,question,context):
        intent,state,message=question_route(question)
        if intent in ('source_quality','download_results') and context.get('reference_execution_id') and not any(w in question for w in ('重新计算','重新估算','重新分析','重新统计','重跑','重算')):
            try:reference=self.get(context['reference_execution_id'])
            except Exception as error:
                if getattr(error,'status',None)!=404 and not isinstance(error,KeyError):raise
                reference=None
            if not reference or reference['status']!='succeeded' or not reference.get('result_ref') or not reference.get('result'):
                return {'intent':intent,'status':'needs_data','missing_fields':['reference_execution_id'],'blocking_gaps':['引用的原执行不存在或尚未成功，不能改绑其他成果。'],'executable':False}
            return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':intent,'status':'ready','missing_fields':[],'blocking_gaps':[],'executable':True,'force':False,'tool':{'id':'result_reference','version':TOOLS['result_reference']['version'],'inputs':{'execution_id':reference['id'],'operation':intent}},'plan_ref':reference.get('plan_ref'),'source_snapshot':{'reference_execution_id':reference['id'],'binding':reference['binding']},'planner':'bounded_rules_no_general_llm'}
        if context.get('dataset_id')=='xt-fixture':return {'intent':'user_vector_analysis','status':'unsupported','missing_fields':[],'blocking_gaps':['教学样例已移除，请上传实际业务数据后分析。'],'executable':False}
        if intent in ('temporal_analysis','adaptive_segmentation'):return self.prepare_processing(question,context,intent)
        if intent in ('vegetation_cover','field_zonal_statistics'):return self.prepare_agriculture(question,context,intent)
        explicit_indices=detect_indices(question)
        # A selected-area question must not silently fall through to the fixed
        # Xiantao city baseline merely because it lacks the word "delineate".
        crop_words=any(word in question for word in ('农田','耕地','耕作地'))
        reference_words=any(word in question for word in ('来源','质量','精度','准确','下载','导出','数据年份','哪年','依据','可信','误差'))
        selected_words=any(word in question for word in ('选区','选中','所选','选定','框选','这块','该地块','这个区域','该区域','这片','这一片','当前范围','当前区域','当前地图','这里'))
        full_scope=any(word in question for word in ('全市','全县','全省','全国','省域','整市','全湖北'))
        eligible=intent in ('cropland_area','cropland_distribution') or (intent is None and state in ('needs_data','unsupported'))
        if ('bbox' in context or selected_words or 'cropland_product' in context) and crop_words and eligible and not explicit_indices and not reference_words and not full_scope:
            neutral=question
            terms=('农田','耕地','耕作地','总面积','面积','多少','几亩','几公顷','公顷','分布','情况','在哪里','哪里','主要','范围','请问','帮我','我想知道','想知道','我要','统计','核算','计算','查询','查看','显示','分析','一下','目前','当前','现在','今年','去年','最新','的','有','是','亩','如何')
            for term in sorted(terms,key=len,reverse=True):neutral=neutral.replace(term,'')
            neutral=re.sub(r'2021年?|2024年?|[\s？?！!。，,；;：:]','',neutral)
            strict_crop=question_route('提取'+question)
            if selected_words or not neutral:
                intent,state,message=strict_crop
            elif intent in ('cropland_area','cropland_distribution') or state=='needs_data' or (strict_crop[0]=='cropland_delineation' and re.search(r'[\u4e00-\u9fff]{1,8}(?:市|县)',question)):
                scope_message='请补充“按地图选区分析”，或明确具体市县的“全市/全县”统计口径。当前地图选区与问题中的行政地区尚未确认一致。'
                return {'intent':intent,'status':'needs_data','missing_fields':['scope_confirmation'],'blocking_gaps':[scope_message],'clarification_message':scope_message,'executable':False}
        if len(explicit_indices)>1:return {'intent':'spectral_analysis','status':'needs_data','missing_fields':[],'blocking_gaps':['当前单次执行处理一个指数，请拆为分别提交的指数问题。'],'executable':False}
        spectral=bool(explicit_indices)
        if spectral and any(w in question for w in ('农田','耕地','耕作地')) and any(w in question for w in ('标注','圈出','勾画','提取','图斑','描绘')):
            return {'intent':'cropland_delineation','status':'unsupported','missing_fields':[],'blocking_gaps':['光谱指数及阈值不等于农田分类，不能用NDVI等指数替代农田图斑标注。'],'executable':False}
        if spectral and any(w in question for w in ('高标准农田','确权','法定','官方耕地','耕地保有','黄鳝','禁捕','播种面积','尾水污染','建筑面积','建筑物数量')):
            return {'intent':'spectral_analysis','status':'unsupported','missing_fields':[],'blocking_gaps':['该业务口径没有真实执行算子，不能以光谱阈值面积代替高标准农田、权属、法定调查或产业监管结论。'],'executable':False}
        if spectral:
            intent='vegetation_change';state=None
        unsupported_index=unsupported_index_requested(question)
        if unsupported_index:return {'intent':'spectral_analysis','status':'unsupported','missing_fields':[],'blocking_gaps':['问题指定的指数尚未接入，不能改用已接入指数代答。'],'executable':False}
        if intent is None and state=='needs_data' and (spectral or any(w in question.lower() for w in ('植被','长势','光谱'))):intent='vegetation_change'
        force=any(w in question for w in ('重新核算','重新计算','重新统计','重算','重跑','再算一次'))
        missing=[];gaps=[];tool=None;inputs=None;plan_ref=None;source_snapshot=None
        vector_words=any(w in question for w in ('导入','这个图层','所选数据','所选要素','这些点','这些要素','用户数据'))
        weather=any(w in question for w in ('天气','气温','降雨','降水'))
        discover=any(w in question.lower() for w in ('检索','搜索影像','找影像','找一下影像','有哪些影像','卫星目录','雷达目录','stac'))
        pure_weather=weather and not any(w in question for w in ('耕地','农田','面积','灾损','损失','分类','产量','淹水'))
        pure_discovery=discover and not any(w in question for w in ('统计','核算','计算','面积','解译','分类','灾损','产量','损失'))
        if any(w in question for w in ('灾损','受灾损失','作物分类')):return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['光谱指数不能代替未接入的作物分类、灾损或法定业务结论。'],'executable':False}
        if any(w in question for w in ('水体变化','水域变化','淹水')) and not spectral:return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['当前没有该分类/灾损/水体动态真实算子，不用目录查询或单年面积代替。'],'executable':False}
        if state=='unsupported' and not (pure_weather or pure_discovery):return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':[message],'clarification_message':message,'executable':False}
        new_compute_request=bool(re.match(r'^(?:请|帮我|替我|能否|麻烦)?\s*(?:重新|重算|计算|分析|对比|比较|核算|统计|检索|搜索)',question))
        explicit_time=bool(re.search(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)|\d{1,2}月',question))
        if explicit_time and (spectral or any(w in question.lower() for w in ('植被','长势','光谱'))) and any(w in question for w in ('分析','计算','对比','比较','变化','选区','所选','选中','范围')):new_compute_request=True
        reference_followup=context.get('reference_execution_id') and any(w in question for w in ('来源','质量','精度','准吗','准不准','有效像元','下载','导出','用的什么影像')) and not force and not new_compute_request
        if reference_followup:intent='download_results' if any(w in question for w in ('下载','导出')) else 'source_quality'
        if context.get('reference_execution_id') and not force and not new_compute_request and (reference_followup or intent in ('source_quality','download_results')):
            try:reference=self.get(context['reference_execution_id'])
            except Exception:reference=None
            if not reference or reference['status']!='succeeded' or not reference.get('result_ref') or not reference.get('result'):
                return {'intent':intent,'status':'needs_data','missing_fields':['reference_execution_id'],'blocking_gaps':['引用的原执行不存在或尚未成功，不能改绑其他任务代答。'],'clarification_message':'请选择那次已经完成的执行结果再追问。','executable':False}
            return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':intent,'status':'ready','missing_fields':[],'blocking_gaps':[],'executable':True,'force':False,'tool':{'id':'result_reference','version':TOOLS['result_reference']['version'],'inputs':{'execution_id':reference['id'],'operation':intent}},'plan_ref':reference.get('plan_ref'),'source_snapshot':{'reference_execution_id':reference['id'],'binding':reference['binding']},'planner':'bounded_rules_no_general_llm'}
        if weather and not pure_weather:return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['不能以天气查询代替未实现的业务分析。'],'executable':False}
        if discover and not pure_discovery and intent!='vegetation_change':return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['目录检索不能代替所请求的统计、解译或业务分析。'],'executable':False}
        if intent=='cropland_delineation':
            tool='cropland_delineation'
            if any(k in context for k in ('period','periods','observation_id','source_id','dataset_id')):
                return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['此能力固定提取2021年WorldCover分类产品，不能继承光谱影像、其他日期或用户图层作为分类输入。'],'executable':False}
            from cropland_services import product_profile
            years=re.findall(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)',question)
            selected=context.get('cropland_product','io_lulc_2024' if years and set(years)=={'2024'} else PRODUCT['id'])
            try:profile=product_profile(selected)
            except (GeoError,KeyError,TypeError):
                return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['未接通所选年度分类产品，不能替换为另一年份。'],'executable':False}
            if any(year!=str(profile['year']) for year in years):
                return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['问题明示年度与所选分类产品不一致；请统一年度和产品，不会静默回退。'],'executable':False}
            inputs={'product':profile['id'],'requirement':question}
            if 'bbox' not in context:missing.append('bbox')
            else:
                from cropland_services import make_plan as cropland_plan
                inputs['bbox']=context['bbox']
                try:source_snapshot=cropland_plan(inputs)
                except GeoError as e:
                    scope_error=e.code in ('AOI_REQUIRED','INVALID_BBOX','AOI_TOO_LARGE','AOI_GRID_LIMIT')
                    return {'intent':intent,'status':'needs_data' if scope_error else 'unsupported','missing_fields':['bbox'] if scope_error else [],'blocking_gaps':[e.message],'clarification_message':e.message,'executable':False}
        elif context.get('dataset_id') and vector_words:
            if any(w in question for w in ('全省','全市耕地','官方','法定')):return {'intent':'vector','status':'unsupported','missing_fields':[],'blocking_gaps':['用户图层几何不能冒充全市耕地分类或法定认定。'],'executable':False}
            dataset=self.store.get_dataset(context['dataset_id'])
            if dataset.get('source_type')!='user':return {'intent':'vector','status':'unsupported','missing_fields':[],'blocking_gaps':['客户真实执行只接受明确用户导入数据；合成fixture只能用于标注教学，不能充当用户业务数据。'],'executable':False}
            algorithm='nearest' if any(w in question for w in ('最近','距离','邻近')) else 'ndvi' if 'ndvi' in question.lower() and '属性' in question else 'spatial_summary'
            tool='vector_analysis';inputs={'dataset_id':dataset['id'],'algorithm_id':algorithm,'parameters':{}};source_snapshot={'dataset_id':dataset['id'],'sha256':dataset['sha256'],'source_type':dataset['source_type']};intent='user_vector_analysis'
        elif pure_weather:
            if re.search(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)',question) or any(w in question for w in ('去年','上年','历史','上个月')):return {'intent':'weather','status':'unsupported','missing_fields':[],'blocking_gaps':['当前只接实时模型预报，没有历史天气Archive执行器。'],'executable':False}
            query={'question':re.sub(r'今天|今日|当前|现在|目前|实时','',question),'goal':'weather'}
            if 'bbox' in context:query['bbox']=context['bbox']
            if 'region' in context:query['region']=context['region']
            plan=self.store.data_catalog.create_plan(query);plan_ref={'id':plan['id'],'url':'/api/data-plans/'+plan['id']};source_snapshot=plan
            if plan['executable']:
                tool='weather_forecast';inputs=parse_qs(urlsplit(plan['execution_contract']['service']).query)
            else:missing=['location'] if any(g['code']=='LOCATION_REQUIRED' for g in plan['gaps']) else [];gaps=[g['message'] for g in plan['gaps'] if g['severity']=='blocking']
            intent='weather'
        elif intent=='vegetation_change':
            tool='sentinel_native';intent='public_native_raster';body={'requirement':question,'mode':'sentinel_cog','sampling':'native','index_id':choose_index(question)['id']}
            if 'bbox' not in context:missing.append('bbox')
            else:body['bbox']=context['bbox']
            goal='change' if any(w in question for w in ('对比','比较','两期','变化','下降','上升','减少','增加')) else 'ndvi'
            body['goal']=goal
            if any(w in question for w in ('全市','全省','省域','全国')) and not any(w in question for w in ('样区','小窗','小范围','选区','选中','所选')):
                return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['当前原生执行器仅支持有界区域，不能以小窗替代您请求的整市/省域分析。'],'executable':False}
            if 'periods' in context:body['periods']=context['periods']
            elif goal=='ndvi':
                period=observation_period(context,question)
                if period:
                    try:first=date.fromisoformat(period['start']);previous=(first-timedelta(days=1)).isoformat()
                    except (ValueError,TypeError,KeyError):raise GeoError('INVALID_PERIOD','观测时段格式无效')
                    body['periods']={'before':[previous,previous],'after':[period['start'],period['end']]}
                else:missing.append('period')
            else:
                dates=re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',question)
                months=re.findall(r'\d{1,2}月',question)
                years=re.findall(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)',question)
                if (len(dates)<2 and len(months)<2) or (len(months)>=2 and not years and len(dates)<2):missing.append('periods')
            for field in ('source_id','observation_id'):
                if field in context:body[field]=context[field]
            if not missing:
                if body.get('observation_id'):body=self.store.production.resolve_input(body)
                try:native=make_plan(body)
                except GeoError as e:
                    if e.code=='INVALID_PRODUCTION_INPUT':return {'intent':intent,'status':'needs_data','missing_fields':[],'blocking_gaps':['原生像元执行器尚未就绪，不能退回代表采样网格。'],'clarification_message':'当前服务尚未载入原生像元执行器，请稍后重试。','executable':False}
                    raise
                if native['blocking_gaps']:gaps.extend(native['blocking_gaps'])
                conflict=temporal_context_conflict(question,context,goal,native['periods'])
                if conflict:gaps.append(conflict)
                if any('暂用' in w for w in native.get('warnings',[])):missing.append('periods')
                source_snapshot=native;inputs=body
        elif pure_discovery:
            query={'question':question,'goal':'general_discovery'}
            if 'bbox' in context:query['bbox']=context['bbox']
            elif '仙桃' in question:
                _,meta=load_boundary(self.store.landcover.source_dir);query['bbox']=meta['bbox']
            else:missing.append('bbox')
            period=observation_period(context,question)
            if period:query['period']=period
            else:missing.append('period')
            text=question.lower()
            if any(w in text for w in ('sentinel-1','sentinel1','s1','雷达')):query['source_ids']=['pc_sentinel1_rtc']
            elif 'landsat' in text:query['source_ids']=['pc_landsat_c2_l2']
            elif any(w in text for w in ('sentinel-2','sentinel2','s2')):query['source_ids']=['pc_sentinel2_l2a']
            if not missing:
                plan=self.store.data_catalog.create_plan(query);plan_ref={'id':plan['id'],'url':'/api/data-plans/'+plan['id']};source_snapshot=plan
                if plan['executable']:tool='public_catalog_search';inputs=plan['execution_contract']['body']
                else:gaps=[g['message'] for g in plan['gaps'] if g['severity']=='blocking']
            intent='catalog_discovery'
        elif intent in ('cropland_area','cropland_distribution','landcover_composition','water_area','tree_cover_area','source_quality','download_results','recompute_area'):
            if context.get('cropland_product',PRODUCT['id'])!=PRODUCT['id']:
                return {'intent':intent,'status':'unsupported','missing_fields':[],'blocking_gaps':['固定全市基准仅有WorldCover2021；所选其他产品不能被静默改为2021全市结果。'],'executable':False}
            plan=self.store.data_catalog.create_plan({'question':question,'goal':'cropland_area'});plan_ref={'id':plan['id'],'url':'/api/data-plans/'+plan['id']};source_snapshot=plan
            if plan['executable']:tool='landcover_area';inputs=plan['execution_contract']['body']
            else:gaps=[g['message'] for g in plan['gaps'] if g['severity']=='blocking']
        else:gaps=[message or '当前没有可执行的固定业务能力，不能以演示结果代替。']
        if missing:
            labels={'bbox':'在地图上明确要分析的小范围','period':'说明目标观测日期或月份','periods':'说明含年份的前后两个观测时段','location':'在地图上明确天气查询地点'}
            clarification='请一次补充：'+'；'.join(labels.get(k,k) for k in dict.fromkeys(missing))+'。原始问题会保留，无需选择波段或算法。'
        else:clarification='；'.join(gaps)
        return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':intent,'status':'ready' if tool and inputs is not None and not missing and not gaps else 'needs_data','missing_fields':list(dict.fromkeys(missing)),'blocking_gaps':gaps,'clarification_message':clarification,'executable':bool(tool and inputs is not None and not missing and not gaps),'tool':{'id':tool,'version':TOOLS[tool]['version'],'inputs':inputs} if tool else None,'force':force,'plan_ref':plan_ref,'source_snapshot':source_snapshot,'planner':'bounded_rules_no_general_llm'}
    def input_key(self,tool,inputs):
        if tool=='raster_processing':return self.store.raster_processing.prepare(inputs)['input_fingerprint']
        if tool=='agriculture_analysis':return self.store.agriculture.prepare(inputs)['input_fingerprint']
        if tool=='cropland_delineation':
            from cropland_services import product_profile
            profile=product_profile(inputs.get('product',PRODUCT['id']))
            return digest({'tool':tool,'version':TOOLS[tool]['version'],'product':profile['id'],'source_id':profile['source_id'],'year':profile['year'],'bbox':inputs['bbox'],'class_code':profile['class_code'],'area_method':profile['area_method'],'mask_rule':'pixel_center','connectivity':4})
        if tool=='landcover_area':
            _,meta=load_boundary(self.store.landcover.source_dir);return digest({'tool':tool,'version':TOOLS[tool]['version'],'product':PRODUCT['id'],'boundary':meta['sha256']})
        if tool=='sentinel_native':
            p=make_plan(inputs);return digest({'tool':tool,'version':TOOLS[tool]['version'],'sampling':'native','mode':'sentinel_cog','source_id':p.get('source_id','pc_sentinel2_l2a'),'scene_id':p.get('scene_id'),'observation_ref':p.get('observation_ref'),'bbox':p['aoi']['bbox'],'goal':p['goal'],'index_id':p.get('index_id','ndvi'),'index_version':p.get('index',specification())['version'],'reference_band':p.get('index',specification())['reference_band'],'periods':p['periods'],'direction':p.get('direction'),'threshold':p['threshold'],'selection':{k:p.get('selection',{}).get(k) for k in ('operator','field')},'cloud':p['cloud'],'algorithm_version':NATIVE_VERSION,'quality_policy':p.get('index',{}).get('quality_policy',SCL_RETAINED),'grid_policy':'aligned_native_reference_grid','target_resolution_m':p.get('target_resolution_m')})
        return digest({'tool':tool,'version':TOOLS[tool]['version'],'inputs':inputs})
    def compatible_child(self,tool,task,inputs,key):
        try:
            if task['status'] not in ('queued','running','succeeded'):return False
            if tool=='raster_processing':
                if task.get('plan',{}).get('input_fingerprint')!=key:return False
                if task['status']=='succeeded':
                    from raster_processing_services import files,TEMPORAL
                    provenance=task['result']['provenance'];plan=task['plan']
                    if plan.get('algorithm_id')!=inputs['algorithm_id']:return False
                    if inputs['algorithm_id'] in TEMPORAL:
                        collection=plan.get('collection')
                        if not isinstance(collection,dict) or not collection.get('selection_sha256') or collection.get('selection_sha256')!=(provenance.get('scene_manifest') or {}).get('selection_sha256'):return False
                    elif plan.get('source')!=provenance.get('source_ref'):return False
                    if provenance.get('processing_service_version')!=PROCESSING_VERSION:return False
                    for name in files(inputs['algorithm_id']):self.store.raster_processing.artifact(task['id'],name)
                return True
            if tool=='agriculture_analysis':
                if task.get('plan',{}).get('input_fingerprint')!=key:return False
                if task['status']=='succeeded':
                    from agriculture_services import VERSION as AGRICULTURE_VERSION,FILES
                    if task['result']['provenance'].get('algorithm_version')!=AGRICULTURE_VERSION or task['result']['provenance'].get('input_fingerprint')!=key:return False
                    for name in FILES[inputs['algorithm_id']]:self.store.agriculture.artifact(task['id'],name)
                return True
            if tool=='cropland_delineation':
                from cropland_services import product_profile
                profile=product_profile(inputs.get('product',PRODUCT['id']))
                p=task['plan']
                if p.get('bbox')!=inputs['bbox'] or p.get('product',{}).get('id')!=profile['id'] or p.get('class_code',profile['class_code'])!=profile['class_code']:return False
                if task['status']=='succeeded':
                    r=task['result'];s=r['summary'];provenance=r['provenance']
                    if s['reference_year']!=profile['year'] or provenance.get('statistics_resampled') is not False or provenance.get('product',{}).get('id')!=profile['id']:return False
                    if 'area_method' in provenance and provenance['area_method']!=profile['area_method']:return False
                    if profile['id']!='worldcover_2021_v200' and provenance.get('area_method')!=profile['area_method']:return False
                    for metadata in (s,provenance):
                        if 'area_method' in metadata and metadata['area_method']!=profile['area_method']:return False
                        if 'class_code' in metadata and metadata['class_code']!=profile['class_code']:return False
                        if 'source_id' in metadata and metadata['source_id']!=profile['source_id']:return False
                    if len(r['geojson']['features'])!=s['patch_count']:return False
                    for name in ('cropland.geojson','classification.tif','patches.csv','result.json','overlay.png'):self.store.cropland.artifact(task['id'],name)
                return True
            if tool=='landcover_area':
                if task['status']!='succeeded':return task['plan'].get('reference_year')==2021
                r=task['result'];_,meta=load_boundary(self.store.landcover.source_dir)
                if r['provenance']['product']['id']!=PRODUCT['id'] or r['provenance']['year']!=2021 or r['provenance']['boundary_sha256']!=meta['sha256'] or r['provenance']['statistics_resampled'] is not False:return False
                for name in ('overlay.png','classification.tif','result.json','row_counts.csv'):self.store.landcover.artifact(task['id'],name)
                return True
            if task['plan'].get('sampling')!='native' or task['plan'].get('mode')!='sentinel_cog':return False
            p=task['plan'];old={'requirement':p['requirement'],'mode':'sentinel_cog','sampling':'native','source_id':p.get('source_id','pc_sentinel2_l2a'),'scene_id':p.get('scene_id'),'observation_ref':p.get('observation_ref'),'bbox':p['aoi']['bbox'],'goal':p['goal'],'index_id':p.get('index_id','ndvi'),'periods':p['periods'],'direction':p.get('direction','absolute'),'threshold':p['threshold'],'cloud':p['cloud']}
            if self.input_key(tool,old)!=key:return False
            if task['status']=='succeeded':
                expected_index=p.get('index_id','ndvi');actual_algorithm=task['result']['provenance'].get('algorithm')
                if actual_algorithm and (actual_algorithm.get('id')!=expected_index or actual_algorithm.get('version')!=specification(expected_index)['version']):return False
                if expected_index!='ndvi' and (not actual_algorithm or not task['result']['provenance'].get('raster_evidence')):return False
                version=task['result']['provenance'].get('algorithm_version');version_ok=version==NATIVE_VERSION or (version=='3.1-native' and expected_index in ('ndvi','ndwi','mndwi','ndmi','ndbi','nbr') and specification(expected_index)['version']=='1.0')
                if task['result']['provenance']['source_type']!='public_satellite_pixels' or task['result']['summary'].get('sampling')!='native' or task['result']['provenance'].get('sampling')!='native' or not version_ok:return False
                for name in ('overlay.png','analysis.tif','result.json'):self.store.production.artifact(task['id'],name)
            return True
        except (KeyError,TypeError,ValueError,GeoError,OSError):return False
    def bind_async(self,id,tool,inputs,force):
        key=self.input_key(tool,inputs);kind={'landcover_area':'landcover','sentinel_native':'production','cropland_delineation':'cropland','agriculture_analysis':'agriculture','raster_processing':'raster_processing'}[tool];service=getattr(self.store,kind)
        # Publish ownership atomically with dispatch so cancellation cannot miss
        # a child created immediately before the former out-of-lock bind step.
        with self.assignment_lock,self.store.lock:
            self.check(id)
            child=binding=None
            if not force:
                for child in service.list():
                    if self.compatible_child(tool,child,inputs,key):
                        binding={'type':kind,'task_id':child['id'],'reused':True,'input_key':key,'owned':False};break
            if binding is None:
                child=service.create(inputs);binding={'type':kind,'task_id':child['id'],'reused':False,'input_key':key,'owned':True}
            def publish(job):
                job['binding']=copy.deepcopy(binding)
                if job.get('tool_calls'):job['tool_calls'][0].update(binding=copy.deepcopy(binding),reused=binding['reused'])
            self.change(id,publish)
            return child,binding
    def process(self,id):
        try:
            self.check(id);job=self.get(id)
            if job.get('plan_snapshot'):plan=job['plan_snapshot']
            else:
                def planning(j):j.update(status='planning',message='正在校验业务范围、时期与真实工具');j['steps'][0].update(status='running',started_at=now(),input_summary={'问题':j['question'],'业务上下文':j['context']})
                self.change(id,planning);plan=self.prepare(job['question'],job['context'])
                def planned(j):j.update(plan_snapshot=plan,plan_ref=plan.get('plan_ref'),intent=plan.get('intent'),goal=plan.get('source_snapshot',{}).get('goal') if isinstance(plan.get('source_snapshot'),dict) else None,progress=5);j['steps'][0].update(status='succeeded',finished_at=now(),output_summary={'可执行':plan['executable'],'缺项':plan.get('missing_fields',[]),'缺口':plan.get('blocking_gaps',[]),'工具':plan.get('tool')})
                self.change(id,planned)
            if not plan['executable']:
                block_message=plan.get('clarification_message') or '；'.join(plan.get('blocking_gaps',[])) or '该任务没有已实现的真实执行器'
                def blocked(j):j.update(status='unsupported' if plan['status']=='unsupported' else 'needs_data',message=block_message,clarification={'missing_fields':plan.get('missing_fields',[]),'message':block_message},finished_at=now());j['logs'].append({'time':now(),'message':j['message']})
                self.change(id,blocked);return
            self.check(id);tool=plan['tool']['id'];inputs=copy.deepcopy(plan['tool']['inputs']);job=self.get(id)
            if not job['tool_calls']:
                def begin(j):j.update(status='running',message='正在调用真实工具：'+TOOLS[tool]['name']);j['steps'][1].update(status='running',started_at=now());j['tool_calls'].append({'id':uid('toolcall'),'tool_id':tool,'version':TOOLS[tool]['version'],'inputs':inputs,'status':'running','started_at':now(),'binding':None,'reused':False});j['logs'].append({'time':now(),'message':'调用固定工具 '+tool+'，没有执行任意HTTP合同'})
                self.change(id,begin)
            job=self.get(id)
            if tool in ('landcover_area','sentinel_native','cropland_delineation','agriculture_analysis','raster_processing'):
                service=getattr(self.store,{'landcover_area':'landcover','sentinel_native':'production','cropland_delineation':'cropland','agriculture_analysis':'agriculture','raster_processing':'raster_processing'}[tool])
                if job.get('binding'):binding=job['binding'];child=service.get(binding['task_id'])
                else:child,binding=self.bind_async(id,tool,inputs,plan['force'])
                def bound(j):j['binding']=binding;j['tool_calls'][0].update(binding=binding,reused=binding['reused']);j['logs'].append({'time':now(),'message':('复用已存在真实任务 ' if binding['reused'] else '已创建真实子任务 ')+binding['task_id']})
                self.change(id,bound)
                while child['status'] in ('queued','running'):
                    self.check(id)
                    def sync(j):j.update(progress=5+round(child.get('progress',0)*.9),child_steps=copy.deepcopy(child.get('nodes',[])),message='真实子任务正在执行：%d%%'%child.get('progress',0));j['tool_calls'][0]['child_status']=child['status']
                    self.change(id,sync);time.sleep(.2);child=service.get(binding['task_id'])
                self.check(id)
                if child['status']!='succeeded':raise GeoError('CHILD_NOT_SUCCEEDED',child.get('error',{}).get('message','真实工具未成功；未生成替代结果'),502)
                if not self.compatible_child(tool,child,inputs,self.input_key(tool,inputs)):raise GeoError('RESULT_VALIDATION_FAILED','真实子任务的来源/原生模式或产物不完整，不能作为执行成功')
                result=copy.deepcopy(child['result']);result_ref={'type':binding['type'],'task_id':child['id'],'url':'/api/'+('raster-processing' if binding['type']=='raster_processing' else binding['type'])+'/tasks/'+child['id'],'image_kind':child['plan']['algorithm_id'] if tool in ('agriculture_analysis','raster_processing') else 'cropland' if tool in ('landcover_area','cropland_delineation') else 'change' if child['plan']['goal']=='change' else child['plan'].get('index_id','ndvi')};child_steps=child.get('nodes',[])
                summary='指定范围的真实公开原生像元分析已完成；质量与统计见实际来源报告。'
                if tool=='landcover_area':
                    intent=plan['intent'];result_ref['image_kind']='classes' if intent in ('landcover_composition','water_area','tree_cover_area') else 'cropland'
                    result_ref['map_effect']='none' if intent in ('source_quality','download_results') else 'show'
                    if intent in ('water_area','tree_cover_area'):
                        code=80 if intent=='water_area' else 10;item=next(c for c in result['classes'] if c['code']==code);result_ref['selected_class']=code;summary='真实2021分类基准：%s(code%d)面积%.3f公顷，不等于法定水域/养殖塘或林地资源认定。'%(item['name'],code,item['area_ha'])
                    elif intent=='landcover_composition':summary='真实全市2021土地覆盖构成已核算；分类面积表与完整分类图来自同一任务，不是只展示耕作地。'
                    elif intent=='source_quality':summary='已读取真实2021分类产品与2017参考市界的来源；有效覆盖率不是分类精度。'
                    elif intent=='download_results':summary='已关联本次真实成果下载，不重新生成另一组数字。'
                    else:summary='真实全市2021分类基准核算完成，耕作地%.3f公顷；不是当前官方耕地调查。'%result['summary']['cropland_ha']
            elif tool=='result_reference':
                reference=self.get(inputs['execution_id'])
                if reference['status']!='succeeded' or not reference.get('result'):raise GeoError('REFERENCE_UNAVAILABLE','引用的原执行没有完整成功结果')
                result=copy.deepcopy(reference['result']);result_ref=copy.deepcopy(reference['result_ref']);result_ref['map_effect']='none';binding={**reference['binding'],'owned':False,'reused':True,'reference_execution_id':reference['id']};child_steps=[];summary='已读取原执行'+reference['id']+'的真实来源/质量与产物；未启动新核算，也未切换到其他任务。'
            elif tool=='public_catalog_search':
                result=self.store.data_catalog.search(inputs)
                if result['status']=='failed':raise GeoError('CATALOG_FAILED','真实目录查询失败；没有伪造候选',502)
                binding={'type':'discovery','task_id':result['id'],'reused':False,'owned':True};result_ref={'type':'discovery','task_id':result['id'],'url':'/api/data-searches/'+result['id'],'image_kind':'catalog_only'};child_steps=[];summary='已实际检索%d个公开目录候选；这不是像素读取或遥感解译。'%sum(len(r['items']) for r in result['results'])
            elif tool=='weather_forecast':
                result=self.store.public_sources.weather(inputs);binding={'type':'weather','task_id':id,'reused':False,'owned':True};result_ref={'type':'execution','task_id':id,'url':'/api/executions/'+id,'image_kind':'weather_point'};child_steps=[];summary='已获取指定位置的真实天气模型预报；请求坐标与返回格点均保留，不是实测站数据。'
            elif tool=='vector_analysis':
                result=self.store.create_analysis(inputs);binding={'type':'analysis','task_id':result['id'],'reused':False,'owned':True};result_ref={'type':'analysis','task_id':result['id'],'url':'/api/analysis/'+result['id'],'image_kind':'geojson'};child_steps=[];summary='已对明确绑定的用户数据实际执行空间/属性算法；这不是卫星像素解译或法定认定。'
            else:raise GeoError('TOOL_NOT_REGISTERED','没有该工具的真实固定实现')
            self.check(id)
            def collecting(j):j.update(progress=98,binding=binding,child_steps=copy.deepcopy(child_steps));j['steps'][1].update(status='succeeded',finished_at=now(),output_summary={'真实任务ID':binding['task_id'],'复用':binding['reused'],'来源类型':binding['type']});j['tool_calls'][0].update(status='succeeded',finished_at=now(),binding=binding,reused=binding['reused'],output_ref=result_ref);j['steps'][2].update(status='running',started_at=now())
            self.change(id,collecting);self.check(id)
            def finish(j):j.update(status='succeeded',progress=100,message='真实执行完成，结果已归档',summary=summary,result=result,result_ref=result_ref,finished_at=now());j['steps'][2].update(status='succeeded',finished_at=now(),output_summary={'结果引用':result_ref,'真实来源保留':True});j['logs'].append({'time':now(),'message':'实际结果与工具调用绑定已归档；不是仅返回计划合同'})
            self.change(id,finish)
        except Exception as e:
            if self.stopping:return
            code=e.code if isinstance(e,GeoError) else 'EXECUTION_FAILED';message=e.message if isinstance(e,GeoError) else '真实执行失败，未生成演示或替代数据'
            def fail(j):
                j.update(status='failed',error={'code':code,'message':message},message=message,finished_at=now());j['logs'].append({'time':now(),'message':message})
                for step in j['steps']:
                    if step['status']=='running':step.update(status='failed',message=message,finished_at=now())
                for call in j['tool_calls']:
                    if call['status']=='running':call.update(status='failed',error={'code':code,'message':message},finished_at=now())
            self.change(id,fail)
    def cancel(self,id):
        with self.store.lock:
            job=self.get(id)
            if job['status'] in TERMINAL:raise GeoError('INVALID_STATE','只有待处理/规划/运行中的执行可取消',409)
            job.update(status='cancelled',message='本执行已取消；若任务仍被其他问题引用则不取消共享计算',finished_at=now(),updated_at=now());job['logs'].append({'time':now(),'message':job['message']})
            for step in job['steps']:
                if step['status'] in ('pending','running'):step.update(status='cancelled',finished_at=now())
            for call in job.get('tool_calls',[]):
                if call.get('status')=='running':call.update(status='cancelled',finished_at=now(),message='本执行取消，真实子任务是否继续取决于共享引用')
            self.store.save('execution',job);binding=job.get('binding')
            if binding and binding.get('owned') and binding['type'] in ('landcover','production','cropland','agriculture','raster_processing'):
                shared=any(j['id']!=id and j['status'] not in TERMINAL and j.get('binding',{} ) and j['binding'].get('task_id')==binding['task_id'] for j in self.list())
                shared=shared or any(q['status']=='processing' and q.get('binding') and q['binding'].get('task_id')==binding['task_id'] for q in self.store.list('assistant_question'))
                if not shared:
                    try:getattr(self.store,binding['type']).cancel(binding['task_id'])
                    except Exception:pass
            return job
    def retry(self,id):
        job=self.get(id)
        if job['status'] not in ('failed','cancelled','needs_data'):raise GeoError('INVALID_STATE','只有失败/取消/待补条件的执行可重试',409)
        return self.create({'question':job['question'],'context':job['context']},parent_id=id)
    def continue_job(self,id,body):
        job=self.get(id)
        if job['status']!='needs_data':raise GeoError('INVALID_STATE','只有待补业务条件的执行可继续',409)
        if not isinstance(body,dict) or set(body)-{'context','request_id'} or not isinstance(body.get('context'),dict):raise GeoError('INVALID_CONTINUATION','请提供要补充的业务上下文',400)
        context=copy.deepcopy(job['context']);context.update(body['context']);payload={'question':job['question'],'context':context}
        if body.get('request_id'):payload['request_id']=body['request_id']
        return self.create(payload,parent_id=id)
    def close(self):
        self.stopping=True
        for thread in self.threads:thread.join(timeout=.5)
