import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '../services/api'
import * as contract from '../services/contracts'
import type { Analysis, Area, Bootstrap, Dataset, DataSource, Entity, MapTab, RequestOptions, ResourceKey, ResourceState, Task, View } from '../types/api'
export function readStored<T>(key: string, fallback: T): T { try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback } catch { return fallback } }
export function saveStored(key: string, value: unknown) { try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage may be disabled */ } }
export function validArea(value: unknown): value is Area {
 const area = value as Area | null
 return !!area && typeof area.name === 'string' && Array.isArray(area.bbox) && area.bbox.length === 4 && area.bbox.every(Number.isFinite) && area.bbox[0] < area.bbox[2] && area.bbox[1] < area.bbox[3] && area.bbox[0] >= -180 && area.bbox[2] <= 180 && area.bbox[1] >= -85 && area.bbox[3] <= 85
}
export const resourceNames: Record<ResourceKey, string> = { datasets:'我的图层', algorithms:'算法目录', analyses:'分析成果', cropland:'耕作地任务', agriculture:'农业任务', rasterProcessing:'影像处理任务', production:'遥感任务', landcover:'区域核算任务', sources:'数据来源' }
const paths: Record<ResourceKey, string> = { datasets:'/datasets', algorithms:'/algorithms', analyses:'/analysis', cropland:'/cropland/tasks', agriculture:'/agriculture/tasks', rasterProcessing:'/raster-processing/tasks', production:'/production/tasks', landcover:'/landcover/tasks', sources:'/data-sources' }
const resourceKeys = Object.keys(paths) as ResourceKey[]
const dataTabs = ['uploads','catalog','library','search'] as const
export type DataTab = typeof dataTabs[number]
const tabs: MapTab[] = ['map','applications','history','chat']
export const useWorkspaceStore = defineStore('workspace', () => {
 const initialTab = readStored<unknown>('geoai-v11-panel-tab', 'applications')
 const view = ref<View>('map'), tab = ref<MapTab>(tabs.includes(initialTab as MapTab) ? initialTab as MapTab : 'applications')
 const storedDataTab=readStored<unknown>('geoai-vue-data-tab','uploads')
 const dataTab=ref<DataTab>(dataTabs.includes(storedDataTab as DataTab)?storedDataTab as DataTab:'uploads')
 const ready = ref(false), loading = ref(false), error = ref(''), sidebar = ref(false)
 const boot = ref<Bootstrap | null>(null), datasets = ref<Dataset[]>([]), analyses = ref<Analysis[]>([]), algorithms = ref<Entity[]>([]), sources = ref<DataSource[]>([])
 const tasks = ref<Record<string, Task[]>>({ production: [], landcover: [], cropland: [], agriculture: [], rasterProcessing: [] })
 const resources = ref(Object.fromEntries(resourceKeys.map(key=>[key,{status:'idle',error:'',updatedAt:null}])) as Record<ResourceKey, ResourceState>)
 const issues = computed(()=>resourceKeys.filter(key=>resources.value[key].status==='error'))
 const stored = readStored<unknown>('geoai-v9-selected-area', null), area = ref<Area | null>(validArea(stored) ? stored : null)
 const notice = ref(''), dialog = ref<{ title: string; html: string; actions: string } | null>(null), overlay = ref<'upload' | 'command' | 'tools' | 'guide' | null>(null)
 const versions = Object.fromEntries(resourceKeys.map(key=>[key,0])) as Record<ResourceKey,number>
 const controllers = new Map<ResourceKey, AbortController>()
 let noticeTimer: ReturnType<typeof setTimeout> | undefined, loadId=0, bootstrapController:AbortController|undefined, disposed=false
 function toast(message: string) { notice.value=message;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{notice.value=''},3300) }
 function setArea(value: Area | null) { area.value=validArea(value)?{name:value.name,bbox:[...value.bbox]}:null;saveStored('geoai-v9-selected-area',area.value) }
 function setTab(value: MapTab) { tab.value=tabs.includes(value)?value:'applications';saveStored('geoai-v11-panel-tab',tab.value) }
 function setDataTab(value:DataTab){dataTab.value=dataTabs.includes(value)?value:'uploads';saveStored('geoai-vue-data-tab',dataTab.value)}
 function showDialog(title:string,html:string,actions=''){overlay.value=null;dialog.value={title,html,actions}}
 function cancel(key:ResourceKey){++versions[key];controllers.get(key)?.abort();controllers.delete(key);if(resources.value[key].status==='loading')resources.value[key]={...resources.value[key],status:resources.value[key].updatedAt?'ready':'idle'}}
 function commit(key:ResourceKey,value:unknown){
  if(key==='datasets')datasets.value=contract.list(value,contract.dataset,'图层列表')
  else if(key==='algorithms')algorithms.value=contract.list(value,contract.entity,'算法目录')
  else if(key==='analyses')analyses.value=contract.list(value,contract.analysis,'分析列表')
  else if(key==='sources')sources.value=contract.sourceCatalog(value).sources
  else tasks.value[key]=contract.list(value,contract.task,'任务列表')
 }
 async function refreshResource(key:ResourceKey):Promise<boolean>{
  if(disposed)return false
  cancel(key);const version=versions[key],controller=new AbortController();controllers.set(key,controller)
  resources.value[key]={...resources.value[key],status:'loading',error:''}
  try{
   const value=await api<unknown>(paths[key],{signal:controller.signal})
   if(disposed||version!==versions[key])return false
   commit(key,value)
   resources.value[key]={status:'ready',error:'',updatedAt:new Date().toISOString()};return true
  }catch(e){
   if(!disposed&&version===versions[key])resources.value[key]={...resources.value[key],status:'error',error:e instanceof Error?e.message:'读取失败'}
   return false
  }finally{if(version===versions[key])controllers.delete(key)}
 }
 async function refreshDatasets(){return refreshResource('datasets')}
 async function importDataset(name:string,geojson:unknown){
  const item=contract.dataset(await api<unknown>('/datasets',{method:'POST',body:{name,geojson}}))
  // A list requested before this write must not later erase the new record.
  cancel('datasets');datasets.value=[item,...datasets.value.filter(d=>d.id!==item.id)]
  resources.value.datasets={status:'ready',error:'',updatedAt:new Date().toISOString()};return item
 }
 async function initialize(){
  if(disposed)return
  const id=++loadId;bootstrapController?.abort();bootstrapController=new AbortController()
  for(const key of resourceKeys)cancel(key)
  loading.value=true;error.value=''
  try{
   const data=contract.bootstrap(await api<unknown>('/bootstrap',{signal:bootstrapController.signal}))
   if(disposed||id!==loadId)return
   boot.value=data
   await Promise.all(resourceKeys.map(refreshResource))
   if(disposed||id!==loadId)return
   ready.value=true
   if(issues.value.length)toast(`${issues.value.length}类资源更新未完成，已有内容保留；可单独重试`)
  }catch(e){if(!disposed&&id===loadId)error.value=e instanceof Error?e.message:'服务暂未连接'}
  finally{if(id===loadId)loading.value=false}
 }
 function dispose(){disposed=true;clearTimeout(noticeTimer);++loadId;bootstrapController?.abort();for(const key of resourceKeys)cancel(key)}
 return {view,tab,dataTab,setDataTab,ready,loading,error,sidebar,boot,datasets,analyses,algorithms,sources,tasks,resources,issues,area,notice,dialog,overlay,initialize,refreshResource,refreshDatasets,importDataset,setArea,setTab,toast,showDialog,dispose}
})
