#!/usr/bin/env python3
"""GeoAI local workspace: bounded spatial algorithms and fixed public-data adapters."""
import argparse
import copy
import json
import math
import mimetypes
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from backend.paths import PROJECT_ROOT
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from backend.core.geo_services import ALGORITHMS, GeoError, PublicSources, analyse, digest, validate_geojson
from backend.agriculture.agriculture_atlas import AgricultureAtlas
from backend.agriculture.agri_research_services import AgriResearchServices
from backend.agriculture.crop_reference_services import CropReferenceServices
from backend.workflows.scene_workflow_services import SceneWorkflowServices
from backend.core.map_services import MapServices
from backend.analysis.landcover_services import LandcoverServices, capabilities as landcover_capabilities
from backend.analysis.cropland_services import CroplandServices, capabilities as cropland_capabilities
from backend.workflows.assistant_services import AssistantServices, capabilities as assistant_capabilities
from backend.catalog.data_catalog_services import DataCatalogServices
from backend.workflows.execution_services import ExecutionServices, capabilities as execution_capabilities
from backend.workflows.application_services import ApplicationServices
from backend.core.search_services import SearchServices
from backend.core.inspection_services import InspectionServices
from backend.core.result_bundle_services import ResultBundleServices
from backend.catalog.business_scenarios import catalog as business_scenarios_catalog
from backend.integrations.tianditu_services import TiandituServices
from backend.core.observation_repository import ObservationRepository
from backend.catalog.ai_data_tools import catalog as ai_data_tools
from backend.catalog.algorithm_catalog import catalog as algorithm_catalog, match_observation
from backend.analysis.production_services import ProductionServices, capabilities as production_capabilities
from backend.agriculture.agriculture_services import AgricultureServices
from backend.analysis.raster_processing_services import RasterProcessingServices, capabilities as raster_processing_capabilities
from backend.integrations.codex_gateway import CodexGateway
from backend.integrations.codex_planner import CodexPlanner

ROOT = PROJECT_ROOT
MAX_BODY = 2 * 1024 * 1024
KINDS = ("farm", "industry", "alert", "machine", "water")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex[:14]


SCENARIOS = [
    {"id": "baseline", "name": "农业底数一张图", "description": "关联地块、经营主体与设施，建立统一空间台账。", "category": "资源底数", "icon": "▱"},
    {"id": "crop", "name": "秋冬播遥感监测", "description": "设计多期影像对比、地块关联与现场核查流程。", "category": "生产保护", "icon": "♧"},
    {"id": "farmland", "name": "高标准农田监管", "description": "关联工程台账与地块，组织建设变化核查。", "category": "重点工程", "icon": "▤"},
    {"id": "water", "name": "养殖尾水治理", "description": "关联养殖设施、治理工程与待核查问题。", "category": "重点工程", "icon": "≈"},
    {"id": "disaster", "name": "农业防灾减灾", "description": "关联受影响地块和预警线索，形成核查任务。", "category": "风险治理", "icon": "△"},
    {"id": "fishing", "name": "长江禁捕巡查", "description": "组织巡查点、现场记录与事件核查。", "category": "风险治理", "icon": "◎"},
    {"id": "eel", "name": "黄鳝全产业链", "description": "贯通养殖、加工、冷链与经授权的供应链指标。", "category": "特色产业", "icon": "◇"},
    {"id": "service", "name": "农机与冷链服务", "description": "汇集北斗农机作业与冷链设施，分析服务分布。", "category": "协同服务", "icon": "⇄"},
]
MODULES = [
    {"id": "baseline", "name": "农业底数", "category": "资源底数", "description": "地块、主体、设施的统一空间查询。"},
    {"id": "crop", "name": "遥感变化监测", "category": "生产保护", "description": "多期影像与待核查图斑展示，专业引擎待接入。"},
    {"id": "farmland", "name": "高标准农田", "category": "重点工程", "description": "关联工程范围、建设台账与核查记录。"},
    {"id": "water", "name": "尾水治理", "category": "重点工程", "description": "治理设施、养殖主体与核查线索联动。"},
    {"id": "disaster", "name": "防灾减灾", "category": "风险治理", "description": "展示预警线索与影响地块，需专业数据支撑。"},
    {"id": "fishing", "name": "禁捕巡查", "category": "风险治理", "description": "巡查点与现场核查，不自动认定违规。"},
    {"id": "eel", "name": "黄鳝产业链", "category": "特色产业", "description": "养殖、加工、物流主体的空间关联。"},
    {"id": "machinery", "name": "北斗农机轨迹", "category": "协同服务", "description": "设备位置与作业记录，真实轨迹接口待接入。"},
    {"id": "coldchain", "name": "冷链物流", "category": "协同服务", "description": "节点位置、容量字段与服务分布。"},
    {"id": "land", "name": "土地确权", "category": "资源底数", "description": "关联经授权的地块编码，不作权属认定。"},
    {"id": "mobile", "name": "移动核查填报", "category": "协同服务", "description": "待核查任务与现场回填接口。"},
]
RESOURCES = [
    {"id": "s2", "name": "Sentinel-2 多时相影像", "type": "遥感影像", "source": "Element 84 Earth Search 公开目录", "status": "目录接口按需查询；有界像素分析见遥感生产", "resolution": "场景元数据 / 10 m 影像目录"},
    {"id": "weather-public", "name": "Open-Meteo 气象预报", "type": "公开模型预报", "source": "Open-Meteo", "status": "按需请求，非田间实测", "resolution": "服务格点 / 当前与逐日预报"},
    {"id": "boundary", "name": "行政区划与地块边界", "type": "矢量地块", "source": "相关主管部门（拟接入）", "status": "待授权", "resolution": "市 / 镇 / 地块"},
    {"id": "projects", "name": "高标准农田工程台账", "type": "项目台账", "source": "农业农村相关部门（拟接入）", "status": "待授权", "resolution": "项目级"},
    {"id": "eel-subjects", "name": "黄鳝养殖加工主体", "type": "产业台账", "source": "农业农村相关部门（拟接入）", "status": "待授权", "resolution": "主体级"},
    {"id": "machine-tracks", "name": "北斗农机作业轨迹", "type": "轨迹数据", "source": "社会化服务平台（拟接入）", "status": "待接入", "resolution": "设备 / 作业批次"},
    {"id": "coldchain-nodes", "name": "冷链物流节点", "type": "设施台账", "source": "业务主管部门（拟接入）", "status": "待授权", "resolution": "个数 / 容量"},
]
INTEGRATIONS = [
    {"id": "remote-sensing", "name": "遥感专业引擎", "description": "接入授权遥感检索、解译与计算服务；专业引擎权属独立。", "status": "待接入"},
    {"id": "agri-water", "name": "农业与水利业务系统", "description": "连接工程台账、土地确权与水利图层，保留源系统权属。", "status": "待接入"},
    {"id": "supply-chain", "name": "黄鳝供应链平台", "description": "关联经授权的产业主体与物流汇总指标。", "status": "待接入"},
    {"id": "machinery-mobile", "name": "北斗农机与移动填报", "description": "关联作业轨迹、待核查任务与现场回填。", "status": "待接入"},
]
LIMITATIONS = [
    "当前仅在本机运行；不提供跨机构认证或公网共享。",
    "需求规划使用明确标识的规则辅助，尚未连接大模型。",
    "客户问答实际调用白名单工具；旧版工作流为合成GeoJSON。原生光谱分析有范围限制；不提供整市现状耕地分类或通用灾损解译。",
    "业务台账需由用户授权导入；公开栅格、参考边界和用户图层分别保留来源与年份。",
    "行政认定、土地权属、食品安全与执法结论必须有授权数据和人工核查。",
]
SCENARIO_KINDS = {
    "baseline": set(KINDS), "crop": {"farm", "alert"}, "farmland": {"farm"},
    "water": {"water", "industry"}, "disaster": {"farm", "alert"},
    "fishing": {"water", "alert"}, "eel": {"industry", "water"}, "service": {"machine", "industry"},
}
SUPPORTED_REGIONS = {"仙桃市", "湖北省 · 仙桃市", "湖北省仙桃市", "湖北省 / 仙桃市", "湖北省·仙桃市", "仙桃"}


class APIError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status


def text(value, name, maximum=200, required=True):
    if not isinstance(value, str):
        raise APIError("INVALID_FIELD", name + "必须为字符串")
    value = value.strip()
    if required and not value:
        raise APIError("INVALID_FIELD", name + "不能为空")
    if len(value) > maximum or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise APIError("INVALID_FIELD", name + "长度或字符不符合要求")
    return value


def polygon_area_ha(ring):
    """Local equirectangular area approximation, explicit in provenance."""
    latitude = sum(p[1] for p in ring[:-1]) / (len(ring) - 1)
    origin = ring[0]
    points = [((p[0] - origin[0]) * 111320 * math.cos(math.radians(latitude)),
               (p[1] - origin[1]) * 111320) for p in ring]
    return abs(sum(points[i][0] * points[i + 1][1] - points[i + 1][0] * points[i][1]
                   for i in range(len(points) - 1))) / 20000


def fixture():
    features = []
    for i in range(36):
        col, row = i % 9, i // 9
        lon = 113.19 + col * .024 + (row % 2) * .006
        lat = 30.235 + row * .021
        kind = KINDS[i % len(KINDS)]
        town = "张沟镇" if col < 5 else "彭场镇"
        prefix = {"farm": "示例农业地块", "industry": "示例产业节点", "alert": "示例待核查点", "machine": "示例农机服务点", "water": "示例尾水治理点"}[kind]
        props = {"id": "feature-%02d" % (i + 1), "name": prefix + " %02d" % (i + 1), "kind": kind,
                 "town": town, "status": "待核查" if kind == "alert" else "示例数据", "source": "GeoAI 本机合成示例 · 非真实业务数据"}
        if kind == "farm":
            ring = [[lon, lat], [lon + .007, lat], [lon + .007, lat + .0045], [lon, lat + .0045], [lon, lat]]
            geometry = {"type": "Polygon", "coordinates": [ring]}
            props["area_ha"] = round(polygon_area_ha(ring), 3)
        else:
            geometry = {"type": "Point", "coordinates": [lon, lat]}
        features.append({"type": "Feature", "id": props["id"], "properties": props, "geometry": geometry})
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"mode": "synthetic", "crs": "EPSG:4326", "source": "本机合成示例", "not_for_decision": True}}


# Kept as a compatibility constant for old test imports; production never seeds
# fabricated features. The pure fixture() helper is only used explicitly by tests.
FIXTURE = {"type": "FeatureCollection", "features": [], "metadata": {"mode": "empty_workspace", "crs": "EPSG:4326"}}


def region_town(region):
    compact = re.sub(r"[\s·/＞>]+", "", region)
    if compact in {"仙桃", "仙桃市", "湖北省仙桃市"}:
        return None
    for town in ("张沟镇", "彭场镇"):
        if compact in {town, "仙桃市" + town, "湖北省仙桃市" + town}:
            return town
    raise APIError("UNSUPPORTED_REGION", "本机示例仅支持仙桃市、张沟镇、彭场镇，不能生成其他区域的虚假结果", 422)


REQUIREMENT_RULES = [
    ("baseline", "农业资源底数", ("底数", "农资", "门店", "养殖场", "鸡场", "猪场", "主体", "设施")),
    ("crop", "粮食保护与秋冬播监测", ("秋冬", "播种", "遥感", "种植", "粮食", "农产品保护")),
    ("farmland", "高标准农田工程", ("高标准", "农田")),
    ("water", "尾水与环境治理", ("尾水", "水质", "污染")),
    ("disaster", "农业防灾减灾", ("防灾", "减灾", "灾情", "洪涝", "干旱", "气象")),
    ("fishing", "长江禁捕巡查", ("禁捕", "禁补", "渔政", "巡查")),
    ("eel", "黄鳝与特色产业链", ("黄鳝", "产业链", "加工", "莲藕", "藕展示", "供应链")),
    ("service", "农机与冷链服务", ("北斗", "农机", "冷链", "物流", "作业轨迹", "社会化服务")),
    ("land", "二轮延包与土地确权", ("延包", "确权", "土地权属")),
    ("integration", "水利及既有系统协同", ("水利", "已有系统", "平台对接", "系统对接")),
    ("mobile", "移动核查与小程序填报", ("小程序", "填报", "移动核查")),
]


def parse_requirements(requirement):
    return [{"id": "requirement-" + sid, "title": title,
             "scenario_id": sid if sid in SCENARIO_KINDS else "baseline", "domain_id": sid,
             "description": "从原始需求识别，需业务确认数据和成果范围。", "status": "needs_data"}
            for sid, title, words in REQUIREMENT_RULES if any(w in requirement for w in words)]


def local_model_config(path):
    """Local opt-in only; tests/new workspaces do not inherit account usage."""
    path=Path(path)
    if not path.is_file():return {'enabled':False,'timeout':180}
    if path.stat().st_size>4096:raise ValueError('model configuration is too large')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or set(value)-{'provider','enabled','model','timeout'}:
        raise ValueError('model configuration only accepts provider/enabled/model/timeout')
    if value.get('provider')!='codex_cli':raise ValueError('only codex_cli is configured for this local test')
    return {'enabled':value.get('enabled',False),'model':value.get('model'),'timeout':value.get('timeout',180)}


class Workspace:
    def __init__(self, db_path=None, node_delay=.25):
        self.db_path = str(db_path or ROOT / "data" / "workspace.sqlite3")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.node_delay = node_delay
        self.stopping = False
        self.public_sources = PublicSources()
        self.tianditu = TiandituServices(config_path=Path(self.db_path).parent / "integrations" / "tianditu.json")
        self.map_services = MapServices({"type": "FeatureCollection", "features": []}, self.public_sources, tianditu=self.tianditu)
        self.threads = []
        with self.connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(kind,id))")
        # Explicit recovery: persisted in-flight jobs may not silently become successful.
        for run in self.list("run"):
            if run["status"] in ("queued", "running"):
                run["status"] = "failed"
                run["error"] = "本机服务重启，原作业中断；请重试创建新运行记录。"
                run["logs"].append({"time": now(), "message": run["error"]})
                for node in run["nodes"]:
                    if node["status"] == "running":
                        node.update(status="failed", message="服务重启中断")
                self.save("run", run)
        self.observations = ObservationRepository(self)
        self.agriculture_atlas = AgricultureAtlas(Path(self.db_path).parent / "agriculture-atlas")
        self.agri_research = AgriResearchServices(self)
        self.crop_reference = CropReferenceServices(self)
        self.production = ProductionServices(self)
        self.landcover = LandcoverServices(self)
        self.cropland = CroplandServices(self)
        self.data_catalog = DataCatalogServices(self)
        self.agriculture = AgricultureServices(self)
        self.raster_processing = RasterProcessingServices(self)
        self.executions = ExecutionServices(self)
        self.applications = ApplicationServices(self)
        self.model_gateway = CodexGateway(**local_model_config(Path(self.db_path).parent / 'integrations' / 'model.json'))
        self.model_planner = CodexPlanner(self, self.model_gateway)
        self.assistant = AssistantServices(self)
        self.scene_workflows = SceneWorkflowServices(self)
        self.search_services = SearchServices(self, tianditu=self.tianditu)
        self.inspection_services = InspectionServices(self)
        self.result_bundles = ResultBundleServices(self)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save(self, kind, obj):
        with self.lock, self.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO records(kind,id,data,created_at) VALUES(?,?,?,?)",
                         (kind, obj["id"], json.dumps(obj, ensure_ascii=False), obj.get("created_at", now())))
        return copy.deepcopy(obj)

    def get(self, kind, ident):
        with self.lock, self.connection() as conn:
            row = conn.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, ident)).fetchone()
        if not row:
            raise APIError("NOT_FOUND", "未找到请求的记录", 404)
        return json.loads(row[0])

    def list(self, kind):
        with self.lock, self.connection() as conn:
            rows = conn.execute("SELECT data FROM records WHERE kind=? ORDER BY created_at DESC,id DESC", (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def datasets(self):
        # No virtual seed dataset: an empty workspace must remain empty.
        return [{k: v for k, v in obj.items() if k != "geojson"} for obj in self.list("dataset")]

    def get_dataset(self, ident):
        if ident == "xt-fixture":
            raise GeoError("SAMPLE_DATASET_REMOVED", "合成样例数据集已移除，请上传真实数据或选择已完成的遥感成果", 410)
        return self.get("dataset", ident)

    def create_dataset(self, body):
        if set(body) - {"name", "geojson"}:
            raise APIError("INVALID_FIELD", "数据导入仅接受 name 与 geojson 字段")
        name = text(body.get("name"), "数据集名称", 100)
        normalized, metadata = validate_geojson(body.get("geojson"))
        return self.save("dataset", {"id": new_id("dataset"), "name": name, "source_type": "user", "version": 1,
                                    "sha256": digest(normalized), "created_at": now(), "geojson": normalized, **metadata})

    def create_analysis(self, body):
        if set(body) - {"dataset_id", "algorithm_id", "parameters"}:
            raise APIError("INVALID_FIELD", "分析请求仅接受数据集、算法与参数")
        dataset = self.get_dataset(text(body.get("dataset_id"), "数据集 ID", 100))
        algorithm_id = text(body.get("algorithm_id"), "算法 ID", 100)
        parameters = body.get("parameters", {})
        output, summary = analyse(dataset["geojson"], algorithm_id, parameters)
        created = now()
        result = {"id": new_id("analysis"), "dataset_id": dataset["id"], "algorithm_id": algorithm_id, "status": "succeeded",
                  "feature_count": len(output["features"]), "geojson": output, "summary": summary, "created_at": created,
                  "provenance": {"source_type": dataset["source_type"], "dataset_id": dataset["id"], "dataset_name": dataset["name"], "dataset_version": dataset["version"],
                                 "sha256": dataset["sha256"], "algorithm_id": algorithm_id, "algorithm_version": next(a["version"] for a in ALGORITHMS if a["id"] == algorithm_id),
                                 "created_at": created, "parameters": copy.deepcopy(parameters), "crs": "EPSG:4326", "review_status": "待业务核查", "external_calls": False,
                                 "mode": "fixture-spatial-analysis" if dataset["source_type"] == "synthetic" else "user-data-analysis",
                                 "source": "本机合成示例" if dataset["source_type"] == "synthetic" else "用户导入；原始真实性由提供方负责"}}
        return self.save("analysis", result)

    def bootstrap(self):
        integrations = copy.deepcopy(INTEGRATIONS)
        configured = {x["id"]: x for x in self.list("integration")}
        for item in integrations:
            if item["id"] in configured:
                item.update(configured[item["id"]])
        datasets = self.datasets()
        resources = [r for r in RESOURCES if r["id"] != "xt-fixture"]
        return {"mode": "local", "workspace": {"name": "时空智能工作台", "region": "按用户选区与来源范围分析"},
                "scenarios": SCENARIOS, "modules": MODULES, "resources": resources, "integrations": integrations,
                "metrics": {"resources": len(resources) + len(datasets), "workflows": len(self.list("execution")), "apps": len(self.list("app")), "capabilities": len(MODULES), "datasets": len(datasets), "analyses": len(self.list("analysis"))},
                "datasets": datasets, "algorithms": ALGORITHMS, "public_sources": self.public_sources.sources(),
                "limitations": ["只提供已接入的真实数据与算法；没有数据时不生成示例业务图层。", "用户数据可进行空间与属性计算；原始真实性由提供方负责。"] + LIMITATIONS}

    def map_data(self, scenario=None, region="仙桃市"):
        # This compatibility route no longer invents business features. Real
        # user layers and raster tasks are fetched through their typed APIs.
        return {"type": "FeatureCollection", "features": [],
                "metadata": {"source_type": "empty", "mode": "empty_workspace", "crs": "EPSG:4326",
                             "message": "请显示已上传的真实图层，或运行已接入的遥感分析。"}}

    def create_plan(self, body):
        raise APIError("LEGACY_SAMPLE_WORKFLOW_REMOVED", "旧版合成示例工作流已移除，请从专题分析提交真实数据分析需求", 410)

    def create_run(self, body):
        raise APIError("LEGACY_SAMPLE_WORKFLOW_REMOVED", "旧版合成示例运行已移除，请使用真实执行与专题分析接口", 410)

    def modify_run(self, ident, change):
        with self.lock:
            run = self.get("run", ident)
            if run["status"] in ("cancelled", "succeeded", "failed"):
                return False
            change(run)
            self.save("run", run)
            return True

    def execute(self, run_id, plan):
        raise APIError("LEGACY_SAMPLE_WORKFLOW_REMOVED", "合成示例执行器已停用，不生成替代数据或模拟结果", 410)

    def cancel(self, ident):
        with self.lock:
            run = self.get("run", ident)
            if run["status"] not in ("queued", "running"):
                raise APIError("INVALID_STATE", "只有排队或运行中的任务可以取消", 409)
            run["status"] = "cancelled"
            run["finished_at"] = now()
            for node in run["nodes"]:
                if node["status"] in ("running", "pending"):
                    node.update(status="cancelled", message="用户取消")
            run["logs"].append({"time": now(), "message": "用户取消了此次运行，保留日志。"})
            return self.save("run", run)

    def retry(self, ident):
        run = self.get("run", ident)
        if run["status"] not in ("failed", "cancelled"):
            raise APIError("INVALID_STATE", "只有失败或取消的运行可以重试", 409)
        return self.create_run({"plan_id": run["plan_id"]})

    def validate_app(self, body, old=None):
        raise APIError("LEGACY_SAMPLE_APP_REMOVED", "旧版样例应用配置已移除；请使用真实专题和已归档分析成果", 410)

    def create_app(self, body):
        raise APIError("LEGACY_SAMPLE_APP_REMOVED", "旧版样例应用配置已移除；请使用真实专题和已归档分析成果", 410)

    def update_app(self, ident, body):
        raise APIError("LEGACY_SAMPLE_APP_REMOVED", "旧版样例应用配置已移除，不能更新样例预览", 410)

    def publish_app(self, ident, body):
        raise APIError("LEGACY_SAMPLE_APP_REMOVED", "旧版样例应用发布已移除，不发布模拟业务能力", 410)

    def clone_app(self, ident, body=None):
        raise APIError("LEGACY_SAMPLE_APP_REMOVED", "旧版样例应用复制已移除，不再生成样例配置", 410)

    def configure_integration(self, ident, body):
        if ident not in {i["id"] for i in INTEGRATIONS}:
            raise APIError("NOT_FOUND", "未找到此集成服务", 404)
        if set(body) - {"endpoint", "protocol"}:
            raise APIError("INVALID_FIELD", "不接受凭据或其他字段；请仅保存服务地址与协议")
        endpoint = text(body.get("endpoint"), "服务地址", 1000)
        try:
            parsed = urlsplit(endpoint)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError()
            parsed.port
            if any(re.search(r"token|secret|key|password|credential|auth", k, re.I) for k in parse_qs(parsed.query, keep_blank_values=True)):
                raise ValueError()
        except ValueError:
            raise APIError("INVALID_ENDPOINT", "请输入不包含凭据、密钥或片段的 HTTP(S) 服务地址")
        protocol = text(body.get("protocol", "REST API"), "协议", 50)
        if protocol not in ("REST API", "REST", "OGC WMS / WMTS", "WMS", "WMTS", "GeoJSON"):
            raise APIError("INVALID_PROTOCOL", "不支持该集成协议")
        return self.save("integration", {"id": ident, "endpoint": endpoint, "protocol": protocol, "status": "草案已保存", "verified": False, "created_at": now()})

    def close(self):
        self.model_gateway.close()
        self.executions.close()
        self.assistant.close()
        self.raster_processing.close()
        self.agriculture.close()
        self.landcover.close()
        self.cropland.close()
        self.production.close()
        self.stopping = True
        for thread in self.threads:
            thread.join(timeout=2)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(workspace, static_dir=None):
    static_root = Path(static_dir or ROOT / "dist").resolve()

    class Handler(SimpleHTTPRequestHandler):
        server_version = "GeoAI/1.0"
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_root), **kwargs)

        def log_message(self, fmt, *args):
            if not getattr(self.server, "quiet", False):
                super().log_message(fmt, *args)

        def end_headers(self):
            if not urlsplit(self.path).path.startswith("/api/"):
                self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            super().end_headers()

        def json_response(self, obj, status=200, content_type="application/json"):
            payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type+"; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def tile_response(self, result, source):
            self.send_response(200)
            self.send_header("Content-Type", result["content_type"])
            self.send_header("Content-Length", str(len(result["data"])))
            self.send_header("Cache-Control", result["cache_control"])
            self.send_header("X-Tile-Cache", "HIT" if result["cached"] else "MISS")
            self.send_header("X-Source-Id", source)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(result["data"])

        def artifact_response(self, path, download_name=None):
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "private, max-age=3600")
            if download_name is not None:
                self.send_header("Content-Disposition", 'attachment; filename="'+download_name+'"')
            self.end_headers()
            if self.command != "HEAD":
                with path.open("rb") as artifact:
                    while True:
                        block = artifact.read(1024 * 1024)
                        if not block: break
                        self.wfile.write(block)

        def error(self, exc):
            self.json_response({"error": {"code": exc.code, "message": exc.message}}, exc.status)

        def body(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise APIError("INVALID_BODY", "无效请求长度")
            if length < 0 or length > MAX_BODY:
                if MAX_BODY < length <= MAX_BODY * 2:
                    self.connection.settimeout(3)
                    try:
                        self.rfile.read(length)
                    except (OSError, TimeoutError):
                        pass
                self.close_connection = True
                raise APIError("BODY_TOO_LARGE", "请求体超出 2 MB 上限", 413)
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise APIError("INVALID_CONTENT_TYPE", "请使用 application/json 请求", 415)
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"), parse_constant=lambda val: (_ for _ in ()).throw(ValueError(val)))
            except (ValueError, UnicodeError):
                raise APIError("INVALID_JSON", "请求体必须为合法 JSON")
            if not isinstance(value, dict):
                raise APIError("INVALID_BODY", "请求体必须为 JSON 对象")
            return value

        def check_origin(self):
            origin = self.headers.get("Origin")
            if not origin:
                return
            try:
                url = urlsplit(origin)
                current_host = self.headers.get("Host", "")
                if url.scheme not in ("http", "https") or url.netloc != current_host:
                    raise ValueError()
            except ValueError:
                raise APIError("ORIGIN_DENIED", "仅允许当前工作空间同源请求", 403)

        def api(self, method):
            parsed = urlsplit(self.path)
            path = parsed.path.rstrip("/")
            parts = path.split("/")[2:]
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                    raise APIError("HOST_DENIED", "本服务仅用于本机访问", 403)
                if method in ("POST", "PATCH"):
                    self.check_origin()
                    body = self.body()
                if method == "GET":
                    if parts == ["crop-reference", "capabilities"]:return self.json_response({"source_id":"globalrice20_2024_esa","year":2024,"operations":["rice_reference_area","native_raster_map"],"scope_modes":["selected_aoi","administrative","xiantao_city"],"administrative_provider":"tianditu_reference","max_native_pixels":20000000,"selected_max_span_deg":0.05,"region_specific_source":False})
                    if parts and parts[0]=='agri-research':
                        if set(query)-{'series_sha256'} or 'series_sha256' in query and (len(query['series_sha256'])!=1 or not re.fullmatch(r'[a-f0-9]{64}',query['series_sha256'][0])):raise APIError('INVALID_SERIES_QUERY','仅接受单个series_sha256版本条件',400)
                        expected=query.get('series_sha256',[None])[0]
                        if parts==['agri-research','catalog']:
                            if query:raise APIError('INVALID_SERIES_QUERY','目录不接受版本条件',400)
                            return self.json_response(workspace.agri_research.catalog())
                        if len(parts)>=3 and parts[1]=='series':
                            if len(parts)==3:return self.json_response(workspace.agri_research.get(parts[2],expected))
                            if len(parts)>=5 and parts[3]=='files':return self.artifact_response(workspace.agri_research.artifact(parts[2],'/'.join(parts[4:]),expected))
                        if len(parts) in (3,4) and parts[1]=='reviews':
                            if query:raise APIError('INVALID_SERIES_QUERY','核查记录不接受时序版本条件',400)
                            review=workspace.agri_research.review(parts[2])
                            if len(parts)==3:return self.json_response(review)
                            if parts[3]=='geojson':return self.json_response(review['geojson'])
                    if parts and parts[0]=="agriculture-atlas":
                        if set(query)-{"product_sha256"} or "product_sha256" in query and (len(parts)<2 or len(query["product_sha256"])!=1):
                            raise APIError("INVALID_ATLAS_QUERY","成果只接受单个product_sha256版本条件")
                        if "product_sha256" in query:workspace.agriculture_atlas.require_version(parts[1],query["product_sha256"][0])
                    if parts == ["agriculture-atlas"]:
                        return self.json_response(workspace.agriculture_atlas.list())
                    if len(parts)==2 and parts[0]=="agriculture-atlas":
                        return self.json_response(workspace.agriculture_atlas.get(parts[1]))
                    if len(parts)==3 and parts[0]=="agriculture-atlas" and parts[2]=="bundle.zip":
                        return self.artifact_response(workspace.agriculture_atlas.bundle(parts[1]),parts[1]+'.zip')
                    if len(parts)==4 and parts[0]=="agriculture-atlas" and parts[2]=="files":
                        return self.artifact_response(workspace.agriculture_atlas.artifact(parts[1],parts[3]))
                    if parts == ["scene-workflows"]:
                        return self.json_response(workspace.scene_workflows.catalog())
                    if parts == ["ai-tools"]:
                        return self.json_response(ai_data_tools(workspace.model_gateway.status()))
                    if parts == ["assistant", "model"]:
                        return self.json_response({**workspace.model_gateway.status(), 'mode':'local_test', 'execution':'validated_registered_tools'})
                    if parts == ["observations"]:
                        return self.json_response(workspace.observations.list(query))
                    if len(parts) == 2 and parts[0] == "observations":
                        return self.json_response(workspace.observations.get(parts[1]))
                    if parts == ["algorithm-contracts"]:
                        return self.json_response(algorithm_catalog())
                    if parts == ["integrations", "tianditu"]:
                        return self.json_response(workspace.tianditu.status())
                    if parts == ["tianditu", "reverse"]:
                        return self.json_response(workspace.tianditu.reverse(query.get("lon", [None])[0], query.get("lat", [None])[0]))
                    if parts == ["tianditu", "administrative"]:
                        return self.json_response(workspace.tianditu.administrative(query.get("keyword", [""])[0]))
                    if len(parts) == 6 and parts[:2] == ["tianditu", "tiles"]:
                        if query:
                            raise APIError("INVALID_TILE", "瓦片端点不接受查询参数")
                        return self.tile_response(workspace.tianditu.tile(*parts[2:]), "tianditu_"+parts[2])
                    if len(parts)==3 and parts[0]=="analysis" and parts[2]=="result.geojson":
                        return self.json_response(workspace.get("analysis",parts[1])["geojson"],content_type="application/geo+json")
                    if parts == ["map", "inspect"]:
                        if set(query)-{"kind","task_id","lon","lat","product_sha256"} or any(len(v)!=1 for v in query.values()):raise APIError("INVALID_INSPECTION_QUERY","像元核查参数不支持或重复")
                        return self.json_response(workspace.inspection_services.inspect(query.get("kind", [None])[0], query.get("task_id", [None])[0], query.get("lon", [None])[0], query.get("lat", [None])[0],query.get("product_sha256",[None])[0]))
                    if parts == ["map", "search"]:
                        if query.get("remote", ["1"])[0] not in ("0", "1"):
                            raise APIError("INVALID_SEARCH", "remote须为0或1")
                        return self.json_response(workspace.search_services.search(query.get("q", [""])[0], query.get("limit", [12])[0], remote=query.get("remote", ["1"])[0] == "1"))
                    if parts == ["applications"]:
                        return self.json_response(workspace.applications.catalog())
                    if parts == ["health"]:
                        map_config = workspace.map_services.config()
                        agriculture = workspace.agriculture.capabilities()
                        processing = raster_processing_capabilities()
                        model = workspace.model_gateway.status()
                        return self.json_response({"status": "ok", "version": "0.18.0", "planner": "codex_cli_with_validated_tools" if model['enabled'] and model['available'] else "bounded_rules_no_general_llm", "model":model, "mode": "local", "storage": "sqlite", "observation_library": "versioned_sqlite_assets", "external_engines": "professional_engines_not_connected", "production": production_capabilities(), "agriculture": agriculture, "agriculture_algorithms": len(agriculture["algorithms"]), "raster_processing": processing, "raster_processing_algorithms": len(processing["algorithms"]), "data_import": "available", "algorithms": len(ALGORITHMS), "algorithm_contracts": len(algorithm_catalog()["contracts"]), "public_adapters": "request_on_demand", "map_basemaps": sum(b.get("available", True) for b in map_config["basemaps"]), "map_layers": len(map_config["layers"]), "integrations": {"tianditu": workspace.tianditu.status()}})
                    if parts == ["executions", "capabilities"]:
                        return self.json_response(execution_capabilities())
                    if parts == ["business-scenarios"]:
                        return self.json_response(business_scenarios_catalog())
                    if len(parts)==4 and parts[0]=="results":
                        if parts[3]=="manifest":return self.json_response(workspace.result_bundles.manifest(parts[1],parts[2]))
                        if parts[3]=="bundle.zip":return self.artifact_response(workspace.result_bundles.artifact(parts[1],parts[2]),'GeoAI-'+parts[1]+'-'+parts[2]+'.zip')
                    if parts == ["executions"]:
                        return self.json_response(workspace.executions.list())
                    if len(parts) == 2 and parts[0] == "executions":
                        return self.json_response(workspace.executions.get(parts[1]))
                    if parts == ["data-sources"]:
                        return self.json_response(workspace.data_catalog.sources())
                    if len(parts) == 2 and parts[0] == "data-sources":
                        return self.json_response(workspace.data_catalog.get_source(unquote(parts[1])))
                    if parts == ["data-searches"]:
                        return self.json_response(workspace.data_catalog.list_searches())
                    if len(parts) == 2 and parts[0] == "data-searches":
                        return self.json_response(workspace.data_catalog.get_search(parts[1]))
                    if parts == ["data-plans"]:
                        return self.json_response(workspace.data_catalog.list_plans())
                    if len(parts) == 2 and parts[0] == "data-plans":
                        return self.json_response(workspace.data_catalog.get_plan(parts[1]))
                    if parts == ["assistant", "capabilities"]:
                        return self.json_response(assistant_capabilities())
                    if parts == ["assistant", "questions"]:
                        return self.json_response(workspace.assistant.list())
                    if len(parts) == 3 and parts[:2] == ["assistant", "questions"]:
                        return self.json_response(workspace.assistant.get(parts[2]))
                    if parts == ["assistant", "shortcuts"]:
                        return self.json_response(workspace.assistant.shortcuts())
                    if parts == ["landcover", "capabilities"]:
                        return self.json_response(landcover_capabilities(workspace.landcover.source_dir))
                    if parts == ["cropland", "capabilities"]:
                        return self.json_response(cropland_capabilities())
                    if parts == ["agriculture", "capabilities"]:
                        return self.json_response(workspace.agriculture.capabilities())
                    if parts == ["raster-processing", "capabilities"]:
                        return self.json_response(raster_processing_capabilities())
                    if parts == ["raster-processing", "tasks"]:
                        return self.json_response(workspace.raster_processing.list())
                    if len(parts) == 3 and parts[:2] == ["raster-processing", "tasks"]:
                        return self.json_response(workspace.raster_processing.get(parts[2]))
                    if len(parts) == 4 and parts[:2] == ["raster-processing", "tasks"]:
                        return self.artifact_response(workspace.raster_processing.artifact(parts[2], parts[3]))
                    if parts == ["agriculture", "tasks"]:
                        return self.json_response(workspace.agriculture.list())
                    if len(parts) == 3 and parts[:2] == ["agriculture", "tasks"]:
                        return self.json_response(workspace.agriculture.get(parts[2]))
                    if len(parts) == 4 and parts[:2] == ["agriculture", "tasks"]:
                        return self.artifact_response(workspace.agriculture.artifact(parts[2], parts[3]))
                    if parts == ["cropland", "tasks"]:
                        return self.json_response(workspace.cropland.list())
                    if len(parts) == 3 and parts[:2] == ["cropland", "tasks"]:
                        return self.json_response(workspace.cropland.get(parts[2]))
                    if len(parts) == 4 and parts[:2] == ["cropland", "tasks"]:
                        return self.artifact_response(workspace.cropland.artifact(parts[2], parts[3]))
                    if parts == ["landcover", "tasks"]:
                        return self.json_response(workspace.landcover.list())
                    if len(parts) == 3 and parts[:2] == ["landcover", "tasks"]:
                        return self.json_response(workspace.landcover.get(parts[2]))
                    if len(parts) == 4 and parts[:2] == ["landcover", "tasks"]:
                        return self.artifact_response(workspace.landcover.artifact(parts[2], parts[3]))
                    if parts == ["production", "capabilities"]:
                        return self.json_response(production_capabilities())
                    if parts == ["production", "tasks"]:
                        return self.json_response(workspace.production.list())
                    if len(parts) == 3 and parts[:2] == ["production", "tasks"]:
                        return self.json_response(workspace.production.get(parts[2]))
                    if len(parts) == 4 and parts[:2] == ["production", "tasks"]:
                        return self.artifact_response(workspace.production.artifact(parts[2], parts[3]))
                    if parts == ["bootstrap"]:
                        return self.json_response(workspace.bootstrap())
                    if parts == ["map", "config"]:
                        return self.json_response(workspace.map_services.config())
                    if len(parts) == 3 and parts[:2] == ["map", "layers"]:
                        return self.json_response(workspace.map_services.layer(parts[2]))
                    if len(parts) == 5 and parts[0] == "tiles":
                        if query:
                            raise APIError("INVALID_TILE", "瓦片端点不接受 URL 或查询参数")
                        return self.tile_response(workspace.map_services.tile(*parts[1:]), parts[1])
                    if parts == ["map"]:
                        return self.json_response(workspace.map_data(query.get("scenario", [None])[0], query.get("region", ["仙桃市"])[0]))
                    if parts == ["datasets"]:
                        return self.json_response(workspace.datasets())
                    if len(parts) == 2 and parts[0] == "datasets":
                        return self.json_response(workspace.get_dataset(parts[1]))
                    if parts == ["algorithms"]:
                        return self.json_response(ALGORITHMS)
                    if parts == ["public", "weather"]:
                        return self.json_response(workspace.public_sources.weather(query))
                    if parts == ["public", "satellite"]:
                        return self.json_response(workspace.public_sources.satellite(query))
                    mapping = {"plans": "plan", "runs": "run", "apps": "app", "analysis": "analysis"}
                    if len(parts) in (1, 2) and parts[0] in mapping:
                        obj = workspace.list(mapping[parts[0]]) if len(parts) == 1 else workspace.get(mapping[parts[0]], parts[1])
                        return self.json_response(obj)
                elif method == "POST":
                    if parts == ["crop-reference", "analyses"]:return self.json_response(workspace.crop_reference.analyze(body)[0],201)
                    if parts==['agri-research','samples']:return self.json_response(workspace.agri_research.import_samples(body),201)
                    if parts==['agri-research','train']:return self.json_response(workspace.agri_research.train(body),201)
                    if parts==['agri-research','reviews']:return self.json_response(workspace.agri_research.create_review(body),201)
                    if len(parts)==4 and parts[:2]==['agri-research','reviews'] and parts[3]=='status':return self.json_response(workspace.agri_research.update_review(parts[2],body))
                    if len(parts)==3 and parts[0]=="scene-workflows" and parts[2]=="scope":
                        return self.json_response(workspace.scene_workflows.scope(parts[1],body))
                    if len(parts)==3 and parts[0]=="scene-workflows" and parts[2]=="run":
                        return self.json_response(workspace.scene_workflows.run(parts[1],body),201)
                    if parts == ["observations", "ingest"]:
                        return self.json_response(workspace.observations.ingest(body), 201)
                    if parts == ["algorithm-matches"]:
                        if set(body)-{"observation_id", "index_id"}:
                            raise APIError("INVALID_ALGORITHM_MATCH", "匹配仅接受观测ID和指数ID")
                        return self.json_response(match_observation(workspace.observations.get(body.get("observation_id")), body.get("index_id")))
                    if parts == ["integrations", "tianditu"]:
                        if body == {"action": "clear"}:
                            return self.json_response(workspace.tianditu.clear())
                        return self.json_response(workspace.tianditu.configure(body))
                    if parts == ["integrations", "tianditu", "check"]:
                        if body:
                            raise APIError("INVALID_FIELD", "连通性检查不接受额外参数")
                        return self.json_response(workspace.tianditu.test())
                    if parts == ["application-runs"]:
                        return self.json_response(workspace.applications.run(body), 201)
                    if parts == ["executions"]:
                        return self.json_response(workspace.executions.create(body), 201)
                    if len(parts) == 3 and parts[0] == "executions":
                        if parts[2] == "cancel": return self.json_response(workspace.executions.cancel(parts[1]))
                        if parts[2] == "retry": return self.json_response(workspace.executions.retry(parts[1]), 201)
                        if parts[2] == "continue": return self.json_response(workspace.executions.continue_job(parts[1], body), 201)
                    if parts == ["data-sources", "search"]:
                        return self.json_response(workspace.data_catalog.search(body), 201)
                    if parts == ["data-sources", "drafts"]:
                        return self.json_response(workspace.data_catalog.create_draft(body), 201)
                    if parts == ["data-plans"]:
                        return self.json_response(workspace.data_catalog.create_plan(body), 201)
                    if parts == ["assistant", "questions"]:
                        return self.json_response(workspace.assistant.create(body), 201)
                    if len(parts) == 4 and parts[:2] == ["assistant", "questions"]:
                        if parts[3] == "cancel": return self.json_response(workspace.assistant.cancel(parts[2]))
                        if parts[3] == "retry": return self.json_response(workspace.assistant.retry(parts[2]), 201)
                    if parts == ["assistant", "shortcuts"]:
                        return self.json_response(workspace.assistant.save_shortcut(body), 201)
                    if parts == ["landcover", "tasks"]:
                        return self.json_response(workspace.landcover.create(body), 201)
                    if parts == ["cropland", "tasks"]:
                        return self.json_response(workspace.cropland.create(body), 201)
                    if parts == ["agriculture", "tasks"]:
                        return self.json_response(workspace.agriculture.create(body), 201)
                    if parts == ["raster-processing", "tasks"]:
                        return self.json_response(workspace.raster_processing.create(body), 201)
                    if len(parts) == 4 and parts[:2] == ["raster-processing", "tasks"] and parts[3] in ("cancel", "retry"):
                        if body:raise APIError("INVALID_PROCESSING_REQUEST", "取消或重试不接受额外参数")
                        if parts[3] == "cancel":return self.json_response(workspace.raster_processing.cancel(parts[2]))
                        return self.json_response(workspace.raster_processing.retry(parts[2]), 201)
                    if len(parts) == 4 and parts[:2] == ["agriculture", "tasks"] and parts[3] in ("cancel", "retry"):
                        if body:raise APIError("INVALID_AGRICULTURE_REQUEST", "取消或重试不接受额外参数")
                        if parts[3] == "cancel":return self.json_response(workspace.agriculture.cancel(parts[2]))
                        return self.json_response(workspace.agriculture.retry(parts[2]), 201)
                    if len(parts) == 4 and parts[:2] == ["cropland", "tasks"]:
                        if parts[3] == "cancel": return self.json_response(workspace.cropland.cancel(parts[2]))
                        if parts[3] == "retry": return self.json_response(workspace.cropland.retry(parts[2]), 201)
                    if len(parts) == 4 and parts[:2] == ["landcover", "tasks"]:
                        if parts[3] == "cancel": return self.json_response(workspace.landcover.cancel(parts[2]))
                        if parts[3] == "retry": return self.json_response(workspace.landcover.retry(parts[2]), 201)
                    if parts == ["production", "tasks"]:
                        if body.get('mode')!='sentinel_cog' or body.get('sampling')!='native':raise GeoError('SAMPLE_MODE_REMOVED','人工样本与代表网格入口已停用；仅接受真实原生影像处理',410)
                        if 'bbox' not in body:raise GeoError('AOI_REQUIRED','请明确选择实际分析范围，已取消固定样区默认值',422)
                        from backend.analysis.production_services import make_plan as production_plan
                        prepared=workspace.production.resolve_input(body)
                        if production_plan(prepared).get('periods_defaulted'):raise GeoError('PERIOD_REQUIRED','请明确观测日期，已取消历史样例时段默认值',422)
                        return self.json_response(workspace.production.create(body), 201)
                    if len(parts) == 4 and parts[:2] == ["production", "tasks"]:
                        if parts[3] == "cancel":
                            return self.json_response(workspace.production.cancel(parts[2]))
                        if parts[3] == "retry":
                            archived=workspace.production.get(parts[2])
                            if archived.get('plan',{}).get('mode')!='sentinel_cog' or archived.get('plan',{}).get('sampling')!='native':raise GeoError('SAMPLE_MODE_REMOVED','旧样本与代表网格仅供历史查阅，请重新选择范围进行原生分析',410)
                            return self.json_response(workspace.production.retry(parts[2]), 201)
                    if parts == ["datasets"]:
                        result = workspace.create_dataset(body)
                        return self.json_response({k: v for k, v in result.items() if k != "geojson"}, 201)
                    if parts == ["analysis"]:
                        return self.json_response(workspace.create_analysis(body), 201)
                    if parts == ["plans"]:
                        return self.json_response(workspace.create_plan(body), 201)
                    if parts == ["runs"]:
                        return self.json_response(workspace.create_run(body), 201)
                    if parts == ["apps"]:
                        return self.json_response(workspace.create_app(body), 201)
                    if len(parts) == 3 and parts[0] == "runs":
                        if parts[2] == "cancel":
                            return self.json_response(workspace.cancel(parts[1]))
                        if parts[2] == "retry":
                            return self.json_response(workspace.retry(parts[1]), 201)
                    if len(parts) == 3 and parts[0] == "apps":
                        if parts[2] == "publish":
                            return self.json_response(workspace.publish_app(parts[1], body))
                        if parts[2] == "clone":
                            return self.json_response(workspace.clone_app(parts[1]), 201)
                    if len(parts) == 2 and parts[0] == "integrations":
                        return self.json_response(workspace.configure_integration(parts[1], body))
                elif method == "PATCH" and len(parts) == 2 and parts[0] == "apps":
                    return self.json_response(workspace.update_app(parts[1], body))
                raise APIError("NOT_FOUND", "未找到 API 端点", 404)
            except (APIError, GeoError) as exc:
                return self.error(exc)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                return self.error(APIError("INTERNAL_ERROR", "服务暂时无法完成请求，请重试", 500))

        def translate_path(self, path):
            candidate = Path(super().translate_path(path)).resolve()
            try:
                candidate.relative_to(static_root)
                return str(candidate)
            except ValueError:
                return str(static_root / "__outside_root_denied__")

        def static_allowed(self):
            decoded = unquote(urlsplit(self.path).path)
            return not any(part.startswith(".") for part in decoded.split("/") if part) and ".." not in decoded.split("/")

        def do_GET(self):
            if urlsplit(self.path).path.startswith("/api/"):
                return self.api("GET")
            if not self.static_allowed():
                return self.send_error(404)
            # Only dist is exposed; directory indexes are not served.
            return super().do_GET()

        def do_HEAD(self):
            if urlsplit(self.path).path.startswith("/api/"):
                return self.api("GET")
            if not self.static_allowed():
                return self.send_error(404)
            return super().do_HEAD()

        def do_POST(self):
            return self.api("POST")

        def do_PATCH(self):
            return self.api("PATCH")

        def list_directory(self, path):
            self.send_error(404)
            return None

    return Handler


def main():
    parser = argparse.ArgumentParser(description="GeoAI local service (loopback only)")
    parser.add_argument("--port", type=int, default=8016)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "workspace.sqlite3")
    args = parser.parse_args()
    workspace = Workspace(args.db)
    server = LocalServer(("127.0.0.1", args.port), make_handler(workspace))
    print("GeoAI local workspace: http://127.0.0.1:%s" % args.port, flush=True)
    print("SQLite: %s | registered spatial tools | model bridge: %s" % (args.db, 'Codex local test' if workspace.model_gateway.enabled else 'disabled'), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        workspace.close()
        server.server_close()


if __name__ == "__main__":
    main()
