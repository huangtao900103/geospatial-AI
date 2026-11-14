"""Bounded optical ImageCollection reducers over real calibrated native pixels.

This implements fixed local reducers inspired by Earth Engine's public methods;
it does not invoke or claim access to the Earth Engine service. All selected
scenes remain in the collection, including scenes with no valid AOI pixels.
"""
import copy
import csv
import hashlib
import json
import math
import re
import warnings
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from geo_services import GeoError, finite
from spectral_indices import specification

VERSION = 'collection-native-1.0'
FILES = ('analysis.tif', 'overlay.png', 'result.geojson', 'series.csv', 'result.json')
MAX_SCENES = 6
MAX_CATALOG_SCENES = 200
SOURCES = {'pc_sentinel2_l2a': 'sentinel-2-l2a', 'pc_landsat_c2_l2': 'landsat-c2-l2'}
ALGORITHMS = {
    'temporal_median': {'name': '时段指数中位数合成', 'reducer': 'median', 'minimum_observations': 2},
    'temporal_mean': {'name': '时段指数均值合成', 'reducer': 'mean', 'minimum_observations': 2},
    'temporal_trend': {'name': '指数时间序列线性趋势', 'reducer': 'ordinary_least_squares', 'minimum_observations': 3},
}
REFERENCES = [
    {'name': 'Earth Engine ImageCollection reductions', 'url': 'https://developers.google.com/earth-engine/guides/ic_reducing'},
    {'name': 'Earth Engine linear regression reducers', 'url': 'https://developers.google.com/earth-engine/guides/reducers_regression'},
]
LIMITATIONS = [
    '按明确时段中的最多6个独立UTC观测日期取样，不是每日连续监测或全部可用影像的无遗漏合成。',
    '仅在所选单一传感器数据集合内处理；不将Sentinel-2与Landsat混合，也未进行跨卫星偏差校准。',
    '质量掩膜沿用S2产品定标/SCL或Landsat实际定标/QA；保留的SCL7不是严格无云保证。',
    '合成像元至少需要2次有效观测，趋势至少需要3次；不足支持保持未知/透明，不补0或插值。',
    '线性斜率是观测时段内指数/日的描述统计，不是趋势显著性、长势诊断、作物分类、产量或灾损结论。',
    '逐景全有效均值可能使用不同空间支持；固定共同支持列与逐像元回归分别报告。',
    '原始光谱和质量窗口的地址、定标与读取指纹保留；提供方不保证同一公开资产URI永久内容版本不变。',
    '借鉴公开算法方法并在本地执行，不表示调用了Google Earth Engine或OpenGE云服务。',
]


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise ValueError('missing observation time')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('ambiguous observation timezone')
    return parsed.astimezone(timezone.utc)


def capabilities():
    return {'version': VERSION, 'max_scenes': MAX_SCENES, 'max_catalog_scenes': MAX_CATALOG_SCENES,
            'source_ids': list(SOURCES), 'algorithms': [{'id': key, **copy.deepcopy(value)} for key, value in ALGORITHMS.items()],
            'references': copy.deepcopy(REFERENCES), 'limitations': copy.deepcopy(LIMITATIONS)}


def make_plan(body):
    allowed = {'algorithm_id', 'source_id', 'index_id', 'bbox', 'start', 'end', 'scene_limit', 'cloud', 'requirement'}
    if not isinstance(body, dict) or set(body) - allowed:
        raise GeoError('INVALID_COLLECTION_INPUT', '时序分析包含不支持字段', 400)
    algorithm_id = body.get('algorithm_id')
    if algorithm_id not in ALGORITHMS:
        raise GeoError('COLLECTION_ALGORITHM_UNSUPPORTED', '请选择中位数合成、均值合成或线性趋势', 422)
    source_id = body.get('source_id', 'pc_sentinel2_l2a')
    if source_id not in SOURCES:
        raise GeoError('COLLECTION_SOURCE_UNSUPPORTED', '时序执行仅支持明确的Sentinel-2或Landsat8/9来源', 422)
    index_id = body.get('index_id', 'ndvi')
    if not isinstance(index_id, str):
        raise GeoError('COLLECTION_INDEX_INVALID', '光谱指数ID须为已登记名称', 422)
    if source_id == 'pc_landsat_c2_l2':
        from landsat_reader import specification as landsat_specification
        index = landsat_specification(index_id)
    else:
        index = specification(index_id)
    bbox = body.get('bbox')
    if not isinstance(bbox, list) or len(bbox) != 4 or not all(finite(v) for v in bbox):
        raise GeoError('COLLECTION_AOI_REQUIRED', '请明确指定WGS84小范围选区', 422)
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -80 <= south < north <= 80) or east - west > .050001 or north - south > .050001:
        raise GeoError('COLLECTION_AOI_LIMIT', '时序原生选区单边须≤0.05°，不能用小窗替代全市或全省', 422)
    try:
        start, end = body['start'], body['end']
        if any(not isinstance(v, str) or len(v) != 10 for v in (start, end)):
            raise ValueError()
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last or (last - first).days > 183:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise GeoError('COLLECTION_PERIOD_REQUIRED', '请填写有效起止日期，每次窗口最多183天', 422)
    scene_limit = body.get('scene_limit', 4)
    minimum = ALGORITHMS[algorithm_id]['minimum_observations']
    if isinstance(scene_limit, bool) or not isinstance(scene_limit, int) or not minimum <= scene_limit <= MAX_SCENES:
        raise GeoError('COLLECTION_SCENE_LIMIT', '场景上限须为%d–6个独立UTC日期' % minimum, 422)
    cloud = body.get('cloud', 40)
    if not finite(cloud) or not 0 <= cloud <= 100:
        raise GeoError('COLLECTION_CLOUD_INVALID', '景云量上限须为0–100的有限数值', 422)
    requirement = body.get('requirement', ALGORITHMS[algorithm_id]['name'])
    if not isinstance(requirement, str) or not requirement.strip() or len(requirement) > 4000:
        raise GeoError('COLLECTION_REQUIREMENT_INVALID', '分析需求须为1–4000字', 422)
    if any(word in requirement for word in ('全市', '全县', '全省', '全国', '省域', '全域', '全境')):
        raise GeoError('COLLECTION_FULL_REGION_UNSUPPORTED', '此时序算子只处理明确小范围，不能用小窗代替完整行政区域', 422)
    if any(word in requirement for word in ('产量', '病虫害', '灾损', '受灾损失', '确权', '法定', '高标准农田', '作物分类', '叶面积指数')) or (any(word in requirement for word in ('农田', '耕地', '耕作地')) and any(word in requirement for word in ('标注', '提取', '图斑', '总面积', '多少亩'))):
        raise GeoError('COLLECTION_BUSINESS_METRIC_UNSUPPORTED', '时序指数归约不能替代作物分类、业务面积、产量或灾损认定', 422)
    plan = {'algorithm_id': algorithm_id, 'algorithm_version': VERSION, 'source_id': source_id,
            'index_id': index_id, 'index': copy.deepcopy(index), 'bbox': list(bbox), 'aoi': {'bbox': list(bbox)},
            'start': start, 'end': end, 'scene_limit': scene_limit, 'cloud': float(cloud),
            'minimum_observations': minimum, 'mode': 'sentinel_cog', 'sampling': 'native', 'goal': 'ndvi',
            'periods': {'after': [start, end]}, 'allow_sparse_quality': True, 'requirement': requirement.strip(),
            'limitations': copy.deepcopy(LIMITATIONS)}
    plan['plan_fingerprint'] = _hash({k: plan[k] for k in ('algorithm_id', 'algorithm_version', 'source_id', 'index_id', 'bbox', 'start', 'end', 'scene_limit', 'cloud', 'minimum_observations')} | {'index_version': index['version']})
    return plan


def _catalog_document(reader, url, check):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname != 'planetarycomputer.microsoft.com' or parts.path != '/api/stac/v1/search' or parts.username or parts.password or parts.port is not None or parts.fragment or len(url) > 12000:
        raise GeoError('COLLECTION_CATALOG_DENIED', '只访问固定PC影像集合检索接口', 502)
    try:
        raw = reader.fetch_bytes(url, check, max_bytes=4 * 1024 * 1024)
        doc = json.loads(raw.decode('utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(doc, dict) or not isinstance(doc.get('features'), list):
            raise ValueError()
        return doc, hashlib.sha256(raw).hexdigest()
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise GeoError('COLLECTION_CATALOG_SCHEMA', '影像时序目录格式无效，未构造替代场景', 502)


def discover_collection(plan, reader, check=lambda: None):
    params = {'collections': SOURCES[plan['source_id']], 'bbox': ','.join(str(v) for v in plan['bbox']),
              'datetime': plan['start'] + 'T00:00:00Z/' + plan['end'] + 'T23:59:59Z',
              'query': json.dumps({'eo:cloud_cover': {'lte': plan['cloud']}}, separators=(',', ':')),
              'limit': 50, 'sortby': '+datetime'}
    if plan['source_id'] == 'pc_landsat_c2_l2':
        params['query'] = json.dumps({'platform': {'in': ['landsat-8', 'landsat-9']}, 'eo:cloud_cover': {'lte': plan['cloud']}}, separators=(',', ':'))
    url = 'https://planetarycomputer.microsoft.com/api/stac/v1/search?' + urlencode(params)
    candidates = {};pages = [];seen = set();total = 0
    for page in range(4):
        check()
        if url in seen:
            raise GeoError('COLLECTION_CATALOG_LOOP', '时序目录分页重复，未使用不完整候选集', 502)
        seen.add(url);doc, digest = _catalog_document(reader, url, check)
        total += len(doc['features'])
        if len(doc['features']) > 50 or total > MAX_CATALOG_SCENES:
            raise GeoError('COLLECTION_CATALOG_LIMIT', '时序候选超过200景，请缩短时段；未按不完整候选集执行', 422)
        pages.append({'url': url, 'sha256': digest, 'feature_count': len(doc['features'])})
        for item in doc['features']:
            if not isinstance(item, dict):
                continue
            props = item.get('properties') or {};cloud = props.get('eo:cloud_cover');box = item.get('bbox');scene_id = item.get('id')
            try:
                stamp = _time(props.get('datetime'));day = stamp.date().isoformat()
            except (TypeError, ValueError, OverflowError):
                continue
            if not isinstance(scene_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,240}', scene_id) or not finite(cloud) or not 0 <= cloud <= plan['cloud'] or not plan['start'] <= day <= plan['end']:
                continue
            if item.get('collection') not in (None, SOURCES[plan['source_id']]) or not isinstance(box, list) or len(box) < 4 or not all(finite(v) for v in box[:4]):
                continue
            if plan['source_id'] == 'pc_landsat_c2_l2' and (props.get('platform') not in ('landsat-8', 'landsat-9') or not re.fullmatch(r'LC0[89]_[A-Z0-9_]+', scene_id)):
                continue
            aoi = plan['bbox']
            if not (box[0] <= aoi[0] and box[1] <= aoi[1] and box[2] >= aoi[2] and box[3] >= aoi[3]):
                continue
            candidate = {'id': scene_id, 'day': day, 'datetime': props['datetime'], 'cloud_cover': float(cloud), 'platform': props.get('platform'), 'item_sha256': _hash(item)}
            old = candidates.get(day)
            if old is None or (candidate['cloud_cover'], candidate['id']) < (old['cloud_cover'], old['id']):
                candidates[day] = candidate
        next_links = [link for link in doc.get('links', []) if isinstance(link, dict) and link.get('rel') == 'next']
        if not next_links:
            break
        if page == 3:
            raise GeoError('COLLECTION_CATALOG_LIMIT', '时序目录仍有未遍历分页，请缩短时段；未静默截断', 422)
        if len(next_links) != 1 or next_links[0].get('method', 'GET').upper() != 'GET' or not isinstance(next_links[0].get('href'), str):
            raise GeoError('COLLECTION_PAGINATION_UNSUPPORTED', '此目录分页形式尚未支持，未使用不完整候选集', 502)
        url = next_links[0]['href']
    ordered = sorted(candidates.values(), key=lambda value: (value['day'], value['id']))
    if len(ordered) < plan['minimum_observations']:
        raise GeoError('COLLECTION_TOO_FEW_DATES', '时段内只有%d个覆盖、日期和景云量合格的独立UTC日期，至少需要%d个' % (len(ordered), plan['minimum_observations']), 422)
    if len(ordered) > plan['scene_limit']:
        indices = [round(i * (len(ordered) - 1) / (plan['scene_limit'] - 1)) for i in range(plan['scene_limit'])]
        chosen = [ordered[i] for i in indices]
    else:
        chosen = ordered
    scenes = []
    for candidate in chosen:
        check();single = copy.deepcopy(plan);single.update(scene_id=candidate['id'], goal='ndvi', periods={'after': [candidate['day'], candidate['day']]})
        scene = reader.discover(single, check)['after']
        if scene.get('id') != candidate['id'] or _time(scene.get('datetime')).date().isoformat() != candidate['day']:
            raise GeoError('COLLECTION_SCENE_CHANGED', '锁定时序场景或日期不一致，未换景', 409)
        scene.update(source_id=plan['source_id'], platform=candidate['platform'], collection=SOURCES[plan['source_id']], catalog_item_sha256=candidate['item_sha256'])
        scenes.append(scene)
    manifest = {'version': VERSION, 'plan_fingerprint': plan['plan_fingerprint'], 'source_id': plan['source_id'],
                'index_id': plan['index_id'], 'candidate_scene_count': total, 'eligible_unique_date_count': len(ordered),
                'selected_count': len(scenes), 'selected_scenes': scenes, 'catalog_pages': pages,
                'selection_rule': 'complete bounded catalogue; full bbox containment; one lowest-cloud scene per UTC day (ID tie-break); evenly spaced date indices including first/last when over scene_limit',
                'selection_scope': 'at most 6 sampled observation dates, not all scenes or a daily/monthly mean'}
    manifest['selection_sha256'] = _hash(manifest)
    return manifest


def _validate_manifest(plan, manifest):
    canonical = make_plan({key: plan[key] for key in ('algorithm_id', 'source_id', 'index_id', 'bbox', 'start', 'end', 'scene_limit', 'cloud')})
    if plan.get('plan_fingerprint') != canonical['plan_fingerprint'] or plan.get('index') != canonical['index'] or plan.get('aoi', {}).get('bbox') != plan['bbox'] or plan.get('minimum_observations') != canonical['minimum_observations']:
        raise GeoError('COLLECTION_PLAN_CHANGED', '时序输入或指数方法与冻结计划不一致，不能按旧集合执行', 409)
    if not isinstance(manifest, dict) or manifest.get('version') != VERSION or manifest.get('plan_fingerprint') != plan['plan_fingerprint'] or manifest.get('source_id') != plan['source_id'] or manifest.get('index_id') != plan['index_id']:
        raise GeoError('COLLECTION_BINDING_CONFLICT', '冻结影像集合与当前方法、来源或范围不一致', 409)
    if manifest.get('selection_sha256') != _hash({k: v for k, v in manifest.items() if k != 'selection_sha256'}):
        raise GeoError('COLLECTION_BINDING_CHANGED', '冻结影像集合内容指纹变化，不能执行', 409)
    scenes = manifest.get('selected_scenes')
    if not isinstance(scenes, list) or not plan['minimum_observations'] <= len(scenes) <= plan['scene_limit']:
        raise GeoError('COLLECTION_BINDING_INVALID', '冻结集合的场景数无效', 409)
    try:
        days = [_time(scene['datetime']).date().isoformat() for scene in scenes]
        ids = [scene['id'] for scene in scenes]
    except (TypeError, KeyError, ValueError):
        raise GeoError('COLLECTION_BINDING_INVALID', '冻结集合缺少明确场景和日期', 409)
    if len(set(days)) != len(days) or len(set(ids)) != len(ids) or days != sorted(days) or any(not plan['start'] <= day <= plan['end'] for day in days) or any(scene.get('source_id', plan['source_id']) != plan['source_id'] for scene in scenes):
        raise GeoError('COLLECTION_BINDING_INVALID', '冻结集合存在重复或无效日期，不能加权冒充多次观测', 409)
    return scenes


def reduce_stack(stack, times_days, aoi_mask, algorithm_id, check=lambda: None):
    """Pure fixed reducers; input is float index values with NaN for invalid data."""
    import numpy as np
    values = np.asarray(stack, dtype='float64');times = np.asarray(times_days, dtype='float64');inside = np.asarray(aoi_mask, dtype='bool')
    if algorithm_id not in ALGORITHMS or values.ndim != 3 or values.shape[0] != len(times) or values.shape[1:] != inside.shape or not np.isfinite(times).all() or len(np.unique(times)) != len(times) or not np.all(np.diff(times) > 0):
        raise GeoError('COLLECTION_STACK_INVALID', '时序输入格网、方法或独立时间轴无效', 422)
    minimum = ALGORITHMS[algorithm_id]['minimum_observations']
    if not minimum <= len(times) <= MAX_SCENES:
        raise GeoError('COLLECTION_TOO_FEW_DATES', '时序归约缺少足够独立日期', 422)
    good = np.isfinite(values) & inside[None, :, :];values = np.where(good, values, np.nan)
    count = good.sum(axis=0).astype('uint8');valid = inside & (count >= minimum);check()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        output = np.nanmedian(values, axis=0) if algorithm_id == 'temporal_median' else np.nanmean(values, axis=0)
    arrays = {'value': np.where(valid, output, np.nan).astype('float32'), 'valid_observation_count': np.where(inside, count, np.nan).astype('float32'), 'valid_mask': np.where(inside, valid, np.nan).astype('float32'), 'aoi_mask': inside.astype('float32')}
    if algorithm_id == 'temporal_trend':
        x = times[:, None, None];n = count.astype('float64');sy = np.nansum(values, axis=0);sx = np.sum(np.where(good, x, 0), axis=0)
        sxx = np.sum(np.where(good, x * x, 0), axis=0);sxy = np.nansum(values * x, axis=0);syy = np.nansum(values * values, axis=0)
        denominator = n * sxx - sx * sx;valid &= denominator > 1e-10
        slope = np.full(inside.shape, np.nan);np.divide(n * sxy - sx * sy, denominator, out=slope, where=valid)
        intercept = np.full(inside.shape, np.nan);np.divide(sy - slope * sx, n, out=intercept, where=valid)
        tss = np.maximum(syy - np.divide(sy * sy, n, out=np.zeros_like(sy), where=n > 0), 0)
        rss = np.maximum(syy - 2 * slope * sxy - 2 * intercept * sy + slope * slope * sxx + 2 * slope * intercept * sx + n * intercept * intercept, 0)
        r_squared = np.full(inside.shape, np.nan);np.divide(rss, tss, out=r_squared, where=valid & (tss > 1e-12));r_squared = np.where(valid & (tss > 1e-12), np.clip(1 - r_squared, 0, 1), np.nan)
        first = np.min(np.where(good, x, np.inf), axis=0);last = np.max(np.where(good, x, -np.inf), axis=0)
        arrays.update(value=np.where(valid, slope, np.nan).astype('float32'), valid_mask=np.where(inside, valid, np.nan).astype('float32'), intercept_at_time_origin=np.where(valid, intercept, np.nan).astype('float32'), r_squared=r_squared.astype('float32'), observation_span_days=np.where(valid, last - first, np.nan).astype('float32'))
    for i in range(len(times)):
        arrays['scene_%02d_index' % i] = values[i].astype('float32')
        arrays['scene_%02d_valid_mask' % i] = np.where(inside, good[i], np.nan).astype('float32')
    check();return arrays


def _distribution(value):
    import numpy as np
    if not value.size:
        return {key: None for key in ('mean', 'median', 'min', 'max', 'std', 'p10', 'p90')}
    return {'mean': float(np.mean(value, dtype='float64')), 'median': float(np.median(value)), 'min': float(value.min()), 'max': float(value.max()), 'std': float(np.std(value, dtype='float64')), 'p10': float(np.percentile(value, 10)), 'p90': float(np.percentile(value, 90))}


def _preview(value, valid, meta, algorithm, trend, path):
    import numpy as np
    from affine import Affine
    from rasterio.transform import array_bounds, from_bounds
    from rasterio.warp import calculate_default_transform, transform_bounds, reproject, Resampling
    from PIL import Image
    rows, cols = value.shape;transform = Affine(*meta['transform']);bounds = array_bounds(rows, cols, transform)
    _, width, height = calculate_default_transform(meta['crs'], 'EPSG:3857', cols, rows, *bounds)
    factor = max(width / 1536, height / 1536, 1);width = max(1, int(width / factor));height = max(1, int(height / factor))
    mercator = transform_bounds(meta['crs'], 'EPSG:3857', *bounds, densify_pts=21);target = np.full((height, width), np.nan, dtype='float32')
    reproject(np.where(valid, value, np.nan).astype('float32'), target, src_transform=transform, src_crs=meta['crs'], src_nodata=np.nan, dst_transform=from_bounds(*mercator, width, height), dst_crs='EPSG:3857', dst_nodata=np.nan, resampling=Resampling.nearest)
    rgba = np.zeros((height, width, 4), dtype='uint8');usable = np.isfinite(target);display_range = None
    if trend:
        limit = max(float(np.percentile(np.abs(value[valid]), 98)), 1e-6) if valid.any() else 1e-6
        scaled = np.clip(target / limit, -1, 1);negative = np.array([202, 119, 103]);neutral = np.array([229, 227, 204]);positive = np.array([67, 159, 122])
        for channel in range(3):
            raw = np.where(scaled < 0, neutral[channel] + (-scaled) * (negative[channel] - neutral[channel]), neutral[channel] + scaled * (positive[channel] - neutral[channel]))
            rgba[:, :, channel] = np.where(usable, np.nan_to_num(raw, nan=0), 0).astype('uint8')
        rgba[usable, 3] = 220;display_range = [-limit, limit]
        legend = [{'label':('≤ %.6g' % -limit) + ' 指数/日（色阶）', 'color': '#ca7767'}, {'label':'0 指数/日', 'color':'#e5e3cc'}, {'label':('≥ %.6g' % limit) + ' 指数/日（色阶）', 'color':'#439f7a'}]
    else:
        visualization = algorithm.get('visualization') or {'breaks': [0, .2, .5] if algorithm['id'] == 'ndvi' else [-.2, 0, .2], 'colors': ['#dc8d78', '#e0c687', '#afc98a', '#65c4a1']}
        breaks, colors = visualization['breaks'], visualization['colors'];categories = [target < breaks[0], *[(target >= breaks[i - 1]) & (target < breaks[i]) for i in range(1, len(breaks))], target >= breaks[-1]]
        labels = [algorithm['label'] + ' < ' + str(breaks[0]), *[str(breaks[i - 1]) + ' ≤ ' + algorithm['label'] + ' < ' + str(breaks[i]) for i in range(1, len(breaks))], algorithm['label'] + ' ≥ ' + str(breaks[-1])]
        legend = [{'label': label, 'color': color} for label, color in zip(labels, colors)]
        for category, color in zip(categories, colors):
            rgba[usable & category] = [int(color.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4)] + [220]
    Image.fromarray(rgba).save(path)
    return {'bounds': list(transform_bounds('EPSG:3857', 'EPSG:4326', *mercator, densify_pts=21)), 'crs': 'EPSG:3857', 'width': width, 'height': height, 'legend': legend, 'statistics_from_preview': False, 'visualization_range': display_range, 'display_note': '趋势色阶按有效斜率绝对值98分位拉伸；仅颜色截断，统计和GeoTIFF不裁剪。' if trend else '按指数固定色阶显示，统计不从预览图计算。'}


def run_collection(plan, reader, directory, check=lambda: None, progress=None):
    import numpy as np
    import rasterio
    from affine import Affine
    from native_raster import read_native, grids, VERSION as NATIVE_VERSION
    def announce(stage, message):
        check()
        if progress is not None:
            progress(stage, message)
    announce('discover', '锁定真实影像集合与独立观测日期')
    manifest = copy.deepcopy(plan['collection']) if 'collection' in plan else discover_collection(plan, reader, check)
    scenes = _validate_manifest(plan, manifest);mapping = {'after' if i == len(scenes) - 1 else 'scene_%02d' % i: scene for i, scene in enumerate(scenes)}
    announce('read', '读取全部锁定场景的定标波段与质量掩膜')
    bands = read_native(plan, mapping, reader, check);meta = bands['metadata'];ordered_values = [];reasons = [];series = []
    for i, scene in enumerate(scenes):
        check();key = 'after' if i == len(scenes) - 1 else 'scene_%02d' % i
        per_scene, per_reasons = grids({'after': bands[key], '_mask': bands['_mask'], 'metadata': meta}, plan['index_id'])
        value, valid = per_scene['after'];ordered_values.append(value);reasons.append(per_reasons['after'])
        item = {'scene_number': i, 'scene_id': scene['id'], 'datetime': scene['datetime'], 'utc_date': _time(scene['datetime']).date().isoformat(), 'scene_cloud_cover': scene['cloud_cover'], 'valid_pixel_count': int(valid.sum()), 'aoi_pixel_count': int(bands['_mask'].sum()), 'valid_ratio': int(valid.sum()) / int(bands['_mask'].sum()), 'mean_index_all_valid': float(value[valid].mean(dtype='float64')) if valid.any() else None, 'min_index_all_valid': float(value[valid].min()) if valid.any() else None, 'max_index_all_valid': float(value[valid].max()) if valid.any() else None}
        series.append(item)
    origin = _time(scenes[0]['datetime']);times = [(_time(scene['datetime']) - origin).total_seconds() / 86400 for scene in scenes]
    announce('compute', '按真实不规则时间轴执行固定归约并保留观测支持')
    stack = np.stack(ordered_values);arrays = reduce_stack(stack, times, bands['_mask'], plan['algorithm_id'], check)
    valid = arrays['valid_mask'] == 1;inside = bands['_mask'];count = int(inside.sum());support = arrays['valid_observation_count'];common = inside & np.isfinite(stack).all(axis=0);common_count = int(common.sum())
    for i, item in enumerate(series):
        item.update(elapsed_days=times[i], common_support_pixel_count=common_count, mean_index_common_support=float(stack[i][common].mean(dtype='float64')) if common_count else None)
    values = arrays['value'][valid];distribution = _distribution(values);trend = plan['algorithm_id'] == 'temporal_trend';unit = 'index_per_day' if trend else 'index';cell_ha = meta['cell_area_m2'] / 10000
    summary = {'algorithm_id': plan['algorithm_id'], 'algorithm_version': VERSION, 'reducer': ALGORITHMS[plan['algorithm_id']]['reducer'], 'index_id': plan['index_id'], 'index': copy.deepcopy(plan['index']), 'unit': unit, 'scene_count': len(scenes), 'unique_date_count': len(scenes), 'minimum_observations': plan['minimum_observations'], 'actual_dates': [scene['datetime'] for scene in scenes], 'native_pixel_count': count, 'valid_count': int(valid.sum()), 'invalid_count': count - int(valid.sum()), 'valid_ratio': int(valid.sum()) / count, 'valid_area_ha': int(valid.sum()) * cell_ha, 'unknown_area_ha': (count - int(valid.sum())) * cell_ha, 'common_support_pixel_count': common_count, 'statistics': distribution, 'mean_index': None if trend else distribution['mean'], 'mean_slope_per_day': distribution['mean'] if trend else None, 'observation_count_histogram': [{'observations': i, 'pixels': int((inside & (support == i)).sum())} for i in range(len(scenes) + 1)], 'zero_valid_scene_count': sum(item['valid_pixel_count'] == 0 for item in series), 'time_origin': origin.isoformat(), 'time_axis_unit': 'elapsed_UTC_days', 'area_method': 'native_projected_grid', 'grid_crs': meta['crs'], 'reference_resolution_m': plan['index']['reference_resolution_m'], 'quality_warning': '无像元具备最低有效观测次数；值为空、预览透明，不能解读为指数或变化等于0。' if not valid.any() else '输出有效支持不足选区50%；无支持像元保持未知。' if int(valid.sum()) / count < .5 else '', 'series_support_note': 'all_valid均值使用逐景有效范围；common_support均值固定在所有日期共同有效像元，不能互相替代。'}
    directory = Path(directory);directory.mkdir(parents=True, exist_ok=True);task_id = directory.name;names = list(arrays);prefix = '/api/raster-processing/tasks/' + task_id
    announce('archive', '归档原生输出、逐景指数、时序明细与来源指纹')
    with rasterio.open(directory / 'analysis.tif', 'w', driver='GTiff', height=inside.shape[0], width=inside.shape[1], count=len(names), dtype='float32', crs=meta['crs'], transform=Affine(*meta['transform']), nodata=float('nan'), compress='deflate', predictor=3, tiled=True, blockxsize=256, blockysize=256) as dst:
        for i, name in enumerate(names, 1):
            check();dst.write(arrays[name].astype('float32'), i);dst.set_band_description(i, name)
        dst.update_tags(algorithm_version=VERSION, index_id=plan['index_id'], unit=unit, input_sha256=meta['input_sha256'], time_origin=origin.isoformat(), selection_sha256=manifest['selection_sha256'], minimum_observations=plan['minimum_observations'])
    image = _preview(arrays['value'], valid, meta, plan['index'], trend, directory / 'overlay.png');image['url'] = prefix + '/overlay.png'
    with (directory / 'series.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(series[0]));writer.writeheader();writer.writerows(series)
    west, south, east, north = plan['bbox'];geo = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'id': task_id + '-aoi', 'geometry': {'type': 'Polygon', 'coordinates': [[[west, south], [east, south], [east, north], [west, north], [west, south]]]}, 'properties': {'name': ALGORITHMS[plan['algorithm_id']]['name'] + '实际范围', 'algorithm_id': plan['algorithm_id'], 'index_id': plan['index_id'], 'valid_ratio': summary['valid_ratio'], 'mean_value': distribution['mean'], 'unit': unit}}]}
    provenance = {'algorithm_id': plan['algorithm_id'], 'algorithm_version': VERSION, 'source_type': 'public_satellite_pixels', 'source_id': plan['source_id'], 'sampling': 'native', 'index_id': plan['index_id'], 'index': copy.deepcopy(plan['index']), 'bbox': plan['bbox'], 'requested_period': {'start': plan['start'], 'end': plan['end']}, 'actual_dates': summary['actual_dates'], 'scene_manifest': manifest, 'input_sha256': meta['input_sha256'], 'native_reader_version': NATIVE_VERSION, 'source_details': meta, 'grid': {'crs': meta['crs'], 'transform': meta['transform'], 'rows': inside.shape[0], 'cols': inside.shape[1], 'cell_area_m2': meta['cell_area_m2'], 'reference_resolution_m': plan['index']['reference_resolution_m'], 'reference_scene_id': scenes[-1]['id']}, 'reference_resolution_m': plan['index']['reference_resolution_m'], 'minimum_observations': plan['minimum_observations'], 'mask_policy': 'each scene masked independently; count only finite quality-valid pixels; no interpolation; minimum independent UTC-date support', 'quality_reasons_by_scene': [{'scene_id': scene['id'], 'reasons': reason} for scene, reason in zip(scenes, reasons)], 'raster_evidence': {'band_descriptions': names, 'note': 'Per-scene index and validity bands reproduce the temporal reducer. Calibrated original source windows are not duplicated in this TIFF; asset/calibration/window hashes remain in source_details.'}, 'method_references': copy.deepcopy(REFERENCES), 'external_engine_called': False, 'provider_content_version_guaranteed': False}
    report = {'title': ALGORITHMS[plan['algorithm_id']]['name'], 'input': {k: copy.deepcopy(plan[k]) for k in ('requirement', 'source_id', 'index_id', 'bbox', 'start', 'end', 'scene_limit', 'cloud')}, 'findings': ['已读取全部%d个锁定独立UTC日期；没有因零有效像元静默删除场景。' % len(scenes), '%d/%d个原生像元达到最低%d次有效观测支持。' % (summary['valid_count'], count, plan['minimum_observations']), summary['series_support_note']] + ([summary['quality_warning']] if summary['quality_warning'] else []), 'limitations': copy.deepcopy(LIMITATIONS), 'provenance': provenance}
    result = {'summary': summary, 'geojson': geo, 'image': image, 'series': series, 'provenance': provenance, 'report': report, 'artifacts': [{'name': label, 'type': kind, 'url': prefix + '/' + name} for name, label, kind in [('analysis.tif', '原生时序分析与逐景指数', 'geotiff'), ('overlay.png', '配准时序结果预览', 'png'), ('result.geojson', '分析范围与结果摘要', 'geojson'), ('series.csv', '逐景时序与共同支持统计', 'csv'), ('result.json', '结果与完整来源报告', 'json')]]}
    (directory / 'result.geojson').write_text(json.dumps(geo, ensure_ascii=False, indent=2, allow_nan=False), 'utf-8')
    (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), 'utf-8');check();return result
