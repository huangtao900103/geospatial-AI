"""Bounded GeoJSON processing and fixed-provider public data adapters (stdlib)."""
import copy
import hashlib
import json
import math
import threading
import time
from collections import Counter
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

EARTH_RADIUS_M = 6371008.8
MAX_FEATURES = 500
MAX_VERTICES = 5000
MAX_RING_VERTICES = 300
ALGORITHM_VERSION = "1.0.0"


class GeoError(Exception):
    def __init__(self, code, message, status=422):
        self.code, self.message, self.status = code, message, status


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def finite(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(geojson):
    return hashlib.sha256(canonical(geojson)).hexdigest()


def ring_area_m2(ring):
    # Chamberlain-Duquette spherical ring integration, winding-independent.
    integral = sum(math.radians(b[0] - a[0]) * (2 + math.sin(math.radians(a[1])) + math.sin(math.radians(b[1])))
                   for a, b in zip(ring, ring[1:]))
    return abs(integral) * EARTH_RADIUS_M ** 2 / 2



def local_ring_area_m2(ring):
    """Origin-shifted local plane detects degenerate/collinear rings reliably."""
    origin = ring[0]
    scale_x = EARTH_RADIUS_M * math.cos(math.radians(origin[1]))
    points = [(math.radians(p[0] - origin[0]) * scale_x,
               math.radians(p[1] - origin[1]) * EARTH_RADIUS_M) for p in ring]
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:]))) / 2


def polygon_area_m2(polygon):
    return ring_area_m2(polygon[0]) - sum(ring_area_m2(hole) for hole in polygon[1:])


def orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def on_segment(a, b, p):
    return abs(orientation(a, b, p)) < 1e-12 and min(a[0], b[0]) - 1e-12 <= p[0] <= max(a[0], b[0]) + 1e-12 and min(a[1], b[1]) - 1e-12 <= p[1] <= max(a[1], b[1]) + 1e-12


def segments_intersect(a, b, c, d):
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]) or max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
        return False
    o1, o2, o3, o4 = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    return (abs(o1) < 1e-12 and on_segment(a, b, c)) or (abs(o2) < 1e-12 and on_segment(a, b, d)) or (abs(o3) < 1e-12 and on_segment(c, d, a)) or (abs(o4) < 1e-12 and on_segment(c, d, b))


def point_in_ring(point, ring):
    inside = False
    for a, b in zip(ring, ring[1:]):
        if on_segment(a, b, point):
            return False  # Strict containment; touching boundary is not accepted for holes.
        if (a[1] > point[1]) != (b[1] > point[1]):
            if point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]:
                inside = not inside
    return inside


def rings_intersect(first, second):
    return any(segments_intersect(a, b, c, d) for a, b in zip(first, first[1:]) for c, d in zip(second, second[1:]))


def polygons_overlap(first, second):
    if any(rings_intersect(a, b) for a in first for b in second):
        return True
    def inside(point, polygon):
        return point_in_ring(point, polygon[0]) and not any(point_in_ring(point, h) for h in polygon[1:])
    return inside(first[0][0], second) or inside(second[0][0], first)


def validate_geojson(value):
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection" or not isinstance(value.get("features"), list):
        raise GeoError("INVALID_GEOJSON", "请导入 GeoJSON FeatureCollection")
    if value.get("crs") is not None:
        raise GeoError("INVALID_CRS", "仅接受不带其他 CRS 声明的 WGS84 经度/纬度坐标")
    if not 1 <= len(value["features"]) <= MAX_FEATURES:
        raise GeoError("FEATURE_LIMIT", "每份数据须包含 1–500 个要素")
    all_positions, result, identifiers = [], [], set()

    def position(coords):
        if not isinstance(coords, list) or len(coords) != 2 or not all(finite(n) for n in coords):
            raise GeoError("INVALID_COORDINATE", "坐标必须为两个有限数值 [经度, 纬度]，不接受布尔值或高程维度")
        if not -180 <= coords[0] <= 180 or not -85 <= coords[1] <= 85:
            raise GeoError("COORDINATE_RANGE", "经度范围为 [-180,180]，纬度范围为 [-85,85]")
        all_positions.append(coords)
        if len(all_positions) > MAX_VERTICES:
            raise GeoError("VERTEX_LIMIT", "每份数据最多 5000 个坐标点")
        return list(coords)

    def line(coords, ring=False):
        minimum = 4 if ring else 2
        if not isinstance(coords, list) or not minimum <= len(coords) <= MAX_RING_VERTICES:
            raise GeoError("INVALID_GEOMETRY", "线/环须满足最低顶点数且不超过 300 个顶点")
        points = [position(p) for p in coords]
        if ring:
            if points[0] != points[-1]:
                raise GeoError("OPEN_RING", "Polygon 环必须首尾闭合")
            if len({tuple(p) for p in points[:-1]}) != len(points) - 1:
                raise GeoError("DEGENERATE_RING", "环不能包含重复顶点（闭合点除外）")
            if max(p[0] for p in points) - min(p[0] for p in points) > 10 or max(p[1] for p in points) - min(p[1] for p in points) > 10:
                raise GeoError("POLYGON_EXTENT", "本轮仅支持跨度不超过 10° 的局部面，跨 180° 经线或超大跨度面请先拆分")
            if local_ring_area_m2(points) < .01 or ring_area_m2(points) < .01:
                raise GeoError("DEGENERATE_RING", "Polygon 环面积为零或过小")
            edges = len(points) - 1
            for i in range(edges):
                for j in range(i + 1, edges):
                    if j == i + 1 or (i == 0 and j == edges - 1):
                        continue
                    if segments_intersect(points[i], points[i + 1], points[j], points[j + 1]):
                        raise GeoError("SELF_INTERSECTION", "Polygon 环不能自交；请修复几何后导入")
        return points

    def polygon(coords):
        if not isinstance(coords, list) or not 1 <= len(coords) <= 20:
            raise GeoError("INVALID_POLYGON", "Polygon 须包含外环，可有最多 19 个孔")
        rings = [line(ring, True) for ring in coords]
        outer = rings[0]
        for i, hole in enumerate(rings[1:]):
            if not point_in_ring(hole[0], outer) or rings_intersect(outer, hole):
                raise GeoError("INVALID_HOLE", "孔必须严格位于外环内部，不能接触或穿过外边界")
            for other in rings[1:i + 1]:
                if rings_intersect(hole, other) or point_in_ring(hole[0], other) or point_in_ring(other[0], hole):
                    raise GeoError("OVERLAPPING_HOLES", "Polygon 孔不能相交、接触或互相包含")
        if polygon_area_m2(rings) <= 0:
            raise GeoError("INVALID_HOLE", "孔面积不能大于外环面积")
        return rings

    for index, feature in enumerate(value["features"]):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise GeoError("INVALID_FEATURE", "每个要素必须为 GeoJSON Feature")
        geom = feature.get("geometry")
        if not isinstance(geom, dict):
            raise GeoError("INVALID_GEOMETRY", "不接受空几何")
        kind, coords = geom.get("type"), geom.get("coordinates")
        if kind == "Point":
            normalized = position(coords)
        elif kind == "LineString":
            normalized = line(coords)
            if len({tuple(p) for p in normalized}) < 2:
                raise GeoError("DEGENERATE_LINE", "LineString 至少需要两个不同坐标")
        elif kind == "Polygon":
            normalized = polygon(coords)
        elif kind == "MultiPolygon":
            if not isinstance(coords, list) or not 1 <= len(coords) <= 20:
                raise GeoError("INVALID_MULTIPOLYGON", "MultiPolygon 须含 1–20 个面")
            normalized = [polygon(poly) for poly in coords]
            positions = [p for poly in normalized for ring in poly for p in ring]
            if max(p[0] for p in positions) - min(p[0] for p in positions) > 10 or max(p[1] for p in positions) - min(p[1] for p in positions) > 10:
                raise GeoError("POLYGON_EXTENT", "MultiPolygon 整体跨度不能超过 10°，跨 180°经线或跨区域面请拆分")
            for i, poly in enumerate(normalized):
                for other in normalized[:i]:
                    if polygons_overlap(poly, other):
                        raise GeoError("OVERLAPPING_POLYGONS", "MultiPolygon 成员不能相交、接触或重叠；请预先修复")
        else:
            raise GeoError("UNSUPPORTED_GEOMETRY", "仅支持 Point、LineString、Polygon、MultiPolygon")
        props = feature.get("properties", {})
        if props is None:
            props = {}
        if not isinstance(props, dict) or len(props) > 40:
            raise GeoError("INVALID_PROPERTIES", "属性必须为不超过 40 项的普通键值对象")
        for key, val in props.items():
            if not isinstance(key, str) or not key or len(key) > 80 or key in ("__proto__", "constructor", "prototype"):
                raise GeoError("INVALID_PROPERTIES", "属性键不符合安全格式")
            if val is not None and not isinstance(val, (str, bool)) and not finite(val):
                raise GeoError("INVALID_PROPERTIES", "属性仅支持有限数值、字符串、布尔值或 null，不接受嵌套对象")
            if isinstance(val, str) and len(val) > 1000:
                raise GeoError("INVALID_PROPERTIES", "单个属性文本不能超过 1000 字符")
        ident = feature.get("id", "feature-%d" % (index + 1))
        if isinstance(ident, bool) or not isinstance(ident, (str, int)) or len(str(ident)) > 100:
            raise GeoError("INVALID_FEATURE_ID", "要素 ID 仅支持不超过 100 字符的字符串或整数")
        if str(ident) in identifiers:
            raise GeoError("DUPLICATE_FEATURE_ID", "要素 ID 必须唯一")
        identifiers.add(str(ident))
        result.append({"type": "Feature", "id": ident, "geometry": {"type": kind, "coordinates": normalized}, "properties": copy.deepcopy(props)})
    bbox = [min(p[0] for p in all_positions), min(p[1] for p in all_positions), max(p[0] for p in all_positions), max(p[1] for p in all_positions)]
    normalized = {"type": "FeatureCollection", "features": result}
    return normalized, {"feature_count": len(result), "bbox": bbox, "geometry_types": dict(Counter(f["geometry"]["type"] for f in result))}


ALGORITHMS = [
    {"id": "spatial_summary", "name": "空间要素统计", "description": "统计几何类型、属性分组与球面近似面积（外环减孔）。", "input_requirements": ["已导入的有效 GeoJSON", "可选 group_by 属性名"], "limitations": ["面积为球面近似，不是测绘成果", "跨要素面积为逐要素面积求和，不作空间去重或 union"], "version": ALGORITHM_VERSION},
    {"id": "nearest", "name": "最近邻距离", "description": "对 Point 要素寻找最近其他点，计算 Haversine 距离。", "input_requirements": ["至少两个 Point", "max_distance_m 默认 5000 米"], "limitations": ["仅球面直线距离，不代表道路、运输时间或实际服务可达性", "LineString/Polygon 不参与点距离"], "version": ALGORITHM_VERSION},
    {"id": "ndvi", "name": "NDVI 属性计算", "description": "读取属性 red / nir，计算 (NIR−RED)/(NIR+RED)。", "input_requirements": ["同尺度非负 red / nir 数值属性", "至少一个有效反射率记录"], "limitations": ["仅针对导入属性计算，不处理遥感栅格或自动识别作物", "缺失、负数、非数值和零分母记录标记无效"], "version": ALGORITHM_VERSION},
]


def haversine(a, b):
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, math.radians(b[0] - a[0])
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.asin(math.sqrt(min(1, max(0, value))))


def analyse(geojson, algorithm_id, parameters):
    if not isinstance(parameters, dict):
        raise GeoError("INVALID_PARAMETERS", "算法参数必须为 JSON 对象", 400)
    if algorithm_id not in {a["id"] for a in ALGORITHMS}:
        raise GeoError("UNKNOWN_ALGORITHM", "未找到此算法", 404)
    output = copy.deepcopy(geojson)
    features = output["features"]
    if not features:
        raise GeoError("NO_APPLICABLE_FEATURES", "没有可参与计算的要素")
    if algorithm_id == "spatial_summary":
        if set(parameters) - {"group_by"}:
            raise GeoError("INVALID_PARAMETERS", "空间统计仅支持 group_by 参数", 400)
        group_by = parameters.get("group_by", "kind")
        if not isinstance(group_by, str) or not 1 <= len(group_by) <= 80:
            raise GeoError("INVALID_PARAMETERS", "group_by 必须为属性字段名称", 400)
        total_area, polygon_count, groups, types = 0, 0, Counter(), Counter()
        for feat in features:
            geom, props = feat["geometry"], feat["properties"]
            kind = geom["type"]
            types[kind] += 1
            group = props.get(group_by)
            groups["(未分组)" if group is None else str(group)] += 1
            if kind in ("Polygon", "MultiPolygon"):
                area = polygon_area_m2(geom["coordinates"]) if kind == "Polygon" else sum(polygon_area_m2(p) for p in geom["coordinates"])
                props["computed_area_ha"] = round(area / 10000, 6)
                total_area += area
                polygon_count += 1
        summary = {"feature_count": len(features), "geometry_counts": dict(types), "group_by": group_by, "groups": dict(groups), "polygon_feature_count": polygon_count, "area_sum_ha": round(total_area / 10000, 6), "area_method": "球面近似：外环减孔；逐要素相加，未作跨要素去重", "review_required": True}
    elif algorithm_id == "nearest":
        if set(parameters) - {"max_distance_m"}:
            raise GeoError("INVALID_PARAMETERS", "最近邻仅支持 max_distance_m 参数", 400)
        limit = parameters.get("max_distance_m", 5000)
        if not finite(limit) or not 0 < limit <= 20000000:
            raise GeoError("INVALID_PARAMETERS", "max_distance_m 须为 (0,20000000] 米的有限数值", 400)
        points = [f for f in features if f["geometry"]["type"] == "Point"]
        if len(points) < 2:
            raise GeoError("NO_APPLICABLE_FEATURES", "最近邻至少需要两个 Point 要素")
        distances, over = [], 0
        for feat in points:
            candidates = [(haversine(feat["geometry"]["coordinates"], other["geometry"]["coordinates"]), str(other["id"]), other) for other in points if other is not feat]
            distance, _, nearest = min(candidates, key=lambda x: (x[0], x[1]))
            feat["properties"].update(nearest_distance_m=round(distance, 3), nearest_id=nearest["id"], within_max_distance=distance <= limit)
            distances.append(distance)
            over += distance > limit
        summary = {"point_count": len(points), "ignored_nonpoint_count": len(features) - len(points), "max_distance_m": limit, "over_distance_count": over, "min_nearest_distance_m": round(min(distances), 3), "mean_nearest_distance_m": round(sum(distances) / len(distances), 3), "method": "Haversine 球面直线距离；不代表路网可达性"}
    else:
        if parameters:
            raise GeoError("INVALID_PARAMETERS", "NDVI 当前固定使用 red / nir 属性，不接受其他参数", 400)
        valid, invalid, categories = [], 0, Counter()
        for feat in features:
            props = feat["properties"]
            red, nir = props.get("red"), props.get("nir")
            reason = None
            if not finite(red) or not finite(nir):
                reason = "missing_or_non_numeric"
            elif red < 0 or nir < 0:
                reason = "negative_reflectance"
            elif red + nir == 0 or not math.isfinite(red + nir):
                reason = "zero_or_invalid_denominator"
            if reason:
                props.update(ndvi=None, ndvi_status="invalid", ndvi_reason=reason, ndvi_category="invalid")
                invalid += 1
                continue
            value = (nir - red) / (nir + red)
            category = "negative" if value < 0 else "0_to_0.2" if value < .2 else "0.2_to_0.5" if value < .5 else "0.5_to_1"
            props.update(ndvi=round(value, 6), ndvi_status="valid", ndvi_category=category)
            props.pop("ndvi_reason", None)
            categories[category] += 1
            valid.append(value)
        if not valid:
            raise GeoError("NO_APPLICABLE_FEATURES", "未找到有效 red/nir 输入；缺失、负值、非数值或零分母不能计算 NDVI")
        summary = {"valid_count": len(valid), "invalid_count": invalid, "mean_ndvi": round(sum(valid) / len(valid), 6), "min_ndvi": round(min(valid), 6), "max_ndvi": round(max(valid), 6), "categories": dict(categories), "formula": "(nir-red)/(nir+red)", "mode": "attribute_only", "interpretation": "数值分段仅便于查看，不是植被类型、作物或食品安全认定"}
    return output, summary


class FixedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != urlsplit(req.full_url).hostname:
            raise GeoError("UPSTREAM_REDIRECT", "上游服务发生未授权跳转", 502)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicSources:
    def __init__(self):
        self.lock = threading.RLock()
        self.cache = {}
        self.states = {}
        self.opener = build_opener(FixedRedirect())

    def sources(self):
        return [{"id": "weather", "name": "Open-Meteo 气象预报", "provider": "Open-Meteo", "source_type": "public_live", "status": self.states.get("weather", {}).get("status", "可请求（尚未验证）"), "last_fetched_at": self.states.get("weather", {}).get("fetched_at"), "limitations": "天气为数值模型预报，非田间实测或灾害认定；免费服务仅供非商业使用，商业部署需相应许可。", "source_url": "https://open-meteo.com/en/docs"},
                {"id": "satellite", "name": "Earth Search Sentinel-2 目录", "provider": "Element 84 Earth Search", "source_type": "public_catalog", "status": self.states.get("satellite", {}).get("status", "可请求（尚未验证）"), "last_fetched_at": self.states.get("satellite", {}).get("fetched_at"), "limitations": "best-effort 目录服务；仅返回场景与整景 footprint，不下载或解译像素，无服务可用性保证。", "source_url": "https://earth-search.aws.element84.com/v1"}]

    def fetch_json(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in ("api.open-meteo.com", "earth-search.aws.element84.com"):
            raise GeoError("UPSTREAM_DENIED", "不允许此公开服务地址", 502)
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "GeoAI-LocalWorkspace/3.0"})
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise GeoError("UPSTREAM_TOO_LARGE", "公开服务返回数据超出大小限制", 502)
            return json.loads(raw.decode("utf-8"), parse_constant=lambda val: (_ for _ in ()).throw(ValueError(val)))
        except GeoError:
            raise
        except (OSError, ValueError, HTTPError, URLError):
            raise GeoError("UPSTREAM_UNAVAILABLE", "公开数据服务当前无法访问或返回无效数据，请稍后重试；未生成替代数据", 502)

    def cached_fetch(self, key, source_id, loader):
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < 300:
                result = copy.deepcopy(hit[1])
                result["cached"] = True
                result["cache_age_seconds"] = round(time.monotonic() - hit[0], 1)
                return result
        try:
            result = loader()
        except GeoError:
            with self.lock:
                self.states[source_id] = {"status": "最近请求失败", "fetched_at": None}
            raise
        result.update(fetched_at=stamp(), cached=False, cache_age_seconds=0)
        result["provenance"] = {k: copy.deepcopy(result[k]) for k in ("provider", "source_type", "fetched_at", "source_url", "attribution", "provider_url", "mode") if k in result}
        for context_field in ("requested_location", "grid_location", "query"):
            if context_field in result:
                result["provenance"][context_field] = copy.deepcopy(result[context_field])
        with self.lock:
            self.cache[key] = (time.monotonic(), copy.deepcopy(result))
            self.states[source_id] = {"status": "最近请求成功", "fetched_at": result["fetched_at"]}
            if len(self.cache) > 100:
                oldest = min(self.cache, key=lambda k: self.cache[k][0])
                del self.cache[oldest]
        return result

    def weather(self, query):
        lat = query_number(query, "lat", 30.36, -85, 85)
        lon = query_number(query, "lon", 113.45, -180, 180)
        url = "https://api.open-meteo.com/v1/forecast?" + urlencode({"latitude": lat, "longitude": lon, "current": "temperature_2m,precipitation,wind_speed_10m", "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum", "timezone": "Asia/Shanghai", "forecast_days": 5, "wind_speed_unit": "ms"})
        def load():
            data = self.fetch_json(url)
            if not isinstance(data, dict) or not isinstance(data.get("current"), dict) or not isinstance(data.get("daily"), dict):
                raise GeoError("UPSTREAM_SCHEMA", "气象服务未返回所需的当前与逐日预报", 502)
            if not finite(data.get("latitude")) or not finite(data.get("longitude")) or not -90 <= data["latitude"] <= 90 or not -180 <= data["longitude"] <= 180:
                raise GeoError("UPSTREAM_SCHEMA", "气象服务未返回有效格点坐标", 502)
            current = {k: data["current"].get(k) for k in ("time", "temperature_2m", "precipitation", "wind_speed_10m")}
            daily = {k: data["daily"].get(k) for k in ("time", "temperature_2m_max", "temperature_2m_min", "precipitation_sum")}
            if not isinstance(current["time"], str) or not all(finite(current[k]) or current[k] is None for k in ("temperature_2m", "precipitation", "wind_speed_10m")) or not isinstance(daily["time"], list) or not daily["time"] or not any(finite(current[k]) for k in ("temperature_2m", "precipitation", "wind_speed_10m")):
                raise GeoError("UPSTREAM_SCHEMA", "气象服务返回的数据格式无效", 502)
            n = len(daily["time"])
            try:
                datetime.fromisoformat(current["time"].replace("Z", "+00:00"))
                if n > 16:
                    raise ValueError()
                for value in daily["time"]:
                    date.fromisoformat(value)
            except (ValueError, TypeError, AttributeError):
                raise GeoError("UPSTREAM_SCHEMA", "气象服务返回了无效的时间字段", 502)
            if any(not isinstance(daily[k], list) or len(daily[k]) != n or not all(finite(v) or v is None for v in daily[k]) for k in ("temperature_2m_max", "temperature_2m_min", "precipitation_sum")):
                raise GeoError("UPSTREAM_SCHEMA", "气象服务逐日预报字段不完整", 502)
            return {"provider": "Open-Meteo", "source_type": "public_live", "latitude": data["latitude"], "longitude": data["longitude"], "requested_latitude": lat, "requested_longitude": lon, "requested_location": {"latitude": lat, "longitude": lon}, "grid_location": {"latitude": data["latitude"], "longitude": data["longitude"]}, "current": current, "daily": daily, "units": {"current": data.get("current_units", {}), "daily": data.get("daily_units", {})}, "timezone": data.get("timezone", "Asia/Shanghai"), "attribution": "Weather data by Open-Meteo.com · Numerical weather model forecast", "source_url": url, "mode": "model_forecast", "provider_url": "https://open-meteo.com/", "attribution_url": "https://open-meteo.com/", "license": "CC BY 4.0; free API non-commercial usage terms apply", "limitations": "数值模型预报，非田间实测或灾害认定；商业使用需按服务条款取得对应许可。"}
        return self.cached_fetch(url, "weather", load)

    def satellite(self, query):
        raw_bbox = query_value(query, "bbox", "113.1,30.1,113.7,30.6")
        try:
            bbox = [float(v) for v in raw_bbox.split(",")]
        except (ValueError, AttributeError):
            raise GeoError("INVALID_BBOX", "bbox 需为 west,south,east,north 四个有限数值", 400)
        if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox) or not (-180 <= bbox[0] < bbox[2] <= 180 and -85 <= bbox[1] < bbox[3] <= 85) or bbox[2] - bbox[0] > 5 or bbox[3] - bbox[1] > 5:
            raise GeoError("INVALID_BBOX", "bbox 须为有效局部经纬度范围，跨度不超过 5°且不跨 180°经线", 400)
        start, end = query_value(query, "start", "2025-09-01"), query_value(query, "end", "2025-11-30")
        try:
            if len(start) != 10 or len(end) != 10:
                raise ValueError()
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if first > last or (last - first).days > 366:
                raise ValueError()
        except (ValueError, TypeError):
            raise GeoError("INVALID_DATE_RANGE", "日期须为 YYYY-MM-DD，开始不晚于结束且范围不超过 366 天", 400)
        cloud = query_number(query, "cloud", 40, 0, 100)
        url = "https://earth-search.aws.element84.com/v1/search?" + urlencode({"collections": "sentinel-2-l2a", "bbox": ",".join(str(v) for v in bbox), "datetime": start + "T00:00:00Z/" + end + "T23:59:59Z", "query": json.dumps({"eo:cloud_cover": {"lte": cloud}}, separators=(",", ":")), "limit": 6})
        def load():
            data = self.fetch_json(url)
            if not isinstance(data, dict) or not isinstance(data.get("features"), list):
                raise GeoError("UPSTREAM_SCHEMA", "影像目录服务未返回有效 FeatureCollection", 502)
            items = []
            for feat in data["features"][:6]:
                if not isinstance(feat, dict) or not isinstance(feat.get("properties"), dict):
                    raise GeoError("UPSTREAM_SCHEMA", "影像目录条目格式无效", 502)
                props = feat["properties"]
                try:
                    if not isinstance(feat.get("id"), str) or not feat["id"] or len(feat["id"]) > 200:
                        raise ValueError()
                    datetime.fromisoformat(props["datetime"].replace("Z", "+00:00"))
                    cloud_value = props.get("eo:cloud_cover")
                    if cloud_value is not None and (not finite(cloud_value) or not 0 <= cloud_value <= 100):
                        raise ValueError()
                    bounds = feat.get("bbox")
                    if not isinstance(bounds, list) or len(bounds) != 4 or not all(finite(n) for n in bounds) or not (-180 <= bounds[0] < bounds[2] <= 180 and -90 <= bounds[1] < bounds[3] <= 90):
                        raise ValueError()
                    validate_geojson({"type": "FeatureCollection", "features": [{"type": "Feature", "id": "upstream", "properties": {}, "geometry": feat.get("geometry")}]})
                except (ValueError, TypeError, KeyError, AttributeError, GeoError):
                    raise GeoError("UPSTREAM_SCHEMA", "影像目录返回的场景标识、时间、范围或几何无效", 502)
                item = {"id": feat.get("id"), "datetime": props.get("datetime"), "cloud_cover": props.get("eo:cloud_cover"), "bbox": feat.get("bbox"), "geometry": feat.get("geometry"), "collection": feat.get("collection", "sentinel-2-l2a")}
                assets = feat.get("assets", {})
                asset = assets.get("thumbnail", assets.get("rendered_preview", {})) if isinstance(assets, dict) else {}
                thumb = asset.get("href") if isinstance(asset, dict) else None
                if isinstance(thumb, str):
                    p = urlsplit(thumb)
                    if p.scheme == "https" and p.hostname and (p.hostname.endswith(".amazonaws.com") or p.hostname.endswith(".element84.com")):
                        item["thumbnail"] = thumb
                items.append(item)
            return {"provider": "Element 84 Earth Search", "source_type": "public_catalog", "items": items, "attribution": "Sentinel-2 data: ESA/Copernicus · Catalog: Element 84 Earth Search", "source_url": url, "mode": "catalog_only", "provider_url": "https://element84.com/earth-search/", "attribution_url": "https://element84.com/earth-search/", "query": {"bbox": bbox, "start": start, "end": end, "cloud": cloud}, "limitations": "仅检索目录与整景覆盖范围，不表示已下载像素或完成遥感解译；服务为 best-effort。"}
        return self.cached_fetch(url, "satellite", load)


def query_value(query, key, default):
    value = query.get(key, [default])
    if isinstance(value, list):
        if len(value) != 1:
            raise GeoError("INVALID_PARAMETER", key + "仅允许一个值", 400)
        value = value[0]
    if not isinstance(value, str) and not finite(value):
        raise GeoError("INVALID_PARAMETER", key + "参数无效", 400)
    return value


def query_number(query, key, default, low, high):
    value = query_value(query, key, default)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise GeoError("INVALID_PARAMETER", key + "必须为数值", 400)
    if not math.isfinite(result) or not low <= result <= high:
        raise GeoError("INVALID_PARAMETER", key + "超出允许范围", 400)
    return result
