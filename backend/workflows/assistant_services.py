"""Grounded map questions: bounded intent routing to persisted real analysis artifacts."""
import copy
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from backend.core.geo_services import GeoError
from backend.analysis.landcover_services import PRODUCT, load_boundary
from backend.analysis.production_services import make_plan,raster_available
from backend.analysis.spectral_indices import detect_indices
from backend.integrations.codex_gateway import CodexGatewayError

METHOD_VERSION='worldcover-native-wgs84-center-v1'
METHOD='WGS84椭球纬带积分；逐原生行面积权重；市界像元中心掩膜'
CAPABILITIES=[
 ('regional_product_read','读取区域真实成果','查看仙桃全市2024年耕作地参考面积'),
 ('temporal_analysis','时段影像分析','对选区NDVI执行2025年10月至11月中位数合成'),
 ('adaptive_segmentation','自适应阈值分区','对所选指数成果执行Otsu自动阈值分区'),
 ('vegetation_cover','植被覆盖度估算','基于所选单期NDVI成果与明确端元估算植被覆盖度'),
 ('field_zonal_statistics','地块分区统计','按所选地块统计已有遥感栅格成果'),
 ('cropland_delineation','选区耕作地标注','标注选区2024年耕作地，汇总图斑与面积'),
 ('cropland_area','全市耕作地面积','仙桃农田有多少亩？'),
 ('cropland_distribution','耕作地分布','农田主要分布在哪里？'),
 ('landcover_composition','土地覆盖构成','仙桃土地主要由哪些构成？'),
 ('water_area','永久水体参考','仙桃水域占比多少？'),
 ('tree_cover_area','树木覆盖参考','仙桃树木覆盖面积多少？'),
 ('source_quality','来源与质量','这些数据准吗，来自哪里？'),
 ('download_results','下载已有成果','下载这次分析结果'),
 ('recompute_area','重新核算基准','重新核算仙桃2021年面积基准'),
 ('vegetation_change','多光谱指数分析','对指定样区分析2025年10月和11月NDVI变化'),
 ('weather','位置天气模型','查询选中范围的当前天气'),
 ('catalog_discovery','多源影像目录','检索选中范围2025年11月可用卫星影像'),
 ('platform_capabilities','查看支持能力','当前可以提供什么数据基线？'),
]


def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def uid(prefix):return prefix+'_'+uuid.uuid4().hex[:14]
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def capabilities():
    descriptions={'regional_product_read':'按明确仙桃市范围、年度或实际日期读取已生产真实成果，冻结内容版本；不是重新遥感计算。','temporal_analysis':'同一传感器多日期原生指数合成与趋势，逐像元保留有效观测次数。','adaptive_segmentation':'明确已有原生光谱成果上的自动阈值和连通分区，不是水体或农田分类。','vegetation_cover':'对明确单期原生NDVI成果与显式端元估算FVC，保留参数假设与截断统计。','field_zonal_statistics':'按明确用户地块统计原生指数或年度分类面积，区分部分覆盖、未知与重叠。','cropland_delineation':'按明确产品年度提取耕作地连通图斑、面积与GIS成果，支持2021与2024历史分类参考。','vegetation_change':'在地图选择范围并说明时段，自动读取真实影像、计算并出图。','weather':'按地图位置实际查询天气模型，不作为灾损认定。','catalog_discovery':'按位置与时间真实检索S2、S1 RTC、Landsat目录；不冒充像素解译。'}
    return [{'id':id,'name':name,'description':descriptions.get(id,'自动组织真实来源与分析，并保留问题与成果绑定。'),'examples':[example],'available':raster_available() if id in ('vegetation_change','cropland_delineation') else True,'source_year':None if id=='cropland_delineation' else None if id in descriptions else 2021,'boundary_year':None if id in descriptions else 2017} for id,name,example in CAPABILITIES]


def route(question,hint=None):
    """Conservative supported language; a capability card never overrides the question."""
    q=question.strip();lower=q.lower()
    followup=not re.match(r'^(?:请|帮我|替我|麻烦)?\s*(?:计算|估算|分析|重新|重算|重跑|统计|按.+?统计)',q) and not re.search(r'重新计算|重新估算|重新分析|重新统计|重跑|重算',q)
    derived_name='fvc' in lower or 'otsu' in lower or any(w in q for w in ('合成','时序趋势','自适应阈值','自动阈值分区')) or any(w in q for w in ('植被覆盖度','分区统计','地块统计'))
    if derived_name and followup and any(w in q for w in ('下载','导出')):return 'download_results',None,None
    if derived_name and followup and any(w in q for w in ('来源','质量','准吗','精度','依据','用的什么影像','来自哪里','来自哪')):return 'source_quality',None,None
    processing='adaptive_segmentation' if 'otsu' in lower or any(w in q for w in ('自适应阈值','自动阈值分区')) else 'temporal_analysis' if any(w in q for w in ('中位数合成','均值合成','时段影像','时序趋势','线性趋势','多景合成')) else None
    if processing:
        if any(w in q for w in ('全省','全市','全县','全国','产量','灾损','病虫害','法定','确权','作物分类','水稻面积','耕地面积','农田面积')):return None,'unsupported','集合与阈值算法仅提供有界光谱统计，不能代替全域分类、产量、灾损或法定业务认定。'
        return processing,None,None
    derived='vegetation_cover' if 'fvc' in lower or '植被覆盖度' in q else 'field_zonal_statistics' if any(w in q for w in ('分区统计','逐地块统计','按地块统计','按所选地块','各地块统计','每块地统计')) else None
    if derived:
        if any(w in q for w in ('产量','灾损','受灾损失','确权','法定','官方耕地','高标准农田','病虫害','施肥量','水稻','小麦','玉米','油菜','棉花','作物分类','永久基本','水田','旱地')):return None,'unsupported','当前农业算子提供光谱与覆盖估算、地块统计，不能认定作物细类、产量、灾损、权属或病虫害。'
        return derived,None,None
    # A selected-area classification request uses its own real raster/vector tool.
    # Keep it ahead of the historical fixed-city routing; NDVI is not cropland.
    delineation=any(w in q for w in ('农田','耕地','耕作地')) and any(w in q for w in ('标注','圈出','勾画','提取','图斑','描绘'))
    if delineation:
        if any(w in q for w in ('高标准','永久基本','基本农田','确权','法定','官方','保有量','播种面积','水田','旱地','水稻','小麦','玉米','油菜','棉花','果园','茶园','温室','大棚','作物分类','非农田','非耕地','非农业','变化','增减','增加','减少')):
            return None,'unsupported','现有耕作地标注不能识别作物细类、变化、工程属性或法定权属，不能用公开地类产品代替。'
        if any(w in q for w in ('全省','全国','省域','全市','全县','整市')):
            return None,'unsupported','选区标注执行器目前处理单边不超过0.05°的小范围，不能以小窗代替整市或全省。'
        temporal=re.sub(r'当前范围|当前选区|当前区域|当前地图|现在选定|目前选定','',q)
        years=re.findall(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)',temporal)
        if any(y not in ('2021','2024') for y in years) or len(set(years))>1 or any(w in temporal for w in ('今年','去年','最新','实时','当前','现在','现状','本年','目前','今日','当期','上年度','上年','上期','本期','明年')):
            return None,'unsupported','选区标注支持2021或2024公开年度分类产品；当期或其他年度识别、跨产品变化尚未接通，不能用历史产品代答。'
        if detect_indices(q):return None,'unsupported','农田分类范围不能用NDVI等指数阈值代替，请分别提出光谱分析或耕作地产品提取。'
        return 'cropland_delineation',None,None
    quality=any(w in q for w in ('来源','依据','准不准','准确','精度','误差','可信','口径','数据年份','数据准','准吗','官方的吗','官方数据吗','是不是官方','是否官方','市界年份','数据来自','数据是哪年','来自哪年','哪一年的数据','来自哪里','用的什么影像','什么影像'))
    if ('边界' in q or '市界' in q) and any(w in q for w in ('哪年','年份','来源','版本','2017','权威')):quality=True
    platform=any(w in q for w in ('支持什么','支持哪些','提供什么','提供哪些','怎么用','能做什么','有什么能力','什么基线','什么数据基线'))
    numeric_request=any(w in q for w in ('多少','几亩','几公顷','占比','比例','总量')) or (any(w in q for w in ('统计','计算','核算','估算','估计','查询')) and any(w in q for w in ('面积','亩','公顷')) and not any(w in q for w in ('方法','公式','原理')))
    quality_only=quality and not numeric_request
    download=any(w in q for w in ('下载','导出','导出文件'))
    vegetation=bool(detect_indices(q)) or any(w in lower for w in ('植被','长势','光谱'))
    # Explicit unsupported scopes and definitions remain unsupported even with a valid hint.
    if any(w in q for w in ('全省','省域','全国','全湖北')) or ('湖北省' in q and '仙桃' not in q):return None,'unsupported','当前只有仙桃全市参考基准，不能以仙桃结果替代全省或其他范围。'
    if any(w in q for w in ('乡镇','各镇','镇级','分镇','各村','村级','分村','某镇','某村','张沟镇','彭场镇')):return None,'unsupported','目前没有可用于该行政层级的可靠统计边界，不能拿全市面积回答乡镇或村级问题。'
    if any(w in q for w in ('这块地','该地块','选中地块','这个地块','这片地','选中区域','当前范围','每块地','各地块','单块','这里有多少','附近有多少')):return None,'needs_data','需要明确且可核验的地块/区域边界；当前全市基准不能充当这块地的面积。'
    for mention in re.findall(r'((?:(?!市|县)[\u4e00-\u9fff]){1,10}(?:市(?!界)|县(?!界)))',q):
        cleaned=re.sub(r'统计|核算|计算|分析|请|我要|参考|全部|完整|的|目前|当前|并|展示|显示|查看|分布|覆盖|范围|情况|土地|耕地|农田|在|内|于|和|与','',mention)
        if '仙桃' not in mention and cleaned not in ('全市','全县','市','县','市界','我市','本市'):return None,'unsupported','检测到其他地区表述，当前仙桃参考成果不能替代该地区结果。'
    if any(w in q for w in ('水田','旱地','高标准农田','播种面积','确权','基本农田','耕地保有','法定林地','森林蓄积','森林资源','黄鳝','禁捕')) and not quality_only:return None,'unsupported','现有土地覆盖产品不支持该业务或法定口径，不能用耕作地/NDVI类别代替。'
    if any(w in q for w in ('养殖塘','鱼塘','养殖水面')) and not any(w in q for w in ('水域','水体')):return None,'unsupported','WorldCover永久水体无法单独识别养殖塘，当前没有该类可靠面积结果。'
    if any(w in q for w in ('水稻','小麦','玉米','油菜','棉花','作物分类','非农田','非耕地','面积排名','农田排名')) and not quality_only:return None,'unsupported','现有基准不能回答该作物细分、非农田或排名口径，不能用全市耕作地总量代答。'
    if any(w in q for w in ('减少','增加','下降','增长','变化','十年','历年','逐年','趋势')) and any(w in q for w in ('农田','耕地','面积')) and not vegetation and not quality_only:return None,'unsupported','目前只有2021单年分类基准，不能据此给出耕地增减、历史变化或趋势。'
    if '官方' in q and not quality_only and not platform:return None,'unsupported','当前只有公开分类产品的参考面积，不能提供官方耕地保有量或调查认定结果。'
    if vegetation and not any(w in q for w in ('耕地面积','农田面积','多少亩','全市面积')):
        return 'vegetation_change',None,None
    years=re.findall(r'(?<!\d)(?:(?:19|20|21)\d{2}|\d{4}(?=年))(?!\d)',q)
    allowed_years={'2021','2017'} if quality_only and ('边界' in q or '市界' in q) else {'2021'}
    if any(y not in allowed_years for y in years):return None,'unsupported','目前可回答的是2021分类产品×2017公开参考市界的基准，无法把其他年份当作已分析。'
    relative=any(w in q for w in ('今年','当前','现在','最新','实时','目前','去年','本年度','本年','现状','今日','上年度','上年'))
    source_frame=any(w in q for w in ('现在可用','当前可用','目前可用','这些数据','该数据','这个结果','已有','可提供'))
    strong_relative=any(w in q for w in ('今年','去年','本年度','本年','今日','上年度','上年','现状'))
    if relative and (numeric_request or not (quality_only or platform or download) or (strong_relative and not platform and not source_frame) or (download and any(w in q for w in ('面积','耕地','农田')) and '2021' not in years and not source_frame)):return None,'unsupported','没有所请求年度或当前全市耕地统计，不能用2021历史基准冒充今年、去年或最新面积。'
    if platform:return 'platform_capabilities',None,None
    if quality_only:return 'source_quality',None,None
    if any(w in q for w in ('重新核算','重新计算','重新统计','重跑','重算','再算一次','更新计算')):return 'recompute_area',None,None
    if download:return 'download_results',None,None
    if any(w in q for w in ('水域','水体','树木','林地')) and any(w in q for w in ('农田','耕地','耕作地')):return 'landcover_composition',None,None
    if any(w in q for w in ('构成','地类','土地类型','土地主要','分类情况','土地覆盖')):return 'landcover_composition',None,None
    if any(w in q for w in ('水域','水体')):return 'water_area',None,None
    if any(w in q for w in ('树木','林地','森林面积')):return 'tree_cover_area',None,None
    if any(w in q for w in ('分布','哪里','地图上')):return 'cropland_distribution',None,None
    if any(w in q for w in ('耕地','农田','耕作地','农业情况','农田情况','面积基准')):return 'cropland_area',None,None
    return None,'needs_data','请明确想了解仙桃的耕作地面积、土地覆盖构成、分布、来源质量或下载成果。'


class AssistantServices:
    def __init__(self,store):
        self.store=store;self.use_execution=hasattr(store,"executions");self.assignment_lock=threading.RLock();self.stopping=False;self.threads=[];self.active={}
        for record in self.store.list('assistant_question'):
            if record['status']=='processing':self.launch(record['id'])
    def raw_get(self,id):return self.store.get('assistant_question',id)
    def get(self,id):
        record=self.raw_get(id)
        if record['status']=='answered':record['answer']=copy.deepcopy(record.get('answer_snapshot',record.get('answer')))
        return record
    def list(self):return [self.get(q['id']) for q in self.store.list('assistant_question')]
    def change(self,id,fn):
        with self.store.lock:
            q=self.raw_get(id)
            if q['status']!='processing':return False
            fn(q);q['updated_at']=now();self.store.save('assistant_question',q);return True
    def create(self,body,application_ref=None):
        if not isinstance(body,dict) or set(body)-{'question','capability_id','context','request_id'}:raise GeoError('INVALID_QUESTION','提问包含不支持字段',400)
        text=body.get('question')
        if not isinstance(text,str) or not text.strip() or len(text)>2000:raise GeoError('INVALID_QUESTION','问题须为1–2000字符的非空文本',400)
        hint=body.get('capability_id')
        if hint is not None and (not isinstance(hint,str) or hint not in {c[0] for c in CAPABILITIES}|{'public_native_raster','weather','catalog_discovery','vector_analysis','user_vector_analysis'}):raise GeoError('INVALID_CAPABILITY','未知能力入口',400)
        context=body.get('context',{})
        if not isinstance(context,dict) or set(context)-{'reference_question_id','source_task_id','source_type','soil_ndvi','vegetation_ndvi','field','threshold','processing_algorithm','index_id','scene_limit','cloud','direction','min_patch_pixels','bbox','periods','period','dataset_id','region','clarification_of','reference_execution_id','source_id','observation_id','cropland_product','atlas_product_sha256'}:raise GeoError('INVALID_CONTEXT','问题上下文包含不支持字段',400)
        for key in ('reference_question_id','source_task_id','source_type','field','processing_algorithm','index_id','direction','clarification_of','reference_execution_id','source_id','observation_id','cropland_product','atlas_product_sha256'):
            if key in context and (not isinstance(context[key],str) or not context[key] or len(context[key])>100):raise GeoError('INVALID_CONTEXT','引用ID格式无效',400)
        if 'atlas_product_sha256' in context and not re.fullmatch(r'[0-9a-f]{64}',context['atlas_product_sha256']):raise GeoError('INVALID_CONTEXT','区域成果版本须为64位小写SHA256',400)
        request_id=body.get('request_id')
        if request_id is not None and (not isinstance(request_id,str) or not request_id or len(request_id)>80):raise GeoError('INVALID_REQUEST_ID','问题request_id格式无效',400)
        if context.get('clarification_of'):
            original=self.get(context['clarification_of'])
            if original['status']!='needs_data':raise GeoError('INVALID_CLARIFICATION','只能补充待完善业务条件的问题',409)
            if not text.strip().startswith(original['question']):raise GeoError('CLARIFICATION_CHANGED','补充请求必须保留原始业务问题',400)
            context={**original['context'],**context};context.pop('reference_question_id',None)
        request_input={'question':text.strip(),'capability_id':hint,'context':context}
        if application_ref is not None:request_input['application_ref']=application_ref
        request_hash=fingerprint(request_input)
        intent,state,message=route(text,hint)
        if self.use_execution:state=None;message='正在校验专题参数并提交分析任务' if application_ref is not None else '正在校验分析条件并组织数据'
        q={'id':uid('question'),'question':text.strip(),'capability_id':intent,'intent':intent,'status':state or 'processing','progress':0,'message':message or '正在匹配真实数据与固定分析能力','context':copy.deepcopy(context),'parent_question_id':context.get('clarification_of') or context.get('reference_question_id'),'request_id':request_id,'request_hash':request_hash,'binding':None,'answer':None,'answer_snapshot':None,'logs':[{'time':now(),'message':message or '问题已记录；尚未生成分析答案'}],'created_at':now(),'updated_at':now()}
        if application_ref is not None:q['application_ref']=copy.deepcopy(application_ref)
        with self.store.lock:
            if self.stopping:raise GeoError('SERVICE_STOPPING','服务正在关闭',503)
            if request_id:
                existing=next((r for r in self.store.list('assistant_question') if r.get('request_id')==request_id),None)
                if existing:
                    if existing.get('request_hash')!=request_hash:raise GeoError('IDEMPOTENCY_CONFLICT','同一request_id不能改变问题或分析范围',409)
                    return self.get(existing['id'])
            if sum(r['status']=='processing' for r in self.store.list('assistant_question'))>=16:raise GeoError('ASSISTANT_BUSY','正在处理的问题较多，请稍后重试',429)
            self.store.save('assistant_question',q)
        if q['status']=='processing':self.launch(q['id'])
        return q
    def launch(self,id):
        thread=threading.Thread(target=self.process,args=(id,),daemon=True);self.threads.append(thread);thread.start()
    def check(self,id):
        if self.stopping:raise GeoError('INTERRUPTED','服务关闭，问题等待中断')
        if self.raw_get(id)['status']!='processing':raise GeoError('QUESTION_STOPPED','问题已经结束')
    def settle(self,id,status,message,answer=None):
        def update(q):
            if status=='answered' and isinstance(answer,dict) and (q.get('binding') or {}).get('type') in ('landcover','production','cropland') and not answer.get('data_plan_ref') and getattr(self.store,'data_catalog',None):
                binding=q['binding']
                try:
                    plan=self.store.data_catalog.create_plan({'question':q['question'],'context':{'task_type':binding['type'],'task_id':binding['task_id']}})
                    answer['data_plan_ref']={'id':plan['id'],'url':'/api/data-plans/'+plan['id'],'schema_version':plan['schema_version'],'not_executed':True}
                    q['data_plan_id']=plan['id']
                except Exception:
                    # A catalog-plan failure must not discard an already verified analysis.
                    answer['data_plan_notice']='选源计划暂不可用；真实分析结果与原始来源记录仍保留。'
                    q['logs'].append({'time':now(),'message':answer['data_plan_notice']})
            q.update(status=status,message=message,progress=100 if status=='answered' else q.get('progress',0),answer=answer,answer_snapshot=copy.deepcopy(answer),finished_at=now());q['logs'].append({'time':now(),'message':message})
        self.change(id,update)
    def base_key(self):
        _,meta=load_boundary(self.store.landcover.source_dir)
        return fingerprint({'product':PRODUCT['id'],'year':2021,'boundary_sha256':meta['sha256'],'method_version':METHOD_VERSION}),meta['sha256']
    def compatible(self,task,boundary_hash):
        try:
            r=task['result'];p=r['provenance'];s=r['summary']
            if task['status']!='succeeded' or p['product']['id']!=PRODUCT['id'] or p['year']!=2021 or s['reference_year']!=2021 or p['boundary_sha256']!=boundary_hash or p['boundary_source']['sha256']!=boundary_hash or s['method']!=METHOD or p['mask_rule']!='pixel center inside polygon, all_touched=False' or p['statistics_resampled'] is not False:return False
            datahash=p['classified_window_sha256']
            if not isinstance(datahash,str) or not re.fullmatch(r'[a-f0-9]{64}',datahash):return False
            for name in ('overlay.png','classes.png','classification.tif','row_counts.csv','result.json'):self.store.landcover.artifact(task['id'],name)
            return True
        except (KeyError,TypeError,GeoError,OSError):return False
    def explicit_task(self,q):
        context=q['context'];direct=context.get('source_task_id');reference=context.get('reference_question_id')
        if reference:
            try:old=self.get(reference)
            except Exception:raise GeoError('REFERENCE_UNAVAILABLE','上一条问题不存在，请重新选择历史问题')
            binding=old.get('binding')
            if not binding or binding.get('type')!='landcover':raise GeoError('REFERENCE_UNAVAILABLE','上一条问题没有可引用的全市面积成果')
            if direct and direct!=binding['task_id']:raise GeoError('REFERENCE_CONFLICT','问题引用与图层引用不一致，请选择要追问的那份成果')
            direct=binding['task_id']
        return direct
    def bind(self,id,task,base,reused,reason):
        binding={'type':'landcover','task_id':task['id'],'cache_key':fingerprint({'base':base,'data_hash':task.get('result',{}).get('provenance',{}).get('classified_window_sha256') if task.get('result') else None}),'base_key':base,'reused':reused,'reason':reason,'method_version':METHOD_VERSION}
        def update(q):q.update(binding=binding,message='已关联真实成果' if task['status']=='succeeded' else '后台正在核算仙桃2021参考基准');q['logs'].append({'time':now(),'message':reason+' · '+task['id']})
        self.change(id,update)
    def landcover_task(self,q):
        base,boundary_hash=self.base_key();explicit=self.explicit_task(q);recompute=q['capability_id']=='recompute_area'
        with self.assignment_lock:
            self.check(q['id'])
            if q.get('binding'):
                task=self.store.landcover.get(q['binding']['task_id']);self.bind(q['id'],task,base,q['binding']['reused'],'恢复原问题绑定');return task,base,boundary_hash
            if explicit and not recompute:
                try:task=self.store.landcover.get(explicit)
                except Exception:raise GeoError('REFERENCE_UNAVAILABLE','引用的成果任务不存在')
                if not self.compatible(task,boundary_hash):raise GeoError('REFERENCE_UNAVAILABLE','引用成果不是同版本完整成功结果；不会静默改绑最新任务')
                self.bind(q['id'],task,base,True,'引用历史问题或图层的原始成果');return task,base,boundary_hash
            if not recompute:
                for task in self.store.landcover.list():
                    if self.compatible(task,boundary_hash):self.bind(q['id'],task,base,True,'复用同产品/市界/方法版本的真实成功结果');return task,base,boundary_hash
                for task in self.store.landcover.list():
                    if task['status'] in ('queued','running'):
                        self.bind(q['id'],task,base,False,'等待已有真实核算完成，不重复创建');return task,base,boundary_hash
            task=self.store.landcover.create({'region':'xiantao','product':PRODUCT['id'],'requirement':'核算仙桃全市2021年公开分类产品耕作地面积基准'})
            self.bind(q['id'],task,base,False,'明确重算，新建真实任务' if recompute else '没有同源完整缓存，自动启动真实核算');return task,base,boundary_hash
    def wait_task(self,q,task,service):
        last=None
        while task['status'] in ('queued','running'):
            self.check(q['id']);progress=task.get('progress',0)
            if progress!=last:
                def update(r):r.update(progress=progress,message='正在组织真实分析：已完成%d%%'%progress)
                self.change(q['id'],update);last=progress
            time.sleep(.2);task=service.get(task['id'])
        self.check(q['id'])
        if task['status']!='succeeded':raise GeoError('ANALYSIS_NOT_COMPLETED',task.get('error',{}).get('message','关联分析未成功，未生成替代答案'),502)
        return task
    def answer_lc(self,q,task,base):
        r=task['result'];s=r['summary'];intent=q['capability_id'];classes=r['classes'];p=r['provenance'];prefix='/api/landcover/tasks/'+task['id']
        warnings=['这是2021分类产品×2017公开概化市界的参考基准，不是当前官方耕地调查。','有效覆盖率不是分类精度；没有仙桃本地独立误差校正或95%置信区间。']
        metrics=[];chart=None;image='cropland';effect='show';title='仙桃全市耕作地参考面积'
        def metric(id,label,value,unit):return {'id':id,'label':label,'value':value,'unit':unit}
        if intent in ('cropland_area','cropland_distribution','recompute_area'):
            text='按2021年ESA WorldCover分类产品及2017公开参考仙桃市界，耕作地（code40）面积为约%.2f公顷（%.2f万亩），占参考市界%.2f%%。'%(s['cropland_ha'],s['cropland_mu']/10000,s['cropland_share']*100)
            if intent=='cropland_distribution':title='仙桃耕作地分布';text+='地图显示真实分类分布，不把它当作确权地块。'
            if intent=='recompute_area':title='重新核算已完成';text+='本次重新执行的是同一2021产品基准，不代表数据更新到当前年份。'
            metrics=[metric('cropland_ha','耕作地分类面积',s['cropland_ha'],'公顷'),metric('cropland_wanmu','折合',s['cropland_mu']/10000,'万亩'),metric('cropland_share','占参考市界',s['cropland_share']*100,'%')]
        elif intent=='landcover_composition':
            title='仙桃土地覆盖构成';image='classes';text='2021年分类产品在参考市界内有效覆盖%.4f%%，以下按实际类别面积汇总；不等于法定土地用途调查。'%(s['coverage_ratio']*100)
            metrics=[metric('total_area_ha','参考市界栅格面积',s['total_area_ha'],'公顷'),metric('coverage_ratio','有效分类覆盖',s['coverage_ratio']*100,'%')]
            chart={'type':'bar','title':'2021土地覆盖类别面积（公顷）','data':[{'label':c['name'],'value':c['area_ha'],'color':c['color'],'code':c['code']} for c in classes if c['area_ha']>0]}
        elif intent in ('water_area','tree_cover_area'):
            code=80 if intent=='water_area' else 10;item=next(c for c in classes if c['code']==code);image='classes';title='永久水体参考面积' if code==80 else '树木覆盖参考面积'
            text='当前可提供的是2021产品中的%s（code%d）：约%.2f公顷，占参考市界%.2f%%。'%(item['name'],code,item['area_ha'],item['share']*100)
            warnings.append('永久水体不等于全部水域、养殖塘或养殖水面。' if code==80 else '树木覆盖不等于法定林地或森林资源清查。')
            text+=warnings[-1];metrics=[metric('class_area_ha',item['name'],item['area_ha'],'公顷'),metric('class_share','占参考市界',item['share']*100,'%')]
        elif intent=='source_quality':
            effect='none';title='数据来源与质量边界';text='面积来自ESA WorldCover2021 v200真实分类栅格，使用2017年geoBoundaries公开参考市界。全市有效分类覆盖%.4f%%，这不代表分类精度为100%%。没有本地独立样本，不能给出仙桃的误差率或置信区间。'%(s['coverage_ratio']*100)
            metrics=[metric('product_year','分类产品年份',2021,'年'),metric('boundary_year','参考边界年份',p['boundary_source'].get('boundary_year_represented',2017),'年'),metric('coverage_ratio','有效分类覆盖',s['coverage_ratio']*100,'%')]
        else:
            effect='none';title='下载这份真实成果';text='以下文件全部来自当前关联的真实核算任务，包括原生分类GeoTIFF、逐行计数CSV、来源报告和配准预览；不是重新生成的一组数字。'
        downloads=[{'label':label,'url':prefix+'/'+name,'type':kind} for name,label,kind in [('classification.tif','原生分类栅格','geotiff'),('row_counts.csv','逐行计数与面积复核','csv'),('result.json','完整来源与结果报告','json'),('overlay.png','耕作地分布图','png'),('classes.png','土地覆盖构成图','png')]]
        downloads.insert(0,{'label':'完整可复核成果包','url':'/api/results/landcover/'+task['id']+'/bundle.zip','type':'zip'})
        key=fingerprint({'base':base,'data_hash':p['classified_window_sha256']})
        return {'question_id':q['id'],'title':title,'summary':text,'metrics':metrics,'source_year':2021,'boundary_year':p['boundary_source'].get('boundary_year_represented',2017),'warnings':warnings,'chart':chart,'map_effect':effect,'result_ref':{'type':'landcover','task_id':task['id'],'url':prefix,'image_kind':image},'downloads':downloads,'detail_ref':prefix,'provenance':{'question_id':q['id'],'task_id':task['id'],'cache_key':key,'product':PRODUCT,'boundary_sha256':p['boundary_sha256'],'data_sha256':p['classified_window_sha256'],'method_version':METHOD_VERSION,'planner_mode':'bounded_rules_no_general_llm','result_created_at':task.get('finished_at'),'answer_created_at':now()}}
    def process(self,id):
        try:
            q=self.get(id);self.check(id)
            planner=getattr(self.store,'model_planner',None)
            gateway=getattr(self.store,'model_gateway',None)
            reference=q.get('context',{}).get('reference_question_id')
            referenced=self.get(reference) if reference else None
            general_reference=bool(referenced and not referenced.get('execution_ref') and not (referenced.get('answer') or {}).get('result_ref') and (referenced.get('answer') or {}).get('provenance',{}).get('planner_mode')=='codex_cli')
            # Explicit forms and already-bound/frozen results retain their typed
            # execution path. Free text can use the locally authenticated model.
            if self.use_execution and planner and gateway and gateway.enabled and not q.get('application_ref') and not q.get('execution_ref') and not q.get('execution_input') and (q.get('capability_id') not in ('source_quality','download_results') or general_reference):
                self.change(id,lambda r:r.update(message='正在理解分析内容与所需条件'))
                try:
                    decision=planner.plan(q,cancel=lambda:self.stopping or self.raw_get(id)['status']!='processing')
                    self.check(id)
                    audit=copy.deepcopy(decision.get('provenance',{}))
                    audit.update(kind=decision['kind'],title=decision.get('title',''))
                    self.change(id,lambda r:r.update(model_plan=audit))
                    if decision['kind']=='reply':
                        answer={'question_id':id,'title':decision.get('title') or '业务说明','summary':decision['message'],'metrics':[],'warnings':[],'chart':None,'map_effect':'none','result_ref':None,'downloads':[],'provenance':{**audit,'planner_mode':'codex_cli','answer_created_at':now()}}
                        return self.settle(id,'answered','业务答复已完成',answer)
                    if decision['kind']=='clarify':
                        self.change(id,lambda r:r.update(clarification=decision.get('clarification') or {'missing_fields':[],'message':decision['message']}))
                        return self.settle(id,'needs_data',decision['message'])
                    if decision['kind']=='execute':
                        execution_input={'question':decision['execution_question'],'context':copy.deepcopy(decision['execution_context'])}
                        def planned(r):
                            r['original_context']=copy.deepcopy(r['context'])
                            r['execution_input']=execution_input
                            r['context']={**r['context'],**execution_input['context']}
                            if decision.get('application_ref'):r['application_ref']=copy.deepcopy(decision['application_ref'])
                            r['message']='分析条件已校验，正在提交处理任务'
                            r['logs'].append({'time':now(),'message':'已采用经校验的分析计划；原始问题和工具参数分别保存'})
                        self.change(id,planned);q=self.get(id)
                    else:
                        self.change(id,lambda r:r['logs'].append({'time':now(),'message':'模型计划未通过执行校验，采用原始问题的固定规则处理'}))
                except CodexGatewayError as error:
                    self.check(id)
                    self.change(id,lambda r:r.update(model_error={'code':error.code,'message':error.message}))
                    if not q.get('capability_id') or general_reference:
                        return self.settle(id,'failed',error.message)
                    self.change(id,lambda r:r['logs'].append({'time':now(),'message':'本次模型服务未完成，原业务请求按固定规则继续处理'}))
            if general_reference and not q.get('execution_input'):
                return self.settle(id,'needs_data','上一条为业务说明，尚未关联统计成果。请指定要查询的数据，或新建分析。')
            if self.use_execution and (q.get('execution_input') or q['capability_id']!='platform_capabilities'):
                return self.process_execution(q)
            if q['capability_id']=='platform_capabilities':
                answer={'question_id':id,'title':'当前可用的固定分析能力','summary':'可以按明确产品年度在地图选区提取2021或2024年耕作地分类图斑、统计面积并下载完整可复核成果包；也可查看仙桃已验证的2021全市分类基准。在其他地区选取明确坐标范围和时段，可真实运行NDVI、NDWI、MNDWI、NDMI、NDBI、NBR及两期指数差值。后台按需读取所需波段，保留质量与原生尺度；也可查询真实影像目录和天气模型。通用模型按用户选择后续配置，未接入算法不会生成假结果。','metrics':[],'source_year':2021,'boundary_year':2017,'warnings':['全市面积为2021产品×2017参考市界；局部NDVI需要明确范围与时相。'],'chart':None,'map_effect':'none','result_ref':None,'downloads':[],'detail_ref':'/api/assistant/capabilities','provenance':{'question_id':id,'planner_mode':'bounded_rules_no_general_llm','answer_created_at':now()}}
                atlas=getattr(self.store,'agriculture_atlas',None)
                products=atlas.list().get('products',[]) if atlas else []
                if products:
                    from backend.agriculture.regional_product_tools import reference_year
                    years=sorted({reference_year(product) for product in (atlas.get(item['id']) for item in products)}-{None})
                    answer['summary']='已有'+str(len(products))+'个仙桃区域成果入口，产品/观测年度包括'+ '、'.join(str(year) for year in years)+'年。可按明确年度、实际日期查询已有指标、上图和下载；这是读取归档，不是重新生产影像。'+answer['summary']
                    answer['source_year']=None;answer['warnings']=['区域成果按各自年度、实际日期及参考市界使用；不代表当前官方调查或实时监管。','明确重算请求不会被当成读取已有成果；新分析仍需兼容来源、范围、时段和算法条件。']
                if gateway and gateway.enabled:
                    answer['summary']=answer['summary'].replace('通用模型按用户选择后续配置，未接入算法不会生成假结果。','本机已启用Codex测试接入，支持业务说明和经校验的任务规划；分析结果保留来源与版本。')
                self.settle(id,'answered','已说明实际可用能力',answer);return
            if q['capability_id']=='vegetation_change':
                context=q['context']
                if 'bbox' not in context or ('periods' not in context and not re.search(r'20\d{2}年\s*\d{1,2}月.*\d{1,2}月',q['question'])):
                    self.settle(id,'needs_data','客户地图尚未接入选区；请在专业工具指定小范围与前后两个观测时段。当前不能用默认仙桃小窗代替您要分析的地块。');return
                body={'requirement':q['question'],'mode':'sentinel_cog','bbox':context['bbox']}
                if 'periods' in context:body['periods']=context['periods']
                plan=make_plan(body)
                if plan['blocking_gaps'] or any('默认' in w or '暂用' in w for w in plan['warnings']):self.settle(id,'needs_data','；'.join(plan['blocking_gaps']+plan['warnings']));return
                if q.get('binding') and q['binding'].get('type')=='production':task=self.store.production.get(q['binding']['task_id'])
                else:
                    task=self.store.production.create(body)
                    def bind(r):r['binding']={'type':'production','task_id':task['id'],'reused':False,'reason':'指定范围和时相后自动执行真实公开像素生产'}
                    self.change(id,bind)
                task=self.wait_task(q,task,self.store.production);r=task['result'];s=r['summary']
                answer={'question_id':id,'title':'指定样区植被光谱分析','summary':'已对指定小范围真实公开波段进行掩膜与NDVI计算，共同有效%d个代表网格；这不是全市耕地分类或面积统计。'%s['valid_count'],'metrics':[{'id':'mean_ndvi','label':'平均NDVI','value':s['mean_ndvi'],'unit':''},{'id':'selected_count','label':'阈值命中网格','value':s['selected_count'],'unit':'个'}],'source_year':None,'boundary_year':None,'warnings':task['plan']['limitations'],'chart':None,'map_effect':'show','result_ref':{'type':'production','task_id':task['id'],'url':'/api/production/tasks/'+task['id'],'image_kind':'geojson'},'downloads':[],'detail_ref':'/api/production/tasks/'+task['id'],'provenance':{'question_id':id,'task_id':task['id'],**r['provenance']}}
                self.settle(id,'answered','真实样区分析已完成',answer);return
            task,base,boundary_hash=self.landcover_task(q);task=self.wait_task(q,task,self.store.landcover)
            if not self.compatible(task,boundary_hash):raise GeoError('RESULT_VERSION_MISMATCH','成果来源、版本或文件不完整，未生成替代答案')
            self.bind(id,task,base,self.get(id)['binding']['reused'],self.get(id)['binding']['reason'])
            self.settle(id,'answered','已基于真实成果回答',self.answer_lc(q,task,base))
        except Exception as e:
            if self.stopping:return
            code=e.code if isinstance(e,GeoError) else 'ASSISTANT_FAILED';message=e.message if isinstance(e,GeoError) else '后台未能完成真实分析，未生成替代答案，请重试'
            state='needs_data' if code in ('REFERENCE_UNAVAILABLE','REFERENCE_CONFLICT','RESULT_VERSION_MISMATCH','BOUNDARY_UNAVAILABLE') else 'failed'
            self.settle(id,state,message)
    def process_execution(self,q):
        execution_input=q.get('execution_input') or {'question':q['question'],'context':q['context']}
        execution_question=execution_input['question']
        id=q['id'];context={k:copy.deepcopy(v) for k,v in execution_input['context'].items() if k in ('bbox','period','periods','dataset_id','region','reference_execution_id','source_id','observation_id','cropland_product','source_type','source_task_id','soil_ndvi','vegetation_ndvi','field','threshold','processing_algorithm','index_id','scene_limit','cloud','direction','min_patch_pixels','atlas_product_sha256')}
        reference=q['context'].get('reference_question_id')
        direct=q['context'].get('source_task_id')
        if reference:
            original=self.get(reference)
            if direct and direct!=(original.get('binding') or {}).get('task_id'):raise GeoError('REFERENCE_CONFLICT','问题引用与图层引用不一致，请选择要追问的那份成果')
            if context.get('reference_execution_id') and context['reference_execution_id']!=(original.get('execution_ref') or {}).get('id'):raise GeoError('REFERENCE_CONFLICT','问题与执行引用不一致')
            if original.get('execution_ref'):context['reference_execution_id']=original['execution_ref']['id']
            elif q['capability_id'] in ('source_quality','download_results') and (original.get('answer') or {}).get('provenance',{}).get('planner_mode')!='codex_cli':
                # Old immutable answers still retain their original LC task lineage.
                task,base,_=self.landcover_task(q)
                return self.settle(id,'answered','已读取历史真实成果',self.answer_lc(q,task,base))
        elif direct and q['capability_id'] in ('source_quality','download_results') and context.get('source_type')!='agriculture_atlas':
            matched=next((j for j in self.store.executions.list() if j['status']=='succeeded' and (j.get('binding') or {}).get('task_id')==direct),None)
            if matched:context['reference_execution_id']=matched['id']
            else:
                task,base,_=self.landcover_task(q)
                return self.settle(id,'answered','已读取历史真实成果',self.answer_lc(q,task,base))
        if q.get('execution_ref'):job=self.store.executions.get(q['execution_ref']['id'])
        else:
            parent=q['context'].get('clarification_of');parent_question=self.get(parent) if parent else None
            request_id='assistant:'+str(q.get('request_id') or id)
            if parent_question and parent_question.get('execution_ref'):
                # The added text may contain newly supplied dates; freeze the full
                # clarified question rather than silently retaining the old text.
                job=self.store.executions.create({'question':execution_question,'context':context,'request_id':request_id},parent_id=parent_question['execution_ref']['id'])
            else:job=self.store.executions.create({'question':execution_question,'context':context,'request_id':request_id})
            linked=self.change(id,lambda r:r.update(execution_ref={'id':job['id'],'url':'/api/executions/'+job['id']}))
            if not linked:
                # Cancellation can arrive while create() is returning its job.
                # Stop that unlinked job instead of leaving an orphan running.
                try:self.store.executions.cancel(job['id'])
                except GeoError:pass
                self.check(id)
        last=None
        while job['status'] in ('queued','planning','running'):
            self.check(id)
            state=(job['status'],job.get('progress'),job.get('message'))
            if state!=last:
                self.change(id,lambda r:r.update(progress=job.get('progress',0),message=job.get('message','真实执行进行中'),execution_status=job['status']))
                last=state
            time.sleep(.2);job=self.store.executions.get(job['id'])
        self.check(id)
        if job['status']!='succeeded':
            self.change(id,lambda r:r.update(clarification=job.get('clarification'),execution_status=job['status']))
            return self.settle(id,job['status'] if job['status'] in ('needs_data','unsupported','cancelled') else 'failed',job.get('message') or job.get('error',{}).get('message','真实执行未成功，未生成替代结果'))
        binding=copy.deepcopy(job['binding']);intent=job.get('intent')
        self.change(id,lambda r:r.update(binding=binding,capability_id=intent or r['capability_id'],execution_status='succeeded'))
        q=self.get(id)
        if binding['type']=='landcover':
            task=self.store.landcover.get(binding['task_id']);base,_=self.base_key();answer=self.answer_lc(q,task,base)
            answer['execution_ref']={'id':job['id'],'url':'/api/executions/'+job['id']}
        else:
            from backend.workflows.execution_answers import build_answer
            answer=build_answer(q,job)
        if q.get('model_plan'):
            answer.setdefault('provenance',{})['model_planning']=copy.deepcopy(q['model_plan'])
        self.settle(id,'answered','读取已有真实成果完成，原版本已记录' if binding['type']=='agriculture_atlas' else '真实数据处理已完成，结果已归档',answer)
    def cancel(self,id):
        with self.store.lock:
            q=self.raw_get(id)
            if q['status']!='processing':raise GeoError('INVALID_STATE','只有处理中问题可取消',409)
            if q.get('execution_ref'):
                try:self.store.executions.cancel(q['execution_ref']['id'])
                except GeoError:pass
            q.update(status='cancelled',message='已取消本问题；独占执行会停止，共享成果继续服务其他请求',updated_at=now(),finished_at=now());q['logs'].append({'time':now(),'message':q['message']});return self.store.save('assistant_question',q)
    def retry(self,id):
        q=self.get(id)
        if q['status'] not in ('failed','cancelled','needs_data'):raise GeoError('INVALID_STATE','只有失败、取消或待补信息的问题可重试',409)
        return self.create({'question':q['question'],'context':q['context']},application_ref=q.get('application_ref'))
    def shortcuts(self):return self.store.list('assistant_shortcut')
    def save_shortcut(self,body):
        if not isinstance(body,dict) or set(body)-{'name','question','source_question_id','question_id'}:raise GeoError('INVALID_SHORTCUT','快捷问法字段不支持',400)
        alias=body.get('question_id');source=body.get('source_question_id',alias)
        if alias and body.get('source_question_id') and alias!=body['source_question_id']:raise GeoError('INVALID_SHORTCUT','两个源问题ID冲突',400)
        original=None
        if source:
            if not isinstance(source,str):raise GeoError('INVALID_SHORTCUT','原问题ID格式无效',400)
            try:original=self.get(source)
            except Exception:raise GeoError('INVALID_SHORTCUT','原问题不存在',422)
        name=body.get('name',next((c[1] for c in CAPABILITIES if original and c[0]==original['capability_id']),'常用问法'));text=body.get('question',original['question'] if original else None)
        if not isinstance(name,str) or not name.strip() or len(name)>40 or not isinstance(text,str) or not text.strip() or len(text)>2000:raise GeoError('INVALID_SHORTCUT','名称限1–40字符，问题限1–2000字符',400)
        if source:
            if not isinstance(source,str):raise GeoError('INVALID_SHORTCUT','原问题ID格式无效',400)
            try:q=self.get(source)
            except Exception:raise GeoError('INVALID_SHORTCUT','原问题不存在',422)
        else:q=next((r for r in self.list() if r['status']=='answered' and r['question']==text.strip()),None)
        intent,status,_=route(text)
        executed=False
        if q and q.get('execution_ref'):
            try:executed=self.store.executions.get(q['execution_ref']['id'])['status']=='succeeded'
            except GeoError:pass
        if not q or q['status']!='answered' or q['question']!=text.strip() or (not executed and (status or not intent)):raise GeoError('INVALID_SHORTCUT','只可保存已真实回答且当前支持的原句，不保存数字答案',422)
        if len(self.shortcuts())>=100:raise GeoError('SHORTCUT_LIMIT','当前最多100条快捷问法',422)
        context={k:copy.deepcopy(v) for k,v in q.get('context',{}).items() if k in ('bbox','period','periods','dataset_id','region','source_id','observation_id','cropland_product','source_type','source_task_id','soil_ndvi','vegetation_ndvi','field','threshold','processing_algorithm','index_id','scene_limit','cloud','direction','min_patch_pixels','atlas_product_sha256')}
        return self.store.save('assistant_shortcut',{'id':uid('shortcut'),'name':name.strip(),'question':text.strip(),'capability_id':q['capability_id'],'context':context,'source_question_id':q['id'],'created_at':now()})
    def close(self):
        self.stopping=True
        for thread in self.threads:thread.join(timeout=.5)
