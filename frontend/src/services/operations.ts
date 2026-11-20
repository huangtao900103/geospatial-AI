import { api } from './api'
import { ContractError, isRecord } from './contracts'
import type { RequestOptions } from '../types/api'
export async function opsApi<T>(path:string,options:RequestOptions={}):Promise<T>{
 const value=await api<unknown>('/agri-operations'+path,options)
 if(!isRecord(value))throw new ContractError('农业业务')
 const route=path.split('?')[0],method=options.method||'GET'
 const required=method==='GET'?(route==='/catalog'?['kinds','hazards','modules','connections']:route==='/registries'?['registries']:['/objects','/events','/tasks','/guidance'].includes(route)?['items']:[]):[]
 if(required.some(key=>!Array.isArray(value[key])))throw new ContractError('农业业务列表')
 if(route==='/objects'&&method==='GET'&&(!Number.isInteger(value.total)||typeof value.has_more!=='boolean'))throw new ContractError('台账分页')
 for(const key of ['items','registries','tasks','modules','kinds','hazards','connections','objects'])if(key in value&&(!Array.isArray(value[key])||!(value[key] as unknown[]).every(row=>isRecord(row)&&typeof row.id==='string')))throw new ContractError('农业业务列表')
 if('total' in value&&(!Number.isInteger(value.total)||Number(value.total)<0))throw new ContractError('台账分页')
 if('revision' in value&&(!Number.isInteger(value.revision)||Number(value.revision)<1))throw new ContractError('业务版本')
 return value as T
}
