"""Conservative, versioned reads of registered regional products.

This tool reads archived calculations. It never presents an archive lookup as a
new raster calculation, and it cannot replace a selected area or source.
"""
import copy
import hashlib
import json
import re
from datetime import date
from backend.core.geo_services import GeoError
from backend.analysis.spectral_indices import detect_indices,unsupported_index_requested

VERSION='regional-product-read-1.0'
REGIONS=('仙桃','仙桃市','xiantao')
FORCE=('重新计算','重新分析','重新核算','重新统计','重新生产','重跑','重算','再算一次','更新计算')
REFERENCE=('来源','来自哪里','来自哪','质量','精度','准吗','准不准','准确','依据','可信','误差','哪年','哪一年','哪一年的','年份','是否官方','是不是官方','官方的吗','官方数据吗','下载','导出','用的什么影像')

def is_recompute(question):
    return any(word in question for word in FORCE) or bool(re.search(r'重新.{0,12}(?:计算|分析|统计|核算|生产|处理)',question))

def product_fingerprint(product):
    """Canonical public product hash; the hash field itself is excluded."""
    value={key:item for key,item in product.items() if key!='product_sha256'}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()

def verified_product(product):
    if not isinstance(product,dict) or product.get('status')!='ready':
        raise GeoError('REGIONAL_PRODUCT_UNAVAILABLE','区域成果未就绪，未使用其他产品代替',409)
    fingerprint=product_fingerprint(product)
    if product.get('product_sha256') not in (None,fingerprint):
        raise GeoError('REGIONAL_PRODUCT_CHANGED','区域成果内容与指纹不一致，未使用替代版本',409)
    return {**copy.deepcopy(product),'product_sha256':fingerprint}

def explicit_dates(question):
    """ISO and Chinese calendar days, preserving order and explicit year."""
    years=list(dict.fromkeys(re.findall(r'(?<!\d)((?:19|20|21)\d{2})(?!\d)',question)))
    values=[];current=None
    pattern=r'(?<!\d)(?:(\d{4})-(\d{2})-(\d{2})|(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日|(?<!\d)(\d{1,2})月\s*(\d{1,2})日)'
    for match in re.finditer(pattern,question):
        a,b,c,y,m,d,bare_m,bare_d=match.groups()
        if a:current=a;month,day=b,c
        elif y:current=y;month,day=m,d
        else:
            current=current or (years[0] if len(years)==1 else None)
            if current is None:raise GeoError('REGIONAL_DATE_YEAR_REQUIRED','月日缺少明确年份，请明确观测年份',422)
            month,day=bare_m,bare_d
        try:value=date(int(current),int(month),int(day)).isoformat()
        except (ValueError,TypeError):raise GeoError('REGIONAL_DATE_INVALID','问题中的实际日期无效，请使用完整日历日期',422)
        values.append(value)
    return values

def subarea_requested(question):
    """Named/unspecified administrative subareas, excluding generic rural prose."""
    if any(term in question for term in ('东部','西部','南部','北部','中部','东侧','西侧','南侧','北侧','东北部','东南部','西北部','西南部','城区','市区','城郊','开发区','高新区','经济区','片区','园区','沿岸','沿江','沿湖','沿河','河岸','湖岸','江岸','两岸','周边','以东','以西','以南','以北')):return True
    for match in re.finditer(r'[\u4e00-\u9fff]{1,12}(?:街道|乡|镇|村)',question):
        if not match.group().endswith(('农村','乡村')):return True
    return False

def scope_text_error(question,region=None,address_lookup=False):
    if region is not None and region not in REGIONS:return '所选行政地区与仙桃归档成果不一致。'
    if any(word in question for word in ('全省','全国','省域','全湖北','全县','乡镇','各镇','各村','镇级','村级','分镇','分村')):
        return '仙桃全市参考成果不能替代其他行政层级或全省范围。'
    if not address_lookup and subarea_requested(question):return '所请求乡镇、城区、方向片区或沿岸等子域没有可核验边界，不能用全市统计代答；请明确提供该范围。'
    for mention in re.findall(r'[\u4e00-\u9fff]{1,12}(?:市(?!界)|县(?!界))',question):
        if '仙桃' not in mention and not mention.endswith(('全市','我市','本市')):
            return '问题包含其他市县，仙桃成果不能替代该地区。'
    if '湖北省' in question and '仙桃' not in question:return '省域请求不能使用仙桃市成果代答。'
    if any(place in question for place in ('武汉','天门','潜江','黄石','十堰','宜昌','襄阳','鄂州','荆门','孝感','荆州','黄冈','咸宁','随州','恩施','神农架','北京','上海','广州','深圳','杭州','成都')):
        return '问题另含其他地区，不能把仙桃归档成果用作该地区或多地区统计。'
    return None

def actual_days(product):
    raw=product.get('summary',{}).get('actual_dates') or product.get('provenance',{}).get('actual_dates') or []
    if isinstance(raw,dict):raw=[raw[key] for key in ('before','after') if raw.get(key)]
    return [str(value)[:10] for value in raw]

def reference_year(product):
    raw=product.get('summary',{}).get('reference_year')
    if isinstance(raw,int):return raw
    days=actual_days(product)
    if days:return int(days[-1][:4])
    years=re.findall(r'(?<!\d)((?:19|20|21)\d{2})(?!\d)',product.get('period_label',''))
    return int(years[0]) if len(set(years))==1 else None

def _interval(value):
    if isinstance(value,dict) and set(value)=={'start','end'}:raw=[value['start'],value['end']]
    elif isinstance(value,(tuple,list)) and len(value)==2:raw=list(value)
    else:raise ValueError()
    if any(not isinstance(v,str) or date.fromisoformat(v).isoformat()!=v for v in raw) or raw[0]>raw[1]:raise ValueError()
    return raw

def compatibility_error(product,question,context):
    """Validate all supplied source/scope/time constraints, including follow-ups."""
    address_lookup=product.get('product_type')=='public_address_clues'
    crop_reference=product.get('product_type')=='crop_reference'
    if crop_reference:
        from backend.agriculture.crop_reference_services import crop_scope_error
        problem=crop_scope_error(product,question,context)
    else:problem=scope_text_error(question,context.get('region'),address_lookup=address_lookup)
    if problem:return problem
    if address_lookup and subarea_requested(question):
        source_only=any(word in question for word in ('来源','质量','精度','依据','可信','准吗')) and not any(word in question for word in ('多少','数量','总数','统计'))
        whole_archive='全市' in question and any(word in question for word in ('已归档','已有','归档')) and any(word in question for word in ('候选','线索'))
        if not (source_only or whole_archive):return '归档地址线索未按所述乡镇或门店名称筛选，不能把全市候选当作该地点结果；请使用地图地址搜索或绑定对应台账。'
        if any(word in question for word in ('多少','总数','数量','几家','统计','全部','所有','全量')):return '地址候选不构成所述乡镇、村或主体的完整名录，不能按全市候选数量代答。'
    if not crop_reference and ('bbox' in context or 'dataset_id' in context or any(word in question for word in ('选区','选中','所选','框选','这块地','该地块','各地块','每块地','附近'))):
        return '本产品是仙桃全市参考成果；请清除选区/地块约束后查看，或对明确选区运行对应分析。'
    if context.get('source_type') not in (None,'agriculture_atlas') or context.get('source_task_id') not in (None,product['id']):
        return '明确选择的源成果与本区域归档产品不一致，未改绑其他数据。'
    if context.get('source_type')=='agriculture_atlas' and not context.get('source_task_id'):
        return '请明确选择区域成果ID，不能从来源类型推测目标产品。'
    if 'atlas_product_sha256' in context:
        if context.get('source_type')!='agriculture_atlas' or context.get('source_task_id')!=product['id']:
            return '区域版本条件必须关联同一明确区域成果，不能附加到其他来源。'
        if context['atlas_product_sha256']!=product['product_sha256']:
            return '当前详情所引用的区域产品版本已变化，请重新打开成果；未改绑更新版本。'
    if 'observation_id' in context:
        return '区域归档产品不能替代明确观测版本；请按该观测执行或移除观测约束后查看归档。'
    source_id=product.get('provenance',{}).get('source_id')
    if product['id'].startswith('xiantao-2024-'):source_id='io_lulc_2024'
    if context.get('source_id') not in (None,'auto',source_id):return '指定数据源与区域成果来源不一致，未更换来源。'
    if 'cropland_product' in context and (context['cropland_product']!='io_lulc_2024' or not product['id'].startswith('xiantao-2024-')):
        return '所选分类产品与当前区域成果不一致，未换年或换模型。'
    if any(key in context for key in ('soil_ndvi','vegetation_ndvi','field','threshold','processing_algorithm','index_id','scene_limit','cloud','direction','min_patch_pixels')):
        return '读取归档不执行额外端元、阈值或处理参数；请使用对应分析工具。'
    if product.get('product_type')=='annual_classification':
        official=any(word in question for word in ('官方','政府','自然资源部门','统计局','地籍','调查','三调','变更调查','登记','保有量','台账'))
        numeric=any(word in question for word in ('多少','几亩','几公顷','计算','统计','核算','汇总'))
        source_only=(context.get('source_type')=='agriculture_atlas' or any(word in question for word in ('这些','这份','这个','该成果','参考','所选'))) and any(word in question for word in REFERENCE) and not numeric
        if official and not source_only:return '年度土地覆盖分类是公开模型参考，不能提供官方耕地/农田面积或地籍、土地、耕地调查、三调及变更调查口径；请接入对应权威调查或台账。'
    indices=detect_indices(question);expected=product.get('summary',{}).get('index_id')
    if indices and (len(indices)!=1 or indices[0]!=expected):return '指定指数与原区域产品不一致，不能读取另一指数代答。'
    if unsupported_index_requested(question):return '问题中的指数没有相应区域归档，不会改用其他指数。'
    if product.get('product_type')=='city_spectral_reference' and any(word in question for word in ('农田','耕地','耕作地')) and any(word in question for word in ('面积','多少亩','标注','图斑','圈出')):
        return '光谱指数不是耕作地类别，不能用此产品回答农田面积或标注。'
    ratio_words=('占比','比例','比重','百分比','百分率','百分之','几成','覆盖率')
    if any(word in question for word in ratio_words):
        coverage=any(word in question for word in ('有效覆盖','有效分类覆盖','有效像元','有效率'))
        declared_ratio=any(any(word in str(metric.get('label','')) for word in ratio_words) and '覆盖' not in str(metric.get('label','')) for metric in product.get('summary_metrics',[]))
        if not coverage and not declared_ratio:return '当前归档问答未提供这类类别占比答案；有效覆盖率不是农田或地类占比，不能以100%覆盖率代答。请查看类别面积统计核对。'
    if product.get('product_type')=='city_spectral_reference' and (any(word in question for word in ('全年','逐月','每月','逐日','每天','日均','季度','各季','整季','季均','季平均','整个秋季','整个冬季','整个春季','整个夏季','整个生长季','完整生长季','全生育期')) or re.search(r'q[1-4]',question,re.I)):
        return '单景或既有双期归档不能满足全年、逐月、季度或完整季节的时间统计；请使用明确时段的多景分析。'
    if any(word in question for word in ('最大值','最小值','中位数','分位数','标准差','阈值','超过','低于','高于','大于','小于','不低于','不高于','>=','<=','≥','≤','>','<','排名','排序','月平均','月均','年平均','年均','时序平均')):
        return '本次只读取归档指标，不执行新的统计、阈值或合成条件；请使用相应分析工具。'
    if any(word in question for word in ('精度','准确率','误差','置信区间')) and any(word in question for word in ('多少','计算','估算','评估','校正','置信区间')):
        return '现有归档没有仙桃本地独立样本精度、面积误差校正或置信区间，不能以有效覆盖率或计算一致性代答。'
    quality=any(word in question for word in REFERENCE)
    if any(word in question for word in ('今年','去年','最新','实时','当前','现在','现状','今日','当期')) and not (quality and any(word in question for word in ('当前可用','目前可用','这些数据','该结果'))):
        return '归档成果按明确年度或实际观测日提供，不能代替最新、实时或当前业务实况。'
    days=actual_days(product);year=reference_year(product)
    if product.get('product_type')=='official_project_notice':
        days=[str(source['published_at'])[:10] for source in product.get('provenance',{}).get('sources',[]) if isinstance(source,dict) and source.get('published_at')][:1]
    elif product.get('product_type')=='public_address_clues' and product.get('provenance',{}).get('fetched_at'):
        days=[str(product['provenance']['fetched_at'])[:10]]
    boundary_question=quality and any(word in question for word in ('市界','边界'))
    years=set(re.findall(r'(?<!\d)((?:19|20|21)\d{2})(?!\d)',question))
    allowed={str(year)} if year else set()
    if boundary_question:allowed.add(str(product.get('provenance',{}).get('boundary_year',2017)))
    if years and not years.issubset(allowed):return '所请求年度与归档产品或实际观测年度不一致，未切换年份代答。'
    try:requested=explicit_dates(question)
    except GeoError as error:return error.message
    months=re.findall(r'(?:(\d{4})年\s*)?(\d{1,2})月',question)
    if (requested or months) and not days:return '年度分类或非观测资料没有该月日的遥感观测，不能用年度产品代替。'
    if requested:
        if len(requested)==1 and (len(days)!=1 or requested[0]!=days[0]):return '所请求实际日期与归档观测不一致。'
        if len(requested)==2 and (requested[0]>requested[1] or (days!=requested if len(days)==2 else not all(requested[0]<=d<=requested[1] for d in days))):return '指定日期或前后期与归档实际观测不一致。'
        if len(requested)>2:return '日期条件超出这份归档观测，请明确单日或已归档的前后两日。'
    elif months:
        requested_months=[];current=next(iter(years)) if len(years)==1 else None
        for specified,month in months:
            current=specified or current
            if current is None:return '月份缺少年份，不能推定归档观测年度。'
            if not 1<=int(month)<=12:return '问题中的月份无效。'
            requested_months.append(current+'-'+month.zfill(2))
        if [day[:7] for day in days]!=requested_months:return '所请求月份与归档实际观测不一致。'
    try:
        if 'period' in context:
            interval=_interval(context['period'])
            if days:
                if not all(interval[0]<=d<=interval[1] for d in days):return '所选观测窗口未包含归档实际观测，不使用其他时相代答。'
            elif year is None or interval!=[str(year)+'-01-01',str(year)+'-12-31']:
                return '年度分类只对应明确全年，不代表所选月份或日期窗口。'
        if 'periods' in context:
            periods=context['periods']
            if len(days)!=2 or not isinstance(periods,dict) or set(periods)!={'before','after'}:return '前后期条件与本产品的观测类型不一致。'
            intervals=[_interval(periods[key]) for key in ('before','after')]
            if intervals[0][1]>=intervals[1][0] or any(not interval[0]<=day<=interval[1] for day,interval in zip(days,intervals)):
                return '所选前后期窗口与归档实际观测不一致。'
    except (ValueError,TypeError,OverflowError):return '所选时间窗口格式无效，请提供明确日历日期。'
    return None

def _blocked(question,context,message,intent='regional_product_read'):
    return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':intent,'status':'needs_data','missing_fields':[],'blocking_gaps':[message],'clarification_message':message,'executable':False,'planner':'bounded_rules_no_general_llm'}

def _primary_read_requested(question):
    """Keep a substantive result request ahead of an appended source request.

    A source/download clause containing an area or product name is still only
    a reference request. Detect a separate result clause, not those nouns alone.
    Scope, dates and scientific definitions are checked before this preference.
    """
    clauses=re.split(r'[，,。！？?!；;\n]|并且|以及|同时|并|(?:和|及|与)(?=.{0,6}(?:来源|依据|质量|精度|下载|导出))',question)
    for clause in clauses:
        if any(word in clause for word in REFERENCE):continue
        if any(word in clause for word in ('多少','几亩','几公顷','几平方','几家')):return True
        target=any(word in clause for word in ('面积','分布','构成','均值','平均值','指标','图层','统计结果'))
        action=any(word in clause for word in ('查看','查询','展示','显示','给出','提供','返回','打开','看看','了解','列出','汇总','统计','计算','核算'))
        if target and action:return True
    return False

def reference_plan(reference,question,context):
    if (reference.get('binding') or {}).get('type')!='agriculture_atlas':
        if context.get('source_type')=='agriculture_atlas' or 'atlas_product_sha256' in context:return _blocked(question,context,'区域详情与原执行引用不是同一来源，不能读取隐藏的旧问题代答。')
        return None
    if is_recompute(question) or not any(word in question for word in REFERENCE):return None
    if re.match(r'^(?:请|帮我|替我|麻烦)?\s*(?:计算|估算|分析|核算|统计)',question):
        return _blocked(question,context,'引用原执行只供查看来源与产物；新的分析请求不能用读取原答案代替，请明确计算条件。')
    try:product=verified_product(reference['result'])
    except (GeoError,KeyError,TypeError):return _blocked(question,context,'原区域执行缺少可核验产品快照；请重新选择成果，未改绑新版本。')
    if reference.get('status')!='succeeded' or product['product_sha256']!=(reference.get('result_ref') or {}).get('product_sha256'):
        return _blocked(question,context,'引用的区域执行尚未成功或内容指纹不一致。')
    problem=compatibility_error(product,question,context)
    intent='download_results' if any(word in question for word in ('下载','导出')) else 'source_quality'
    if problem:return _blocked(question,context,problem,intent)
    return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':intent,'status':'ready','missing_fields':[],'blocking_gaps':[],'executable':True,'force':False,'tool':{'id':'result_reference','version':'execution-reference-v1','inputs':{'execution_id':reference['id'],'operation':intent}},'plan_ref':reference.get('plan_ref'),'source_snapshot':{'reference_execution_id':reference['id'],'binding':copy.deepcopy(reference['binding']),'product_sha256':product['product_sha256']},'planner':'bounded_rules_no_general_llm'}

def prepare_regional(store,question,context):
    atlas=getattr(store,'agriculture_atlas',None)
    if atlas is None:return None
    selected=context.get('source_type')=='agriculture_atlas'
    if not selected and '仙桃' not in question and context.get('region') not in REGIONS:
        if not context.get('reference_execution_id') and any(word in question for word in REFERENCE) and atlas.list().get('products'):
            return _blocked(question,context,'请明确要查看来源或下载的成果；可从当前地图/成果详情进入，或选择一条已完成分析，不能默认绑定另一份2021基准。')
        return None
    products=atlas.list().get('products',[])
    if not products and not selected:return None
    if 'bbox' in context and not selected and not any(word in question for word in ('全市','整市','全省','全国','省域')):return None
    years=set(re.findall(r'(?<!\d)((?:19|20|21)\d{2})(?!\d)',question))
    if years=={'2021'} and not selected:return None
    if any(word in question for word in ('分区统计','按地块统计','逐地块','覆盖度','FVC','fvc','Otsu','otsu','合成','趋势','端元','检索影像','搜索影像','卫星目录')):return None
    if any(word in question for word in ('选区','选中','所选','框选','这块地','该地块')) and not any(word in question for word in ('全市','整市')):return None
    forbidden=('产量','减产','灾损','受灾面积','播种面积','违法','执法认定','确权','权属','永久基本','保有量','法定','水稻','小麦','玉米','油菜','棉花','土壤含水率','水质','达标','交易额','冷库容量','实际建成','完成面积','验收面积','病虫害','污染','氨氮','安全监管','作业轨迹','作业面积','延包','灌溉处方','旱情','灾害等级')
    if any(word in question for word in forbidden):return None
    lowered=question.lower();ident=context.get('source_task_id') if selected else None
    if ident is None:
        if '高标准农田' in question:
            if not any(word in question for word in ('公告','计划','招标')):return None
            ident='xiantao-high-standard-2026'
        elif any(word in question for word in ('跨源','两产品','产品核查')):
            if any(word in question for word in ('增减','减少','增加','变成','减少了')):return None
            ident='xiantao-cropland-review'
        elif any(word in lowered for word in ('ndvi','ndmi','mndwi')) or any(word in question for word in ('植被','长势','光谱')):
            indices=detect_indices(question)
            if len(indices)>1 or any(index not in ('ndvi','ndmi','mndwi') for index in indices) or unsupported_index_requested(question):return None
            index=indices[0] if indices else 'ndvi'
            if index=='ndvi' and any(word in question for word in ('变化','差值','后减前')):index='change'
            ident='xiantao-2026-spectral-'+index
        elif any(word in question for word in ('农资','养殖','黄鳝','冷链','农机')):
            category=next((key for word,key in [('农资','supplies'),('养殖','breeding'),('黄鳝','eel'),('冷链','coldchain'),('农机','machinery')] if word in question),None)
            if any(word in question for word in ('面积','容量','库存','交易','排放','水质')):return _blocked(question,context,'公开地址候选不提供面积、容量、交易或环境监管指标，不能用地址条数代答。')
            if category=='machinery' and any(word in question for word in ('设备','北斗','机具','实时位置')) and not any(word in question for word in ('维修','门店','经销','销售','服务地址','服务点','服务站')):
                return _blocked(question,context,'农机服务地址不是设备位置或北斗轨迹；请接入真实设备和作业台账。')
            if not any(word in question for word in ('地址','门店','位置','分布','候选','线索','服务点','在哪','在哪里')):return None
            if any(word in question for word in ('总数','所有','全部','全量')):return _blocked(question,context,'地址关键词候选不构成全量主体名录，无法给出全市机构总数。')
            ident='xiantao-poi-'+category
        elif any(word in question for word in ('土地覆盖','地类','土地类型','土地构成')) or (any(word in question for word in ('农田','耕地','耕作地')) and any(word in question for word in ('水体','水域','树木'))):ident='xiantao-2024-classification'
        elif any(word in question for word in ('水域','水体')):
            if '永久' in question:return None
            ident='xiantao-2024-water'
        elif any(word in question for word in ('树木','树冠','林冠')):ident='xiantao-2024-trees'
        elif any(word in question for word in ('农田','耕地','耕作地')):
            if any(word in question for word in ('增加','减少','变化','增减','排名','高标准','永久','标注','圈出','勾画','提取','图斑')):return None
            ident='xiantao-2024-cropland'
    if not ident:return None
    if is_recompute(question):return _blocked(question,context,'这是已有区域成果。全市重新生产尚未登记为此问询工具；请明确产品、范围和生产任务，不会把读取归档当重算。')
    if context.get('reference_execution_id'):return _blocked(question,context,'本问题仍带原执行引用；请明确查看原结果或移除引用后提出新的区域需求。')
    try:product=verified_product(atlas.get(ident))
    except GeoError as error:return _blocked(question,context,error.message)
    problem=compatibility_error(product,question,context)
    if problem:return _blocked(question,context,problem)
    intent='regional_product_read' if _primary_read_requested(question) else 'download_results' if any(word in question for word in ('下载','导出')) else 'source_quality' if any(word in question for word in REFERENCE) else 'regional_product_read'
    return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':intent,'status':'ready','missing_fields':[],'blocking_gaps':[],'executable':True,'force':False,'tool':{'id':'regional_product_read','version':VERSION,'inputs':{'product_id':product['id'],'product_sha256':product['product_sha256']}},'plan_ref':None,'source_snapshot':{'product_id':product['id'],'product_sha256':product['product_sha256'],'period_label':product.get('period_label'),'scope_label':product.get('scope_label')},'planner':'bounded_rules_no_general_llm'}

def read_product(store,inputs):
    product=verified_product(store.agriculture_atlas.get(inputs['product_id']))
    if product['product_sha256']!=inputs['product_sha256']:
        raise GeoError('REGIONAL_PRODUCT_VERSION_CHANGED','规划后区域产品版本发生变化，未读取替代版本；请重新选择成果',409)
    return product
