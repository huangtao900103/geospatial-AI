"""Credentialed, bounded TianDiTu server API adapter.

Only the user's own server-side key is used. Keys remain in the environment or
an explicit private config file; no upstream URLs, errors or raw responses with
credentials are returned. Map rendering is separate from analyzable imagery.
Official protocols: lbs.tianditu.gov.cn/server/{search2,geocoding,MapService}.html.
"""
import copy
import hashlib
import json
import math
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from geo_services import GeoError

ROOT = Path(__file__).resolve().parent
APPLY_URL = 'https://cloudcenter.tianditu.gov.cn/center/development/myApp'
DOC_URL = 'https://lbs.tianditu.gov.cn/server/MapService.html'
SEARCH_URL = 'https://api.tianditu.gov.cn/v2/search'
REVERSE_URL = 'https://api.tianditu.gov.cn/geocoder'
ADMIN_URL = 'https://api.tianditu.gov.cn/v2/administrative'
TOKEN_RE = re.compile(r'[0-9a-fA-F]{32}\Z')
TILE_LAYERS = frozenset(('img', 'cia', 'vec', 'cva'))
JSON_LIMIT = 1024 * 1024
TILE_LIMIT = 1024 * 1024
ADMIN_LIMIT = 8 * 1024 * 1024
WKT_POINT_LIMIT = 200000
SERVICE_LABELS = {'tiles': '影像与地图瓦片', 'search': '地名地址检索', 'reverse': '坐标查地址', 'administrative': '行政区参考范围'}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def token_value(value):
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value.strip()):
        raise GeoError('INVALID_TIANDITU_TOKEN', '请输入自己申请的32位十六进制天地图服务端密钥', 400)
    return value.strip()


def coordinate(value, low, high):
    if isinstance(value, bool):
        raise GeoError('INVALID_COORDINATES', '经纬度须为有效有限数值', 400)
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise GeoError('INVALID_COORDINATES', '经纬度须为有效有限数值', 400)
    if not math.isfinite(number) or not low <= number <= high:
        raise GeoError('INVALID_COORDINATES', '经纬度超出支持范围', 400)
    return number


def point(value):
    if not isinstance(value, str) or len(value) > 100:
        return None
    parts = value.split(',')
    if len(parts) != 2:
        return None
    try:
        return [coordinate(parts[0], -180, 180), coordinate(parts[1], -85, 85)]
    except GeoError:
        return None


def text(value, limit=300):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ''
    return ''.join(c for c in str(value) if ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF)[:limit]


def boundary_geometry(wkt, point_limit=WKT_POINT_LIMIT):
    """Strict bounded 2D Polygon/MultiPolygon WKT, preserving all rings.

    Syntax, finite lon/lat and closed-ring structure are checked. This is not a
    topology-repair engine; the provider is responsible for source topology.
    No regex skips unmatched characters and no simplified bound substitutes.
    """
    try: size = len(wkt.encode('utf-8')) if isinstance(wkt, str) else ADMIN_LIMIT + 1
    except UnicodeError: size = ADMIN_LIMIT + 1
    if not isinstance(wkt, str) or not wkt.strip() or size > ADMIN_LIMIT:
        raise GeoError('TIANDITU_INVALID_BOUNDARY', '行政区轮廓为空或超出本次处理大小限制', 502)
    match = re.match(r'\s*(MULTIPOLYGON|POLYGON)\s*', wkt, re.I)
    if not match:
        raise GeoError('TIANDITU_INVALID_BOUNDARY', '行政区轮廓须为二维Polygon或MultiPolygon WKT', 502)
    kind = match[1].upper(); cursor = match.end(); count = 0; points = []
    numeric = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')
    def skip():
        nonlocal cursor
        while cursor < len(wkt) and wkt[cursor].isspace(): cursor += 1
    def peek():
        skip(); return wkt[cursor:cursor + 1]
    def expect(char):
        nonlocal cursor
        skip()
        if wkt[cursor:cursor + 1] != char:
            raise GeoError('TIANDITU_INVALID_BOUNDARY', '行政区轮廓WKT结构无效，未生成替代范围', 502)
        cursor += 1
    def number(low, high):
        nonlocal cursor
        skip(); token = numeric.match(wkt, cursor)
        if token is None:
            raise GeoError('TIANDITU_INVALID_BOUNDARY', '行政区轮廓坐标无效，未生成替代范围', 502)
        cursor = token.end()
        try: return coordinate(token[0], low, high)
        except GeoError: raise GeoError('TIANDITU_INVALID_BOUNDARY', '行政区轮廓坐标越界或非有限值', 502)
    def ring():
        nonlocal count, cursor
        expect('('); result = []
        while True:
            lon = number(-180, 180)
            # WKT ordinates require whitespace, so 114.130.5 is not two values.
            if cursor >= len(wkt) or not wkt[cursor].isspace():
                raise GeoError('TIANDITU_INVALID_BOUNDARY', '行政区轮廓坐标分隔无效', 502)
            lat = number(-85, 85); count += 1
            if count > point_limit:
                raise GeoError('TIANDITU_BOUNDARY_TOO_COMPLEX', '行政区轮廓超出本次处理点数限制', 502)
            result.append([lon, lat]); points.append([lon, lat])
            if peek() != ',': break
            expect(',')
        expect(')')
        if len(result) < 4 or result[0] != result[-1] or len(set(map(tuple, result[:-1]))) < 3:
            raise GeoError('TIANDITU_INVALID_BOUNDARY', '行政区轮廓环须至少四点闭合并含三个不同顶点', 502)
        return result
    def polygon():
        expect('('); result = [ring()]
        while peek() == ',': expect(','); result.append(ring())
        expect(')'); return result
    if kind == 'POLYGON':
        coordinates = polygon(); geometry_type = 'Polygon'
    else:
        expect('('); coordinates = [polygon()]
        while peek() == ',': expect(','); coordinates.append(polygon())
        expect(')'); geometry_type = 'MultiPolygon'
    skip()
    if cursor != len(wkt):
        raise GeoError('TIANDITU_INVALID_BOUNDARY', '行政区轮廓含未支持的尾随数据', 502)
    box = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
    if box[0] >= box[2] or box[1] >= box[3]:
        raise GeoError('TIANDITU_INVALID_BOUNDARY', '行政区轮廓没有可用的二维范围', 502)
    return {'type': geometry_type, 'coordinates': coordinates}, box, count


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The tk query parameter must never be forwarded to another destination.
        raise GeoError('TIANDITU_REDIRECT_DENIED', '天地图请求发生跳转，已停止传送密钥', 502)


class TiandituServices:
    def __init__(self, config_path=None, timeout=4.0, opener=None):
        self.config_path = Path(config_path) if config_path is not None else ROOT / 'data/integrations/tianditu.json'
        self.timeout = max(0.2, min(float(timeout), 10.0))
        self.opener = opener if opener is not None else build_opener(NoRedirect())
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(4)
        self.admission = threading.BoundedSemaphore(28)
        self._generation = 0
        self._checks = {}
        self._checked_key = None
        self._checked_at = None
        self._file_token = None
        self._config_error = False
        try:
            doc = json.loads(self.config_path.read_text('utf-8'))
            self._file_token = token_value(doc.get('token'))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError, GeoError):
            self._config_error = True

    def _credential(self):
        value = os.environ.get('TIANDITU_TOKEN')
        if value:
            try:
                return token_value(value), 'environment'
            except GeoError:
                return None, 'environment_invalid'
        return self._file_token, 'local_file' if self._file_token else None

    def _required_token(self):
        with self.lock:
            token, source = self._credential()
        if not token:
            if source == 'environment_invalid':
                raise GeoError('INVALID_TIANDITU_ENV', 'TIANDITU_TOKEN环境变量格式无效，请在本机修正后重启服务', 503)
            raise GeoError('TIANDITU_NOT_CONFIGURED', '尚未配置天地图服务端密钥，请在数据接入中申请并配置', 503)
        return token

    def status(self):
        with self.lock:
            token, source = self._credential()
            identity = hashlib.sha256(token.encode()).digest() if token else None
            checks = copy.deepcopy(self._checks) if identity == self._checked_key else {}
            checked_at = self._checked_at if checks else None
            if not token:
                status = 'not_configured'
                message = '尚未配置天地图服务端密钥，公开备用底图与本地检索仍可使用'
                if source == 'environment_invalid':
                    status = 'restricted'; message = 'TIANDITU_TOKEN环境变量格式无效，请在本机修正后重启服务'
                elif self._config_error:
                    message = '本机天地图配置文件不可读取，请重新配置自己的服务端密钥'
            elif not checks:
                status = 'configured'; message = '密钥已配置，尚未进行真实连接检查'
            else:
                good = sum(v['status'] == 'verified' for v in checks.values())
                status = 'verified' if good == len(SERVICE_LABELS) else 'partial' if good else 'failed'
                message = '天地图四项服务连接检查通过' if status == 'verified' else '部分天地图服务可用，请查看各项检查结果' if good else '天地图连接检查未通过，请核对授权、配额和网络'
            services = {key: {'label': label, 'status': 'configured' if token else 'not_configured', 'message': '待真实连接检查' if token else '待配置服务端密钥'} for key, label in SERVICE_LABELS.items()}
            for key, value in checks.items():
                if key in services: services[key].update(value)
            return {'id': 'tianditu', 'name': '天地图', 'configured': bool(token), 'status': status, 'message': message,
                    'checked_at': checked_at, 'key_source': source, 'key_type': 'server', 'services': services,
                    'apply_url': APPLY_URL, 'documentation_url': DOC_URL, 'search_documentation_url': 'https://lbs.tianditu.gov.cn/server/search2.html',
                    'reverse_documentation_url': 'https://lbs.tianditu.gov.cn/server/geocoding.html',
                    'administrative_documentation_url': 'https://lbs.tianditu.gov.cn/server/administrative2.html',
                    'capabilities': {'imagery_basemap': True, 'vector_basemap': True, 'chinese_labels': True, 'place_search': True, 'reverse_geocoding': True,
                                     'analysis_pixels': False, 'administrative_boundary': True},
                    'limitations': ['底图与注记用于显示，不能作为有拍摄日期、光谱定标和云掩膜的分析像元。', '地名和地址检索结果仅用于定位，不是行政边界或分析范围。', '实际服务范围与调用额度以天地图账号的服务端授权及服务条款为准。']}

    def configure(self, body):
        if not isinstance(body, dict) or set(body) != {'token'}:
            raise GeoError('INVALID_TIANDITU_CONFIG', '配置请求只接受token字段', 400)
        token = token_value(body['token'])
        with self.lock:
            if os.environ.get('TIANDITU_TOKEN'):
                raise GeoError('TIANDITU_ENV_MANAGED', '当前由TIANDITU_TOKEN环境变量管理，请在本机更新环境变量并重启；界面不覆盖环境配置', 409)
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                fd, temporary = tempfile.mkstemp(prefix='.tianditu-', dir=str(self.config_path.parent))
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    json.dump({'token': token, 'configured_at': timestamp()}, handle)
                    handle.flush(); os.fsync(handle.fileno())
                os.replace(temporary, self.config_path)
                self._file_token = token; self._config_error = False
                self._checks = {}; self._checked_key = None; self._checked_at = None
                self._generation += 1
            except OSError:
                raise GeoError('TIANDITU_CONFIG_WRITE_FAILED', '无法保存本机天地图配置，请检查目录权限', 503)
            finally:
                if temporary:
                    try: Path(temporary).unlink(missing_ok=True)
                    except OSError: pass
        return self.status()

    def clear(self):
        with self.lock:
            try: self.config_path.unlink(missing_ok=True)
            except OSError: raise GeoError('TIANDITU_CONFIG_WRITE_FAILED', '无法删除本机天地图配置，请检查目录权限', 503)
            self._file_token = None; self._config_error = False
            self._checks = {}; self._checked_key = None; self._checked_at = None
            self._generation += 1
        result = self.status()
        if result['key_source'] in ('environment', 'environment_invalid'):
            result['message'] = '本机保存的配置已清除；环境变量仍由本机管理，请移除TIANDITU_TOKEN并重启以停用'
        return result

    def _read(self, url, accept, limit):
        if not self.admission.acquire(blocking=False):
            raise GeoError('TIANDITU_BUSY', '天地图请求较多，请稍后重试', 503)
        acquired = False
        try:
            acquired = self.slots.acquire(timeout=min(3.0, self.timeout))
            if not acquired:
                raise GeoError('TIANDITU_BUSY', '天地图请求排队超时，请稍后重试', 503)
            request = Request(url, headers={'Accept': accept, 'User-Agent': 'GeoAI-SpatialWorkbench/0.12 (local server integration)'})
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    data = response.read(limit + 1)
                    content_type = response.headers.get('Content-Type', '')
                if len(data) > limit:
                    raise GeoError('TIANDITU_RESPONSE_TOO_LARGE', '天地图响应超出本次请求大小限制', 502)
                return data, content_type
            except HTTPError as error:
                if error.code in (401, 403):
                    raise GeoError('TIANDITU_AUTH_REJECTED', '天地图拒绝访问，请核对服务端密钥、IP授权和服务权限', 502)
                if error.code == 429:
                    raise GeoError('TIANDITU_QUOTA', '天地图调用受到限流，请检查账号配额后重试', 503)
                raise GeoError('TIANDITU_UPSTREAM_HTTP', '天地图上游服务响应异常，请稍后重试', 502)
            except GeoError:
                raise
            except (OSError, ValueError, URLError, HTTPException):
                # Exception strings can contain the complete credentialed URL.
                raise GeoError('TIANDITU_UNAVAILABLE', '天地图服务连接失败或超时，请检查网络与账号授权', 503)
        finally:
            if acquired: self.slots.release()
            self.admission.release()

    def _json(self, endpoint, params, token, limit=JSON_LIMIT):
        query = dict(params); query['tk'] = token
        raw, content_type = self._read(endpoint + '?' + urlencode(query), 'application/json', limit)
        try:
            doc = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(doc, dict): raise ValueError()
            return doc
        except (ValueError, UnicodeError, RecursionError):
            raise GeoError('TIANDITU_INVALID_RESPONSE', '天地图未返回有效JSON数据，请核对服务权限', 502)

    def search(self, query, limit=8):
        if not isinstance(query, str) or not query.strip() or len(query) > 200 or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in query):
            raise GeoError('INVALID_SEARCH', '地名地址须为不超过200字符的单行文本', 400)
        if isinstance(limit, bool) or not re.fullmatch(r'\d{1,2}', str(limit)) or not 1 <= int(limit) <= 20:
            raise GeoError('INVALID_SEARCH_LIMIT', '搜索条数须为1–20', 400)
        limit = int(limit); token = self._required_token()
        params = {'keyWord': query.strip(), 'level': 12, 'mapBound': '-180,-85,180,85', 'queryType': 1, 'start': 0, 'count': limit, 'show': 1}
        doc = self._json(SEARCH_URL, {'postStr': json.dumps(params, ensure_ascii=False, separators=(',', ':')), 'type': 'query'}, token)
        status = doc.get('status') or {}
        if not isinstance(status, dict): raise GeoError('TIANDITU_INVALID_RESPONSE', '天地图检索响应状态格式异常', 502)
        code = str(status.get('infocode', ''))
        if code == '3001': return []
        if code != '1000':
            raise GeoError('TIANDITU_SEARCH_REJECTED', '天地图检索未通过，请核对密钥权限、配额与查询条件', 502)
        candidates = doc.get('pois', [])
        if str(doc.get('resultType')) == '3':
            area = doc.get('area') or []
            candidates = area if isinstance(area, list) else [area]
        if not isinstance(candidates, list):
            raise GeoError('TIANDITU_INVALID_RESPONSE', '天地图地点列表格式异常', 502)
        hits = []
        for item in candidates[:100]:
            if not isinstance(item, dict): continue
            center = point(item.get('lonlat'))
            label = text(item.get('name'), 150)
            if not center or not label: continue
            # Even administrative results have navigation points only: do not
            # turn their rectangular bound into an analysis geometry.
            identity = text(item.get('hotPointID') or item.get('uuid') or item.get('adminCode'), 100)
            if not identity: identity = hashlib.sha256((label + str(center)).encode()).hexdigest()[:20]
            address = text(item.get('address'))
            hits.append({'id': 'tianditu:' + identity, 'kind': 'place', 'label': label,
                         'subtitle': (address + ' · ' if address else '') + '天地图地名地址 · 仅用于地图定位',
                         'center': center, 'zoom': 12 if str(doc.get('resultType')) == '3' else 16,
                         'source': '天地图地名地址服务', 'source_type': 'public_geocoding', 'source_url': 'https://www.tianditu.gov.cn/',
                         'aoi_eligible': False, 'place_id': identity, 'address': address, 'coordinate_reference': '天地图返回经纬度；未作GCJ偏移转换',
                         'fetched_at': timestamp()})
            if len(hits) >= limit: break
        return hits

    def reverse(self, lon, lat):
        lon = coordinate(lon, -180, 180); lat = coordinate(lat, -85, 85)
        token = self._required_token()
        doc = self._json(REVERSE_URL, {'postStr': json.dumps({'lon': lon, 'lat': lat, 'ver': 1}, separators=(',', ':')), 'type': 'geocode'}, token)
        if str(doc.get('status')) != '0':
            raise GeoError('TIANDITU_REVERSE_REJECTED', '天地图坐标查地址未通过，请核对服务权限和输入位置', 502)
        result = doc.get('result')
        if not isinstance(result, dict) or not text(result.get('formatted_address')):
            raise GeoError('TIANDITU_NO_ADDRESS', '天地图未返回该坐标的可用地址', 404)
        component = result.get('addressComponent') or {}
        if not isinstance(component, dict): component = {}
        return {'center': [lon, lat], 'address': text(result.get('formatted_address')), 'components': {key: text(component[key]) for key in ('nation', 'province', 'city', 'county', 'town', 'road', 'address', 'poi') if key in component},
                'source': '天地图逆地理编码', 'source_url': 'https://lbs.tianditu.gov.cn/server/geocoding.html', 'source_type': 'public_geocoding', 'aoi_eligible': False,
                'fetched_at': timestamp(), 'message': '地址用于位置参考，不构成现场核实或行政界认定'}

    def administrative(self, keyword):
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 100 or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in keyword):
            raise GeoError('INVALID_ADMINISTRATIVE_QUERY', '行政区名称须为不超过100字符的单行文本', 400)
        token = self._required_token()
        doc = self._json(ADMIN_URL, {'keyword': keyword.strip(), 'childLevel': 0, 'extensions': 'true'}, token, ADMIN_LIMIT)
        if str(doc.get('status')) != '200':
            raise GeoError('TIANDITU_ADMINISTRATIVE_REJECTED', '天地图行政区查询未通过，请核对服务端权限、配额及行政区名称', 502)
        data = doc.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('district', []), list):
            raise GeoError('TIANDITU_INVALID_RESPONSE', '天地图行政区响应格式异常', 502)
        fetched_at = timestamp(); results = []; warnings = []
        for district in data.get('district', [])[:30]:
            if not isinstance(district, dict) or not text(district.get('name')): continue
            name = text(district.get('name'), 150); code = text(district.get('gb'), 30)
            reference = {'name': name, 'code': code, 'source': '天地图行政区划V2公开参考范围', 'source_url': 'https://lbs.tianditu.gov.cn/server/administrative2.html',
                         'source_type': 'public_administrative_reference', 'data_version': None, 'fetched_at': fetched_at}
            item = {'id': 'tianditu-administrative:' + (code or hashlib.sha256(name.encode()).hexdigest()[:20]), **reference,
                    'level': district.get('level') if isinstance(district.get('level'), int) and not isinstance(district.get('level'), bool) else None,
                    'geojson': None, 'boundary_status': 'unavailable', 'message': '提供方未返回可用参考轮廓'}
            center = district.get('center')
            if isinstance(center, dict):
                try: item['center'] = [coordinate(center.get('lng'), -180, 180), coordinate(center.get('lat'), -85, 85)]
                except GeoError: pass
            if district.get('boundary'):
                try:
                    geometry, box, vertex_count = boundary_geometry(district['boundary'])
                    item.update({'geojson': {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'geometry': geometry, 'properties': reference}],
                                             'metadata': {'source': reference['source'], 'source_url': reference['source_url'], 'source_type': reference['source_type'],
                                                          'coordinate_reference': '天地图返回经纬度；未作GCJ偏移转换', 'data_version': None, 'fetched_at': fetched_at,
                                                          'purpose': '参考范围显示与下载；未核定现状、法定界线或面积'}},
                                 'bbox': box, 'vertex_count': vertex_count, 'boundary_status': 'available',
                                 'message': '已按原轮廓保留多面与孔洞；仅为提供方参考界，来源年份未声明'})
                except GeoError as error:
                    item['message'] = error.message
                    warnings.append({'code': error.code, 'message': name + '：' + error.message})
            results.append(item)
        suggestions = []
        source_suggestions=data.get('suggestion') or []
        if not isinstance(source_suggestions,list):
            raise GeoError('TIANDITU_INVALID_RESPONSE', '天地图行政区候选格式异常', 502)
        for suggestion in source_suggestions[:30]:
            if isinstance(suggestion, dict):
                value = suggestion.get('name') or suggestion.get('keyword')
            else: value = suggestion
            name = text(value, 150)
            if name and name not in suggestions: suggestions.append(name)
        if not results and not suggestions:
            warnings.append({'code': 'NO_ADMINISTRATIVE_MATCH', 'message': '未查到匹配行政区，请使用完整行政区名称或编码'})
        return {'query': keyword.strip(), 'results': results, 'suggestions': suggestions, 'warnings': warnings, 'provider': 'tianditu_administrative_v2',
                'fetched_at': fetched_at, 'message': '请明确选择候选后查看参考范围；边界不会自动替换分析范围'}

    def tile(self, layer, z, x, y):
        if layer not in TILE_LAYERS:
            raise GeoError('UNKNOWN_TIANDITU_LAYER', '未找到此天地图图层', 404)
        values = []
        for value in (z, x, y):
            if isinstance(value, bool) or not re.fullmatch(r'\d{1,7}', str(value)):
                raise GeoError('INVALID_TILE', '瓦片坐标须为有效非负整数', 400)
            values.append(int(value))
        z, x, y = values
        if not 1 <= z <= 18 or x >= 2 ** z or y >= 2 ** z:
            raise GeoError('INVALID_TILE', '瓦片坐标超出天地图原生范围', 400)
        token = self._required_token()
        source_url = 'https://t0.tianditu.gov.cn/' + layer + '_w/wmts'
        params = {'SERVICE': 'WMTS', 'REQUEST': 'GetTile', 'VERSION': '1.0.0', 'LAYER': layer, 'STYLE': 'default', 'TILEMATRIXSET': 'w', 'FORMAT': 'tiles', 'TILEMATRIX': z, 'TILEROW': y, 'TILECOL': x, 'tk': token}
        data, reported = self._read(source_url + '?' + urlencode(params), 'image/jpeg,image/png', TILE_LIMIT)
        if data.startswith(b'\x89PNG\r\n\x1a\n') and b'IEND' in data[-16:]: content_type = 'image/png'
        elif data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'): content_type = 'image/jpeg'
        else: raise GeoError('TIANDITU_INVALID_TILE', '天地图未返回有效影像瓦片，请检查地图服务授权与配额', 502)
        if reported and reported.split(';', 1)[0].strip().lower() not in ('image/png', 'image/jpeg', 'image/jpg', 'application/octet-stream'):
            raise GeoError('TIANDITU_INVALID_TILE', '天地图瓦片响应类型异常，未显示替代影像', 502)
        return {'data': data, 'content_type': content_type, 'cached': False, 'source_url': source_url, 'fetched_at': timestamp(), 'cache_control': 'private, no-store'}

    def test(self):
        token = self._required_token()
        identity = hashlib.sha256(token.encode()).digest()
        with self.lock: generation = self._generation
        def run(key, call):
            try:
                with self.lock:
                    current, _ = self._credential()
                    if generation != self._generation or current != token:
                        raise GeoError('TIANDITU_CONFIG_CHANGED', '检查期间配置已改变，请重新检查', 409)
                value = call()
                if key == 'search' and not value:
                    return key, {'status': 'failed', 'message': '检索接口有响应，但检查地点没有返回有效坐标'}
                if key == 'administrative' and not any(item.get('geojson') for item in value['results']):
                    return key, {'status': 'failed', 'message': '行政区接口有响应，但检查地区没有返回可用轮廓'}
                return key, {'status': 'verified', 'message': (SERVICE_LABELS.get(key) or '地图瓦片') + '真实请求通过'}
            except GeoError as error:
                return key, {'status': 'failed', 'message': error.message, 'code': error.code}
        calls = [('tile_' + layer, lambda layer=layer: self.tile(layer, 10, 837, 420)) for layer in ('img','cia','vec','cva')]
        calls += [('search', lambda: self.search('武汉市', 1)), ('reverse', lambda: self.reverse(114.305, 30.593)),
                 ('administrative', lambda: self.administrative('仙桃市'))]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = dict(executor.map(lambda item: run(*item), calls))
        layers={layer:results.pop('tile_' + layer) for layer in ('img','cia','vec','cva')}
        passed=sum(v['status']=='verified' for v in layers.values())
        results['tiles']={'status':'verified' if passed==4 else 'partial' if passed else 'failed','message':'四种地图与注记瓦片真实请求通过' if passed==4 else str(passed)+'/4种地图与注记瓦片检查通过','layers':layers}
        with self.lock:
            current, _ = self._credential()
            unchanged = generation == self._generation and current and hashlib.sha256(current.encode()).digest() == identity
            if unchanged:
                self._checked_key = identity; self._checks = results; self._checked_at = timestamp()
        status = self.status()
        if not unchanged: status['message'] = '检查期间密钥配置已改变，旧检查结果未应用；请重新检查'
        return status
