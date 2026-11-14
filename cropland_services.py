"""Bounded AOI extraction of versioned annual cropland products, with real native pixels.

This is extraction of a published land-cover classification, not a new classifier
for current imagery, cadastral parcels, or a guarantee that all real fields exist.
"""
import copy
import csv
import hashlib
import json
import math
import re
import threading
import uuid
from pathlib import Path

from geo_services import GeoError, finite
from io_lulc_reader import (IOLulcReader, PRODUCT as IO_PRODUCT,
                            vectorize_crops as vectorize_io_crops, make_preview as make_io_preview)
from landcover_services import (PRODUCT, SOURCE_TEMPLATE, account_native, available,
                                covering_tiles, ellipsoid_row_pixel_area,
                                make_preview, now)

MAX_SPAN = .05
MAX_CELLS = 1_048_576
MAX_PATCHES = 20_000
LIMITATIONS = [
    '提取的是ESA WorldCover 2021 v200的Cropland（类别40），不是当期遥感影像的新识别结果，也不是当前官方耕地调查。',
    '图斑是10米级分类像元的四邻域连通范围，不是田埂、确权地块、永久基本农田或高标准农田边界。',
    'WorldCover的Cropland以一年生作物耕作为主，不覆盖木本果园或温室等所有农业利用类型，也不区分水稻、小麦等具体作物。',
    '选区采用原生像元中心掩膜，边缘图斑保留完整像元，可能超出框选边缘半个像元；面积按同一原生像元口径计算。',
    '全量指选区内该产品所有类别40像元，不保证实际农田无漏检或无误检；未做本地独立精度验证及面积误差校正。',
    '2021分类产品与当前底图可能存在时间差；PNG仅供显示，定量结果由原生GeoTIFF逐行椭球面积计算。',
    '不同年度产品的类别定义和统计方法可能不同，不能把WorldCover2021与IO2024面积差直接解释为耕地增减。',
]
STEPS = [('scope', '核验选区与2021产品口径'), ('pixels', '读取选区全量原生分类像元'),
         ('patches', '提取耕作地连通图斑并逐斑计量'), ('map', '生成配准分布与GIS文件'),
         ('report', '核对图斑与栅格面积并归档')]
ARTIFACTS = ('overlay.png', 'classes.png', 'classification.tif', 'cropland.geojson',
             'patches.csv', 'row_counts.csv', 'result.json')


PRODUCTS = {
    PRODUCT['id']: {**PRODUCT, 'source_id':'worldcover_2021',
                    'area_method':'wgs84_ellipsoid_latitude_band', 'limitations':list(LIMITATIONS)},
    IO_PRODUCT['id']: copy.deepcopy(IO_PRODUCT),
}


def product_profile(product_id=PRODUCT['id']):
    if not isinstance(product_id,str) or product_id not in PRODUCTS:
        raise GeoError('YEAR_MISMATCH','仅支持明确登记的WorldCover2021或IO2024年度分类产品，不自动换年',422)
    return copy.deepcopy(PRODUCTS[product_id])


def requests_subannual_period(requirement):
    """Annual maps cannot answer month/day windows or agricultural seasons.

    The check deliberately treats concrete dates as temporal constraints even
    when their year matches the selected product. No annual fallback is made.
    """
    date_or_period = re.search(
        r'(?<!\d)(?:19|20|21)\d{2}\s*[-/.／．－]\s*\d{1,2}(?!\d)'
        r'|(?:\d{1,2}|[一二三四五六七八九十正冬腊]{1,3})\s*月(?:份)?'
        r'|[一二三四1234]\s*季度|上半年|下半年'
        r'|(?<![a-z])q[1-4](?![a-z0-9])'
        r'|(?<!\d)(?:19|20|21)\d{2}\s*年?\s*[春夏秋冬]',
        requirement, re.IGNORECASE)
    season = any(word in requirement for word in (
        '春季','夏季','秋季','冬季','春天','夏天','秋天','冬天',
        '春夏','夏秋','秋冬','冬春','春播','夏播','秋播','冬播',
        '春耕','夏收','秋收','冬种','早稻','晚稻','双季',
        '季节','播期','播种期','收获期','生长期','作物季',
        '本季','上一季','下一季','这季'))
    english_season = re.search(r'(?<![a-z])(?:spring|summer|autumn|fall|winter|seasonal)(?![a-z])',requirement,re.IGNORECASE)
    return bool(date_or_period or season or english_season)


def make_plan(body):
    if not isinstance(body, dict) or set(body) - {'bbox', 'product', 'requirement'}:
        raise GeoError('INVALID_CROPLAND_REQUEST', '农田标注请求包含不支持字段', 400)
    profile=product_profile(body.get('product',PRODUCT['id']));year=profile['year'];code=profile['class_code']
    bbox = body.get('bbox')
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or not all(finite(v) for v in bbox)):
        raise GeoError('AOI_REQUIRED', '请先在地图框选范围，再提取年度耕作地参考图斑', 422)
    west, south, east, north = map(float, bbox)
    if not (-180 <= west < east <= 180 and -60 <= south < north <= 85):
        raise GeoError('INVALID_BBOX', '选区须为已登记产品覆盖纬度内的WGS84经纬度矩形', 422)
    if east - west > MAX_SPAN + 1e-10 or north - south > MAX_SPAN + 1e-10:
        raise GeoError('AOI_TOO_LARGE', '选区每边须不超过0.05度；请缩小选区后提取全量原生图斑', 422)
    if (math.ceil((east-west)*12000)+2)*(math.ceil((north-south)*12000)+2) > MAX_CELLS:
        raise GeoError('AOI_GRID_LIMIT', '选区超过本机全量原生像元处理上限', 422)
    requirement = body.get('requirement', '提取选定区域内'+str(year)+'年耕作地参考图斑并统计面积')
    if not isinstance(requirement, str) or not requirement.strip() or len(requirement) > 2000:
        raise GeoError('INVALID_REQUIREMENT', '需求须为受限非空文本', 400)
    years = re.findall(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)', requirement)
    temporal_text = re.sub(r'当前(?:地图)?(?:选定的?|选中的?|所选的?)?(?:框选范围|地图范围|地图选区|范围|选区|区域|窗口)', '', requirement)
    relative_terms = ('今年', '本年', '当年', '当期', '实时', '最新', '现状', '当前', '现在', '目前',
                      '最近', '近期', '近年', '去年', '前年', '上年', '明年', '后年', '下年',
                      '本年度', '上年度', '下年度', '现时')
    if (any(y != str(year) for y in years) or any(w in temporal_text for w in relative_terms)
            or re.search(r'[二两〇零一三四五六七八九]{4}年', requirement)):
        raise GeoError('YEAR_MISMATCH', '请求涉及其他年度或当期；所选产品固定为'+str(year)+'年，不能静默替换', 422)
    if requests_subannual_period(requirement):
        raise GeoError('YEAR_MISMATCH', '年度分类产品不提供指定日/月窗口、季度、季节或播期农田识别，不能以年度结果替代指定时相', 422)
    if any(w in requirement for w in ('确权', '高标准农田', '基本农田', '法定', '官方',
                                      '水田', '旱地', '水稻', '小麦', '玉米', '油菜', '棉花', '大豆',
                                      '作物分类', '播种面积', '种植面积', '复种', '果园', '温室', '大棚',
                                      '建设用地', '草地', '林地', '森林', '水体', '池塘', '变化', '增加', '减少', '对比', '比较', '趋势', '增减')):
        raise GeoError('CLASSIFICATION_SCOPE_UNSUPPORTED', '年度覆盖类别不支持该法定、工程或作物细分口径', 422)
    if any(w in requirement for w in ('全省', '全国', '全市', '全县', '省域', '全湖北')):
        raise GeoError('FULL_REGION_NOT_SUPPORTED', '本能力处理地图选区，不能将全市或省域需求替换为小范围结果', 422)
    method=('IO2024类别5全原生像元中心掩膜；四邻域图斑；UTM10米投影格网面积'
            if year==2024 else 'WorldCover2021类别40全原生像元中心掩膜；四邻域图斑；逐行WGS84椭球面积')
    return {'bbox': [west, south, east, north], 'product':profile,
            'reference_year':year,'class_code':code,'source_id':profile['source_id'],
            'area_method':profile['area_method'],'requirement':requirement.strip(),
            'operation':'published_classification_extraction','method':method,
            'mask_rule':'pixel center inside selected rectangle; all_touched=False',
            'connectivity':4,'statistics_resampled':False,'limitations':list(profile['limitations'])}


def capabilities():
    return {'available':available(),'product':product_profile(),
            'products':[product_profile(ident) for ident in PRODUCTS],
            'operation':'published_classification_extraction','requires_bbox':True,
            'max_span_degrees':MAX_SPAN,'max_pixels':MAX_CELLS,
            'max_patch_count':MAX_PATCHES,'limitations':list(LIMITATIONS)}


def selection_boundary(bbox):
    west, south, east, north = bbox
    boundary = {'type': 'FeatureCollection', 'features': [{'type': 'Feature',
                'properties': {'name': '用户框选范围', 'source_type': 'user_selected_aoi'},
                'geometry': {'type': 'Polygon', 'coordinates': [[[west, south], [east, south],
                             [east, north], [west, north], [west, south]]]}}]}
    raw = json.dumps(boundary, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    metadata = {'bbox': bbox, 'provider': '用户在地图框选的WGS84矩形',
                'sha256': hashlib.sha256(raw).hexdigest(), 'boundary_year_represented': None,
                'validation': {}}
    return boundary, metadata


def vectorize_cropland(native_path, output_dir, check=lambda: None):
    """Polygonize all class40 components, preserving holes and exact native area.

    The geometry is never simplified. Per-patch counts use geometry in its own
    pixel window, and the aggregate must equal a separate whole-raster count.
    """
    import numpy as np
    import rasterio
    from rasterio.features import bounds as geometry_bounds, geometry_mask, shapes
    from rasterio.windows import Window
    features = []
    total_pixels = 0
    total_area = 0.
    with rasterio.open(native_path) as src:
        if src.width * src.height > MAX_CELLS:
            raise GeoError('AOI_GRID_LIMIT', '原生格网超过安全处理上限，停止而非降采样')
        data = src.read(1)
        crop = data == 40
        row_areas = np.asarray([ellipsoid_row_pixel_area(
            src.transform.f + r * src.transform.e,
            src.transform.f + (r+1) * src.transform.e, src.transform.a)
            for r in range(src.height)])
        expected_pixels = int(crop.sum())
        expected_area = float(np.dot(crop.sum(axis=1, dtype='int64'), row_areas))
        for index, (geometry, value) in enumerate(shapes(data, mask=crop, connectivity=4, transform=src.transform)):
            check()
            if index >= MAX_PATCHES:
                raise GeoError('PATCH_LIMIT', '选区碎斑超过20000个；请缩小选区，系统不会过滤小图斑冒充全量结果', 422)
            if int(value) != 40:
                raise GeoError('CLASS_CODE_MISMATCH', '图斑提取出现非耕作地类别')
            left, bottom, right, top = geometry_bounds(geometry)
            c0 = max(0, round((left-src.transform.c)/src.transform.a))
            c1 = min(src.width, round((right-src.transform.c)/src.transform.a))
            r0 = max(0, round((src.transform.f-top)/(-src.transform.e)))
            r1 = min(src.height, round((src.transform.f-bottom)/(-src.transform.e)))
            mask = geometry_mask([geometry], out_shape=(r1-r0, c1-c0),
                                 transform=src.window_transform(Window(c0, r0, c1-c0, r1-r0)),
                                 invert=True, all_touched=False)
            if np.any(mask & (data[r0:r1, c0:c1] != 40)):
                raise GeoError('PATCH_MASK_MISMATCH', '图斑包含非耕作地像元，停止发布')
            per_row = mask.sum(axis=1, dtype='int64')
            pixel_count = int(per_row.sum())
            area = float(np.dot(per_row, row_areas[r0:r1]))
            total_pixels += pixel_count
            total_area += area
            patch_id = 'crop-' + str(index+1).zfill(6)
            features.append({'type': 'Feature', 'id': patch_id, 'geometry': geometry,
                             'properties': {'patch_id': patch_id, 'class_code': 40,
                             'class_name': '耕作地参考图斑', 'reference_year': 2021,
                             'pixel_count': pixel_count, 'area_m2': area,
                             'area_ha': area/10000, 'area_mu': area/10000*15,
                             'connectivity': 4, 'is_cadastral_parcel': False}})
    if total_pixels != expected_pixels or abs(total_area-expected_area) > max(.001, expected_area*1e-10):
        raise GeoError('PATCH_AREA_BALANCE_ERROR', '图斑与独立栅格面积/像元数不守恒，停止发布')
    geojson = {'type': 'FeatureCollection', 'features': features,
               'metadata': {'product': PRODUCT['name'], 'year': 2021, 'class_code': 40,
                            'crs': 'EPSG:4326', 'connectivity': 4,
                            'geometry_simplified': False, 'is_cadastral_parcel': False,
                            'limitations': list(LIMITATIONS)}}
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'cropland.geojson').write_text(json.dumps(geojson, ensure_ascii=False, separators=(',', ':')), 'utf-8')
    with (directory/'patches.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        keys = ['patch_id', 'class_code', 'reference_year', 'pixel_count', 'area_m2', 'area_ha', 'area_mu', 'connectivity', 'is_cadastral_parcel']
        writer = csv.DictWriter(stream, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        for feature in features:
            writer.writerow(feature['properties'])
    return {'geojson': geojson, 'patch_count': len(features), 'pixel_count': total_pixels,
            'area_m2': total_area, 'balance_verified': True}


class CroplandServices:
    def __init__(self, store, output_dir=None, source_paths=None, io_reader=None):
        self.store = store
        self.output_dir = Path(output_dir or Path(store.db_path).parent/'cropland')
        # Test-only injection is constructor-only. No URL or path is accepted in API input.
        self.source_paths = source_paths
        self.io_reader = io_reader or IOLulcReader()
        self.stopping = False
        self.threads = []
        for task in store.list('cropland_task'):
            if task['status'] in ('queued', 'running'):
                task.update(status='failed', error={'code': 'INTERRUPTED', 'message': '服务重启，选区农田标注中断；请重试'}, finished_at=now())
                for node in task.get('nodes', []):
                    if node['status'] in ('running', 'pending'):
                        node.update(status='failed', message='服务重启中断', finished_at=now())
                store.save('cropland_task', task)

    def get(self, task_id):
        return self.store.get('cropland_task', task_id)

    def list(self):
        return self.store.list('cropland_task')

    def create(self, body):
        plan = make_plan(body)
        task = {'id': 'cropland_'+uuid.uuid4().hex[:14], 'name': '选区耕作地参考图斑 · '+str(plan['reference_year']),
                'status': 'queued', 'progress': 0, 'plan': plan, 'snapshot': copy.deepcopy(plan),
                'nodes': [{'id': ident, 'name': label, 'status': 'pending', 'message': '等待执行'} for ident, label in [(ident,label.replace('2021',str(plan['reference_year']))) for ident,label in STEPS]],
                'logs': [{'time': now(), 'message': '提取'+str(plan['reference_year'])+'公开分类产品耕作地，保留全部原生像元；不是当期影像新识别'}],
                'result': None, 'created_at': now()}
        with self.store.lock:
            if self.stopping:
                raise GeoError('SERVICE_STOPPING', '服务正在关闭', 503)
            if any(t['status'] in ('queued', 'running') for t in self.list()):
                raise GeoError('CROPLAND_BUSY', '本机已有选区耕作地图斑任务运行中', 429)
            self.store.save('cropland_task', task)
            thread = threading.Thread(target=self.execute, args=(task['id'],), daemon=True)
            self.threads.append(thread)
            thread.start()
        return task

    def check(self, task_id):
        if self.stopping:
            raise GeoError('INTERRUPTED', '服务关闭，停止处理')
        if self.get(task_id)['status'] == 'cancelled':
            raise GeoError('CANCELLED', '用户取消')

    def change(self, task_id, fn):
        with self.store.lock:
            task = self.get(task_id)
            if task['status'] in ('succeeded', 'failed', 'cancelled'):
                return False
            fn(task)
            self.store.save('cropland_task', task)
            return True

    def execute(self, task_id):
        directory = self.output_dir/task_id
        statistics = vector = preview = None
        try:
            task = self.get(task_id)
            plan = task['plan']
            profile=product_profile(plan['product']['id']);year=profile['year'];code=profile['class_code'];is_io=profile['id']=='io_lulc_2024'
            boundary, metadata = selection_boundary(plan['bbox'])
            for index,(ident,name) in enumerate([(ident,label.replace('2021',str(year))) for ident,label in STEPS]):
                self.check(task_id)
                def start(t):
                    t.update(status='running')
                    t['nodes'][index].update(status='running', started_at=now(), message=name)
                    t['logs'].append({'time': now(), 'message': '开始：'+name})
                if not self.change(task_id, start):
                    return
                if ident == 'scope':
                    if not available():
                        raise GeoError('RASTER_ENGINE_UNAVAILABLE', '当前Python缺少rasterio/numpy/Pillow')
                    summary = {'bbox': plan['bbox'], 'reference_year':year,'mask_rule':plan['mask_rule']}
                elif ident == 'pixels':
                    def progress(fraction, detail):
                        self.check(task_id)
                        def update(t):
                            t['progress'] = 5 + round(fraction*60)
                            t['nodes'][index]['output_summary'] = detail
                            t['nodes'][index]['message'] = '选区原生格网已读取%.1f%%' % (fraction*100)
                        self.change(task_id, update)
                    statistics = (self.io_reader.account(plan['bbox'],metadata,directory,lambda:self.check(task_id),progress,max_cells=MAX_CELLS) if is_io else account_native(boundary, metadata, directory, lambda: self.check(task_id), progress, source_urls=self.source_paths))
                    if math.prod(statistics['summary']['raster_dimensions']) > MAX_CELLS:
                        raise GeoError('AOI_GRID_LIMIT', '真实原生格网超过处理上限')
                    summary = statistics['summary']
                    summary.update(bbox=plan['bbox'],method=plan['method'],reference_year=year,class_code=code,source_id=profile['source_id'],area_method=profile['area_method'],
                                   accuracy_note='未经该选区本地独立样本验证或面积误差校正，非官方耕地调查；全量仅指所选产品作物覆盖类别像元')
                elif ident == 'patches':
                    vector = (vectorize_io_crops(statistics['native_path'],directory,lambda:self.check(task_id),max_cells=MAX_CELLS,max_patches=MAX_PATCHES) if is_io else vectorize_cropland(statistics['native_path'],directory,lambda:self.check(task_id)))
                    if vector['pixel_count'] != statistics['summary']['cropland_pixel_count'] or abs(vector['area_m2']/10000-statistics['summary']['cropland_ha']) > 1e-7:
                        raise GeoError('PATCH_AREA_BALANCE_ERROR', '图斑与原生分类统计不守恒')
                    summary = {'patch_count': vector['patch_count'], 'cropland_pixel_count': vector['pixel_count'], 'area_balance_verified': True, 'connectivity': 4}
                elif ident == 'map':
                    preview = (make_io_preview if is_io else make_preview)(statistics['native_path'],directory)
                    summary = {'display_crs': preview['display_crs'], 'geometry_simplified': False, 'statistics_resampled': False}
                else:
                    summary = {'valid_coverage_ratio': statistics['summary']['coverage_ratio'], 'area_balance_verified': True,
                               'all_product_cropland_pixels_retained': True, 'patch_count': vector['patch_count']}
                self.check(task_id)
                def finish(t):
                    t['nodes'][index].update(status='succeeded', finished_at=now(), message='实际处理完成', output_summary=summary)
                    t['progress'] = [5, 65, 85, 95, 100][index]
                    t['logs'].append({'time': now(), 'message': '完成：'+name})
                    if ident == 'report':
                        base = '/api/cropland/tasks/'+task_id+'/'
                        statistics['summary'].update(patch_count=vector['patch_count'], area_balance_verified=True,
                            connectivity=4, geometry_simplified=False, is_cadastral_parcel=False,
                            operation='published_classification_extraction', mask_rule=plan['mask_rule'])
                        image = {**preview, 'url': base+'overlay.png', 'classes_url': base+'classes.png',
                                 'legend': [{'color':'#3cc889' if is_io else '#f096ff','label':str(year)+'耕作地参考图斑'}]}
                        provenance = {'product':copy.deepcopy(profile),'year':year,'class_code':code,'source_id':profile['source_id'],'area_method':profile['area_method'],'native_crs':statistics['summary'].get('native_crs','EPSG:4326'),'grid_resolution_m':statistics['summary'].get('grid_resolution_m'),
                            'operation': 'published_classification_extraction', 'source_type': 'public_classification_product',
                            'selected_bbox': plan['bbox'], 'selection_sha256': metadata['sha256'],
                            'source_tiles':statistics['source_tiles'],'source_binding':copy.deepcopy(statistics.get('source_binding')), 'classified_window_sha256': statistics['classified_window_sha256'],
                            'method': plan['method'], 'mask_rule': plan['mask_rule'], 'connectivity': 4,
                            'geometry_simplified': False, 'statistics_resampled': False,
                            'local_accuracy_ci95': None, 'created_at': now()}
                        findings = ['%d年度产品类别%d共%d个连通参考图斑，%.3f公顷（%.3f亩）。' % (
                            year,code,vector['patch_count'], statistics['summary']['cropland_ha'], statistics['summary']['cropland_mu']),
                            '选区原生有效分类覆盖率%.6f%%，未分类/缺测%.3f公顷。' % (
                            statistics['summary']['coverage_ratio']*100, statistics['summary']['nodata_area_ha']),
                            '图斑像元数及逐斑面积之和与完整原生栅格独立统计一致；无小斑过滤、无矢量简化。']
                        result = {'bbox': plan['bbox'], 'summary': statistics['summary'], 'classes': statistics['classes'],
                            'geojson': vector['geojson'], 'boundary': boundary, 'image': image, 'provenance': provenance,
                            'report': {'title':'选区'+str(year)+'年耕作地参考标注报告', 'findings': findings,
                                       'limitations':list(profile['limitations']),'steps':copy.deepcopy(t['nodes'])},
                            'artifacts': [{'name': label, 'url': base+filename} for filename, label in [
                                ('cropland.geojson', '耕作地参考图斑GeoJSON'), ('classification.tif', '原生分类GeoTIFF'),
                                ('patches.csv', '逐图斑面积CSV'), ('row_counts.csv', '逐行分类复算CSV'),
                                ('result.json', '完整结果JSON'), ('overlay.png', '配准耕作地分布PNG')]]}
                        directory.mkdir(parents=True, exist_ok=True)
                        (directory/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
                        t.update(status='succeeded', result=result, finished_at=now())
                if not self.change(task_id, finish):
                    return
        except Exception as error:
            code = error.code if isinstance(error, GeoError) else 'CROPLAND_FAILED'
            message = error.message if isinstance(error, GeoError) else '选区耕作地标注失败；未用模拟图斑或NDVI阈值替代，请检查官方数据网络与引擎'
            def fail(t):
                t.update(status='failed', error={'code': code, 'message': message}, finished_at=now())
                t['logs'].append({'time': now(), 'message': message})
                for node in t['nodes']:
                    if node['status'] in ('running', 'pending'):
                        node.update(status='failed', message=message, finished_at=now())
            self.change(task_id, fail)

    def artifact(self, task_id, name):
        task = self.get(task_id)
        if task['status'] != 'succeeded' or name not in ARTIFACTS:
            raise GeoError('ARTIFACT_UNAVAILABLE', '成果尚未完成或不存在', 404)
        path = self.output_dir/task_id/name
        if not path.is_file():
            raise GeoError('ARTIFACT_UNAVAILABLE', '成果文件不可用', 404)
        return path

    def cancel(self, task_id):
        with self.store.lock:
            task = self.get(task_id)
            if task['status'] not in ('queued', 'running'):
                raise GeoError('INVALID_STATE', '只有待处理或运行任务可取消', 409)
            task.update(status='cancelled', finished_at=now())
            task['logs'].append({'time': now(), 'message': '用户取消，不发布未完成结果'})
            for node in task['nodes']:
                if node['status'] in ('pending', 'running'):
                    node.update(status='cancelled', finished_at=now(), message='用户取消')
            return self.store.save('cropland_task', task)

    def retry(self, task_id):
        old = self.get(task_id)
        if old['status'] not in ('failed', 'cancelled'):
            raise GeoError('INVALID_STATE', '仅失败或取消任务可重试', 409)
        return self.create({'bbox': old['plan']['bbox'], 'product': old['plan']['product']['id'], 'requirement': old['plan']['requirement']})

    def close(self):
        self.stopping = True
        for thread in self.threads:
            thread.join(timeout=1)
