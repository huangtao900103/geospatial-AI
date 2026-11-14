"""Fixed-provider raster tiles and provenance-aware map layers for local GeoAI."""
import copy
import hashlib
import json
import math
import re
import threading
import time
from collections import OrderedDict
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from geo_services import GeoError

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "map_sources"
TILE_LIMIT = 1024 * 1024
CACHE_MAX_FILES = 64
CACHE_MAX_BYTES = 16 * 1024 * 1024
CACHE_TTL = 300
IMAGERY_ATTRIBUTION = "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community"
NASA_ATTRIBUTION = "NASA EOSDIS GIBS · MODIS Terra Corrected Reflectance · 2025-11-07"
NATURAL_EARTH_ATTRIBUTION = "Made with Natural Earth · Public domain · 1:10,000,000"
BASEMAPS = [
    {"id": "imagery", "name": "全球卫星与航空影像", "url": "/api/tiles/imagery/{z}/{x}/{y}", "kind": "imagery", "max_zoom": 18, "max_native_zoom": 18,
     "attribution": IMAGERY_ATTRIBUTION, "source_url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer", "attribution_url": "https://www.esri.com/en-us/legal/terms/full-master-agreement", "status": "network_on_demand",
     "description": "Esri World Imagery 多时相拼接底图，采集日期与分辨率随区域变化，不能据此认定当期种植或工程进度。", "license_note": "保留供应商署名与适用服务条款；本地原型限当前视窗临时缓存，不提供影像批量导出。", "crs": "EPSG:3857", "tile_size": 256},
    {"id": "nasa", "name": "NASA MODIS 归档真彩影像", "url": "/api/tiles/nasa/{z}/{x}/{y}", "kind": "imagery", "max_zoom": 18, "max_native_zoom": 9,
     "attribution": NASA_ATTRIBUTION, "source_url": "https://nasa-gibs.github.io/gibs-api-docs/", "attribution_url": "https://www.earthdata.nasa.gov/engage/open-data-services-and-software/api/gibs", "status": "network_on_demand", "date": "2025-11-07",
     "description": "250 m 级 MODIS 归档真彩影像；最大原生缩放级别 9，继续放大为插值显示，非地块级高分辨率影像。", "license_note": "NASA GIBS / EOSDIS 公开浏览服务，保留产品与日期署名。", "crs": "EPSG:3857", "tile_size": 256},
]
TILE_TEMPLATES = {
    "imagery": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "nasa": "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/2025-11-07/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg",
}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TileRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != urlsplit(req.full_url).hostname:
            raise GeoError("TILE_REDIRECT_DENIED", "瓦片服务发生未授权跳转", 503)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def tile_coordinates(source, z, x, y):
    if source not in TILE_TEMPLATES:
        raise GeoError("UNKNOWN_BASEMAP", "未找到此底图源", 404)
    values = []
    for value in (z, x, y):
        if isinstance(value, bool) or not re.fullmatch(r"\d{1,6}", str(value)):
            raise GeoError("INVALID_TILE", "瓦片坐标须为有效非负整数", 400)
        values.append(int(value))
    z, x, y = values
    max_zoom = 18 if source == "imagery" else 9
    if not 0 <= z <= max_zoom or x >= 2 ** z or y >= 2 ** z:
        raise GeoError("INVALID_TILE", "瓦片坐标超出底图原生范围", 400)
    return z, x, y


def image_type(data, reported=None):
    if not data or len(data) > TILE_LIMIT:
        raise GeoError("INVALID_TILE_IMAGE", "瓦片为空或超过 1 MB 大小限制", 503)
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        content_type = "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in data[-16:]:
        content_type = "image/png"
    else:
        raise GeoError("INVALID_TILE_IMAGE", "上游未返回有效 JPEG/PNG 影像，未生成替代底图", 503)
    if reported and reported.split(";", 1)[0].lower().strip() not in ("image/jpeg", "image/jpg", "image/png"):
        raise GeoError("INVALID_TILE_IMAGE", "上游瓦片响应类型无效", 503)
    return content_type


def response_cache_ttl(headers):
    """Respect upstream no-store/no-cache and max-age; never retain over 5 minutes."""
    control = headers.get("Cache-Control", "").lower()
    if "no-store" in control or "no-cache" in control or "private" in control:
        return 0
    try:
        age = max(0, int(headers.get("Age", "0")))
    except (ValueError, TypeError, OverflowError):
        return 0
    match = re.search(r"(?:^|,)\s*max-age\s*=\s*(\d+)", control)
    if match:
        # Age reduces upstream freshness before the shorter local retention cap.
        ttl = int(match.group(1)) - age
    elif headers.get("Expires"):
        try:
            # Expires is absolute; subtracting Age again would double-count it.
            ttl = int(parsedate_to_datetime(headers["Expires"]).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            ttl = 0
    else:
        ttl = 60 - age
    return max(0, min(ttl, CACHE_TTL))


class MapServices:
    def __init__(self, fixture, public_sources, cache_dir=None, network_concurrency=6, max_waiting=48, queue_timeout=12.0, tianditu=None):
        self.fixture = fixture
        self.public_sources = public_sources
        self.tianditu = tianditu
        # cache_dir is accepted only for constructor compatibility; imagery is never persisted.
        self.cache_dir = cache_dir
        self.cache = OrderedDict()
        self.lock = threading.RLock()
        if not isinstance(network_concurrency, int) or not 1 <= network_concurrency <= 8 or not isinstance(max_waiting, int) or not 0 <= max_waiting <= 96 or not isinstance(queue_timeout, (int, float)) or not math.isfinite(queue_timeout) or not 0 <= queue_timeout <= 30:
            raise ValueError("Invalid bounded tile queue configuration")
        self.network_concurrency = network_concurrency
        self.max_waiting = max_waiting
        self.queue_timeout = queue_timeout
        self.network_slots = threading.BoundedSemaphore(network_concurrency)
        # Bound both active fetches and waiting requests; ordinary viewport bursts queue.
        self.admission_slots = threading.BoundedSemaphore(network_concurrency + max_waiting)
        self.opener = build_opener(TileRedirect())
        self.source_states = {}

    def tile(self, source, z, x, y):
        z, x, y = tile_coordinates(source, z, x, y)
        url = TILE_TEMPLATES[source].format(z=z, x=x, y=y)
        key = (source, z, x, y)
        cached = self.cached_tile(key)
        if cached:
            return cached
        if not self.admission_slots.acquire(blocking=False):
            raise GeoError("TILE_BUSY", "底图短队列已满，请稍后重试", 503)
        network_acquired = False
        try:
            if not self.network_slots.acquire(timeout=self.queue_timeout):
                raise GeoError("TILE_QUEUE_TIMEOUT", "底图请求排队超时，请稍后重试", 503)
            network_acquired = True
            # Another request may have populated the tile while this one waited.
            cached = self.cached_tile(key)
            if cached:
                return cached
            request = Request(url, headers={"Accept": "image/jpeg,image/png", "User-Agent": "GeoAI-LocalMap/4.0"})
            try:
                with self.opener.open(request, timeout=8) as response:
                    reported = response.headers.get("Content-Type", "")
                    ttl = response_cache_ttl(response.headers)
                    data = response.read(TILE_LIMIT + 1)
                kind = image_type(data, reported)
            except GeoError:
                raise
            except (OSError, ValueError, HTTPError, URLError):
                raise GeoError("TILE_UNAVAILABLE", "真实影像底图当前无法访问，可尝试备用影像源；未生成替代图片", 503)
            fetched_at = timestamp()
            result = {"data": data, "content_type": kind, "cached": False, "source_url": url, "fetched_at": fetched_at,
                      "cache_control": "private, max-age=%d" % ttl if ttl > 0 else "private, no-store"}
            with self.lock:
                if ttl > 0:
                    self.cache[key] = {"expires": time.monotonic() + ttl, "result": copy.copy(result)}
                    self.cache.move_to_end(key)
                    self.prune_cache()
                self.source_states[source] = {"status": "last_request_succeeded", "last_fetched_at": fetched_at}
            return result
        except GeoError as error:
            if error.code != "TILE_QUEUE_TIMEOUT":
                with self.lock:
                    self.source_states[source] = {"status": "last_request_failed", "last_fetched_at": None}
            raise
        finally:
            if network_acquired:
                self.network_slots.release()
            self.admission_slots.release()

    def cached_tile(self, key):
        with self.lock:
            self.prune_cache()
            if key not in self.cache:
                return None
            entry = self.cache[key]
            self.cache.move_to_end(key)
            result = copy.copy(entry["result"])
            result["cached"] = True
            remaining = max(0, int(entry["expires"] - time.monotonic()))
            result["cache_control"] = "private, max-age=%d" % remaining
            return result

    def prune_cache(self):
        expired = [key for key, value in self.cache.items() if value["expires"] <= time.monotonic()]
        for key in expired:
            del self.cache[key]
        size = sum(len(value["result"]["data"]) for value in self.cache.values())
        while len(self.cache) > CACHE_MAX_FILES or size > CACHE_MAX_BYTES:
            _, old = self.cache.popitem(last=False)
            size -= len(old["result"]["data"])

    def config(self):
        # Business layers must come from real registered sources or user data.
        # The former five fixture-backed agriculture/facility/alert layers were
        # removed at the user's request; do not silently recreate sample points.
        layers = []
        layers.append({"id": "weather", "title": "气象预报参考位置", "source_type": "public", "source": "Open-Meteo 数值天气模型", "attribution": "Open-Meteo.com · CC BY 4.0", "attribution_url": "https://open-meteo.com/", "source_url": "https://open-meteo.com/en/docs", "feature_count": 1, "default_visible": False, "geometry_type": "Point", "url": "/api/map/layers/weather", "status": "reference_location", "color": "#f5d978", "mode": "model_forecast_reference", "description": "此点为请求坐标，不是气象站；点击按需获取模型预报并保留返回格点坐标。"})
        river_meta = SOURCE_DIR / "natural_earth_rivers_xiantao.metadata.json"
        if river_meta.is_file():
            meta = json.loads(river_meta.read_text("utf-8"))
            layers.append({"id": "rivers", "title": "公开概化水系参考", "source_type": "public", "source": "Natural Earth 1:1000万概化水系", "attribution": NATURAL_EARTH_ATTRIBUTION, "attribution_url": "https://www.naturalearthdata.com/", "source_url": meta["source_url"], "feature_count": meta["feature_count"], "default_visible": False, "geometry_type": "LineString", "url": "/api/map/layers/rivers", "status": "snapshot_ready", "color": "#62bce9", "mode": "public_snapshot", "recommended_max_zoom": 9, "fetched_at": meta["fetched_at"], "sha256": meta["snapshot_sha256"], "description": "真实公开地理参考，1:1000万概化水系；非乡村沟渠、农田边界或权威水利边界。"})
        layers.append({"id": "satellite", "title": "卫星目录覆盖范围", "source_type": "public", "source": "Element 84 Earth Search / ESA Sentinel-2", "attribution": "ESA/Copernicus · Element 84 Earth Search", "source_url": "https://earth-search.aws.element84.com/v1", "feature_count": None, "default_visible": False, "geometry_type": "Polygon", "url": "/api/map/layers/satellite", "status": "network_on_demand", "requires_network": True, "color": "#b5a0ed", "mode": "catalog_only", "date_range": "2025-11-01 / 2025-11-10", "description": "整景目录 footprint，仅表示影像覆盖，非地块或解译成果；按需请求可能受外部服务影响。"})
        basemaps = copy.deepcopy(BASEMAPS)
        for basemap in basemaps:
            # Native browser transport reuses the provider's TLS/HTTP connections.
            # Keep the fixed local proxy URL available for API clients.
            basemap["browser_url"] = TILE_TEMPLATES[basemap["id"]]
            basemap["browser_transport"] = "direct_public_provider"
            basemap.update(self.source_states.get(basemap["id"], {}))
        tianditu_state = self.tianditu.status() if self.tianditu else None
        if tianditu_state:
            configured = tianditu_state["configured"]
            national = []
            for ident, title, layer, labels, category in (
                ("tdt_imagery", "天地图影像与中文注记", "img", "cia", "imagery"),
                ("tdt_vector", "天地图标准地图与中文注记", "vec", "cva", "vector"),
            ):
                national.append({"id": ident, "name": title, "url": "/api/tianditu/tiles/"+layer+"/{z}/{x}/{y}",
                    "browser_url": "/api/tianditu/tiles/"+layer+"/{z}/{x}/{y}",
                    "annotation_url": "/api/tianditu/tiles/"+labels+"/{z}/{x}/{y}",
                    "kind": category, "category": category, "provider": "tianditu", "browser_transport": "local_authenticated_proxy",
                    "min_zoom": 1, "max_zoom": 18, "max_native_zoom": 18, "tile_size": 256, "crs": "EPSG:3857",
                    "available": configured, "status": tianditu_state["status"], "message": tianditu_state["message"],
                    "user_key_required": True, "attribution": "天地图 · 国家地理信息公共服务平台",
                    "source_url": "https://lbs.tianditu.gov.cn/server/MapService.html",
                    "attribution_url": "https://www.tianditu.gov.cn/",
                    "description": "天地图官方在线地图服务；底图与中文注记分层叠加，服务端授权代理。底图不提供可定量分析的光谱波段、观测日期或地块分类。",
                    "license_note": "需本人或单位申请的服务端Key，适用官方许可、配额和账户权限；仅当前视窗按需浏览，不批量下载瓦片。"})
            basemaps = national + basemaps if configured else basemaps + national
        return {"center": [30.32, 113.42], "zoom": 11, "max_zoom": 18, "crs": "EPSG:3857", "geojson_crs": "EPSG:4326", "basemaps": basemaps, "layers": layers,
                "integrations": {"tianditu": tianditu_state} if tianditu_state else {},
                "capabilities": {"real_raster_basemap": True, "fixed_provider_proxy": True, "tile_cache": "memory_lru_only", "tile_cache_max_files": CACHE_MAX_FILES, "tile_cache_max_bytes": CACHE_MAX_BYTES, "tile_cache_ttl_seconds": CACHE_TTL, "tile_upstream_concurrency": self.network_concurrency, "tile_max_waiting": self.max_waiting, "tile_queue_timeout_seconds": self.queue_timeout, "user_key_required": False, "tianditu_key_required": True, "pixel_interpretation": False, "synthetic_layers_available": False, "synthetic_agriculture_is_real": False},
                "limitations": ["底图为供应商多时相拼接影像，不代表实时监测；公开参考数据与用户业务图层分别标识。", "NASA 备用为低分辨率归档影像，继续放大不会产生新的地物细节。", "在线服务受供应商可用性与条款约束，保留图面署名；不提供全域离线打包或批量瓦片下载。"]}

    def layer(self, ident):
        if ident in {"agriculture", "industry", "machinery", "water", "alerts"}:
            raise GeoError("SYNTHETIC_LAYER_REMOVED", "合成样例图层已移除，请上传真实业务数据或运行已接入的遥感分析", 410)
        if ident == "weather":
            return {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "weather-reference", "geometry": {"type": "Point", "coordinates": [113.45, 30.36]}, "properties": {"name": "仙桃周边天气模型请求点", "source_type": "public", "mode": "model_forecast_reference", "source": "Open-Meteo", "description": "真实经纬度参考点，非气象站；天气是模型预报，返回格点可能与本点不同。", "api_url": "/api/public/weather?lat=30.36&lon=113.45", "attribution": "Open-Meteo.com · CC BY 4.0"}}], "metadata": {"source_type": "public", "mode": "model_forecast_reference", "crs": "EPSG:4326"}}
        if ident == "rivers":
            path = SOURCE_DIR / "natural_earth_rivers_xiantao.geojson"
            if not path.is_file():
                raise GeoError("LAYER_UNAVAILABLE", "公开水系快照尚不可用，未生成替代数据", 503)
            return json.loads(path.read_text("utf-8"))
        if ident == "satellite":
            data = self.public_sources.satellite({"bbox": ["113.2,30.2,113.4,30.4"], "start": ["2025-11-01"], "end": ["2025-11-10"], "cloud": ["100"]})
            return {"type": "FeatureCollection", "features": [{"type": "Feature", "id": item["id"], "geometry": item["geometry"], "properties": {"name": item["id"], "datetime": item["datetime"], "cloud_cover": item["cloud_cover"], "source_type": "public", "mode": "catalog_only", "source": data["provider"], "collection": item["collection"], "description": "卫星目录整景覆盖，非地块或解译成果。", "attribution": data["attribution"]}} for item in data["items"]], "metadata": {**data["provenance"], "mode": "catalog_only", "cached": data["cached"], "crs": "EPSG:4326"}}
        raise GeoError("UNKNOWN_LAYER", "未找到此地图图层", 404)


def clip_segment(first, second, bbox):
    """Liang-Barsky clip; bbox=[west,south,east,north] in CRS84."""
    x0, y0 = first[:2]
    x1, y1 = second[:2]
    dx, dy = x1 - x0, y1 - y0
    entering, leaving = 0.0, 1.0
    for p, q in zip((-dx, dx, -dy, dy), (x0 - bbox[0], bbox[2] - x0, y0 - bbox[1], bbox[3] - y0)):
        if p == 0:
            if q < 0:
                return None
            continue
        ratio = q / p
        if p < 0:
            entering = max(entering, ratio)
        else:
            leaving = min(leaving, ratio)
        if entering > leaving:
            return None
    first_out = [x0 + entering * dx, y0 + entering * dy]
    second_out = [x0 + leaving * dx, y0 + leaving * dy]
    if first_out == second_out:
        return None
    return first_out, second_out


def prepare_rivers(source_path, source_dir=None):
    """One-time small regional derivative of the public-domain Natural Earth source."""
    directory = Path(source_dir or SOURCE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    raw = Path(source_path).read_bytes()
    data = json.loads(raw)
    bbox = [112.8, 29.8, 114.1, 30.85]
    features = []
    fetched = timestamp()
    for source_index, feature in enumerate(data["features"]):
        geometry = feature["geometry"]
        lines = geometry["coordinates"] if geometry["type"] == "MultiLineString" else [geometry["coordinates"]] if geometry["type"] == "LineString" else []
        for line_index, line in enumerate(lines):
            segments, current = [], []
            for first, second in zip(line, line[1:]):
                clipped = clip_segment(first, second, bbox)
                if not clipped:
                    if current:
                        segments.append(current)
                        current = []
                    continue
                a, b = clipped
                if current and all(abs(current[-1][j] - a[j]) < 1e-9 for j in range(2)):
                    current.append(b)
                else:
                    if current:
                        segments.append(current)
                    current = [a, b]
            if current:
                segments.append(current)
            for segment_index, coordinates in enumerate(segments):
                props = feature.get("properties", {})
                features.append({"type": "Feature", "id": "ne-river-%d-%d-%d" % (source_index, line_index, segment_index), "geometry": {"type": "LineString", "coordinates": coordinates}, "properties": {"name": props.get("name_en") or props.get("name") or "Unnamed river", "source_type": "public", "source": "Natural Earth", "scale": "1:10,000,000", "attribution": NATURAL_EARTH_ATTRIBUTION, "description": "概化公开水系参考，非村级沟渠或权威水利边界。", "source_feature_index": source_index}})
    if not features:
        raise ValueError("No actual source river geometries intersected the requested area")
    metadata = {"source_type": "public", "provider": "Natural Earth", "source_url": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_rivers_lake_centerlines.geojson", "provider_url": "https://www.naturalearthdata.com/", "license": "Public domain", "license_url": "https://www.naturalearthdata.com/about/terms-of-use/", "attribution": NATURAL_EARTH_ATTRIBUTION, "crs": "OGC:CRS84 / EPSG:4326 longitude,latitude", "scale": "1:10,000,000", "fetched_at": fetched, "source_bytes": len(raw), "source_sha256": hashlib.sha256(raw).hexdigest(), "source_feature_count": len(data["features"]), "feature_count": len(features), "clip_bbox": bbox, "derivation": "Liang-Barsky segment clipping, no invented geometry; no re-projection needed for source CRS84", "limitations": "概化宏观水系，非乡村沟渠、农田边界或权威水利图层；不代表当前河道变化。"}
    result = {"type": "FeatureCollection", "features": features, "metadata": metadata}
    payload = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    (directory / "natural_earth_rivers_xiantao.geojson").write_bytes(payload)
    metadata["snapshot_sha256"] = hashlib.sha256(payload).hexdigest()
    (directory / "natural_earth_rivers_xiantao.metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), "utf-8")
    return metadata
