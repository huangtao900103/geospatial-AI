"""Conservative language adapter for registered multi-tool analysis templates.

The question selects a fixed workflow. It never supplies executable code or
arbitrary bindings. Real inputs and their content versions are frozen by the
workflow service before any child computation starts.
"""
import copy
import re

from backend.core.geo_services import GeoError
from backend.analysis.spectral_indices import choose_index
from backend.analysis.production_services import parse_periods
from backend.workflows.execution_services import temporal_context_conflict


def prepare_workflow_question(store, question, context):
    """Return None for single-tool/knowledge requests, otherwise a typed plan."""
    if not getattr(store, 'analysis_workflows', None):return None
    lower=question.lower()
    partition='otsu' in lower or any(t in question for t in ('自动阈值分区','自适应阈值分区'))
    knowledge=re.search(r'什么是|是什么意思|如何使用|怎么使用|原理|公式|介绍|解释|能否实现|可以实现吗',question)
    chained=bool(context.get('observation_id')) or bool(re.search(r'先.+(?:再|然后)|(?:计算|生成|生产|获取|读取).+(?:并|再|然后|后).*(?:otsu|阈值分区)|连续处理|连续分析',lower))
    if not partition or not chained or knowledge:return None
    def blocked(message):return {'executable':False,'status':'needs_data','message':message,'intent':'spectral_partition'}
    allowed={'bbox','period','source_type','source_task_id','source_id','observation_id','index_id',
             'processing_algorithm','direction','min_patch_pixels','region','clarification_of'}
    if set(context)-allowed:return blocked('连续分析收到其他专题或历史引用条件，请明确本次影像及选区，不会忽略已有条件。')
    if any(word in question for word in ('全市','全省','全县','全国','省域','整市','农田','耕地','水稻','小麦','玉米','产量','灾损','法定','确权','水体面积','水域面积','分类','预测','fvc','FVC','合成','时序')):
        return blocked('本流程计算单期光谱指数及自动阈值分区，不能代替农田或水体分类、全域统计、灾损认定或其他未登记的后续算法。')
    if context.get('processing_algorithm') not in (None,'otsu_segmentation'):
        return blocked('已选择的处理算法与自动阈值分区不一致。')
    if re.search(r'(?:阈值|大于|小于|高于|低于|>=|<=|>|<|≥|≤)\s*(?:为|是|=|：|:)?\s*-?\d',question):
        return blocked('自动阈值由实际像元分布计算；手工阈值不能作为Otsu输入。')
    try:index=choose_index(question,context.get('index_id'))['id']
    except GeoError as error:return blocked(error.message)
    if index not in ('ndvi','ndwi','ndmi'):return blocked('当前连续处理支持NDVI、NDWI和NDMI，请分别运行其他已接入的单项指数。')
    direction=context.get('direction','above')
    direction_text=question.replace('不低于自动阈值','保留高值')
    for terms,value in ((('低于自动阈值','保留低值'),'below'),(('不低于自动阈值','保留高值'),'above')):
        if any(term in direction_text for term in terms):
            if 'direction' in context and context['direction']!=value:return blocked('问题中的分区方向与所选条件不一致。')
            direction=value
    minimum=context.get('min_patch_pixels',1)
    size=re.search(r'最小斑块\s*(?:为|是|=|：|:)?\s*(\d+)\s*(?:个)?像元',question)
    if size:
        value=int(size.group(1))
        if 'min_patch_pixels' in context and minimum!=value:return blocked('问题中的最小斑块与所选条件不一致。')
        minimum=value
    inputs={key:copy.deepcopy(context[key]) for key in ('bbox','period','source_type','source_task_id','source_id','observation_id') if key in context}
    if inputs.get('source_task_id'):
        # Reuse the mature source/year/sensor/index/extent validation contract.
        existing=store.executions.prepare_processing(question,{**inputs,'index_id':index,'direction':direction,'min_patch_pixels':minimum},'adaptive_segmentation')
        if not existing.get('executable'):return blocked(existing.get('clarification_message') or '；'.join(existing.get('blocking_gaps',[])))
    else:
        if not inputs.get('observation_id'):return blocked('请从影像检索中选择并导入一景真实观测，或选择已有成功的单期指数成果；连续处理将沿用该来源。')
        if 'bbox' not in inputs:return blocked('请在地图明确选择分析范围，不能用整景范围代替业务选区。')
        if 'period' not in inputs:
            try:periods,defaulted=parse_periods(question,None,'ndvi')
            except GeoError as error:return blocked(error.message)
            if defaulted:return blocked('请明确含年份的观测开始和结束日期。')
            inputs['period']={'start':periods['after'][0],'end':periods['after'][1]}
        try:
            period=inputs['period']
            conflict=temporal_context_conflict(question,{'period':period},'ndvi',{'after':[period['start'],period['end']]})
            if conflict:return blocked(conflict)
        except (KeyError,TypeError):return blocked('观测时段须包含明确的start和end日期。')
        if re.search(r'今年|去年|最新|实时|当期',question):return blocked('请使用明确的观测日期，归档影像不能代表当前实况。')
    body={'template_id':'spectral_partition','context':inputs,'parameters':{'index_id':index,'direction':direction,'min_patch_pixels':minimum}}
    try:plan=store.analysis_workflows.prepare(body)
    except (GeoError,ValueError,TypeError) as error:return blocked(getattr(error,'message',str(error)))
    source_node=plan['nodes'][0]
    source=source_node.get('frozen_source') or source_node.get('frozen_plan') or {}
    source_id=source.get('source_id')
    named_source='pc_landsat_c2_l2' if re.search(r'landsat|陆地卫星',lower) else 'pc_sentinel2_l2a' if re.search(r'sentinel\s*[-_]?\s*2|哨兵\s*2',lower) else None
    if named_source and source_id!=named_source:return blocked('问题指定的传感器与固定影像来源不同，未更换数据源。')
    if re.search(r'sentinel\s*[-_]?\s*1|哨兵\s*1|modis|高分|天地图|worldcover',lower):
        return blocked('该影像来源不在本流程已登记的光谱像元适配范围内，未使用其他传感器代答。')
    if re.search(r'云量\s*(?:上限|不高于|低于|小于|≤|<=)?\s*\d',question):
        return blocked('连续处理沿用已选影像版本，不重新按云量筛景；请先在影像检索中按云量条件选择观测。')
    return {'executable':bool(plan.get('executable')),'status':plan.get('status','ready'),'message':plan.get('message',''),
            'intent':'spectral_partition','body':body,'plan':plan}
