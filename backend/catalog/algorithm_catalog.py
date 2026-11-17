"""Machine-readable spectral contracts and metadata-only compatibility checks.

This module never fetches an asset or accepts a URL to execute. Matching a
trusted catalogue observation is permission to request the bounded executor,
not proof that clouds, coverage or all pixel values will pass its checks.
"""
import copy
import importlib.util
import math
import re
from datetime import datetime
from urllib.parse import urlsplit
from backend.core.geo_services import GeoError
from backend.analysis.spectral_indices import DEFINITIONS, specification

VERSION='2.0'
SOURCE_IDS=('pc_sentinel2_l2a','pc_landsat_c2_l2')
S2_KEYS={'blue':'B02','green':'B03','red':'B04','nir':'B08','rededge':'B05','nir_narrow':'B8A','swir1':'B11','swir2':'B12'}
LANDSAT_KEYS={'blue':'blue','green':'green','red':'red','nir':'nir08','swir1':'swir16','swir2':'swir22'}
UNITS={'','1','unitless','dimensionless','reflectance','surface_reflectance','reflectance_after_calibration','dn','digital_number','digital_numbers'}


def runtime_ready():
    return importlib.util.find_spec('rasterio') is not None and importlib.util.find_spec('numpy') is not None


def contract(source_id,index_id):
    spec=specification(index_id);land=source_id=='pc_landsat_c2_l2';keys=LANDSAT_KEYS if land else S2_KEYS
    available=source_id in spec['supported_source_ids']
    required=[keys[name] for name in spec['band_keys']] if available else []
    blocking=[] if available else [{'code':'SOURCE_INDEX_UNSUPPORTED','message':'本NDRE定义需要Sentinel-2 B8A/B05原生20m红边与窄NIR；Landsat8/9没有这些波段，不能替换算法。'}]
    quality=['qa_pixel','qa_radsat'] if land else ['SCL']
    return {'id':source_id+':'+index_id+':native-v1','version':VERSION,'index_id':index_id,
            'name':spec['label']+' · '+('Landsat 8/9' if land else 'Sentinel-2'),
            'formula':spec['formula'],'formula_kind':spec['formula_kind'],'index_version':spec['version'],'source_url':spec['source_url'],'input_units':'surface_reflectance_after_calibration',
            'available':available,'source_supported':available,'blocking_reasons':blocking,
            'supported_source_ids':copy.deepcopy(spec['supported_source_ids']),
            'value_range':copy.deepcopy(spec['value_range']),'threshold_range':copy.deepcopy(spec['threshold_range']),
            'visualization_range':copy.deepcopy(spec['visualization_range']),'histogram_policy':spec['histogram_policy'],
            'required_bands':required,'common_names':copy.deepcopy(spec['band_keys']),
            'required_quality_assets':quality,'source_ids':[source_id],
            'collection':'landsat-c2-l2' if land else 'sentinel-2-l2a',
            'platforms':['landsat-8','landsat-9'] if land else ['sentinel-2a','sentinel-2b','sentinel-2c'],
            'calibration_required_before_compute':True,
            'calibration_policy':{'id':'selected_asset_scale_offset' if land else 'product_xml_before_read',
                'method':'DN * scale + offset' if land else '(DN + BOA_ADD_OFFSET) / BOA_QUANTIFICATION_VALUE',
                'metadata_scale_required':land,'expected_scale':0.0000275 if land else None,'expected_offset':-0.2 if land else None,
                'note':'来源特定定标必须在执行器通过；不能将原始DN直接代入指数。'},
            'quality_policy':{'id':'landsat-oli-qa-pixel-radsat-v1','qa_pixel_excluded_bits':[0,1,2,3,4,5],
                'qa_radsat_rule':'all nonzero excluded (conservative)','water_bit_7_retained':True,
                'reflectance_rule':'no clipping; negative inputs and nonpositive denominator excluded'} if land else
                {'id':'sentinel2-scl-4567-v1','scl_retained':[4,5,6,7],
                 'reflectance_rule':'no clipping; negative inputs and nonpositive denominator excluded'},
            'window':{'max_bbox_span_deg':0.05,'max_cells':1048576,'aoi':'explicit WGS84 bounded rectangle'},
            'time':{'max_window_days':183,'scenes_per_period':1,'single_observation_or_two_nonoverlapping_periods':True,
                    'cross_sensor_change_supported':False},
            'formula_domain_policy':{'fixed_formula_dispatch':True,'clip_output':False,'negative_reflectance_excluded':True,
                'invalid_denominator_excluded':True,'negative_radicand_excluded':spec['id']=='msavi2',
                'denominator_epsilon':1e-12 if spec['formula_kind'] in ('evi2','savi','bsi') else 0},
            'resolution':{'reference_m':(30 if land else spec['reference_resolution_m']) if available else None,'native_reference':available,'resampling':'nearest for alignment; preview excluded from statistics'},
            'outputs':['native_index_geotiff','quality_and_aoi_masks','quantitative_statistics','map_preview','source_report'],
            'limitations':[spec['limitations'][0],'只匹配元数据，不证明像元已读取、有效覆盖或分类精度。',
                           '指标与阈值格网不能替代作物分类、法定耕地面积、土壤含水率或灾损认定。',
                           '只支持已实现的来源与小窗执行，不混合不同传感器构造变化结果。']}


def catalog():
    entries=[contract(source,index) for source in SOURCE_IDS for index in DEFINITIONS if source in specification(index)['supported_source_ids']]
    return {'schema_version':'1.0','version':VERSION,'contracts':entries,'algorithms':copy.deepcopy(entries),
            'runtime_available':runtime_ready(),'semantics':'声明可请求的具体执行适配；匹配元数据不等于已读像元或分析完成。',
            'source_ids':list(SOURCE_IDS),'supported_indices':list(DEFINITIONS)}


def _number(v):return not isinstance(v,bool) and isinstance(v,(int,float)) and math.isfinite(v)


def _trusted_href(href,source_id):
    if not isinstance(href,str) or not href or len(href)>2000 or any(ord(c)<33 for c in href):return False
    try:
        p=urlsplit(href)
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or p.query or p.fragment or '%' in p.path or '..' in p.path.split('/'):return False
        if source_id=='pc_landsat_c2_l2':
            return p.port is None and p.hostname=='landsateuwest.blob.core.windows.net' and p.path.startswith('/landsat-c2/level-2/standard/oli-tirs/') and '..' not in p.path and p.path.endswith('.TIF')
        return bool(re.fullmatch(r'sentinel2l2a\d{2}\.blob\.core\.windows\.net',p.hostname or '')) and p.path.startswith('/sentinel2-l2/') and bool(re.fullmatch(r'/[A-Za-z0-9_./-]+\.tif',p.path))
    except (ValueError,TypeError):return False


def _resolution(asset):
    value=asset.get('resolution')
    if isinstance(value,dict):value=value.get('value',value.get('native',value.get('spatial_resolution_m')))
    return value if _number(value) else None


def _matches(obs,c):
    source=c['source_ids'][0];gaps=[];reasons=[];assets=obs.get('assets');by_key={}
    def gap(code,message):
        if not any(g['code']==code and g['message']==message for g in gaps):gaps.append({'code':code,'message':message})
    for blocking in c.get('blocking_reasons',[]):gap(blocking['code'],blocking['message'])
    if obs.get('source_id')!=source:gap('SOURCE_EXECUTOR_UNSUPPORTED','该来源未接入此原生光谱执行器；目录/地形/雷达不能冒充可执行光学输入。')
    if obs.get('collection')!=c['collection']:gap('COLLECTION_INCOMPATIBLE','观测产品集合与该执行器不兼容。')
    platform=obs.get('platform');platform=platform.casefold() if isinstance(platform,str) else ''
    if platform not in c['platforms']:gap('PLATFORM_UNSUPPORTED','平台/传感器不在该产品执行器支持范围内。')
    try:
        date=obs.get('datetime')
        if not isinstance(date,str) or 'T' not in date:raise ValueError()
        parsed=datetime.fromisoformat(date.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError()
    except (ValueError,TypeError):gap('OBSERVATION_TIME_REQUIRED','需要有效且带时区的真实观测时刻。')
    if isinstance(assets,list):
        for asset in assets:
            if not isinstance(asset,dict) or not isinstance(asset.get('key'),str):continue
            key=asset['key']
            if key in by_key:gap('DUPLICATE_ASSET_KEY','观测含重复资产键，无法冻结唯一输入。')
            else:by_key[key]=asset
    else:gap('ASSETS_REQUIRED','观测缺少规范化资产列表。')
    for key in c['required_bands']+c['required_quality_assets']:
        a=by_key.get(key)
        if not a:gap('MISSING_ASSET','缺少算法必需资产：'+key);continue
        if a.get('auth_required') or a.get('requester_pays'):gap('ASSET_AUTH_REQUIRED','资产需要尚未接入的账户或付费授权：'+key)
        if not _trusted_href(a.get('href'),source):gap('ASSET_LOCATOR_UNTRUSTED','资产缺少可信、无签名的官方地址：'+key)
        media=a.get('media_type')
        if isinstance(media,str) and media and not any(v in media.lower() for v in ('image/tiff','image/geotiff','application/geotiff','image/x-geotiff')):
            gap('ASSET_TYPE_UNSUPPORTED','资产不是本执行器支持的GeoTIFF/COG：'+key)
        if key in c['required_quality_assets']:continue
        common=a.get('common_name')
        expected=(dict(zip(S2_KEYS.values(),S2_KEYS)) if source=='pc_sentinel2_l2a' else dict(zip(LANDSAT_KEYS.values(),LANDSAT_KEYS)))[key]
        if isinstance(common,str):
            common={'nir08':'nir' if source=='pc_landsat_c2_l2' else ('nir_narrow' if key=='B8A' else 'nir08'),
                    'rededge1':'rededge','rededge_1':'rededge','swir16':'swir1','swir22':'swir2'}.get(common,common)
            if common!=expected:gap('BAND_SEMANTICS_INCOMPATIBLE','波段键与通用波段含义不一致：'+key)
        unit=a.get('unit')
        if unit is not None and (not isinstance(unit,str) or unit.casefold().strip() not in UNITS):gap('INPUT_UNIT_INCOMPATIBLE','资产物理量不能按本反射率定标策略解释：'+key)
        if source=='pc_landsat_c2_l2':
            if not _number(a.get('scale')) or not math.isclose(a['scale'],0.0000275,rel_tol=1e-9):gap('CALIBRATION_REQUIRED','Landsat资产缺少或不符合产品比例因子：'+key)
            if not _number(a.get('offset')) or not math.isclose(a['offset'],-0.2,abs_tol=1e-12):gap('CALIBRATION_REQUIRED','Landsat资产缺少或不符合产品偏移：'+key)
            if isinstance(a.get('nodata'),bool) or a.get('nodata')!=0:gap('NODATA_POLICY_REQUIRED','Landsat资产需要明确产品nodata=0：'+key)
            if _resolution(a)!=30:gap('RESOLUTION_POLICY_REQUIRED','Landsat8/9光谱资产须声明原生30m：'+key)
        elif c['index_id'] in ('evi2','savi','msavi2','gndvi','ndre','bsi'):
            expected_resolution=20 if key in ('B05','B8A','B11','B12') else 10
            if _resolution(a)!=expected_resolution:gap('RESOLUTION_POLICY_REQUIRED','Sentinel-2光谱资产须声明该波段原生分辨率：'+key)
    stages=obs.get('stages') or {}
    if not isinstance(stages,dict):gap('INVALID_STAGES','观测接入阶段格式无效。');stages={}
    for field in ('pixel_readable','analysis_ready','executor_available'):
        if stages.get(field) is False:gap('EXECUTOR_UNAVAILABLE','该观测标识'+field+'=false，不能标为已可执行。')
    if not runtime_ready():gap('PIXEL_RUNTIME_UNAVAILABLE','当前环境缺少rasterio/numpy原生像元执行依赖。')
    if not gaps:
        reasons=['来源、产品、传感器、必需光谱与质量资产满足元数据契约。',
                 '定标与有效覆盖仍须由真实执行器逐像元核验；未读取像元。']
    return {'id':c['id'],'index_id':c['index_id'],'name':c['name'],'source_id':source,
            'compatible':not gaps,'ready':not gaps,'available':c['available'],'reasons':reasons,'gaps':gaps,
            'required_bands':copy.deepcopy(c['required_bands']),'required_quality_assets':copy.deepcopy(c['required_quality_assets']),
            'resolution_m':c['resolution']['reference_m'],'metadata_only':True,
            'calibration_required_before_compute':True}


def match_observation(observation,index_id=None):
    if not isinstance(observation,dict):raise GeoError('INVALID_OBSERVATION','算法匹配需要已入库观测对象',400)
    if index_id is not None and (not isinstance(index_id,str) or index_id not in DEFINITIONS):raise GeoError('INDEX_UNSUPPORTED','指数尚未注册，不能以其他指数替代',422)
    source=observation.get('source_id')
    # Unknown/unsupported providers deliberately get blocked S2 contracts. Merely
    # possessing two similarly named bands never selects an unimplemented reader.
    family=source if source in SOURCE_IDS else 'pc_sentinel2_l2a'
    matches=[_matches(observation,contract(family,index)) for index in ([index_id] if index_id else DEFINITIONS)]
    return {'schema_version':'1.0','version':VERSION,'observation_id':observation.get('id'),
            'observation_fingerprint':observation.get('fingerprint'),'source_id':source,
            'matches':matches,'ready_count':sum(m['ready'] for m in matches),'metadata_only':True,
            'pixels_read':False,'message':'兼容性只依据已保存元数据；执行还需明确AOI与时段、读取授权、像元质量和实际覆盖检查。'}
