"""Bounded reads of existing city observation series, never a new analysis."""
import calendar
import copy
import math
import re
from datetime import date

from backend.core.geo_services import GeoError
from backend.agriculture.regional_product_tools import REGIONS, explicit_dates, scope_text_error
from backend.analysis.spectral_indices import detect_indices, unsupported_index_requested

VERSION = 'agri-series-read-1.0'
TERMS = ('时序', '观测序列', '长势曲线', '水分曲线', '时间序列')
OPERATIONS = ('合成','ols','趋势','分析','拟合','回归','斜率','重算','重跑','重做',
              '重新计算','重新分析','重新生产','重新处理','分类','识别','标注','预测','插值',
              '计算','统计','提取','下载新影像','获取新影像','重新获取','生产时序','生成','构建','制作')
CONTEXT_KEYS = {'region','bbox','period','periods','dataset_id','source_id','source_type','source_task_id',
                'atlas_product_sha256','reference_execution_id','observation_id','cropland_product',
                'soil_ndvi','vegetation_ndvi','field','threshold','processing_algorithm','index_id',
                'scene_limit','cloud','direction','min_patch_pixels'}
SHA = re.compile(r'[a-f0-9]{64}')


def _plan(question, context, message=None, inputs=None, series=None, requested_index='ndvi'):
    snapshot = {'requested_index':requested_index}
    if series is not None:snapshot['research_series'] = copy.deepcopy(series)
    return {'version':VERSION,'question':question,'context':copy.deepcopy(context),'intent':'agri_series_read',
            'status':'needs_data' if message else 'ready','missing_fields':[],
            'blocking_gaps':[message] if message else [],'clarification_message':message,
            'executable':message is None,'force':False,
            'tool':{'id':'agri_series_read','version':VERSION,'inputs':inputs} if inputs else None,
            'plan_ref':None,'source_snapshot':snapshot,'planner':'bounded_rules_no_general_llm'}


def _period(value):
    if not isinstance(value,dict) or set(value) != {'start','end'}:raise ValueError()
    start,end = value['start'],value['end']
    if not isinstance(start,str) or not isinstance(end,str) or date.fromisoformat(start).isoformat()!=start or date.fromisoformat(end).isoformat()!=end or start>end:raise ValueError()
    return {'start':start,'end':end}


def _time_constraint(question, context):
    """Return explicit years/full window; arbitrary subsetting is not performed."""
    years = set(re.findall(r'(?<!\d)((?:19|20|21)\d{2})(?!\d)',question))
    window = None
    days = explicit_dates(question)
    if days:
        if len(days)!=2 or not re.search(r'至|到|—|–|~|～|\s-\s',question):
            raise ValueError('当前只读取整段归档时序，不按单个指定观测日或离散日期改绑地图。')
        window = _period({'start':days[0],'end':days[1]})
    else:
        match = re.search(r'(?<!\d)(?:(\d{4})年\s*)?(\d{1,2})月?\s*(?:至|到|[-—–~～])\s*(?:(\d{4})年\s*)?(\d{1,2})月',question)
        iso = re.search(r'(?<!\d)(\d{4})-(\d{2})\s*(?:至|到|—|–|~|～)\s*(\d{4})-(\d{2})(?![-\d])',question)
        if match or iso:
            if iso:y1,m1,y2,m2=iso.groups()
            else:y1,m1,y2,m2=match.groups();y1=y1 or (next(iter(years)) if len(years)==1 else None);y2=y2 or y1
            if y1 is None or y2 is None:raise ValueError('月份区间缺少明确年份，不能推定为归档年度。')
            y1,m1,y2,m2=map(int,(y1,m1,y2,m2))
            window=_period({'start':date(y1,m1,1).isoformat(),'end':date(y2,m2,calendar.monthrange(y2,m2)[1]).isoformat()})
        elif re.search(r'月|(?<!\d)\d{4}-\d{2}(?![-\d])',question):
            raise ValueError('尚未支持该月份或区间筛选；请查看完整归档时段，不能用其他月份代答。')
    if 'period' in context:
        selected = _period(context['period'])
        if window and selected!=window:raise ValueError('问题与所选时间区间冲突，未替换原时段。')
        window=selected
    return years,window


def _observations(series, index):
    rows=[]
    for item in series.get('observations',[]):
        day=item.get('date');mean=item.get(index+'_mean');count=item.get(index+'_valid_pixel_count')
        if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:raise ValueError('归档观测日期无效。')
        if isinstance(mean,(int,float)) and not isinstance(mean,bool) and math.isfinite(mean) and isinstance(count,(int,float)) and not isinstance(count,bool) and math.isfinite(count) and count>0:
            rows.append(item)
    return sorted(rows,key=lambda item:(item['date'],item.get('scene_id','')))


def _binding(series):
    rows=_observations(series,'ndvi')
    if not rows:raise ValueError('归档没有可映射的有效NDVI观测，未使用其他日期或产品代替。')
    latest=rows[-1];publication=series.get('provenance',{}).get('publication',{})
    mapping=publication.get('observations',{}).get(latest.get('scene_id'))
    if not isinstance(mapping,dict) or not isinstance(mapping.get('product_id'),str) or not SHA.fullmatch(str(mapping.get('product_sha256',''))):
        raise ValueError('最新有效观测缺少已发布地图及版本映射，未回退其他日期。')
    for key in ('product_id','product_sha256'):
        if latest.get(key) not in (None,mapping[key]):raise ValueError('归档观测与发布地图版本映射不一致。')
    return mapping['product_id'],mapping['product_sha256']


def _compatible(series, question, context, years, window, requested_index):
    if series.get('scope',{}).get('region') not in REGIONS:return '归档实际行政范围不是仙桃市，未使用该归档替代。'
    period=_period(series.get('period'))
    if window and window!=period:return '所请求区间与归档完整时段不一致；本工具不筛选月份、补日期或切换时段。'
    observed=series.get('observations',[])
    actual_years={str(item.get('date',''))[:4] for item in observed}
    if years and (len(years)!=1 or actual_years!=years):return '尚无与指定年度一致的仙桃归档时序，未切换年份代答。'
    platforms={item.get('platform') for item in observed}
    if len(platforms)!=1 or not platforms.issubset({'landsat-8','landsat-9'}):return '归档须为明确单一Landsat平台；混合平台或不明来源不能代作同源时序。'
    if context.get('source_id') not in (None,'auto','pc_landsat_c2_l2'):return '指定数据源与归档Landsat来源不一致，未更换数据源。'
    if re.search(r'sentinel|哨兵|modis|高分|worldcover|天地图|gee|earth\s*engine|io[_ -]?lulc',question,re.I):return '指定数据源尚无对应归档时序，不能用Landsat归档代替。'
    named=re.findall(r'landsat\s*[-_]?\s*(\d+)',question,re.I)
    if named and platforms!={'landsat-'+number for number in named}:return '指定Landsat卫星与归档实际平台不一致，未替换传感器。'
    if len({item['date'] for item in _observations(series,requested_index)})<2:return '所请求指标不足两个有效观测日期，不能把单景当作曲线。'
    return None


def prepare_series_read(store, question, context):
    """Prepare an immutable archive read or return None for other operations."""
    if getattr(store,'agri_research',None) is None:return None
    if not isinstance(question,str) or not any(term in question for term in TERMS):return None
    if any(word in question.lower() for word in OPERATIONS):return None
    if not isinstance(context,dict):return _plan(question,{},'上下文须为明确业务条件。')
    index='ndmi' if '水分曲线' in question or 'ndmi' in question.lower() else 'ndvi' if 'ndvi' in question.lower() or '长势曲线' in question else context.get('index_id') or 'ndvi'
    blocked=lambda message:_plan(question,context,message,requested_index=index)
    if set(context)-CONTEXT_KEYS:return blocked('读取时序不接受未识别的范围、日期或处理条件。')
    problem=scope_text_error(question,context.get('region'))
    if problem:return blocked(problem)
    if '仙桃' not in question and not ('全市' in question and context.get('region') in REGIONS):return blocked('请明确仙桃市或在仙桃地区上下文中查看全市归档时序。')
    whole=any(word in question for word in ('全市','整市','仙桃全域','仙桃市全域'))
    if ('bbox' in context or any(word in question for word in ('框选','选区','选中','所选','这块地','该地块'))) and not whole:return blocked('当前带有框选或地块范围，不能用全市时序替代；请明确查看仙桃全市。')
    if any(key in context for key in ('dataset_id','periods','reference_execution_id','observation_id','cropland_product','soil_ndvi','vegetation_ndvi','field','threshold','processing_algorithm','scene_limit','cloud','direction','min_patch_pixels')):
        return blocked('读取归档不替代明确数据、原执行、地块、前后期或处理参数；请移除冲突条件或执行对应分析。')
    if any(word in question for word in ('农田','耕地','水稻','油菜','小麦','玉米','棉花','土壤含水率')):return blocked('现有全市指数时序尚未按耕地或具体作物筛选，不能作为该类别或土壤实测时序。')
    indices=detect_indices(question)
    if unsupported_index_requested(question) or any(value not in ('ndvi','ndmi') for value in indices) or context.get('index_id') not in (None,index):return blocked('指定指标与NDVI/NDMI归档曲线不一致，不会改用另一指数代答。')
    if '水分曲线' in question and indices==['ndvi']:return blocked('水分曲线与明确NDVI指标冲突；请明确查看NDMI水分线索或NDVI长势。')
    if any(word in question for word in ('全年','整年','逐日','每日','每天','逐月','每月','按月','季度','平均','均值','最大值','最小值','中位数','阈值','准确率','置信区间')):return blocked('现有代表观测归档不提供完整逐月逐日、合成统计或独立精度估计，不能读取归档冒充该计算。')
    relative=any(word in question for word in ('今年','去年','本月','上月','实时','今日','现在','当前','最近','最新'))
    if relative and not any(word in question for word in ('已归档','已有归档','当前归档','现有归档','最新归档')):return blocked('请明确归档年度或查看已有归档；代表观测不能代替实时或当前业务实况。')
    try:years,window=_time_constraint(question,context)
    except (GeoError,ValueError,TypeError,OverflowError) as error:return blocked(getattr(error,'message',str(error)) or '时间条件无效，请使用明确年份及完整归档区间。')
    research=getattr(store,'agri_research',None);atlas=getattr(store,'agriculture_atlas',None)
    if research is None or atlas is None:return blocked('尚未接入可核验的归档时序及发布地图服务。')
    try:catalog=research.catalog()
    except (GeoError,OSError,ValueError,TypeError) as error:return blocked('归档时序目录暂不可核验：'+getattr(error,'message',str(error)))
    candidates=[];gaps=[]
    for entry in catalog.get('series',[]):
        try:
            series=research.get(entry['id'])
            if series.get('status') not in ('ready','insufficient_dates') or not SHA.fullmatch(str(series.get('series_sha256',''))):raise ValueError('归档尚未发布有效内容版本。')
            problem=_compatible(series,question,context,years,window,index)
            if problem:gaps.append(problem);continue
            candidates.append(series)
        except (GeoError,OSError,ValueError,TypeError,KeyError) as error:gaps.append(getattr(error,'message',str(error)))
    if not candidates:return blocked('；'.join(dict.fromkeys(gaps)) or '尚无满足区域、时段和指标的已发布归档时序。')
    series=max(candidates,key=lambda value:(value['period']['end'],value['period']['start'],value['id']))
    try:
        product_id,product_sha=_binding(series)
        source_type=context.get('source_type');selected=context.get('source_task_id')
        if source_type not in (None,'agri_research','agri_research_series','agriculture_atlas'):return blocked('明确选择的来源不是本归档时序，未替换所选数据。')
        expected_id=product_id if source_type=='agriculture_atlas' else series['id']
        if selected is not None and (source_type is None or selected!=expected_id):return blocked('所选来源ID与时序或最新发布地图不一致，未改绑。')
        if source_type is not None and selected is None:return blocked('来源类型缺少明确来源ID，不能推测目标时序。')
        if 'atlas_product_sha256' in context and (source_type!='agriculture_atlas' or selected!=product_id or context['atlas_product_sha256']!=product_sha):return blocked('明确地图版本与归档发布映射不一致，未替换版本。')
        atlas.require_version(product_id,product_sha)
    except (GeoError,ValueError,TypeError,KeyError) as error:return blocked(getattr(error,'message',str(error)) or '归档地图发布版本不可核验。')
    inputs={'series_id':series['id'],'series_sha256':series['series_sha256'],'product_id':product_id,'product_sha256':product_sha}
    return _plan(question,context,inputs=inputs,series=series,requested_index=index)


def read_series_product(store, inputs):
    """Recheck both immutable bindings and return the untouched Atlas product."""
    required={'series_id','series_sha256','product_id','product_sha256'}
    if not isinstance(inputs,dict) or set(inputs)!=required or any(not isinstance(inputs[key],str) or not inputs[key] for key in required) or any(not SHA.fullmatch(inputs[key]) for key in ('series_sha256','product_sha256')):
        raise GeoError('INVALID_SERIES_READ','时序读取须绑定明确时序和地图内容版本',400)
    series=store.agri_research.get(inputs['series_id'],inputs['series_sha256'])
    if series.get('id')!=inputs['series_id'] or series.get('series_sha256')!=inputs['series_sha256'] or series.get('scope',{}).get('region') not in REGIONS:
        raise GeoError('SERIES_READ_VERSION_CHANGED','时序范围或版本发生变化，未改绑新版本',409)
    try:product_id,product_sha=_binding(series)
    except (ValueError,TypeError,KeyError) as error:raise GeoError('SERIES_READ_MAPPING_CHANGED',str(error),409)
    if (product_id,product_sha)!=(inputs['product_id'],inputs['product_sha256']):raise GeoError('SERIES_READ_MAPPING_CHANGED','最新有效观测的发布地图映射发生变化，未读取其他日期',409)
    return store.agriculture_atlas.require_version(product_id,product_sha)
