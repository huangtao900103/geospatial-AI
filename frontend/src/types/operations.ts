export interface Registry {id:string;name:string;kind:string;source:string;year:number|null;revision:number;object_count:number}
export interface BusinessObject {id:string;registry_id:string;kind:string;code:string;name:string;status:string;revision:number;geometry:Record<string,unknown>;properties:Record<string,unknown>;progress?:number;note?:string}
export interface BusinessEvent {id:string;title:string;hazard:string;geometry:Record<string,unknown>;source_name:string;source_kind:string;issued_at:string;revision:number;notice:string}
export interface Impact {id:string;event_id:string;objects:BusinessObject[];counts:Record<string,number>;object_count:number;boundary_only_contacts:number;registry_versions:Record<string,number>;revision:number;notice:string}
export interface WorkOrder {id:string;title:string;kind:string;status:string;assignee:string;due_date:string;object:BusinessObject;revision:number;event_id?:string;instruction?:string;feedback?:{finding:string;note:string;evidence_urls:string[];submitted_by:string};review?:{actor:string;note:string}}
export interface Guidance {id:string;title:string;content:string;registry_id:string;status:string;revision:number;task_count?:number}
export interface OperationsCatalog {kinds:{id:string;name:string}[];hazards:{id:string;name:string}[];modules:{id:string;name:string;status:string;note:string}[];connections:{id:string;name:string;status:string}[];limits:{batch_features:number;page_size:number;impact_objects:number}}
export interface ObjectPage {items:BusinessObject[];total:number;limit:number;offset:number;has_more:boolean}
