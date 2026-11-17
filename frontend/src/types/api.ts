export type BBox = [number, number, number, number]
export interface Area { name: string; bbox: BBox }
export interface FeatureCollection { type: 'FeatureCollection'; features: Array<Record<string, unknown>> }
export interface Entity { id: string; name?: string; [key: string]: unknown }
export interface Dataset extends Entity { source_type: string; geojson?: FeatureCollection; feature_count?: number }
export interface Analysis extends Entity { dataset_id?: string; algorithm_id?: string; geojson?: FeatureCollection }
export interface Task extends Entity { status?: string; result?: Record<string, unknown> | null }
export type View = 'map' | 'data' | 'production' | 'area' | 'vector' | 'integrations'
export type MapTab = 'map' | 'applications' | 'history' | 'chat'
export interface Bootstrap { integrations?: Entity[]; [key: string]: unknown }
export interface RequestOptions { method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'; body?: unknown; signal?: AbortSignal }
export interface ApiClient { <T = unknown>(path: string, options?: RequestOptions): Promise<T> }
export interface SourceStages { registered: boolean; catalog_searchable: boolean; pixel_readable: boolean; analysis_ready: boolean; pixel_applicable?: boolean; connection_status: string }
export interface DataSource extends Entity {
 name: string; provider?: string; source_type: string; platform_shared?: boolean
 stages: SourceStages
 coverage?: { spatial?: { bbox?: BBox | null; scope_note?: string }; temporal?: { product_year?: number | null; available_years?: number[]; connected_years?: number[]; start?: string | null; end?: string | null } }
 resolution?: { description?: string; native?: unknown }
 license?: { name?: string; verified?: boolean }
 integration?: { blocker?: string | null }
 usage_policy?: { mode?: string }
 semantics?: string[]
 provenance?: { source_url?: string | null; article_url?: string | null }
}
export interface SourceCatalog { sources: DataSource[]; catalog_fingerprint?: string }
export type ResourceKey = 'datasets' | 'algorithms' | 'analyses' | 'cropland' | 'agriculture' | 'rasterProcessing' | 'production' | 'landcover' | 'sources'
export interface ResourceState { status: 'idle' | 'loading' | 'ready' | 'error'; error: string; updatedAt: string | null }
export type QuestionStatus = 'processing' | 'answered' | 'needs_data' | 'unsupported' | 'failed' | 'cancelled'
export interface QuestionSummary extends Entity { question: string; status: QuestionStatus; created_at?: string; application_ref?: {name?:string}; answer?: {title?:string;source_year?:number|null}; progress?:number }
export interface AtlasSummary extends Entity { title:string;status:string;product_sha256:string;period_label?:string;scope_label?:string;layer_count?:number;quality?:{status?:string;warning?:string} }
export interface AtlasCatalog { products:AtlasSummary[];production_record_count?:number }
export interface SceneCatalog { runs:Analysis[] }
