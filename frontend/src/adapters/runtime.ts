/** Boundary for tested scientific/map modules. New navigation and shared state live in Vue/Pinia.
 * Dynamic records are confined here because older modules use polymorphic result envelopes.
 */
import { h, markRaw, render, type AppContext } from 'vue'
import TaskHistory from '../components/TaskHistory.vue'
import { MapWorkspace } from '../workspaces/map-workspace.js'
import { MapCopilot } from '../workspaces/map-copilot.js'
import { SourcesWorkspace } from '../workspaces/sources-workspace.js'
import { ProductionWorkspace } from '../workspaces/production-workspace.js'
import { LandcoverWorkspace } from '../workspaces/landcover-workspace.js'
import { api } from '../services/api'
import { useWorkspaceStore } from '../stores/workspace'
import type { Area, Dataset, MapTab, View } from '../types/api'
// Explicit interop boundary; it is not used by the typed store, API client or Vue forms.
type RecordData = { id: string; [key: string]: any }
type Engine = { [key: string]: any }
type Constructor = new (options: { [key: string]: any }) => Engine
export class WorkspaceRuntime {
 private map?: Engine; private copilot?: Engine; private sources?: Engine; private production?: Engine; private landcover?: Engine
 private historyHost?:HTMLElement; private currentHost?: HTMLElement; private contextState: { [key: string]: any } = {}; private disposed = false
 private store = useWorkspaceStore()
 constructor(private navigate: (view: View, tab?: MapTab) => void, private appContext?:AppContext) {}
 private request = <T = unknown>(path: string, options = {}) => api<T>(path, options)
 private common() { return { api: this.request, toast: this.store.toast, showDialog: this.store.showDialog } }
 private getSources(): Engine {
  if (!this.sources) this.sources = markRaw(new (SourcesWorkspace as unknown as Constructor)({ ...this.common(),
   onImport: () => { this.store.overlay = 'upload' }, onDatasetMap: (id: string) => this.showDataset(id),
   onDatasets: () => this.navigate('vector'), onProfessional: () => {this.store.setDataTab('catalog');this.navigate('data')},
   onIntegrationChange: (data: unknown) => this.map?.reloadMapSources(data),
   onBoundary: (record: RecordData) => { this.store.dialog = null; this.contextState.referenceBoundary = { ...record, selection_token: Date.now() }; this.navigate('map', 'map') },
   onObservationMap: (record: RecordData) => { this.store.dialog = null; this.footprint(record); this.navigate('map', 'map') },
   onPrepareAnalysis: async (payload: RecordData) => { this.store.dialog = null; this.footprint(payload.observation); this.navigate('map', 'applications'); await this.getCopilot().prepareObservation(payload); await this.syncMap() }
  }))
  return this.sources
 }
 private footprint(record: RecordData) { this.contextState.observationFootprint = { item: { id: record.item_id || record.id, bbox: record.bbox }, source_id: record.source_id, selection_token: Date.now() } }
 private getCopilot(): Engine {
  if (!this.copilot) this.copilot = markRaw(new (MapCopilot as unknown as Constructor)({ ...this.common(),
   onHistoryHost:(host:HTMLElement|null)=>this.mountHistory(host),
   currentAtlasProduct: () => [...(this.map?.overlays?.values() || [])].find(r => r.visible && r.meta?.atlas_product)?.meta.atlas_product || null,
   onAtlasMap: (product: RecordData) => this.showAtlas(product, {}, true),
   onSceneImport: (name: string, geojson: unknown) => this.store.importDataset(name, geojson),
   onSceneMap: async (result: RecordData, { viewOnly = false } = {}) => {
    this.store.analyses = [result, ...this.store.analyses.filter(a => a.id !== result.id)]; this.contextState.assistantSelection = null
    if (!this.map?.map) throw Error('地图尚未就绪，成果已保留')
    if (viewOnly) this.map.setAssistantOpen(false)
    await this.syncMap(); const key = (result.series_id && result.change_id ? 'agri_review:' : 'analysis:') + result.id
    if (result.series_id && this.map.overlays.has(key)) { const old = this.map.overlays.get(key); this.map.map.removeLayer(old.group); this.map.overlays.delete(key) }
    await this.map.toggleOwned(key, true); this.store.toast('成果已上图，点击要素查看属性')
   },
   onCatalogFootprint: (record: unknown) => this.map?.showCatalogFootprint(record),
   onSelectArea: (options: unknown) => this.map?.beginAreaSelection(options),
   onAreaChange: (area: Area | null) => { this.store.setArea(area); this.map?.showSelectedArea(area) },
   onExecutionCapabilities: (data: { limits?: { max_bbox_span_deg?: number } }) => { if (this.map && Number.isFinite(data.limits?.max_bbox_span_deg)) this.map.areaLimits = { maxSpan: data.limits?.max_bbox_span_deg } },
   onDataPlan: async (question: RecordData) => { const id = question.answer?.data_plan_ref?.id || question.data_plan_id; if (!id) throw Error('该答案尚未关联选源计划'); await this.getSources().showEvidence(id) },
   onTab: (tab: MapTab) => { this.store.setTab(tab); if (this.store.view === 'map') this.navigate('map', tab) },
   onChange: () => {}, onMap: (q: RecordData, options: { fit?: boolean; explicit?: boolean }) => this.showQuestion(q, options)
  }))
  return this.copilot
 }
 private mountHistory(host:HTMLElement|null){
  if(this.disposed)return
  if(!this.historyHost&&!host)return
  this.historyHost??=document.createElement('section')
  const vnode=h(TaskHistory,{active:!!host&&this.store.view==='map'&&this.store.tab==='history',
   onQuestion:(id:string)=>{this.navigate('map','chat');void this.getCopilot().selectQuestion(id,{fit:false}).catch((e:Error)=>this.store.toast(e.message))},
   onAtlas:(id:string,version:string)=>{this.navigate('map','applications');const engine=this.getCopilot();void engine.agricultureAtlas.open(id,{expectedSha:version}).then(()=>engine.render()).catch((e:Error)=>this.store.toast(e.message))},
   onScene:(result:RecordData)=>{this.store.analyses=[result,...this.store.analyses.filter(a=>a.id!==result.id)];void this.syncMap().then(()=>this.map?.toggleOwned((result.series_id?'agri_review:':'analysis:')+result.id,true)).catch((e:Error)=>this.store.toast(e.message))}
  });if(this.appContext)vnode.appContext=this.appContext
  render(vnode,this.historyHost);if(host)host.replaceChildren(this.historyHost)
 }
 private getMap(): Engine {
  if (!this.map) this.map = markRaw(new (MapWorkspace as unknown as Constructor)({ ...this.common(), customerMode: true,
   onAreaChange: (area: Area | null) => this.getCopilot().setArea(area),
   copilotMount: (host: HTMLElement) => this.getCopilot().mount(host, { tab: this.store.tab === 'map' ? 'applications' : this.store.tab }),
   copilotTab: (tab: MapTab) => this.navigate('map', tab),
   copilotAsk: (text: string, capability: string) => { this.navigate('map', 'chat'); return this.getCopilot().ask(text, capability) },
   copilotOpenQuestion: (id: string) => { this.navigate('map', 'chat'); return this.getCopilot().selectQuestion(id, { fit: false }) },
   copilotOpenAtlas: async (id: string) => { this.navigate('map', 'applications'); const atlas = this.getCopilot().agricultureAtlas; const result = await atlas.open(id); if (!result) throw Error(atlas.error || '成果暂未读取'); return result },
   navigate: (view: string) => this.navigate(({ workflow: 'production', lab: 'vector', connections: 'integrations', overview: 'map' } as Record<string, View>)[view] || view as View),
   action: (name: string, payload: HTMLElement) => this.action(name, payload),
   productionMount: (host: HTMLElement) => this.getProduction().mount(host, { compact: true }),
   prompt: (text: string) => { this.getProduction().setRequirement(text); this.navigate('production') }
  }))
  return this.map
 }
 private getProduction(): Engine {
  if (!this.production) this.production = markRaw(new (ProductionWorkspace as unknown as Constructor)({ ...this.common(),
   onChange: (tasks: RecordData[]) => { this.store.tasks.production = tasks; void this.syncMap() },
   onCityArea: (text: string) => { this.getLandcover().setRequirement(text); this.navigate('area') },
   onAdvanced: () => this.store.toast('请使用专题分析或专业光谱表单'), onMap: (task: RecordData) => this.showTask('production', task)
  }))
  return this.production
 }
 private getLandcover(): Engine {
  if (!this.landcover) this.landcover = markRaw(new (LandcoverWorkspace as unknown as Constructor)({ ...this.common(),
   onChange: (tasks: RecordData[]) => { this.store.tasks.landcover = tasks; void this.syncMap() }, onMap: (task: RecordData) => this.showTask('landcover', task)
  }))
  return this.landcover
 }
 private context() { return { ...this.contextState, selectedAOI: this.store.area, datasets: this.store.datasets, analyses: this.store.analyses, runs: [], apps: [],
  productionTasks: this.store.tasks.production, landcoverTasks: this.store.tasks.landcover, croplandTasks: this.store.tasks.cropland, agricultureTasks: this.store.tasks.agriculture, rasterProcessingTasks: this.store.tasks.rasterProcessing } }
 async syncMap() {
  if (!this.map?._mounted || this.disposed) return
  await this.map.updateContext(this.context())
 }
 async mount(view: View, host: HTMLElement) {
  if (this.disposed) return
  this.currentHost = host
  if (view === 'map') {
   this.getCopilot()._suspended = false; const map = this.getMap(); map.rightOpen = this.store.tab !== 'map'
   await map.mount(host, this.context()); this.setTab(this.store.tab)
  } else if (view === 'data') await this.getSources().mount(host)
  else if (view === 'production') { this.getProduction()._suspended=false; await this.getProduction().mount(host, { compact: false }) }
  else if (view === 'area') { this.getLandcover()._suspended=false; await this.getLandcover().mount(host) }
 }
 setTab(tab: MapTab) {
  this.store.setTab(tab)
  if (tab !== 'map' && this.copilot?.tab !== tab) this.getCopilot().setTab(tab)
  this.map?.setAssistantOpen(tab !== 'map')
 }
 deactivate() {
  this.mountHistory(null);this.map?.unmount()
  for (const engine of [this.copilot, this.production, this.landcover]) {
   if (!engine) continue
   engine._suspended = true; clearInterval(engine.timer); clearInterval(engine.pollTimer); engine.timer = null; engine.pollTimer = null
  }
 }
 async showDataset(id: string) {
  const dataset = await api<Dataset>('/datasets/' + encodeURIComponent(id)); if (dataset.source_type !== 'user') throw Error('请选择用户上传的数据')
  this.contextState.assistantSelection = null
  this.contextState.uploadedDatasetSelection = { dataset, dataset_id: id, token: Date.now() }
  // MapWorkspace consumes the frozen dataset selection, not an inferred bbox.
  this.contextState.externalGeoJSON = dataset.geojson; this.contextState.externalSource = 'dataset:' + id; this.contextState.externalMeta = dataset; this.contextState.externalFocus = Date.now()
  this.store.dialog = null; this.navigate('map', 'map'); await this.syncMap()
 }
 async showAnalysis(result: RecordData) { await this.showQuestion({ id: result.id, answer: { result_ref: { type: 'analysis', task_id: result.id } } }, { explicit: true }); this.navigate('map', 'map') }
 private async showAtlas(product: RecordData, options = {}, closePanel = false) {
  if (!this.map?.map) throw Error('地图尚未就绪')
  this.contextState.assistantSelection = null; this.contextState.uploadedDatasetSelection = null
  await this.syncMap(); await this.map.showAtlasProduct(product, options); if (closePanel && window.innerWidth <= 760) this.map.setAssistantOpen(false)
 }
 private async showQuestion(question: RecordData, { fit = true, explicit = false } = {}) {
  const answer = question.answer, ref = answer?.result_ref
  if (!ref || (!explicit && answer.map_effect === 'none')) return
  if (ref.type === 'agriculture_atlas') {
   const product = answer.atlas_product_snapshot
   if (!product || product.id !== ref.task_id || product.product_sha256 !== ref.product_sha256) throw Error('历史答案缺少一致的成果版本')
   await this.showAtlas(product, { fit, expectedSha: ref.product_sha256, questionId: question.id }, explicit); return
  }
  const paths: Record<string, string> = { landcover: 'landcover', production: 'production', raster_processing: 'raster-processing', agriculture: 'agriculture', cropland: 'cropland' }
  let task: RecordData
  if (ref.type === 'analysis') { const a = await api<RecordData>('/analysis/' + encodeURIComponent(ref.task_id)); task = { id: a.id, algorithm_id: a.algorithm_id, result: a } }
  else { if (!paths[ref.type]) throw Error('当前答案未提供可显示的空间成果'); task = await api<RecordData>('/' + paths[ref.type] + '/tasks/' + encodeURIComponent(ref.task_id)) }
  if (!task.result) throw Error('对应任务尚无可展示成果')
  const kind = ref.type === 'raster_processing' ? 'rasterProcessing' : ref.type
  if (kind in this.store.tasks) this.store.tasks[kind] = [task, ...this.store.tasks[kind].filter(t => t.id !== task.id)]
  this.contextState.uploadedDatasetSelection = null; this.contextState.externalSource = null; this.contextState.externalGeoJSON = null
  const selection = { question, task, fit, token: Date.now() }; this.contextState.assistantSelection = selection
  if (explicit && window.innerWidth <= 760) this.map?.setAssistantOpen(false)
  if (this.map?._mounted) await this.map.applyAssistantResult(selection)
 }
 private async showTask(kind: string, task: RecordData) {
  await this.showQuestion({ id: task.id, answer: { result_ref: { type: kind, task_id: task.id } } }, { explicit: true }); this.navigate('map', 'map')
 }
 async mountSourceAdvanced(tab:'library'|'search',host:HTMLElement){const engine=this.getSources();engine.tab=tab;await engine.mount(host)}
 async showSourceDetail(id:string){await this.getSources().detail(id)}
 async action(name: string, target?: HTMLElement) {
  if (name === 'import-dialog') this.store.overlay = 'upload'
  else if (name === 'source-coverage') await this.getSources().showCoverage()
  else if (name === 'tianditu-config') await this.getSources().showTianditu()
  else if (name === 'open-sources') { this.store.dialog = null; this.store.setDataTab('catalog'); this.navigate('data') }
  else if (name === 'production-task' || name === 'landcover-task') {
   const kind = name === 'production-task' ? 'production' : 'landcover', id = target?.dataset.id
   const task = await api<RecordData>('/' + kind + '/tasks/' + encodeURIComponent(id || ''))
   if (kind === 'production') { this.getProduction().selectTask(task, { seedDraft: true }); this.navigate('production') }
   else { this.getLandcover().selectTask(task); this.navigate('area') }
  } else if (name === 'close-dialog') this.store.dialog = null
  else if (name === 'guide') this.store.overlay = 'guide'
 }
 refreshUserData() { if (this.copilot) this.copilot.userDatasets = this.store.datasets.filter(d => d.source_type === 'user'); void this.sources?.refreshDatasets(); void this.syncMap() }
 destroy() {
  this.disposed = true; this.deactivate()
  this.map?._sizeObserver?.disconnect(); this.map?.map?.remove(); clearTimeout(this.map?.searchTimer); clearTimeout(this.map?.baseSlowTimer)
  if(this.historyHost)render(null,this.historyHost);this.historyHost=undefined;this.sources?.destroy?.(); this.currentHost = undefined
 }
}
