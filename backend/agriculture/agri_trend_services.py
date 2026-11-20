"""Bounded Sen diagnostics of immutable, common-support observation series.

This service reads archived scalar evidence only. It neither fetches imagery nor
creates a pixelwise trend map, fills gaps, or makes agricultural loss claims.
"""
import copy
import math
import re
import statistics
import uuid
from datetime import date, datetime, timezone

from backend.agriculture.agri_research_services import SLUG, digest, now
from backend.core.geo_services import GeoError

VERSION = 'agri-sen-trend-1.0'
KIND = 'agri_trend'
TOOL_ID = 'agri_series_trend'
SHA = re.compile(r'[a-f0-9]{64}')
MIN_DATES = 3
MAX_OBSERVATIONS = 128
LIMITATIONS = [
    '仅描述归档实际日期在固定共同有效范围内的指数均值变化，不是全域逐像元趋势图。',
    '未按耕地或具体作物筛选，不能作为农田面积、作物识别、产量、灾损或土壤实测结论。',
    '代表观测不等于完整逐日、逐月监测；缺测不补零、不插值。',
    '30日变化是按日斜率乘30的速率表达，不是未来30日预测或实际30日观测差值。',
    '未评估季节、自相关、显著性或独立精度；短期绿起和回落可能是正常物候，不能解释为年度或年际趋势。',
]


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
        return date.fromisoformat(value)
    except (ValueError, TypeError, OverflowError):
        raise GeoError('TREND_INVALID_DATE', '归档须包含规范、独立的实际UTC观测日期', 422)


def _require(condition, message, code='TREND_SOURCE_INVALID', status=422):
    if not condition:
        raise GeoError(code, message, status)


def _inputs(body):
    _require(isinstance(body, dict) and set(body) == {'series_id', 'series_sha256', 'index_id'},
             '趋势分析须明确且仅提供series_id、series_sha256和index_id', 'INVALID_TREND_INPUT', 400)
    _require(isinstance(body['series_id'], str) and SLUG.fullmatch(body['series_id']),
             '时序标识无效', 'INVALID_TREND_INPUT', 400)
    _require(isinstance(body['series_sha256'], str) and SHA.fullmatch(body['series_sha256']),
             '趋势分析须绑定明确时序内容版本', 'INVALID_TREND_INPUT', 400)
    _require(body['index_id'] in ('ndvi', 'ndmi'),
             '归档稳健趋势只支持NDVI或NDMI', 'INVALID_TREND_INPUT', 422)
    return copy.deepcopy(body)


def _freeze(series, request):
    """Validate scalar evidence without opening imagery or replacing its support."""
    _require(series.get('id') == request['series_id'] and series.get('series_sha256') == request['series_sha256'],
             '时序身份或内容版本发生变化，未使用其他版本代替', 'SERIES_VERSION_CHANGED', 409)
    _require(series.get('status') in ('ready', 'insufficient_dates'), '源时序未发布')
    observations = series.get('observations')
    _require(isinstance(observations, list) and 1 <= len(observations) <= MAX_OBSERVATIONS,
             '归档观测数量超出轻量诊断范围')
    _require(all(isinstance(row, dict) for row in observations), '归档观测结构无效')
    days = [_day(row.get('date')) for row in observations]
    _require(len(set(days)) == len(days), '同一UTC日期不能重复计为独立观测', 'TREND_DUPLICATE_DATE')
    _require(days == sorted(days), '归档实际日期无序，不能重排后冒充原归档', 'TREND_INVALID_DATE')
    for row, day in zip(observations, days):
        if row.get('datetime') is not None:
            try:
                timestamp = datetime.fromisoformat(row['datetime'].replace('Z', '+00:00'))
                valid = timestamp.tzinfo is not None and timestamp.astimezone(timezone.utc).date() == day
            except (ValueError, TypeError, AttributeError, OverflowError):
                valid = False
            _require(valid, '归档日期与实际带时区UTC观测时间不一致', 'TREND_INVALID_DATE')
    _require(all(isinstance(row.get('platform'), str) for row in observations), '观测缺少明确传感器平台')
    platforms = {row['platform'] for row in observations}
    _require(len(platforms) == 1 and platforms.issubset({'landsat-8', 'landsat-9'}) and
             series.get('platform') in platforms and series.get('source_id') == 'pc_landsat_c2_l2',
             '须使用明确同一Landsat平台的原生归档，不混合或替换传感器', 'TREND_SENSOR_MISMATCH')
    period = series.get('period')
    _require(isinstance(period, dict), '归档缺少完整时段')
    first, last = _day(period.get('start')), _day(period.get('end'))
    _require(first <= days[0] <= days[-1] <= last, '实际日期超出冻结归档时段')
    grid = series.get('grid')
    _require(isinstance(grid, dict) and grid.get('resolution_m') == 30 and grid.get('cell_area_m2') == 900 and
             isinstance(grid.get('crs'), str) and bool(grid['crs']), '归档缺少明确30米原生格网')
    _require(all(row.get('grid', grid) == grid for row in observations),
             '观测格网与冻结参考格网不一致', 'TREND_GRID_MISMATCH')
    comparison = series.get('quality_comparison')
    _require(isinstance(comparison, dict), '须先完成质量合格日期的固定共同范围比较', 'TREND_COMMON_SUPPORT_REQUIRED')
    count = comparison.get('common_support_pixels')
    _require(comparison.get('status') == 'ready' and type(count) is int and count > 0,
             '质量合格观测没有共同有效像元，不能生成趋势', 'TREND_NO_COMMON_SUPPORT')
    ratio, area = comparison.get('common_support_ratio'), comparison.get('common_area_ha_30m')
    _require(_finite(ratio) and 0 < ratio <= 1 and _finite(area) and abs(area - count * .09) < 1e-7,
             '共同覆盖率或面积与冻结30米支持不一致')
    _require(isinstance(comparison.get('sha256'), str) and SHA.fullmatch(comparison['sha256']),
             '共同有效范围缺少内容校验版本')
    rule = comparison.get('rule')
    quality_fields = {'qa_valid_ratio': 'qa_valid_ratio_minimum',
                      'ndvi_valid_ratio': 'ndvi_valid_ratio_minimum',
                      'ndmi_valid_ratio': 'ndmi_valid_ratio_minimum'}
    _require(isinstance(rule, dict) and rule.get('same_spatial_mask_for_both_indices') is True and
             all(_finite(rule.get(key)) and 0 < rule[key] <= 1 for key in quality_fields.values()),
             '共同范围须声明NDVI/NDMI相同空间掩膜和有效率门槛')
    expected_sources = []
    for row in observations:
        _require(isinstance(row.get('sha256'), str) and SHA.fullmatch(row['sha256']), '实际观测缺少源像元SHA')
        expected_sources.append({'date': row['date'], 'sha256': row['sha256']})
    _require(comparison.get('source_sha256s') == expected_sources,
             '共同范围来源与实际观测SHA不一致', 'TREND_SOURCE_HASH_MISMATCH', 409)
    dates = comparison.get('dates')
    _require(isinstance(dates, list) and all(isinstance(day, str) for day in dates) and
             len(set(dates)) == len(dates), '共同范围日期清单无效')
    eligible = [row['date'] for row in observations if row.get('comparison_eligible') is True]
    _require(dates == eligible and all(type(row.get('comparison_eligible')) is bool for row in observations),
             '共同范围日期与逐期准入记录不一致')
    exclusion_map = {}
    declared_exclusions = comparison.get('excluded_dates_with_reasons', [])
    _require(isinstance(declared_exclusions, list), '排除日期记录无效')
    for item in declared_exclusions:
        _require(isinstance(item, dict) and isinstance(item.get('date'), str) and
                 isinstance(item.get('reasons'), list) and item['date'] not in exclusion_map,
                 '排除日期及原因记录无效')
        exclusion_map[item['date']] = copy.deepcopy(item['reasons'])
    _require(set(exclusion_map) == {row['date'] for row in observations if not row['comparison_eligible']},
             '排除日期清单与质量准入不一致')
    selected, excluded, inventory = [], [], []
    index = request['index_id']
    for row in observations:
        quality_pass = all(_finite(row.get(field)) and rule[minimum] <= row[field] <= 1
                           for field, minimum in quality_fields.items())
        _require(row['comparison_eligible'] == quality_pass, '质量准入与冻结门槛不一致')
        value = row.get('quality_common_mean_' + index)
        item = {'date': row['date'], 'scene_id': row.get('scene_id'), 'platform': row['platform'],
                'source_sha256': row['sha256'], 'comparison_eligible': row['comparison_eligible'],
                'mean_common_support': None, 'common_support_pixel_count': count,
                **{field: row.get(field) for field in quality_fields}}
        if row['comparison_eligible']:
            _require(_finite(value) and -1 <= value <= 1,
                     '共同范围合格日期缺少有效指数均值，未改用逐景均值', 'TREND_COMMON_MEAN_REQUIRED')
            item['mean_common_support'] = float(value)
            selected.append(copy.deepcopy(item))
        else:
            _require(value is None, '被排除日期仍声明共同范围均值，来源记录不一致')
            item['exclusion_reasons'] = exclusion_map[row['date']]
            excluded.append(copy.deepcopy(item))
        inventory.append(item)
    _require(len(selected) >= MIN_DATES, '稳健趋势至少需要3个质量合格且共同有效的独立日期', 'TREND_INSUFFICIENT_DATES')
    scope = series.get('scope')
    _require(isinstance(scope, dict) and isinstance(scope.get('region'), str) and bool(scope['region']),
             '源时序缺少明确实际行政范围')
    source = {'series_id': series['id'], 'series_sha256': series['series_sha256'],
              'title': series.get('title', '归档观测序列'), 'source_id': series['source_id'],
              'platform': series['platform'], 'scope': copy.deepcopy(scope), 'period': copy.deepcopy(period),
              'grid': copy.deepcopy(grid), 'common_support_pixel_count': count,
              'common_support_ratio': float(ratio), 'common_support_area_ha': float(area),
              'common_support_sha256': comparison['sha256'], 'quality_rule': copy.deepcopy(rule),
              'source_sha256s': expected_sources}
    inherited_limits = series.get('limitations') or []
    _require(isinstance(inherited_limits, list) and all(isinstance(item, str) for item in inherited_limits),
             '源时序的解释限制无效')
    return {'version': VERSION, 'algorithm_id': 'sen_slope', 'algorithm_version': VERSION,
            'request': request, 'index_id': index, 'source': source, 'observations': selected,
            'excluded_observations': excluded, 'time_series': inventory,
            'missing_windows': copy.deepcopy(series.get('sequence_summary', {}).get('missing_windows', [])),
            'limitations': list(dict.fromkeys(LIMITATIONS + inherited_limits))}


class AgriTrendServices:
    def __init__(self, store):
        self.store = store

    def prepare(self, body):
        request = _inputs(body)
        research = getattr(self.store, 'agri_research', None)
        _require(research is not None, '尚未接入可核验的归档时序服务', 'TREND_SERIES_UNAVAILABLE', 503)
        series = research.get(request['series_id'], request['series_sha256'])
        plan = _freeze(series, request)
        plan['input_fingerprint'] = digest(plan)
        return plan

    def create(self, body):
        plan = self.prepare(body)
        rows = plan['observations']
        origin = _day(rows[0]['date'])
        times = [(_day(row['date']) - origin).days for row in rows]
        slopes = [(rows[j]['mean_common_support'] - rows[i]['mean_common_support']) / (times[j] - times[i])
                  for i in range(len(rows)) for j in range(i + 1, len(rows))]
        slope = float(statistics.median(slopes))
        source = plan['source']
        summary = {'index_id': plan['index_id'], 'method': 'sen_pairwise_median',
                   'slope_per_day': slope, 'change_per_30_days': slope * 30,
                   'slope_unit': 'index/day', 'time_axis_unit': 'elapsed_actual_UTC_calendar_days',
                   'trend_direction': 'increasing' if slope > 0 else 'decreasing' if slope < 0 else 'unchanged',
                   'observation_count': len(rows), 'excluded_observation_count': len(plan['excluded_observations']),
                   'pair_count': len(slopes), 'span_days': times[-1],
                   'max_gap_days': max(b - a for a, b in zip(times, times[1:])),
                   'actual_dates': [row['date'] for row in rows],
                   'common_support_pixel_count': source['common_support_pixel_count'],
                   'common_support_ratio': source['common_support_ratio'],
                   'common_support_area_ha': source['common_support_area_ha'],
                   'support_scope': '质量合格观测共同范围', 'significance_status': 'not_assessed',
                   'p_value': None, 'is_significant': None, 'confidence_interval': None,
                   'interpretation': 'descriptive_only', 'crop_filtered': False,
                   'annual_trend_assessed': False, 'forecast': False}
        provenance = {**copy.deepcopy(source), 'algorithm_id': 'sen_slope', 'algorithm_version': VERSION,
                      'input_fingerprint': plan['input_fingerprint'],
                      'formula': 'median((value_j-value_i)/(actual_UTC_date_j-actual_UTC_date_i).days), j>i',
                      'input_values': 'quality_common_mean_' + plan['index_id'],
                      'missing_value_imputation': False, 'interpolation': False,
                      'external_engine_called': False, 'new_imagery_downloaded': False,
                      'pixelwise_trend_map_created': False, 'significance_status': 'not_assessed',
                      'significance_reason': '未建模季节与自相关；本工具仅提供冻结实际观测的描述性稳健斜率。',
                      'verification_scope': '归档内容SHA和标量支持契约；没有重新读取原生影像或开展独立地面精度验证。'}
        result = {'title': plan['index_id'].upper() + '时序稳健趋势', 'summary': summary,
                  'observations': copy.deepcopy(rows), 'excluded_observations': copy.deepcopy(plan['excluded_observations']),
                  'time_series': copy.deepcopy(plan['time_series']), 'missing_windows': copy.deepcopy(plan['missing_windows']),
                  'provenance': provenance, 'limitations': copy.deepcopy(plan['limitations']),
                  'map_effect': 'none', 'downloads': []}
        result['report'] = {'title': result['title'], 'findings': [
            '%d个质量合格实际日期，跨度%d天，最大观测间隔%d天。' % (len(rows), times[-1], summary['max_gap_days']),
            '共同有效范围%.2f公顷；Sen斜率%.8g指数/日。' % (source['common_support_area_ha'], slope),
            '显著性未评估；不作为全年、作物、灾损或预测结论。'],
            'limitations': copy.deepcopy(plan['limitations'])}
        # Recheck the frozen source before committing a separate derived record.
        verified = self.prepare(plan['request'])
        _require(verified['input_fingerprint'] == plan['input_fingerprint'],
                 '计算期间源时序内容发生变化，未保存派生记录', 'SERIES_VERSION_CHANGED', 409)
        stamp = now()
        ident = 'agri_trend_' + uuid.uuid4().hex[:14]
        result['downloads'] = [{'label': '稳健趋势报告与来源JSON', 'type': 'json',
                                'url': '/api/agri-trends/' + ident}]
        row = {'id': ident, 'name': result['title'],
               'status': 'succeeded', 'progress': 100, 'created_at': stamp, 'finished_at': stamp,
               'plan': plan, 'snapshot': copy.deepcopy(plan), 'result': result, 'result_sha256': digest(result)}
        row['record_sha256'] = digest(row)
        return self.store.save(KIND, row)

    def get(self, ident, expected=None):
        _require(isinstance(ident, str) and re.fullmatch(r'agri_trend_[a-f0-9]{14}', ident),
                 '趋势记录标识无效', 'INVALID_TREND_ID', 400)
        _require(expected is None or isinstance(expected, str) and SHA.fullmatch(expected),
                 '结果版本须为SHA256', 'INVALID_TREND_INPUT', 400)
        row = self.store.get(KIND, ident)
        _require(row.get('id') == ident and row.get('status') == 'succeeded' and isinstance(row.get('result'), dict),
                 '趋势派生记录未完成', 'TREND_RECORD_INVALID', 409)
        frozen = {key: value for key, value in row.items() if key != 'record_sha256'}
        _require(row.get('result_sha256') == digest(row['result']) and row.get('record_sha256') == digest(frozen) and
                 (expected is None or expected == row['record_sha256']),
                 '趋势结果与归档内容版本不一致', 'TREND_RESULT_CHANGED', 409)
        return copy.deepcopy(row)

    def list(self):
        return [self.get(row.get('id')) for row in self.store.list(KIND)]


def trend_answer(record):
    """Structured answer with explicit scalar provenance and no map substitution."""
    result = record['result']
    summary, source = result['summary'], result['provenance']
    name = summary['index_id'].upper()
    direction = {'increasing': '正', 'decreasing': '负', 'unchanged': '零'}[summary['trend_direction']]
    text = ('%s归档%s在%d个质量合格实际日期的共同有效范围内，Sen斜率为%s（%.6g指数/日），'
            '按30日折算变化为%.6g；实际跨度%d天。显著性未评估，不能据此认定农业灾损或年度趋势。') % (
                source['scope']['region'], name, summary['observation_count'], direction,
                summary['slope_per_day'], summary['change_per_30_days'], summary['span_days'])
    research = {'id': source['series_id'], 'series_sha256': source['series_sha256'],
                'title': source['title'], 'period': copy.deepcopy(source['period']),
                'scope': copy.deepcopy(source['scope']), 'summary': copy.deepcopy(summary),
                'observations': copy.deepcopy(result['time_series']), 'time_series': copy.deepcopy(result['time_series']),
                'limitations': copy.deepcopy(result['limitations'])}
    return {'title': result['title'], 'summary': text,
            'metrics': [{'label': 'Sen斜率', 'value': summary['slope_per_day'], 'unit': '指数/日'},
                        {'label': '30日折算变化', 'value': summary['change_per_30_days'], 'unit': '指数'},
                        {'label': '有效日期', 'value': summary['observation_count'], 'unit': '个'},
                        {'label': '共同有效面积', 'value': summary['common_support_area_ha'], 'unit': '公顷'}],
            'warnings': copy.deepcopy(result['limitations']),
            'trend_diagnostics': {**copy.deepcopy(summary), 'method_version': VERSION,
                                  'excluded_observations': copy.deepcopy(result['excluded_observations']),
                                  'missing_windows': copy.deepcopy(result['missing_windows'])},
            'research_series': research, 'provenance': {**copy.deepcopy(source),
                'derived_record_id': record['id'], 'result_sha256': record['result_sha256'],
                'record_sha256': record['record_sha256']},
            'trend_ref': {'id': record['id'], 'url': '/api/agri-trends/' + record['id'],
                          'record_sha256': record['record_sha256']},
            'map_effect': 'none', 'result_ref': None, 'downloads': copy.deepcopy(result['downloads'])}


def prepare_trend_question(store, question, context):
    """Deterministic archive-only routing; retain scope/time/source constraints."""
    from backend.agriculture.agri_series_read import CONTEXT_KEYS, _compatible, _time_constraint
    from backend.agriculture.regional_product_tools import REGIONS, scope_text_error
    from backend.analysis.spectral_indices import detect_indices, unsupported_index_requested

    if not isinstance(question, str) or not re.search(r'时序|序列|长势曲线|水分曲线', question) or not re.search(r'趋势|斜率|\bsen\b|theil', question, re.I):
        return None
    knowledge = re.search(r'什么是|原理|定义|公式|科普|介绍|如何计算|怎么计算|如何理解', question)
    explicit_action = re.search(r'分析|诊断|帮我|给出.*结果', question)
    if re.match(r'^\s*(?:请)?(?:什么是|介绍|解释|科普|讲解|如何理解|如何计算|怎么计算)', question) or knowledge and not explicit_action:
        return None

    def response(message=None, inputs=None):
        return {'version': VERSION, 'intent': TOOL_ID, 'status': 'needs_data' if message else 'ready',
                'executable': message is None, 'inputs': inputs, 'message': message,
                'blocking_gaps': [message] if message else [], 'missing_fields': [],
                'tool': {'id': TOOL_ID, 'version': VERSION, 'inputs': inputs} if inputs else None}

    if not isinstance(context, dict):
        return response('上下文须为明确业务条件。')
    if set(context) - (CONTEXT_KEYS | {'series_sha256'}):
        return response('趋势诊断不接受未识别的范围、时段或处理条件。')
    problem = scope_text_error(question, context.get('region'))
    if problem:
        return response(problem)
    if '仙桃' not in question and not ('全市' in question and context.get('region') in REGIONS):
        return response('请明确仙桃全市归档时序；不能推测目标行政范围。')
    whole = any(word in question for word in ('全市', '整市', '仙桃全域', '仙桃市全域'))
    if ('bbox' in context or any(word in question for word in ('框选', '选区', '选中', '所选', '这块地', '该地块'))) and not whole:
        return response('当前选择了局部范围，不能用全市归档趋势代替；请明确全市范围。')
    if any(key in context for key in ('dataset_id', 'periods', 'reference_execution_id', 'observation_id',
            'cropland_product', 'soil_ndvi', 'vegetation_ndvi', 'field', 'threshold', 'processing_algorithm',
            'scene_limit', 'cloud', 'direction', 'min_patch_pixels', 'atlas_product_sha256')):
        return response('归档趋势不替代所选地块、地图版本、原任务或处理参数；请使用匹配的数据分析能力。')
    if any(word in question for word in ('农田', '耕地', '水稻', '油菜', '小麦', '玉米', '棉花', '土壤含水率',
            '产量', '灾损', '受灾', '减产', '赔偿', '面积', '识别', '分类', '标注', '预测', '插值', '物候',
            '显著', '置信', '全年', '整年', '年际', '逐年', '多年', '年度趋势', '逐日', '每日', '逐月', '每月')):
        return response('现有工具仅诊断全市共同有效范围的代表观测指数；未具备作物掩膜、完整年度、显著性或农业认定依据。')
    indices = detect_indices(question)
    index = ('ndmi' if '水分曲线' in question or 'ndmi' in indices else
             'ndvi' if 'ndvi' in indices or '长势曲线' in question else context.get('index_id') or 'ndvi')
    if unsupported_index_requested(question) or any(value not in ('ndvi', 'ndmi') for value in indices) or len(set(indices)) > 1 or context.get('index_id') not in (None, index) or index not in ('ndvi', 'ndmi'):
        return response('请明确单一NDVI或NDMI指标，不能替换或混算指标。')
    if '水分曲线' in question and indices == ['ndvi']:
        return response('水分曲线与明确NDVI指标冲突，请明确NDMI或NDVI。')
    if any(word in question for word in ('今年', '去年', '本月', '上月', '实时', '今日', '现在', '当前', '最近', '最新')) and not any(word in question for word in ('已归档', '已有归档', '当前归档', '现有归档', '最新归档')):
        return response('请明确归档年度；代表观测不能替代实时或当前业务实况。')
    if context.get('source_type') not in (None, 'agri_research', 'agri_research_series'):
        return response('所选来源不是归档时序，不能用其他来源或NDVI地图代替趋势输入。')
    selected = context.get('source_task_id')
    if (selected is None) != (context.get('source_type') is None):
        return response('指定时序须同时提供明确来源类型与来源ID。')
    try:
        years, window = _time_constraint(question, context)
        research = getattr(store, 'agri_research', None)
        _require(research is not None, '尚未接入可核验归档；不会自动下载或生成替代数据')
        entries = research.catalog().get('series', [])
        if selected is not None:
            entries = [entry for entry in entries if entry.get('id') == selected]
        candidates, gaps = [], []
        service = getattr(store, 'agri_trend', None) or AgriTrendServices(store)
        for entry in entries:
            try:
                series = research.get(entry['id'])
                problem = _compatible(series, question, context, years, window, index)
                if problem:
                    gaps.append(problem)
                    continue
                if context.get('series_sha256') is not None and context['series_sha256'] != series.get('series_sha256'):
                    gaps.append('指定时序SHA与当前归档版本不一致，未换用新版本。')
                    continue
                inputs = {'series_id': series['id'], 'series_sha256': series.get('series_sha256'), 'index_id': index}
                service.prepare(inputs)
                candidates.append(inputs)
            except (GeoError, OSError, ValueError, TypeError, KeyError) as error:
                gaps.append(getattr(error, 'message', str(error)))
        if len(candidates) > 1:
            return response('存在多个符合条件的归档时序，请明确时序来源或完整时段；没有自动选择最近版本。')
        if not candidates:
            return response('；'.join(dict.fromkeys(gaps)) or '尚无匹配的已发布归档时序，不会自动下载影像或改用其他地区。')
        return response(inputs=candidates[0])
    except (GeoError, OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        return response(getattr(error, 'message', str(error)) or '趋势诊断条件无效。')
