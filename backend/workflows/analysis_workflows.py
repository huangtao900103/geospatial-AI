"""Bounded, checkpointed analysis chains over registered local services.

The first template is deliberately small: a pinned single native spectral
observation (or an immutable successful production result), followed by Otsu.
No model-generated code, URLs, paths, expressions or arbitrary DAGs are accepted.
Construction is passive; call resume_pending() after all workspace services exist.
"""
import copy
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from backend.core.geo_services import GeoError
from backend.analysis.landcover_services import now
from backend.analysis.production_services import make_plan
from backend.analysis.raster_processing_services import files, source_fingerprint

VERSION = 'analysis-workflows-1.0'
KIND = 'analysis_workflow'
TERMINAL = {'succeeded', 'failed', 'cancelled'}
ACTIVE = {'queued', 'running'}
TOOLS = frozenset({'production_result', 'sentinel_native', 'raster_processing'})
INDICES = frozenset({'ndvi', 'ndwi', 'ndmi'})
LIMITATIONS = [
    '自动分区是光谱阈值条件面积，不认定农田、水体、作物或灾损。',
    '仅使用明确成功成果或已入库固定版本观测；不自动换景、换年或跨传感器回退。',
    '未知像元保留未知，有效覆盖不等于分类精度；本机仅支持有界原生影像分析。',
]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def stable_plan(plan):
    # make_plan assigns a fresh display id/time even for identical science inputs.
    return {key: copy.deepcopy(value) for key, value in plan.items() if key not in ('id', 'created_at')}


def _id(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise GeoError('INVALID_WORKFLOW_INPUT', label + '必须为已登记的标识符', 422)
    return value


class AnalysisWorkflowServices:
    def __init__(self, store):
        self.store = store
        self.stopping = False
        self.threads = []
        self.running_ids = set()
        self.assignment_lock = threading.RLock()

    def capabilities(self):
        return {'version': VERSION, 'templates': [{'id': 'spectral_partition',
                'name': '光谱分析与自动分区', 'indices': sorted(INDICES),
                'tools': sorted(TOOLS), 'max_nodes': 2}],
                'limitations': copy.deepcopy(LIMITATIONS), 'arbitrary_code': False}

    def list(self):
        return self.store.list(KIND)

    def get(self, ident):
        return self.store.get(KIND, ident)

    def verified_result(self, ident):
        """Recheck archived evidence before turning a saved chain into an answer."""
        job=self.get(ident)
        if job['status']!='succeeded':
            raise GeoError('WORKFLOW_RESULT_NOT_READY','分析流程尚未形成完整成功结果',409)
        for node in job['nodes']:
            if node.get('status')!='succeeded' or not node.get('binding') or not node.get('evidence'):
                raise GeoError('WORKFLOW_RESULT_INVALID','分析流程缺少已完成节点及校验证据',409)
            self._validate(node,node['binding'],node['evidence'])
        return job

    def prepare(self, body):
        if not isinstance(body, dict) or set(body) - {'template_id', 'context', 'parameters', 'request_id'}:
            raise GeoError('INVALID_WORKFLOW_INPUT', '分析流程包含不支持字段', 400)
        if body.get('template_id') != 'spectral_partition':
            raise GeoError('WORKFLOW_TEMPLATE_UNSUPPORTED', '请选择已实现的光谱分析与自动分区流程', 422)
        context = copy.deepcopy(body.get('context', {}))
        parameters = copy.deepcopy(body.get('parameters', {}))
        if not isinstance(context, dict) or set(context) - {'source_type', 'source_task_id', 'observation_id', 'bbox', 'period', 'source_id'}:
            raise GeoError('INVALID_WORKFLOW_CONTEXT', '请提供已有成果，或明确观测、地图选区与时期', 400)
        if not isinstance(parameters, dict) or set(parameters) - {'index_id', 'direction', 'min_patch_pixels'}:
            raise GeoError('INVALID_WORKFLOW_PARAMETERS', '分析流程包含不支持参数', 400)
        request_id = body.get('request_id')
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 100):
            raise GeoError('INVALID_REQUEST_ID', '请求标识须为1至100字的字符串', 400)
        direction = parameters.get('direction', 'above')
        minimum = parameters.get('min_patch_pixels', 1)
        if direction not in ('above', 'below') or isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= 1000:
            raise GeoError('INVALID_WORKFLOW_PARAMETERS', '方向须为above/below，最小斑块须为1至1000整数', 422)
        if context.get('source_type', 'production') != 'production':
            raise GeoError('WORKFLOW_SOURCE_UNSUPPORTED', '本流程仅接收原生production成果或固定观测', 422)
        if context.get('source_task_id'):
            if set(context) - {'source_task_id', 'source_type', 'bbox'}:
                raise GeoError('WORKFLOW_INPUT_CONFLICT', '已有成果继承其范围、日期与来源，不能叠加另一观测或时期', 422)
            ident = _id(context['source_task_id'], '源成果')
            partition = self.store.raster_processing.prepare({'algorithm_id': 'otsu_segmentation',
                         'source_task_id': ident, 'direction': direction, 'min_patch_pixels': minimum})
            if 'bbox' in context and context['bbox'] != partition['source']['bbox']:
                raise GeoError('WORKFLOW_SCOPE_CONFLICT', '当前地图范围与固定源成果不同，请选择原结果范围或重新生产影像', 422)
            index = partition['source']['index_id']
            if parameters.get('index_id', index) != index:
                raise GeoError('WORKFLOW_INDEX_CONFLICT', '请求指数与已有成果不同，不能替换源结果', 422)
            first = {'id': 'spectral', 'name': '复核已有原生光谱成果', 'tool_id': 'production_result',
                     'depends_on': [], 'request': {'source_task_id': ident},
                     'frozen_source': copy.deepcopy(partition['source'])}
        else:
            if not all(key in context for key in ('observation_id', 'bbox', 'period')):
                raise GeoError('WORKFLOW_INPUT_REQUIRED', '请明确选择已入库观测、地图范围和含年份的观测时段，或选择已有光谱成果', 422)
            _id(context['observation_id'], '观测')
            period = context['period']
            if not isinstance(period, dict) or set(period) != {'start', 'end'}:
                raise GeoError('INVALID_WORKFLOW_PERIOD', '观测时段须为start/end日期对象', 422)
            try:
                unused_before = (date.fromisoformat(period['start']) - timedelta(days=1)).isoformat()
            except (ValueError, TypeError, OverflowError):
                raise GeoError('INVALID_WORKFLOW_PERIOD', '观测时期须为合法YYYY-MM-DD日期', 422)
            index = parameters.get('index_id', 'ndvi')
            if index not in INDICES:
                raise GeoError('WORKFLOW_INDEX_UNSUPPORTED', '首版支持NDVI、NDWI和NDMI', 422)
            request = {'requirement': '对明确观测进行单期' + index.upper() + '原生光谱分析',
                       'mode': 'sentinel_cog', 'sampling': 'native', 'goal': 'ndvi',
                       'observation_id': context['observation_id'], 'bbox': context['bbox'],
                       # Legacy production schema requires two ordered windows;
                       # goal=ndvi reads only after. This inert predecessor is
                       # never a requested observation or a second computation.
                       'periods': {'before': [unused_before, unused_before],
                                   'after': [period['start'], period['end']]}, 'index_id': index,
                       'source_id': context.get('source_id', 'auto')}
            frozen = stable_plan(make_plan(self.store.production.resolve_input(request)))
            if frozen.get('blocking_gaps') or frozen.get('aoi_defaulted') or frozen.get('periods_defaulted'):
                raise GeoError('WORKFLOW_INPUT_REQUIRED', '原生光谱输入不完整，不能使用默认样区或默认时期', 422)
            if not frozen.get('observation_ref') or not frozen.get('scene_id'):
                raise GeoError('WORKFLOW_PIN_REQUIRED', '本流程必须锁定已入库真实观测，不自动选择影像', 422)
            first = {'id': 'spectral', 'name': '生产固定观测原生光谱成果', 'tool_id': 'sentinel_native',
                     'depends_on': [], 'request': request, 'frozen_plan': frozen}
        if index not in INDICES:
            raise GeoError('WORKFLOW_INDEX_UNSUPPORTED', '首版支持NDVI、NDWI和NDMI', 422)
        parameters.update(index_id=index, direction=direction, min_patch_pixels=minimum)
        nodes = [first, {'id': 'partition', 'name': '自动阈值分区并归档地图', 'tool_id': 'raster_processing',
                        'depends_on': ['spectral'], 'request': {'algorithm_id': 'otsu_segmentation',
                        'direction': direction, 'min_patch_pixels': minimum}}]
        frozen_request = {'template_id': 'spectral_partition', 'context': context, 'parameters': parameters}
        plan = {'version': VERSION, 'template_id': 'spectral_partition', 'status': 'ready', 'executable': True,
                'frozen_request': frozen_request, 'nodes': nodes, 'final_node_id': 'partition',
                'limitations': copy.deepcopy(LIMITATIONS)}
        plan['plan_sha256'] = digest(plan)
        return plan

    def create(self, body):
        # Request idempotency is checked before re-reading an upstream source.
        request_hash = digest({key: value for key, value in body.items() if key != 'request_id'}) if isinstance(body, dict) else None
        with self.assignment_lock, self.store.lock:
            if self.stopping:
                raise GeoError('SERVICE_STOPPING', '分析流程服务正在关闭', 503)
            request_id = body.get('request_id') if isinstance(body, dict) else None
            if request_id:
                existing = next((job for job in self.list() if job.get('request_id') == request_id), None)
                if existing:
                    if existing['request_hash'] != request_hash:
                        raise GeoError('IDEMPOTENCY_CONFLICT', '相同请求标识不能绑定不同分析输入', 409)
                    return existing
            plan = self.prepare(body)
            return self._create(plan, request_id, request_hash)

    def _create(self, plan, request_id=None, request_hash=None, parent=None, prefix=None):
        if sum(job['status'] in ACTIVE for job in self.list()) >= 8:
            raise GeoError('WORKFLOW_BUSY', '本机已有多个分析流程，请稍后提交', 429)
        nodes = []
        for item in plan['nodes']:
            node = copy.deepcopy(item)
            node.update(status='pending', binding=None, evidence=None, output_ref=None)
            previous = next((p for p in prefix or [] if p['id'] == node['id']), None)
            if previous:
                if previous['status'] == 'succeeded':
                    node.update({key: copy.deepcopy(previous.get(key)) for key in ('binding', 'evidence', 'output_ref')})
                    if previous.get('resolved_request'):
                        node['resolved_request'] = copy.deepcopy(previous['resolved_request'])
                    node['status'] = 'succeeded'
                elif previous.get('binding'):
                    node['retry_binding'] = copy.deepcopy(previous['binding'])
            nodes.append(node)
        job = {'id': 'workflow_' + uuid.uuid4().hex[:14], 'version': VERSION, 'template_id': plan['template_id'],
               'status': 'queued', 'progress': 0, 'message': '已冻结分析输入，等待执行',
               'plan_snapshot': copy.deepcopy(plan), 'plan_sha256': plan['plan_sha256'], 'nodes': nodes,
               'request_id': request_id, 'request_hash': request_hash, 'parent_workflow_id': parent,
               'result': None, 'result_ref': None, 'binding': None, 'summary': None, 'evidence': [],
               'logs': [{'time': now(), 'message': '仅调用登记工具；原始输入、依赖与版本已保存'}],
               'created_at': now(), 'updated_at': now(), 'error': None}
        self.store.save(KIND, job)
        self.launch(job['id'])
        return job

    def launch(self, ident):
        with self.store.lock:
            if ident in self.running_ids or self.stopping:
                return
            self.running_ids.add(ident)
            thread = threading.Thread(target=self.process, args=(ident,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def resume_pending(self):
        for job in self.list():
            if job['status'] in ACTIVE:
                if any(node['status'] == 'dispatching' and not node.get('binding') for node in job['nodes']):
                    self._fail(job['id'], GeoError('WORKFLOW_DISPATCH_INTERRUPTED', '派发时服务中断且未记录子任务绑定；请显式重试，不自动重复计算', 409))
                else:
                    self.launch(job['id'])

    def _change(self, ident, fn):
        with self.store.lock:
            job = self.get(ident)
            if job['status'] in TERMINAL:
                return False
            fn(job)
            job['updated_at'] = now()
            self.store.save(KIND, job)
            return True

    def _check(self, ident):
        if self.stopping:
            raise GeoError('INTERRUPTED', '服务正在停止，分析流程保留检查点', 409)
        if self.get(ident)['status'] not in ACTIVE:
            raise GeoError('WORKFLOW_STOPPED', '分析流程已结束', 409)

    def _node(self, ident, node_id, **changes):
        return self._change(ident, lambda job: next(n for n in job['nodes'] if n['id'] == node_id).update(changes))

    def _source_plan(self, node):
        if node['tool_id'] == 'production_result':
            plan = self.store.raster_processing.prepare({'algorithm_id': 'otsu_segmentation',
                    'source_task_id': node['request']['source_task_id']})
            if plan['source'] != node['frozen_source']:
                raise GeoError('WORKFLOW_SOURCE_CHANGED', '固定源成果的内容、资产或观测已变化，不能静默替换', 409)
            return plan
        plan = stable_plan(make_plan(self.store.production.resolve_input(node['request'])))
        if digest(plan) != digest(node['frozen_plan']):
            raise GeoError('WORKFLOW_OBSERVATION_CHANGED', '固定观测版本或算法计划已变化，请重新选择后创建流程', 409)
        return plan

    def _manifest(self, kind, task):
        service = getattr(self.store, kind)
        names = files('otsu_segmentation') if kind == 'raster_processing' else ('analysis.tif', 'overlay.png', 'result.json')
        root = Path(getattr(service, 'output_dir', Path(self.store.db_path).parent / kind))
        manifest = []
        for name in names:
            path = Path(service.artifact(task['id'], name))
            if path.absolute() != (root / task['id'] / name).absolute() or not path.resolve().is_relative_to(root.resolve()):
                raise GeoError('WORKFLOW_ARTIFACT_INVALID', '产物不属于固定任务归档目录', 409)
            current = path
            while current.absolute() != root.absolute():
                if current.is_symlink():
                    raise GeoError('WORKFLOW_ARTIFACT_INVALID', '归档产物不能通过文件链接读取其他位置', 409)
                current = current.parent
            hasher = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    hasher.update(chunk)
            if name == 'result.json' and digest(json.loads(path.read_text('utf-8'))) != digest(task['result']):
                raise GeoError('WORKFLOW_RESULT_MISMATCH', '持久化结果与归档结果文件不一致', 409)
            manifest.append({'name': name, 'sha256': hasher.hexdigest(), 'bytes': path.stat().st_size})
        return manifest

    def _validate(self, node, binding, expected=None):
        kind = binding['type']
        task = getattr(self.store, kind).get(binding['task_id'])
        if task['status'] != 'succeeded' or not isinstance(task.get('result'), dict):
            raise GeoError('WORKFLOW_CHILD_NOT_SUCCEEDED', '前置任务未成功，不执行后续算法', 502)
        if kind == 'production':
            if node['tool_id'] == 'production_result':
                self._source_plan(node)
            else:
                self._source_plan(node)
                key = self.store.executions.input_key('sentinel_native', self.store.production.resolve_input(node['request']))
                if not self.store.executions.compatible_child('sentinel_native', task, node['request'], key):
                    raise GeoError('WORKFLOW_RESULT_INVALID', '原生光谱任务的输入、版本或产物校验失败', 409)
            # Reuse the scientific source contract: single native real pixels only.
            source = self.store.raster_processing.prepare({'algorithm_id': 'otsu_segmentation', 'source_task_id': task['id']})['source']
        else:
            request = node['resolved_request']
            key = self.store.executions.input_key('raster_processing', request)
            if not self.store.executions.compatible_child('raster_processing', task, request, key):
                raise GeoError('WORKFLOW_RESULT_INVALID', '分区算法、源版本或归档产物校验失败', 409)
            self.store.raster_processing.verify_source(task['plan'], lambda: None)
            source = task['plan']['source']
        result = task['result']
        evidence = {'node_id': node['id'], 'tool_id': node['tool_id'], 'task_id': task['id'],
                    'type': kind, 'record_sha256': source_fingerprint(task), 'source': copy.deepcopy(source),
                    'artifacts': self._manifest(kind, task)}
        if expected is not None and evidence != expected:
            raise GeoError('WORKFLOW_CHECKPOINT_CHANGED', '已完成检查点的源、算法或产物发生变化，不能复用', 409)
        ref = {'type': kind, 'task_id': task['id'], 'url': '/api/' + ('raster-processing' if kind == 'raster_processing' else kind) + '/tasks/' + task['id'],
               'image_kind': 'otsu_segmentation' if kind == 'raster_processing' else source['index_id']}
        return task, evidence, ref

    def _bind(self, ident, node):
        kind = 'production' if node['id'] == 'spectral' else 'raster_processing'
        service = getattr(self.store, kind)
        with self.assignment_lock, self.store.lock:
            self._check(ident)
            current = next(n for n in self.get(ident)['nodes'] if n['id'] == node['id'])
            if current.get('binding'):
                return service.get(current['binding']['task_id']), current['binding']
            if node['tool_id'] == 'production_result':
                self._source_plan(node)
                task = service.get(node['request']['source_task_id'])
                binding = {'type': kind, 'task_id': task['id'], 'owned': False, 'reused': True}
            else:
                if kind == 'production':
                    self._source_plan(node)
                    request = node['request']
                    key = self.store.executions.input_key('sentinel_native', self.store.production.resolve_input(request))
                else:
                    parent = next(n for n in self.get(ident)['nodes'] if n['id'] == 'spectral')
                    if parent['status'] != 'succeeded':
                        raise GeoError('WORKFLOW_DEPENDENCY_FAILED', '前置光谱成果未通过校验', 409)
                    self._validate(parent, parent['binding'], parent['evidence'])
                    request = {**node['request'], 'source_task_id': parent['binding']['task_id']}
                    self.store.raster_processing.prepare(request)
                    self._node(ident, node['id'], resolved_request=copy.deepcopy(request))
                    node['resolved_request'] = request
                    key = self.store.executions.input_key('raster_processing', request)
                task = next((task for task in service.list() if self.store.executions.compatible_child(
                    'sentinel_native' if kind == 'production' else 'raster_processing', task, request, key)), None)
                if task is not None:
                    binding = {'type': kind, 'task_id': task['id'], 'owned': False, 'reused': True, 'input_key': key}
                else:
                    self._node(ident, node['id'], status='dispatching')
                    old = node.get('retry_binding')
                    if old and kind == 'raster_processing' and service.get(old['task_id'])['status'] in ('failed', 'cancelled'):
                        task = service.retry(old['task_id'])
                        if task['plan']['input_fingerprint'] != key:
                            raise GeoError('WORKFLOW_RETRY_CHANGED', '冻结重试的算法输入不一致', 409)
                    else:
                        task = service.create(copy.deepcopy(request))
                    binding = {'type': kind, 'task_id': task['id'], 'owned': True, 'reused': False, 'input_key': key}
            self._node(ident, node['id'], status='running', binding=copy.deepcopy(binding), started_at=now())
            return task, binding

    def process(self, ident):
        try:
            self._check(ident)
            job = self.get(ident)
            plan = copy.deepcopy(job['plan_snapshot'])
            checksum = plan.pop('plan_sha256', None)
            if checksum != digest(plan) or checksum != job['plan_sha256'] or plan.get('version') != VERSION:
                raise GeoError('WORKFLOW_PLAN_CHANGED', '冻结分析计划损坏或版本不兼容', 409)
            if [n['tool_id'] for n in plan['nodes']] not in (['production_result', 'raster_processing'], ['sentinel_native', 'raster_processing']):
                raise GeoError('WORKFLOW_TOOL_NOT_REGISTERED', '只允许固定光谱与分区工具链', 422)
            self._change(ident, lambda job: job.update(status='running', message='正在执行固定观测光谱分析与自动分区'))
            for position in range(2):
                self._check(ident)
                node = self.get(ident)['nodes'][position]
                if node['status'] == 'succeeded':
                    self._validate(node, node['binding'], node['evidence'])
                    continue
                task, binding = self._bind(ident, node)
                while task['status'] in ('queued', 'running'):
                    self._check(ident)
                    self._change(ident, lambda job: job.update(progress=position * 50 + round(task.get('progress', 0) * .45), message=node['name']))
                    time.sleep(.05)
                    task = getattr(self.store, binding['type']).get(binding['task_id'])
                self._check(ident)
                node = self.get(ident)['nodes'][position]
                task, evidence, ref = self._validate(node, binding)
                self._node(ident, node['id'], status='succeeded', evidence=evidence, output_ref=ref, finished_at=now())
                self._change(ident, lambda job: job.update(progress=(position + 1) * 50))
            self._check(ident)
            job = self.get(ident)
            # A final validation also detects prefix changes while the suffix ran.
            for node in job['nodes']:
                task, _, _ = self._validate(node, node['binding'], node['evidence'])
            final = job['nodes'][-1]
            def finish(record):
                record.update(status='succeeded', progress=100, message='光谱分析与自动分区已完成，地图和来源已归档',
                              result=copy.deepcopy(task['result']), result_ref=final['output_ref'], binding=final['binding'],
                              summary=copy.deepcopy(task['result']['summary']), evidence=[n['evidence'] for n in job['nodes']], finished_at=now())
            self._change(ident, finish)
        except Exception as error:
            if not self.stopping:
                self._fail(ident, error)
        finally:
            with self.store.lock:
                self.running_ids.discard(ident)

    def _fail(self, ident, error):
        code = getattr(error, 'code', 'WORKFLOW_FAILED')
        message = getattr(error, 'message', '真实分析流程未完成，未生成替代结果；请查看条件后重试')
        def fail(job):
            job.update(status='failed', error={'code': code, 'message': message}, message=message, finished_at=now())
            for node in job['nodes']:
                if node['status'] not in ('succeeded', 'cancelled'):
                    node.update(status='failed' if node.get('binding') or node['status'] == 'dispatching' else 'blocked', finished_at=now())
            job['logs'].append({'time': now(), 'message': message})
        self._change(ident, fail)

    def is_child_referenced(self, kind, task_id, exclude_workflow_id=None):
        return any(job['id'] != exclude_workflow_id and job['status'] in ACTIVE and any(
            node.get('binding', {}).get('type') == kind and node.get('binding', {}).get('task_id') == task_id
            for node in job['nodes'] if node.get('binding')) for job in self.list())

    def _shared(self, kind, task_id, ident):
        if self.is_child_referenced(kind, task_id, ident):
            return True
        for record_kind, statuses in (('execution', {'queued', 'planning', 'running'}), ('assistant_question', {'processing'})):
            if any(job['status'] in statuses and (job.get('binding') or {}).get('type') == kind
                   and (job.get('binding') or {}).get('task_id') == task_id for job in self.store.list(record_kind)):
                return True
        return False

    def cancel(self, ident):
        with self.assignment_lock, self.store.lock:
            job = self.get(ident)
            if job['status'] not in ACTIVE:
                raise GeoError('INVALID_STATE', '只有待处理或运行中的流程可取消', 409)
            job.update(status='cancelled', message='已取消本流程；共享计算继续供其他请求使用', finished_at=now(), updated_at=now())
            for node in job['nodes']:
                if node['status'] != 'succeeded':
                    node['status'] = 'cancelled'
            self.store.save(KIND, job)
            for node in job['nodes']:
                binding = node.get('binding')
                if binding and binding.get('owned') and not self._shared(binding['type'], binding['task_id'], ident):
                    service = getattr(self.store, binding['type'])
                    if service.get(binding['task_id'])['status'] in ('queued', 'running'):
                        service.cancel(binding['task_id'])
            return job

    def retry(self, ident):
        with self.assignment_lock, self.store.lock:
            if self.stopping:
                raise GeoError('SERVICE_STOPPING', '分析流程服务正在关闭', 503)
            job = self.get(ident)
            if job['status'] not in ('failed', 'cancelled'):
                raise GeoError('INVALID_STATE', '只有失败或取消的分析流程可重试', 409)
            for node in job['nodes']:
                if node['status'] == 'succeeded':
                    self._validate(node, node['binding'], node['evidence'])
            return self._create(job['plan_snapshot'], parent=ident, prefix=job['nodes'])

    def close(self):
        self.stopping = True
        for thread in self.threads:
            thread.join(timeout=.5)
