/** Validate HTTP payloads before they can replace trusted UI state. Extra server fields are preserved. */
import type { Analysis, Bootstrap, DataSource, Dataset, Entity, SourceCatalog, Task } from '../types/api'
export class ContractError extends Error {
 readonly code = 'INVALID_RESPONSE'; readonly status = 502
 constructor(label: string) { super(`${label}返回格式不符合接口约定，已保留原有内容`); this.name = 'ContractError' }
}
export const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const string = (v: unknown): v is string => typeof v === 'string'
const id = (v: unknown): v is string => string(v) && v.length > 0 && v.length <= 200
const numericYear = (v: unknown) => Number.isInteger(v) && Number(v) >= 1900 && Number(v) <= 2200
const years = (v: unknown) => Array.isArray(v) && v.every(numericYear)
export function entity(value: unknown, label = '记录'): Entity {
 if (!isRecord(value) || !id(value.id) || (value.name !== undefined && !string(value.name))) throw new ContractError(label)
 return value as Entity
}
export function dataset(value: unknown): Dataset {
 const item = entity(value, '图层')
 if (!string(item.source_type) || (item.feature_count !== undefined && (!Number.isInteger(item.feature_count) || Number(item.feature_count) < 0))) throw new ContractError('图层')
 if (item.geojson !== undefined && (!isRecord(item.geojson) || item.geojson.type !== 'FeatureCollection' || !Array.isArray(item.geojson.features))) throw new ContractError('图层')
 return item as Dataset
}
export function analysis(value: unknown): Analysis {
 const item = entity(value, '分析')
 if (item.geojson !== undefined && (!isRecord(item.geojson) || item.geojson.type !== 'FeatureCollection' || !Array.isArray(item.geojson.features))) throw new ContractError('分析')
 return item as Analysis
}
export function task(value: unknown): Task {
 const item = entity(value, '任务')
 if (item.status !== undefined && !['queued','running','succeeded','failed','cancelled','needs_data','blocked'].includes(String(item.status))) throw new ContractError('任务')
 if (item.result !== undefined && item.result !== null && !isRecord(item.result)) throw new ContractError('任务')
 return item as Task
}
export function list<T extends Entity>(value: unknown, decode: (v: unknown) => T, label: string): T[] {
 if (!Array.isArray(value)) throw new ContractError(label)
 const items = value.map(decode), ids = items.map(item => (item as Entity).id)
 if (new Set(ids).size !== ids.length) throw new ContractError(label)
 return items
}
export function source(value: unknown): DataSource {
 const item = entity(value, '数据源')
 if (!string(item.name) || !string(item.source_type) || !isRecord(item.stages)) throw new ContractError('数据源')
 for (const key of ['registered','catalog_searchable','pixel_readable','analysis_ready']) if (typeof item.stages[key] !== 'boolean') throw new ContractError('数据源')
 if (!string(item.stages.connection_status)) throw new ContractError('数据源')
 if (item.coverage !== undefined) {
  if (!isRecord(item.coverage)) throw new ContractError('数据源覆盖')
  const t = item.coverage.temporal
  if (t !== undefined) {
   if (!isRecord(t)) throw new ContractError('数据源年份')
   for (const key of ['available_years','connected_years']) if (t[key] !== undefined && !years(t[key])) throw new ContractError('数据源年份')
   if (t.product_year !== undefined && t.product_year !== null && !numericYear(t.product_year)) throw new ContractError('数据源年份')
   if (Array.isArray(t.available_years) && Array.isArray(t.connected_years) && t.connected_years.some(y=>!(t.available_years as unknown[]).includes(y))) throw new ContractError('数据源年份')
  }
 }
 if (item.semantics !== undefined && (!Array.isArray(item.semantics) || !item.semantics.every(string))) throw new ContractError('数据源')
 return item as unknown as DataSource
}
export function sourceCatalog(value: unknown): SourceCatalog {
 if (!isRecord(value)) throw new ContractError('数据源目录')
 return { ...value, sources: list(value.sources, source, '数据源目录') }
}
export function bootstrap(value: unknown): Bootstrap {
 if (!isRecord(value)) throw new ContractError('工作空间')
 if (value.integrations !== undefined) list(value.integrations, entity, '集成目录')
 return value as Bootstrap
}
export function validateResponse(path: string, method: string, value: unknown): unknown {
 const route = path.split('?')[0]
 if (route === '/bootstrap') return bootstrap(value)
 if (method === 'GET' && route === '/assistant/questions') return list(value, question, '问答任务')
 if (method === 'GET' && route === '/agriculture-atlas') return atlasCatalog(value)
 if (route === '/data-sources') return sourceCatalog(value)
 if (method === 'GET' && /^\/data-sources\/[^/]+$/.test(route)) return source(value)
 if (route === '/datasets') return method === 'GET' ? list(value, dataset, '图层列表') : dataset(value)
 if (/^\/datasets\/[^/]+$/.test(route)) return dataset(value)
 if (route === '/algorithms') return list(value, entity, '算法目录')
 if (route === '/analysis') return method === 'GET' ? list(value, analysis, '分析列表') : analysis(value)
 if (/^\/analysis\/[^/]+$/.test(route)) return analysis(value)
 if (/^\/(production|landcover|cropland|agriculture|raster-processing)\/tasks$/.test(route)) return method === 'GET' ? list(value, task, '任务列表') : task(value)
 if (/^\/(production|landcover|cropland|agriculture|raster-processing)\/tasks\/[^/]+$/.test(route)) return task(value)
 return value
}
export function question(value:unknown):import('../types/api').QuestionSummary{
 const item=entity(value,'问答任务')
 if(!string(item.question)||!['processing','answered','needs_data','unsupported','failed','cancelled'].includes(String(item.status)))throw new ContractError('问答任务')
 return item as import('../types/api').QuestionSummary
}
export function atlasCatalog(value:unknown):import('../types/api').AtlasCatalog{
 if(!isRecord(value))throw new ContractError('区域成果')
 const products=list(value.products,v=>{
  const item=entity(v,'区域成果')
  if(!string(item.title)||!string(item.status)||!string(item.product_sha256)||!/^[a-f0-9]{64}$/.test(item.product_sha256))throw new ContractError('区域成果版本')
  return item as import('../types/api').AtlasSummary
 },'区域成果')
 if(value.production_record_count!==undefined&&(!Number.isInteger(value.production_record_count)||Number(value.production_record_count)<0))throw new ContractError('生产记录数')
 return {...value,products} as import('../types/api').AtlasCatalog
}
