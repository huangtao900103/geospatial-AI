"""Bounded, provenance-aware raster production. Never substitutes samples for public pixels."""
import copy
import calendar
import queue
import time
import xml.etree.ElementTree as ET
import hashlib
import importlib.util
import json
import math
import re
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from geo_services import GeoError, finite, polygon_area_m2
from spectral_indices import choose_index,specification,available_indices,BANDS,detect_indices
from native_raster import VERSION as NATIVE_VERSION

VERSION = "1.0.0"
SAMPLE_BBOX = [113.36, 30.30, 113.40, 30.34]
SCL_RETAINED = [4, 5, 6, 7]
STEPS = [
    ("validate", "需求与能力校验", "production.validator", ["requirement", "aoi", "periods"], ["validated_plan"]),
    ("discover", "组织波段与时相", "production.source_selector", ["validated_plan"], ["source_bindings"]),
    ("read", "读取配准波段小窗", "production.raster_window", ["source_bindings"], ["aligned_bands"]),
    ("quality", "质量掩膜与反射率", "production.quality_mask", ["aligned_bands"], ["valid_masks"]),
    ("compute", "NDVI与光谱变化计算", "production.ndvi_change", ["aligned_bands", "valid_masks"], ["ndvi", "delta", "statistics"]),
    ("publish", "GIS成果与来源报告", "production.geojson_report", ["ndvi", "statistics"], ["geojson", "report", "provenance"]),
]


def now(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
def ident(): return "production_" + uuid.uuid4().hex[:14]
def sha(obj): return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def raster_available():
    return importlib.util.find_spec("rasterio") is not None and importlib.util.find_spec("numpy") is not None


def capabilities():
    ready = raster_available()
    return {"modes": [{"id": "sentinel_cog", "name": "公开遥感原生影像", "available": ready, "sampling": "native", "description": "按实际传感器定标和质量规则读取公开Sentinel-2或Landsat 8/9 COG；使用所选指数的原生参考格网。", "reason": "" if ready else "当前服务Python环境缺少rasterio/numpy，不能读取影像像素。"}],
            "goals": [{"id": "ndvi", "name": "单期光谱指数"}, {"id": "change", "name": "两期光谱变化"}], "default_bbox": None,
            "default_mode": "sentinel_cog", "default_sampling": "native", "native_sampling": {"available":ready,"algorithm_version":NATIVE_VERSION,"max_bbox_span_deg":.05,"max_cells":1048576,"reference":"per-index original reference grid: Sentinel-2 10/20m or Landsat 8/9 30m; required bands and quality masks aligned"}, "algorithm_version": VERSION, "indices":available_indices(ready),
            "limitations": ["必须明确分析范围与观测时间；数据不可用时如实失败，不填充替代结果。", "仅处理≤0.05°局部AOI；按原生参考格网统计，光谱指标不等于作物分类或法定调查。", "历史公开影像代表网格成果仅供查阅，不提供新增代表网格运行模式。", "通用大模型尚未接入；当前为规则辅助需求组织。"]}


def txt(value, name, maximum=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GeoError("INVALID_PRODUCTION_INPUT", name + "必须为非空且长度受限的文本", 400)
    return value.strip()


def parse_periods(requirement, supplied, goal):
    defaulted = False
    if supplied is None:
        dates = re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", requirement)
        years = list(dict.fromkeys(re.findall(r"(?<!\d)(20\d{2})(?!\d)", requirement)))
        if len(dates)>=4:
            supplied={"before":dates[:2],"after":dates[2:4]}
        elif len(dates) >= 2:
            try: first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[1])
            except ValueError: raise GeoError("INVALID_PERIODS", "需求中的日期无效")
            if goal == "change":
                supplied = {"before": [(first-timedelta(days=15)).isoformat(), (first+timedelta(days=15)).isoformat()], "after": [(last-timedelta(days=15)).isoformat(), (last+timedelta(days=15)).isoformat()]}
            else:
                supplied = {"before": [(first-timedelta(days=60)).isoformat(), (first-timedelta(days=1)).isoformat()], "after": [first.isoformat(), last.isoformat()]}
        else:
            months = re.findall(r"(?:(20\d{2})年\s*)?(\d{1,2})月", requirement)
            year = int(years[0]) if years else 2025
            ym=[]
            for y,m in months:
                if y: year=int(y)
                month=int(m)
                if not 1<=month<=12:raise GeoError("INVALID_PERIODS","需求月份无效")
                ym.append((year,month))
            def window(y,m):return [date(y,m,1).isoformat(),date(y,m,calendar.monthrange(y,m)[1]).isoformat()]
            if len(ym)>=2:
                first,last=window(*ym[0]),window(*ym[1])
                supplied={"before":first,"after":last} if goal=="change" else {"before":[(date.fromisoformat(first[0])-timedelta(days=60)).isoformat(),(date.fromisoformat(first[0])-timedelta(days=1)).isoformat()],"after":[first[0],last[1]]}
            elif len(ym)==1:
                y,m=ym[0];prev=(y-1,12) if m==1 else (y,m-1)
                supplied={"before":window(*prev),"after":window(y,m)}
            elif len(years)>=2:
                supplied={"before":[years[0]+"-09-01",years[0]+"-11-30"],"after":[years[1]+"-09-01",years[1]+"-11-30"]}
            elif len(years)==1:
                supplied={"before":[str(int(years[0])-1)+"-09-01",str(int(years[0])-1)+"-11-30"],"after":[years[0]+"-09-01",years[0]+"-11-30"]}
            else:
                defaulted=True
                supplied={"before":["2025-09-01","2025-11-30"],"after":["2026-09-01","2026-11-30"]}
    if not isinstance(supplied, dict) or set(supplied) != {"before", "after"}:
        raise GeoError("INVALID_PERIODS", "periods必须含before、after两个日期窗口")
    parsed = {}
    for key in ("before", "after"):
        pair = supplied[key]
        if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(d, str) or len(d) != 10 for d in pair):
            raise GeoError("INVALID_PERIODS", "时相窗口必须为[start,end]的YYYY-MM-DD日期")
        try: first, last = map(date.fromisoformat, pair)
        except ValueError: raise GeoError("INVALID_PERIODS", "时相日期无效")
        if first > last or (last-first).days > 183:
            raise GeoError("INVALID_PERIODS", "每个时相须开始不晚于结束，最长183天")
        parsed[key] = [first.isoformat(), last.isoformat()]
    if parsed["before"][1] >= parsed["after"][0]:
        raise GeoError("OVERLAPPING_PERIODS", "before必须早于after，两个检索窗口不能重叠")
    return parsed, defaulted


def selection_parameters(requirement, body, goal, direction):
    """Conservative comparison parser: never extract years/cloud values as NDVI thresholds."""
    natural=None;operator='gte';percent=False
    for clause in re.split(r'[,，;；。\n]',requirement):
        lower=clause.lower()
        if '云量' in clause and not detect_indices(clause) and not any(w in lower for w in ('指数','下降','上升','变化','阈值')):continue
        if not detect_indices(clause) and not any(w in lower for w in ('指数','下降','上升','减少','增加','变化','阈值')):continue
        match=re.search(r'(不高于|不低于|至少|至多|低于|小于|高于|大于|超过|>=|<=|≥|≤|>|<|阈值\s*(?:为|是|=|:|：)?)\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(%)?',clause)
        if not match:match=re.search(r'(下降|上升|减少|增加)\s*(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(%)?',clause)
        if match:
            word,number,mark=match.groups();natural=float(number);percent=bool(mark)
            operator='lte' if word in ('不高于','至多','<=','≤') else 'lt' if word in ('低于','小于','<') else 'gt' if word in ('高于','大于','超过','>') else 'gte'
            break
    threshold=body.get('threshold', natural if natural is not None else .2)
    cloud_match=re.search(r'云量\s*(?:低于|小于|不高于|至多|上限|≤|<=)?\s*(\d+(?:\.\d+)?)',requirement)
    cloud=body.get('cloud',float(cloud_match.group(1)) if cloud_match else 40)
    comparison=operator
    label={'lt':'低于','lte':'不高于','gt':'高于','gte':'不低于'}[comparison]
    field='after_ndvi' if goal=='ndvi' else 'decrease_magnitude' if direction=='decrease' else 'increase_magnitude' if direction=='increase' else 'absolute_delta_ndvi'
    selection={'field':field,'operator':comparison,'threshold':threshold,'threshold_source':'request_body' if 'threshold' in body else 'natural_language' if natural is not None else 'default','cloud_source':'request_body' if 'cloud' in body else 'natural_language' if cloud_match else 'default','description':field+' '+label+' '+str(threshold)}
    return threshold,cloud,selection,percent


def selected_value(value, threshold, operator):
    return value<threshold if operator=='lt' else value<=threshold if operator=='lte' else value>threshold if operator=='gt' else value>=threshold


def make_plan(body):
    if not isinstance(body, dict) or set(body)-{"requirement", "mode", "region", "bbox", "goal", "threshold", "cloud", "periods", "direction", "sampling", "index_id", "source_id", "observation_id", "scene_id", "observation_ref"}:
        raise GeoError("INVALID_PRODUCTION_INPUT", "生产请求包含不支持字段", 400)
    requirement = txt(body.get("requirement"), "需求")
    index=choose_index(requirement,body.get("index_id"))
    if any(w in requirement for w in ("高标准农田","确权","法定","官方耕地","耕地保有","黄鳝","禁捕","播种面积","尾水污染","灾损","受灾损失","建筑面积","建筑物数量")) and index["id"]!="ndvi":raise GeoError("BUSINESS_METRIC_UNSUPPORTED","光谱指数不能替代未接入业务认定或面积口径",422)
    mode = body.get("mode", "sample_pixels")
    if mode not in ("sample_pixels", "sentinel_cog"):
        raise GeoError("INVALID_MODE", "mode仅支持sample_pixels或sentinel_cog", 400)
    # A crop-area inventory requires classification and complete spatial coverage.
    # Never replace it with the default NDVI reference window, even with a bbox.
    if re.search(r"(?:耕地|农田).{0,12}(?:面积|多少亩|多少公顷)|(?:面积|多少亩|多少公顷).{0,12}(?:耕地|农田)", requirement):
        raise GeoError("CROPLAND_AREA_REQUIRES_CLASSIFICATION",
                       "已识别农田/耕地面积统计需求。需明确统计年度、地类口径、分析行政边界，并具备耕地分类、全覆盖面积核算及独立精度验证；当前仅支持有界光谱指数分析，不能给出耕地总面积，也不会改成仙桃参考样区。", 422)
    if any(term in requirement for term in ("全省", "全市", "全县", "全域", "全境")):
        raise GeoError("FULL_REGION_NOT_SUPPORTED",
                       "已识别完整行政区域的分析需求。当前小窗引擎没有全域分块与覆盖核验能力，不能将参考小窗替代全省、全市或全县结果。", 422)
    goal = body.get("goal")
    if goal is None:
        goal = "change" if any(w in requirement for w in ("变化", "对比", "比较", "两期", "时序", "秋冬播", "播种", "下降", "上升", "减少", "增加", "降低", "升高")) else "ndvi"
    if goal not in ("ndvi", "change"):
        raise GeoError("INVALID_GOAL", "目标仅支持ndvi或change", 400)
    direction=body.get("direction", "decrease" if any(w in requirement for w in ("下降","减少","减弱","降低")) else "increase" if any(w in requirement for w in ("增加","上升","增强","升高")) else "absolute")
    if direction not in ("decrease","increase","absolute"):raise GeoError("INVALID_DIRECTION","direction须为decrease/increase/absolute")
    threshold,cloud,selection,relative_percent=selection_parameters(requirement,body,goal,direction)
    if index["id"]!="ndvi":
        selection={k:v.replace("ndvi",index["id"]) if isinstance(v,str) else v for k,v in selection.items()}
    limits=index.get('change_threshold_range',[0,2]) if goal=='change' else index.get('threshold_range',[-1,1])
    if not relative_percent and (not finite(threshold) or not (limits[0] < threshold <= limits[1] if goal=='change' else limits[0] <= threshold <= limits[1])):
        raise GeoError("INVALID_THRESHOLD", index['label']+"阈值须为声明范围内的有限数值："+str(limits)+('，变化幅度须大于0' if goal=='change' else ''))
    if not finite(cloud) or not 0 <= cloud <= 100:
        raise GeoError("INVALID_CLOUD", "景云量上限须为0–100的有限数值")
    bbox = body.get("bbox", SAMPLE_BBOX)
    if not isinstance(bbox, list) or len(bbox) != 4 or not all(finite(v) for v in bbox) or not (-180 <= bbox[0] < bbox[2] <= 180 and -80 <= bbox[1] < bbox[3] <= 80) or bbox[2]-bbox[0] > .050001 or bbox[3]-bbox[1] > .050001:
        raise GeoError("INVALID_AOI", "仅支持合法WGS84局部bbox，单边跨度≤0.05°，不得跨180°")
    sampling=body.get("sampling","representative")
    if sampling not in ("representative","native") or (sampling=="native" and mode!="sentinel_cog"):raise GeoError("INVALID_SAMPLING","native仅用于真实Sentinel-2像素模式",400)
    if index["id"]!="ndvi" and (mode!="sentinel_cog" or sampling!="native"):raise GeoError("INDEX_REQUIRES_NATIVE","新增指数仅在真实Sentinel-2原生执行器运行；不能退到人工样本或代表网格",422)
    periods, defaulted = parse_periods(requirement, body.get("periods"), goal)
    requested_source=body.get('source_id','pc_landsat_c2_l2' if 'landsat' in requirement.lower() else 'pc_sentinel2_l2a')
    if requested_source not in ('auto','pc_sentinel2_l2a','pc_landsat_c2_l2'):raise GeoError('SOURCE_NOT_EXECUTABLE','该来源尚无已接入的光谱像元执行器',422)
    source_id=requested_source
    if source_id=='auto':
        earliest=periods['before'][0] if goal=='change' else periods['after'][0]
        source_id='pc_landsat_c2_l2' if earliest<'2015-06-27' or 'landsat' in requirement.lower() else 'pc_sentinel2_l2a'
    if 'landsat' in requirement.lower() and source_id!='pc_landsat_c2_l2':raise GeoError('SOURCE_CONFLICT','原文指定Landsat，不能改用Sentinel-2',422)
    if source_id=='pc_landsat_c2_l2':
        if mode!='sentinel_cog' or sampling!='native':raise GeoError('LANDSAT_REQUIRES_NATIVE','Landsat8/9只支持真实原生格网，不回退代表网格或样本',422)
        from landsat_reader import specification as landsat_specification
        index=landsat_specification(index['id'])
    scene_id=body.get('scene_id')
    if scene_id is not None and (not isinstance(scene_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,240}',scene_id)):raise GeoError('INVALID_SCENE_ID','观测场景ID无效',400)
    if scene_id and goal=='change':raise GeoError('PIN_REQUIRES_SINGLE_PERIOD','单个资源锁定只适用于单期分析，不能默换两期场景',422)
    region = txt(body.get("region", "仙桃参考小窗" if "bbox" not in body else "指定坐标小窗"), "区域", 100)
    blocking, warnings = [], []
    if relative_percent:blocking.append("相对百分比变化尚未实现；请提供绝对指数/差值阈值（例如下降超过0.1），不能把百分比偷偷换为绝对变化。")
    if not any(w in requirement.lower() for w in ("ndvi", "ndwi", "mndwi", "ndmi", "ndbi", "nbr", "evi2", "savi", "msavi2", "gndvi", "ndre", "bsi", "植被", "长势", "遥感", "秋冬播", "播种", "变化", "对比", "两期", "光谱")):
        blocking.append("尚未识别NDVI或两期光谱变化目标，请补充具体分析对象。")
    if mode == "sample_pixels" and any(abs(a-b)>1e-9 for a,b in zip(bbox,SAMPLE_BBOX)):
        blocking.append("人工波段样本仅对应固定教学AOI，不能迁移到用户指定地点冒充观测。")
    unsupported=[label for word,label in (("黄鳝","黄鳝产业/安全监管"),("禁捕","禁捕执法"),("确权","土地确权"),("工程进度","工程进度认定"),("冷链","冷链服务"),("尾水","尾水污染认定"),("所有业务","全业务上图"),("全部业务","全业务上图")) if word in requirement]
    if unsupported:
        warnings.append("尚未实现的业务目标："+'、'.join(unsupported)+"；本次只能交付明确选择的局部光谱线索。")
        if not any(w in requirement.lower() for w in ("ndvi", "ndwi", "mndwi", "ndmi", "ndbi", "nbr","植被","光谱","长势")):blocking.append("当前不能将这些业务目标自动转为NDVI："+'、'.join(unsupported))
    region_mentions = re.findall(r"([\u4e00-\u9fff]{2,8}(?:市|县))", requirement)
    if "bbox" not in body and (("region" in body and "仙桃" not in region) or any("仙桃" not in name for name in region_mentions)):
        blocking.append("需求包含未解析地区；请提供准确的小范围bbox，不能将外地需求默认套用仙桃。")
    if mode == "sentinel_cog" and not raster_available():
        blocking.append("当前服务Python缺少rasterio/numpy，真实波段小窗读取不可执行；不回退人工样本。")
    if defaulted: warnings.append("未识别完整时相，暂用2025与2026秋季窗口；可传periods明确指定。")
    if "bbox" not in body: warnings.append("未提供bbox，使用固定0.04°参考小窗，不代表全市分析。")
    limitations = ["规则辅助规划，非通用大模型推理。", "光谱指数/差值仅为光谱线索，不认定作物、水体类别、建成区、播种面积、土地权属或灾损。", "输出为代表采样网格，网格面积不等于真实变化地块面积。"]
    if mode == "sample_pixels": limitations.insert(0, "人工构造的两期二维波段，不是卫星观测；需求时相仅保留为请求，样本无真实观测日期。")
    else: limitations.insert(0, "公开Sentinel-2 L2A COG按需读取；目录、网络、云量与有效率不足会明确失败。")
    if sampling=="native":
        limitations=[item for item in limitations if "代表采样网格" not in item]
        limitations.append("统计基于末期B04原生10m投影格网；前期及SCL最近邻共配准。网格投影面积不是作物或法定地块面积。")
    if sampling=="native":
        limitations=[w.replace("末期B04原生10m投影格网；前期及SCL最近邻共配准", "末期"+index["reference_band"]+"原生"+str(index["reference_resolution_m"])+"m投影格网；所需波段/前期/SCL最近邻共配准") for w in limitations]+index["limitations"]
    if index["id"]!="ndvi" and selection["threshold_source"]=="default":warnings.append("未指定指数阈值，本次用默认0.2显示条件统计；它不是本地分类阈值或业务认定。")
    if source_id=="pc_landsat_c2_l2":limitations=[v.replace("公开Sentinel-2 L2A", "公开Landsat8/9 L2 SR").replace("/前期/SCL", "/前期/QA").replace("SCL最近邻", "QA最近邻") for v in limitations]
    steps = [{"id": i, "name": (index["label"]+"与光谱变化计算") if i=="compute" else n, "engine": e, "inputs": a, "outputs": b, "depends_on": [] if step_index==0 else [STEPS[step_index-1][0]]} for step_index,(i,n,e,a,b) in enumerate(STEPS)]
    return {"id": "plan_"+uuid.uuid4().hex[:12], "version": 2 if sampling=="native" else 1, "sampling":sampling, "index_id":index["id"], "index":index, "requirement": requirement, "mode": mode, "execution_mode": mode, "region": region,
            "source_id":source_id,"source_selection":{"requested":requested_source,"resolved":source_id,"reason":"按明确来源或时段规则选择；不隐式跨传感器失败回退"},"scene_id":scene_id,"observation_ref":copy.deepcopy(body.get("observation_ref")), "aoi": {"bbox": list(bbox), "crs": "EPSG:4326"}, "aoi_defaulted": "bbox" not in body, "periods_defaulted": defaulted, "periods": periods, "goal": goal, "direction": direction, "selection":selection, "threshold": threshold, "cloud": cloud,
            "strategy": "人工二维波段→质量掩膜→NDVI/差分→GIS" if mode=="sample_pixels" else "公开PC Landsat目录→实际STAC定标→30mCOG窗口→QA_PIXEL/RADSAT→指数/差分→GIS" if source_id=="pc_landsat_c2_l2" else "公开PC目录→所需波段XML定标→白名单COG窗口/缓存→SCL掩云→指数/差分→GIS",
            "interpreted": {"region": region, "bbox": list(bbox), "time_start": periods["before"][0], "time_end": periods["after"][1], "analysis": goal},
            "source_bindings": [{"source_type": "artificial_band_sample" if mode=="sample_pixels" else "public_satellite_pixels", "provider": "人工教学样本" if mode=="sample_pixels" else "Microsoft Planetary Computer / USGS Landsat8/9" if source_id=="pc_landsat_c2_l2" else "Microsoft Planetary Computer / ESA Sentinel-2", "status": "to_read"}],
            "steps": steps, "executable": not blocking, "can_run": not blocking, "blocking_gaps": blocking, "gaps": blocking+warnings, "warnings": warnings, "limitations": limitations,
            "delivered_scope": ("实际选区原生参考格网NDVI/光谱变化（非全市）" if sampling=="native" else "参考小样区代表网格NDVI（非全市）" if goal=="ndvi" else "参考小样区两期光谱变化线索（非全市）"), "created_at": now()}


def sample_bands():
    """Manual deterministic teaching arrays; no random imagery or claimed observation."""
    before, after = {"red": [], "nir": [], "scl": []}, {"red": [], "nir": [], "scl": []}
    for r in range(8):
        for band in before: before[band].append([]); after[band].append([])
        for c in range(8):
            red = [.10,.12,.16,.20][(r+c)%4]
            nir = [.48,.52,.56,.60][r%4]
            delta = -.32 if (r<3 and c<4) else (.18 if r>=4 and c>=3 else .01)
            before["red"][r].append(red); before["nir"][r].append(nir); after["red"][r].append(red); after["nir"][r].append(max(.02,nir+delta))
            before["scl"][r].append(9 if (r,c)==(0,0) else 4); after["scl"][r].append(3 if (r,c) in ((1,1),(2,2)) else 4)
    before["red"][7][7] = None
    metadata = {"source_type": "artificial_band_sample", "provider": "GeoAI人工二维波段教学样本", "source_dates": ["sample_epoch_1","sample_epoch_2"], "bbox": SAMPLE_BBOX, "rows": 8, "cols": 8, "scale": 1, "offset": 0, "crs": "EPSG:4326", "description": "矩阵值为人工构造反射率，质量类别也为人工构造；不是Sentinel观测。"}
    metadata["input_sha256"] = sha({"before":before,"after":after,"metadata":metadata})
    return {"before":before,"after":after,"metadata":metadata}


def asset_url(value):
    if not isinstance(value,str):raise GeoError("INVALID_ASSET","波段资产缺少有效HTTPS地址")
    p=urlsplit(value)
    azure=bool(p.hostname and re.fullmatch(r"sentinel2l2a\d{2}\.blob\.core\.windows\.net",p.hostname)) and p.path.startswith('/sentinel2-l2/')
    aws=p.hostname in ("sentinel-cogs.s3.us-west-2.amazonaws.com","sentinel-cogs.s3.amazonaws.com") and p.path.startswith('/sentinel-s2-l2a-cogs/')
    if p.scheme!='https' or not (azure or aws) or p.username or p.password or p.query or p.fragment or p.port not in (None,443) or not re.fullmatch(r"/[A-Za-z0-9_./-]+\.(?:tif|xml)",p.path) or '..' in p.path.split('/'):
        raise GeoError("ASSET_NOT_ALLOWED","仅允许固定公开Sentinel桶的无凭据资产")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise GeoError('PUBLIC_REDIRECT_DENIED','公开数据请求出现未授权跳转',502)


class SentinelReader:
    def __init__(self, public_sources):
        self.public_sources=public_sources;self.tokens={};self.network_slots=threading.BoundedSemaphore(4);self.opener=build_opener(NoRedirect())
    def fetch_bytes(self,url,check,max_bytes=1024*1024):
        host=urlsplit(url).hostname
        if host!='planetarycomputer.microsoft.com' and not (host and re.fullmatch(r"sentinel2l2a\d{2}\.blob\.core\.windows\.net",host)):
            raise GeoError('PUBLIC_SOURCE_DENIED','只允许固定公开影像来源',502)
        if not self.network_slots.acquire(blocking=False):raise GeoError('PUBLIC_SOURCE_BUSY','公开数据请求仍在完成，请稍后重试',503)
        channel=queue.Queue(maxsize=1)
        def fetch():
            try:
                with self.opener.open(Request(url,headers={'Accept':'application/json,application/xml','User-Agent':'GeoAI-Production/5.0'}),timeout=12) as response:
                    data=response.read(max_bytes+1)
                    if len(data)>max_bytes:raise ValueError('response too large')
                    channel.put((True,data))
            except Exception:channel.put((False,None))
            finally:self.network_slots.release()
        threading.Thread(target=fetch,daemon=True).start();deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            check()
            try:
                success,data=channel.get(timeout=.1)
                if not success:raise GeoError('PUBLIC_SOURCE_UNAVAILABLE','公开影像服务请求失败；没有使用替代样本',502)
                return data
            except queue.Empty:pass
        raise GeoError('PUBLIC_SOURCE_TIMEOUT','公开数据请求总等待超过20秒；可重试，未回退样本',502)
    def fetch_json(self,url,check):
        try:return json.loads(self.fetch_bytes(url,check).decode('utf-8'),parse_constant=lambda v:(_ for _ in ()).throw(ValueError()))
        except (ValueError,UnicodeError):raise GeoError('PUBLIC_SOURCE_SCHEMA','公开影像服务返回无效JSON',502)
    def signed_url(self,url,check):
        asset_url(url);p=urlsplit(url)
        if not p.hostname.endswith('.blob.core.windows.net'):return url
        account=p.hostname.split('.')[0];container=p.path.split('/')[1];key=(account,container)
        hit=self.tokens.get(key)
        if hit and time.monotonic()-hit[0]<300:token=hit[1]
        else:
            data=self.fetch_json('https://planetarycomputer.microsoft.com/api/sas/v1/token/'+account+'/'+container,check)
            token=data.get('token') if isinstance(data,dict) else None
            if not isinstance(token,str) or not token or len(token)>5000:raise GeoError('PUBLIC_SIGNING_FAILED','公开访问令牌不可用，未保存或回显凭据',502)
            self.tokens[key]=(time.monotonic(),token)
        return url+'?'+token.lstrip('?')
    def calibration(self,metadata_href,check,bands=("red","nir")):
        unsigned=asset_url(metadata_href);raw=self.fetch_bytes(self.signed_url(unsigned,check),check,max_bytes=256*1024)
        if b'<!DOCTYPE' in raw.upper():raise GeoError('CALIBRATION_XML','产品元数据不允许DOCTYPE',502)
        try:root=ET.fromstring(raw)
        except ET.ParseError:raise GeoError('CALIBRATION_XML','无法读取产品定标元数据',502)
        q=None;offsets={}
        for element in root.iter():
            name=element.tag.split('}')[-1]
            if name=='BOA_QUANTIFICATION_VALUE':
                try:q=float(element.text)
                except (ValueError,TypeError):pass
            elif name=='BOA_ADD_OFFSET':
                try:offsets[element.attrib.get('band_id')]=float(element.text)
                except (ValueError,TypeError):pass
        required={band:BANDS[band] for band in bands}
        if not finite(q) or q<=0 or any(not finite(offsets.get(info['xml_band_id'])) for info in required.values()):
            raise GeoError('MISSING_CALIBRATION','产品XML缺少所需波段的明确BOA定标量/偏移，不猜测原DN反射率',502)
        values={band:{'scale':1/q,'offset':offsets[info['xml_band_id']]/q,'nodata':0} for band,info in required.items()}
        values['provenance']={'source_url':unsigned,'xml_sha256':hashlib.sha256(raw).hexdigest(),'quantification_value':q,'boa_add_offset':{info['id']:offsets[info['xml_band_id']] for info in required.values()},'method':'reflectance=(DN+BOA_ADD_OFFSET)/BOA_QUANTIFICATION_VALUE; DN0 nodata'}
        return values
    def discover(self,plan,check):
        scenes={}
        for epoch in (['before','after'] if plan['goal']=='change' else ['after']):
            check();start,end=plan['periods'][epoch]
            url='https://planetarycomputer.microsoft.com/api/stac/v1/search?'+urlencode({'collections':'sentinel-2-l2a','bbox':','.join(str(v) for v in plan['aoi']['bbox']),'datetime':start+'T00:00:00Z/'+end+'T23:59:59Z','query':json.dumps({'eo:cloud_cover':{'lte':plan['cloud']}},separators=(',',':')),'limit':12})
            if plan.get('scene_id'):url+='&'+urlencode({'ids':plan['scene_id']})
            data=self.fetch_json(url,check);check()
            if not isinstance(data,dict) or not isinstance(data.get('features'),list):raise GeoError('CATALOG_SCHEMA','公开目录返回无效数据',502)
            candidates=[]
            for scene in data['features']:
                if plan.get('scene_id') and scene.get('id')!=plan['scene_id']:continue
                if not isinstance(scene,dict):continue
                props=scene.get('properties',{});cloud=props.get('eo:cloud_cover');dt=props.get('datetime','');b=scene.get('bbox',[])
                if not finite(cloud) or cloud>plan['cloud'] or not isinstance(dt,str) or not start<=dt[:10]<=end or not isinstance(b,list) or len(b)<4 or not all(finite(v) for v in b[:4]):continue
                if not (b[0]<=plan['aoi']['bbox'][0] and b[1]<=plan['aoi']['bbox'][1] and b[2]>=plan['aoi']['bbox'][2] and b[3]>=plan['aoi']['bbox'][3]):continue
                candidates.append(scene)
            if not candidates:raise GeoError('NO_SUITABLE_SCENE',epoch+'窗口未找到满足覆盖/日期/云量的公开影像；未切换样本')
            scene=min(candidates,key=lambda f:(f['properties']['eo:cloud_cover'],f['properties']['datetime']))
            if not isinstance(scene.get('id'),str) or not scene['id']:raise GeoError('CATALOG_SCHEMA','目录场景缺少ID',502)
            metadata=scene.get('assets',{}).get('product-metadata',{})
            algorithm=plan.get('index') or specification(plan.get('index_id','ndvi'));needed=[b['name'] for b in algorithm['required_bands']]
            calibration=self.calibration(metadata.get('href'),check,needed)
            assets={}
            for band,key in [(name,BANDS[name]['id']) for name in needed]+[('scl','SCL')]:
                a=scene.get('assets',{}).get(key)
                if not isinstance(a,dict):raise GeoError('MISSING_BAND','影像缺少'+key+'波段资产')
                assets[band]={'href':asset_url(a.get('href')),'asset_key':key,'native_resolution_m':BANDS[band]['native_resolution_m'] if band!='scl' else 20,**(calibration[band] if band!='scl' else {'scale':1,'offset':0,'nodata':0})}
            scenes[epoch]={'id':scene['id'],'datetime':scene['properties']['datetime'],'cloud_cover':scene['properties']['eo:cloud_cover'],'assets':assets,'catalog_url':url,'calibration':calibration['provenance'],'provider':'Microsoft Planetary Computer / ESA Sentinel-2'}
        if plan['goal']=='change' and scenes['before']['id']==scenes['after']['id']:raise GeoError('DUPLICATE_SCENE','两期不能使用同一个影像场景')
        return scenes

    def read_native(self,plan,scenes,check):
        from native_raster import read_native
        return read_native(plan,scenes,self,check)

    def read(self,plan,scenes,check):
        try:
            import numpy as np
            import rasterio
            from rasterio.windows import from_bounds
            from rasterio.warp import reproject,transform_bounds,Resampling
            from rasterio.transform import from_bounds as output_transform
        except ImportError:raise GeoError("PIXEL_ENGINE_UNAVAILABLE","当前Python缺少rasterio/numpy，不能读取公开像素；未回退样本")
        result={};bbox=plan['aoi']['bbox'];size=16;metadata={"source_type":"public_satellite_pixels","provider":"ESA Sentinel-2 L2A / Microsoft Planetary Computer COG","bbox":bbox,"rows":size,"cols":size,"crs":"EPSG:4326","scenes":copy.deepcopy(scenes),"resampling":"nearest representative sample for red/nir/SCL, same EPSG:4326 grid","scl_retained":SCL_RETAINED}
        dst_transform=output_transform(*bbox,size,size)
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif,.tiff',GDAL_HTTP_TIMEOUT='10',GDAL_HTTP_MAX_RETRY='0',GDAL_CACHEMAX=32*1024*1024):
            for epoch,scene in scenes.items():
                result[epoch]={}
                for band,a in scene['assets'].items():
                    check()
                    try:
                        with rasterio.open(self.signed_url(a['href'],check)) as ds:
                            if ds.crs is None:raise GeoError("MISSING_CRS","COG缺少坐标基准")
                            bounds=transform_bounds('EPSG:4326',ds.crs,*bbox,densify_pts=21)
                            window=from_bounds(*bounds,ds.transform).round_offsets().round_lengths()
                            if window.width<1 or window.height<1 or window.width>2048 or window.height>2048:raise GeoError("WINDOW_LIMIT","原生波段窗口不在1–2048像素边界内")
                            raw=ds.read(1,window=window,boundless=True,masked=True).astype('float64').filled(np.nan)
                            if a.get('nodata') is not None:raw[raw==a['nodata']]=np.nan
                            if band!='scl':raw=raw*a['scale']+a['offset']
                            target=np.full((size,size),np.nan,dtype='float64')
                            reproject(raw,target,src_transform=ds.window_transform(window),src_crs=ds.crs,src_nodata=np.nan,dst_transform=dst_transform,dst_crs='EPSG:4326',dst_nodata=np.nan,resampling=Resampling.nearest)
                            result[epoch][band]=[[float(v) if math.isfinite(float(v)) else None for v in row] for row in target]
                    except GeoError:raise
                    except Exception:raise GeoError("COG_READ_FAILED","公开COG小窗无法读取/配准，请检查网络、范围或影像；未使用替代波段",502)
                    check()
        metadata['input_sha256']=sha({"arrays":result,"metadata":metadata});result['metadata']=metadata
        return result


def ndvi_grid(bands):
    red,nir,scl=bands.get('red'),bands.get('nir'),bands.get('scl')
    if not isinstance(red,list) or not red or not isinstance(red[0],list) or not red[0] or len(red)>32 or len(red[0])>32:raise GeoError("INVALID_BAND_GRID","波段须为≤32×32二维矩阵")
    rows,cols=len(red),len(red[0]);output=[];reasons={}
    if any(not isinstance(b,list) or len(b)!=rows or any(not isinstance(row,list) or len(row)!=cols for row in b) for b in (red,nir,scl)):raise GeoError("GRID_MISMATCH","红/NIR/SCL必须位于同一网格")
    for r in range(rows):
        row=[]
        for c in range(cols):
            a,b,q=red[r][c],nir[r][c],scl[r][c];reason=None
            if not finite(a) or not finite(b):reason='nodata'
            elif not finite(q) or q not in SCL_RETAINED:reason='scl_excluded'
            elif a<0 or b<0:reason='negative_reflectance'
            elif not finite(a+b) or a+b==0:reason='zero_or_invalid_denominator'
            if reason:reasons[reason]=reasons.get(reason,0)+1;row.append(None)
            else:row.append((b-a)/(b+a))
        output.append(row)
    return output,reasons


def compute_product(plan,bands,run_id,check=lambda:None):
    after,ar=ndvi_grid(bands['after']);before,br=(ndvi_grid(bands['before']) if plan['goal']=='change' else (None,{}))
    rows,cols=len(after),len(after[0])
    if before is not None and (len(before)!=rows or any(len(row)!=cols for row in before)):raise GeoError("GRID_MISMATCH","两期必须使用相同配准网格")
    bbox=plan['aoi']['bbox'];west,south,east,north=bbox;dx=(east-west)/cols;dy=(north-south)/rows
    features=[];valid=[];deltas=[];changed=0;changed_area=0;before_values=[]
    for r in range(rows):
        check()
        for c in range(cols):
            a=after[r][c];b=before[r][c] if before is not None else None;ok=a is not None and (before is None or b is not None)
            delta=a-b if ok and before is not None else None
            value=(-delta if plan.get('direction')=='decrease' else delta if plan.get('direction')=='increase' else abs(delta)) if delta is not None else a
            selected=ok and (delta is None or plan.get('direction') not in ('decrease','increase') or value>=0) and selected_value(value,plan['threshold'],plan.get('selection',{}).get('operator','gte'))
            x0=west+c*dx;x1=x0+dx;y1=north-r*dy;y0=y1-dy;ring=[[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]
            area=polygon_area_m2([ring])/10000
            if ok:
                valid.append(a)
                if before is not None:before_values.append(b);deltas.append(delta)
                if selected:changed+=1;changed_area+=area
            category='invalid' if not ok else ('decrease' if delta is not None and delta<=-plan['threshold'] else 'increase' if delta is not None and delta>=plan['threshold'] else 'stable') if before is not None else ('below_threshold' if a is not None and a<plan['threshold'] else 'at_threshold' if a==plan['threshold'] else 'above_threshold')
            if not ok:category='invalid'
            props={"name":"分析网格 %d-%d"%(r+1,c+1),"row":r,"col":c,"ndvi":round(a,6) if a is not None else None,"before_ndvi":round(b,6) if b is not None else None,"delta_ndvi":round(delta,6) if delta is not None else None,"valid":ok,"selected":bool(selected),"category":category,"grid_area_ha":round(area,6),"source_type":bands['metadata']['source_type'],"run_id":run_id,"review_status":"待业务核查"}
            features.append({"type":"Feature","id":run_id+"-cell-%d-%d"%(r,c),"geometry":{"type":"Polygon","coordinates":[ring]},"properties":props})
    count=rows*cols;ratio=len(valid)/count
    if not valid or ratio<.1:raise GeoError("INSUFFICIENT_VALID_PIXELS","共同有效像元不足10%或全部无效，不能发布NDVI/变化成果；未填0或切换样本")
    summary={"pixel_count":count,"valid_count":len(valid),"invalid_count":count-len(valid),"valid_ratio":round(ratio,4),"after_valid_count":sum(v is not None for row in after for v in row),"before_valid_count":sum(v is not None for row in before for v in row) if before else None,"mean_ndvi":round(sum(valid)/len(valid),6),"mean_before_ndvi":round(sum(before_values)/len(before_values),6) if before_values else None,"mean_delta":round(sum(deltas)/len(deltas),6) if deltas else None,"changed_count":changed if before is not None else None,"selected_count":changed,"threshold":plan['threshold'],"direction":plan.get('direction','absolute'),"selection":plan.get('selection',{}),"selected_grid_area_ha":round(changed_area,6),"area_ha":round(changed_area,6),"area_label":"阈值命中代表网格面积，非作物/变化地块面积","mask_reasons":{"before":br,"after":ar},"quality_warning":"有效比例低于50%，仅供质量检查" if ratio<.5 else "", "formula":"(nir-red)/(nir+red)","change_formula":"after_ndvi-before_ndvi"}
    provenance={"mode":plan['mode'],"source_type":bands['metadata']['source_type'],"source":bands['metadata']['provider'],"algorithm_version":VERSION,"run_id":run_id,"requirement":plan['requirement'],"bbox":bbox,"requested_periods":plan['periods'],"input_sha256":bands['metadata']['input_sha256'],"review_status":"待业务核查","crs":"EPSG:4326","grid":{"rows":rows,"cols":cols,"bounds":bbox,"order":"north-to-south rows, west-to-east columns","resampling":bands['metadata'].get('resampling','manual teaching grid')},"quality":{"scl_retained":SCL_RETAINED,"valid_ratio":summary['valid_ratio'],"after_valid_count":summary['after_valid_count'],"before_valid_count":summary['before_valid_count']},"source_details":bands['metadata'],"threshold":plan['threshold'],"direction":plan.get('direction','absolute'),"selection":plan.get('selection',{}),"external_calls":plan['mode']=='sentinel_cog',"created_at":now()}
    findings=["已对%d个配准分析网格实际计算，%d个共同有效，%d个命中阈值。"%(count,len(valid),changed),summary['area_label']+"；不作实际作物或行政认定。"]
    if summary['quality_warning']:findings.append(summary['quality_warning'])
    report={"title":"人工二维波段计算报告" if plan['mode']=='sample_pixels' else "公开Sentinel-2小窗光谱分析报告","input":{"mode":plan['mode'],"requirement":plan['requirement'],"bbox":bbox,"requested_periods":plan['periods'],"goal":plan['goal'],"threshold":plan['threshold'],"selection":plan['selection'],"source_type":bands['metadata']['source_type']},"steps":[{"id":s['id'],"name":s['name'],"engine":s['engine']} for s in plan['steps']],"findings":findings,"limitations":plan['limitations'],"provenance":provenance}
    geojson={"type":"FeatureCollection","features":features,"metadata":provenance}
    return {"geojson":geojson,"summary":summary,"report":report,"provenance":provenance,"artifacts":[{"id":run_id+'-geojson',"type":"geojson","name":"配准NDVI/光谱变化网格","feature_count":count},{"id":run_id+'-report',"type":"report","name":report['title']}]}


class ProductionServices:
    def __init__(self,store,reader=None):
        self.store=store;self.output_dir=Path(store.db_path).parent/'production';self.reader=reader or SentinelReader(store.public_sources);self.stopping=False;self.threads=[]
        from landsat_reader import LandsatReader
        self.landsat_reader=LandsatReader(store.public_sources)
        for task in store.list('production_task'):
            if task['status'] in ('queued','running'):
                task.update(status='failed',error={"code":"INTERRUPTED","message":"服务重启，生产任务中断；可重试"},finished_at=now())
                for node in task.get('nodes',[]):
                    if node.get('status')=='running':node.update(status='failed',message='服务重启中断',finished_at=now())
                task['logs'].append({"time":now(),"message":"服务重启中断，不生成替代成果"});store.save('production_task',task)
    def resolve_input(self,body):
        body=copy.deepcopy(body)
        if not isinstance(body,dict):raise GeoError('INVALID_PRODUCTION_INPUT','生产输入须为对象',400)
        obs_id=body.get('observation_id')
        if not obs_id:
            if body.get('observation_ref') is not None:raise GeoError('INVALID_OBSERVATION_REFERENCE','观测版本必须来自资源库',422)
            return body
        obs=self.store.observations.get(obs_id)
        if not obs['stages']['analysis_ready']:raise GeoError('OBSERVATION_NOT_EXECUTABLE','所选观测缺少兼容像元执行条件，请查看算法匹配缺口',422)
        requested=body.get('source_id','auto')
        if requested not in ('auto',obs['source_id']):raise GeoError('SOURCE_CONFLICT','指定来源与锁定观测来源不一致',422)
        body.update(source_id=obs['source_id'],scene_id=obs['item_id'],observation_ref={'id':obs_id,'metadata_sha256':obs['metadata_sha256'],'collection':obs['collection'],'item_id':obs['item_id']})
        plan=make_plan(body)
        if plan['goal']=='change':raise GeoError('PIN_REQUIRES_SINGLE_PERIOD','资源锁定仅支持单期分析',422)
        dt=obs.get('datetime','')[:10];start,end=plan['periods']['after'];b=obs['bbox'];a=plan['aoi']['bbox']
        if not start<=dt<=end:raise GeoError('OBSERVATION_PERIOD_CONFLICT','分析日期不包含已锁定观测的日期',422)
        if not(b[0]<=a[0]<a[2]<=b[2] and b[1]<=a[1]<a[3]<=b[3]):raise GeoError('OBSERVATION_AREA_CONFLICT','分析选区不在已锁定观测包络内',422)
        from algorithm_catalog import match_observation
        if not any(v['ready'] for v in match_observation(obs,plan['index_id'])['matches']):raise GeoError('OBSERVATION_ALGORITHM_CONFLICT','已锁定观测不满足所选算法的波段/质量/定标条件',422)
        return body
    def verify_bound_scene(self,plan,bindings):
        ref=plan.get('observation_ref')
        if not ref:return
        obs=self.store.observations.get(ref['id']);scene=bindings.get('after',{})
        def changed():raise GeoError('OBSERVATION_VERSION_CHANGED','上游观测日期、必要资产或定标与绑定版本不一致；请重新检索入库，不替换场景',422)
        if obs['metadata_sha256']!=ref.get('metadata_sha256') or scene.get('id')!=obs['item_id']:changed()
        try:
            a=datetime.fromisoformat(scene['datetime'].replace('Z','+00:00'));b=datetime.fromisoformat(obs['datetime'].replace('Z','+00:00'))
            if a!=b:changed()
        except (KeyError,ValueError,TypeError):changed()
        if scene.get('collection',obs['collection'])!=obs['collection']:changed()
        if scene.get('source_id',obs['source_id'])!=obs['source_id']:changed()
        if scene.get('platform') and obs.get('platform') and str(scene['platform']).casefold()!=str(obs['platform']).casefold():changed()
        for common,actual in scene.get('assets',{}).items():
            spectral_keys={b['name']:b.get('asset_key',b['id']) for b in plan['index']['required_bands']}
            asset_key=actual.get('asset_key') or {'scl':'SCL','qa_pixel':'qa_pixel','qa_radsat':'qa_radsat'}.get(common) or spectral_keys.get(common)
            candidates=[a for a in obs['assets'] if a.get('key')==asset_key]
            if len(candidates)!=1 or candidates[0].get('href')!=actual.get('href'):changed()
            expected=candidates[0]
            if common not in ('scl','qa_pixel','qa_radsat'):
                for field in ('scale','offset','nodata'):
                    if expected.get(field) is not None and (not finite(actual.get(field)) or abs(expected[field]-actual[field])>1e-12):changed()
    def create(self,body):
        body=self.resolve_input(body)
        plan=make_plan(body);task={"id":ident(),"name":("两期光谱变化" if plan['goal']=='change' else "单期"+plan.get("index",{}).get("label","NDVI"))+" · "+("人工样本" if plan['mode']=='sample_pixels' else "公开像素"),"status":"queued","progress":0,"plan":plan,"snapshot":copy.deepcopy(plan),"nodes":[{"id":s['id'],"name":s['name'],"engine":s['engine'],"status":"pending","message":"等待执行"} for s in plan['steps']],"logs":[{"time":now(),"message":"已创建不可变生产计划；模式："+plan['mode']}],"result":None,"created_at":now()}
        with self.store.lock:
            if self.stopping:raise GeoError('SERVICE_STOPPING','服务正在关闭',503)
            if sum(t['status'] in ('queued','running') for t in self.store.list('production_task'))>=4:raise GeoError('PRODUCTION_BUSY','本机最多4个并行生产任务',429)
            self.store.save('production_task',task);thread=threading.Thread(target=self.execute,args=(task['id'],plan),daemon=True);self.threads.append(thread);thread.start()
        return task
    def list(self):return self.store.list('production_task')
    def get(self,task_id):return self.store.get('production_task',task_id)
    def change(self,task_id,callback):
        with self.store.lock:
            task=self.get(task_id)
            if task['status'] in ('failed','cancelled','succeeded'):return False
            callback(task);self.store.save('production_task',task);return True
    def check(self,task_id):
        if self.stopping:raise GeoError('INTERRUPTED','服务关闭中断任务')
        if self.get(task_id)['status']=='cancelled':raise GeoError('CANCELLED','用户取消了任务')
    def execute(self,task_id,plan):
        bindings=bands=product=None
        reader=self.landsat_reader if plan.get("source_id")=="pc_landsat_c2_l2" else self.reader
        try:
            for index,step in enumerate(plan['steps']):
                self.check(task_id)
                def start(task):
                    task.update(status='running');task['nodes'][index].update(status='running',message='执行：'+step['name'],started_at=now(),input_summary={"分析区域":plan["region"],"bbox":plan["aoi"]["bbox"],"目标":plan["goal"],"模式":plan["mode"],"时相":plan["periods"]} if index==0 else {"输入":step["inputs"],"数据来源":plan["mode"]});task['logs'].append({"time":now(),"message":"开始："+step['name']})
                if not self.change(task_id,start):return
                if step['id']=='validate':
                    if plan['blocking_gaps']:raise GeoError('BLOCKING_GAPS','；'.join(plan['blocking_gaps']))
                elif step['id']=='discover':
                    bindings={"sample":"manual-band-grid-v1"} if plan['mode']=='sample_pixels' else reader.discover(plan,lambda:self.check(task_id))
                    self.verify_bound_scene(plan,bindings)
                elif step['id']=='read':
                    bands=sample_bands() if plan['mode']=='sample_pixels' else reader.read_native(plan,bindings,lambda:self.check(task_id)) if plan.get('sampling')=='native' else reader.read(plan,bindings,lambda:self.check(task_id))
                elif step['id']=='quality':
                    if plan.get('sampling')=='native':
                        from native_raster import quality_summary
                        quality_summary(bands,plan.get("index_id","ndvi"))
                    else:
                        ndvi_grid(bands['after'])
                        if plan['goal']=='change':ndvi_grid(bands['before'])
                elif step['id']=='compute':
                    if plan.get('sampling')=='native':
                        from native_raster import compute_native
                        product=compute_native(plan,bands,task_id,Path(self.store.db_path).parent/'production'/task_id,lambda:self.check(task_id))
                    else:product=compute_product(plan,bands,task_id,lambda:self.check(task_id))
                elif step['id']=='publish':
                    if product is None:raise GeoError('MISSING_RESULT','没有可归档计算结果')
                    product['provenance']['source_id']=plan.get('source_id','pc_sentinel2_l2a')
                    attribution='USGS / NASA Landsat Collection 2 · Microsoft Planetary Computer' if plan.get('source_id')=='pc_landsat_c2_l2' else 'ESA / Copernicus Sentinel-2 · Microsoft Planetary Computer'
                    product['provenance']['attribution']=attribution
                    if isinstance(product.get('image'),dict):product['image']['attribution']=attribution
                    product['provenance']['observation_ref']=copy.deepcopy(plan.get('observation_ref'))

                self.check(task_id)
                output_summary={}
                if step['id']=='validate':output_summary={"目标":plan['goal'],"方向":plan['direction'],"筛选条件":plan['selection'],"范围":plan['delivered_scope'],"警示":plan['warnings']}
                elif step['id']=='discover':output_summary={"来源":"人工二维波段样本","时相":"sample_epoch_1/2（无真实观测日期）"} if plan['mode']=='sample_pixels' else {epoch:{"场景ID":scene['id'],"采集时间":scene['datetime'],"景云量":scene['cloud_cover'],"定标元数据哈希":scene['calibration'].get('xml_sha256') or scene['calibration'].get('stac_item_sha256')} for epoch,scene in bindings.items()}
                elif step['id']=='read':output_summary={"行":bands['metadata']['rows'],"列":bands['metadata']['cols'],"波段":[b["id"] for b in plan.get("index",specification())["required_bands"]]+(["QA_PIXEL","QA_RADSAT"] if plan.get("source_id")=="pc_landsat_c2_l2" else ["SCL"]),"时相数量":2 if plan['goal']=='change' else 1,"输入哈希":bands['metadata']['input_sha256'],"基准":bands["metadata"].get("crs","EPSG:4326")+"同网格","来源":bands['metadata']['provider']}
                elif step['id']=='quality':
                    output_summary={"质量策略": "QA_PIXEL位0–5与所有非零QA_RADSAT排除"} if plan.get("source_id")=="pc_landsat_c2_l2" else {"SCL保留类别":SCL_RETAINED}
                    for epoch in (['before','after'] if plan['goal']=='change' else ['after']):
                        
                        if plan.get('sampling')=='native':
                            from native_raster import quality_summary
                            output_summary[epoch]=quality_summary(bands,plan.get("index_id","ndvi"))[epoch]
                        else:
                            grid,reasons=ndvi_grid(bands[epoch]);output_summary[epoch]={"有效":sum(v is not None for row in grid for v in row),"排除原因":reasons}
                elif step['id']=='compute':output_summary=copy.deepcopy(product['summary'])
                elif step['id']=='publish':output_summary={"产物数":len(product['artifacts']),"GIS网格数":len(product['geojson']['features']),"输入哈希":product['provenance']['input_sha256'],"来源":product['provenance']['source']}
                def finish(task):
                    task['nodes'][index].update(status='succeeded',message='实际处理完成',finished_at=now(),output_summary=output_summary);task['progress']=round((index+1)/len(plan['steps'])*100);task['logs'].append({"time":now(),"message":"完成："+step['name']})
                    if step['id']=='discover':task['source_bindings']=copy.deepcopy(bindings)
                    if step['id']=='publish':
                        product['report']['steps']=copy.deepcopy(task['nodes'])
                        if plan.get('sampling')=='native':(Path(self.store.db_path).parent/'production'/task_id/'result.json').write_text(json.dumps(product,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
                        task.update(status='succeeded',result=product,finished_at=now())
                if not self.change(task_id,finish):return
                if step['id']=='publish' and plan.get('observation_ref'):
                    self.store.observations.record_pixel_use(plan['observation_ref']['id'],task_id,product['provenance']['input_sha256'])
        except Exception as exc:
            code=exc.code if isinstance(exc,GeoError) else 'PRODUCTION_FAILED';message=exc.message if isinstance(exc,GeoError) else '生产处理失败，请检查数据、网络或引擎；没有生成替代成果'
            def fail(task):
                task.update(status='failed',error={"code":code,"message":message},finished_at=now());task['logs'].append({"time":now(),"message":message})
                for node in task['nodes']:
                    if node['status']=='running':node.update(status='failed',message=message,finished_at=now())
            self.change(task_id,fail)
    def cancel(self,task_id):
        with self.store.lock:
            task=self.get(task_id)
            if task['status'] not in ('queued','running'):raise GeoError('INVALID_STATE','只有排队/运行任务可取消',409)
            task.update(status='cancelled',finished_at=now());task['logs'].append({"time":now(),"message":"用户取消；保留已执行记录，不发布结果"})
            for node in task['nodes']:
                if node['status'] in ('pending','running'):node.update(status='cancelled',message='用户取消',finished_at=now())
            return self.store.save('production_task',task)
    def retry(self,task_id):
        old=self.get(task_id)
        if old['status'] not in ('failed','cancelled'):raise GeoError('INVALID_STATE','只有失败/取消的任务可重试',409)
        plan=old['plan'];body={"requirement":plan['requirement'],"mode":plan['mode'],"region":plan['region'],"bbox":plan['aoi']['bbox'],"goal":plan['goal'],"threshold":plan['threshold'],"cloud":plan['cloud'],"direction":plan.get('direction','absolute'),"periods":copy.deepcopy(plan['periods']),"sampling":plan.get("sampling","representative"),"index_id":plan.get("index_id","ndvi")}
        # Retry the frozen sensor and observation, including explicitly selected
        # Landsat whose human requirement does not contain the provider name.
        for key in ('source_id','scene_id','observation_ref'):
            if plan.get(key) is not None:body[key]=copy.deepcopy(plan[key])
        return self.create(body)
    def artifact(self,task_id,name):
        if name not in ('analysis.tif','overlay.png','result.json'):raise GeoError('ARTIFACT_NOT_FOUND','没有此分析产物',404)
        task=self.get(task_id)
        if task['status']!='succeeded' or task['plan'].get('sampling')!='native':raise GeoError('ARTIFACT_NOT_READY','该任务没有已完成的原生分析产物',404)
        path=Path(self.store.db_path).parent/'production'/task_id/name
        if not path.is_file():raise GeoError('ARTIFACT_MISSING','分析产物文件缺失，未生成替代文件',404)
        return path
    def close(self):
        self.stopping=True
        for thread in self.threads:thread.join(timeout=1)
