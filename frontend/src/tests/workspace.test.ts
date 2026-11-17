import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { api } from '../services/api'
import { useWorkspaceStore, validArea } from '../stores/workspace'
import { parseRoute, routeHash } from '../services/navigation'
vi.mock('../services/api',()=>({api:vi.fn()}))
beforeEach(()=>{setActivePinia(createPinia());vi.clearAllMocks();const values=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)||null,setItem:(key:string,value:string)=>values.set(key,value)})})
describe('reactive workspace and routes',()=>{
 it('clearing selection removes persistent area and does not resurrect it',()=>{
  const store=useWorkspaceStore();store.setArea({name:'selected',bbox:[113,30,113.01,30.01]});expect(store.area?.bbox).toEqual([113,30,113.01,30.01]);store.setArea(null);expect(store.area).toBeNull();expect(localStorage.getItem('geoai-v9-selected-area')).toBe('null')
 })
 it('rejects invalid regions rather than treating them as full-city scope',()=>{
  expect(validArea({name:'bad',bbox:[1,2,0,3]})).toBe(false);expect(validArea({name:'bad',bbox:[1,2,Infinity,3]})).toBe(false)
 })
 it('does not drop existing data when upload fails',async()=>{
  const store=useWorkspaceStore();store.datasets=[{id:'original',source_type:'user'}];vi.mocked(api).mockRejectedValueOnce(Error('invalid geometry'))
  await expect(store.importDataset('new',{})).rejects.toThrow('invalid geometry');expect(store.datasets.map(d=>d.id)).toEqual(['original'])
 })
 it('imports the actual server response without manufacturing identifiers',async()=>{
  const store=useWorkspaceStore();vi.mocked(api).mockResolvedValueOnce({id:'actual',source_type:'user',feature_count:1})
  expect((await store.importDataset('name',{})).id).toBe('actual');expect(store.datasets[0].id).toBe('actual')
 })
 it('keeps supported resources if one list is unavailable',async()=>{
  vi.mocked(api).mockImplementation(async(path)=>{if(path==='/bootstrap')return {};if(path==='/data-sources')return {sources:[]};if(path==='/datasets')return [{id:'d',source_type:'user'}];if(path==='/analysis')throw Error('unavailable');return []})
  const store=useWorkspaceStore();await store.initialize();expect(store.ready).toBe(true);expect(store.datasets[0].id).toBe('d');expect(store.analyses).toEqual([]);store.dispose()
 })
 it('keeps old bookmarks and browser back routes compatible',()=>{
  expect(parseRoute('#overview')).toEqual({view:'map',tab:undefined});expect(parseRoute('#workflow').view).toBe('production');expect(parseRoute('#/map/history')).toEqual({view:'map',tab:'history'});expect(routeHash('data')).toBe('#/data');expect(parseRoute('#/bad').view).toBe('map')
 })
})
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(r=>{resolve=r});return {promise,resolve}}
describe('non-destructive refresh and request ordering',()=>{
 it('keeps an existing list and its timestamp after transport failure',async()=>{
  const store=useWorkspaceStore();store.datasets=[{id:'old',source_type:'user'}];store.resources.datasets={status:'ready',updatedAt:'2026-01-01T00:00:00Z',error:''}
  vi.mocked(api).mockRejectedValueOnce(Error('offline'))
  expect(await store.refreshDatasets()).toBe(false);expect(store.datasets[0].id).toBe('old');expect(store.resources.datasets).toMatchObject({status:'error',updatedAt:'2026-01-01T00:00:00Z',error:'offline'})
 })
 it('keeps an existing list after a malformed HTTP200, then accepts a real empty retry',async()=>{
  const store=useWorkspaceStore();store.datasets=[{id:'old',source_type:'user'}]
  vi.mocked(api).mockResolvedValueOnce({records:[]});await store.refreshDatasets();expect(store.datasets[0].id).toBe('old');expect(store.resources.datasets.status).toBe('error')
  vi.mocked(api).mockResolvedValueOnce([]);expect(await store.refreshDatasets()).toBe(true);expect(store.datasets).toEqual([]);expect(store.resources.datasets.status).toBe('ready')
 })
 it('an older refresh cannot overwrite a newer refresh even if transport ignores abort',async()=>{
  const old=deferred<unknown>();vi.mocked(api).mockReturnValueOnce(old.promise).mockResolvedValueOnce([{id:'latest',source_type:'user'}])
  const store=useWorkspaceStore(),first=store.refreshDatasets();await store.refreshDatasets();old.resolve([{id:'obsolete',source_type:'user'}]);await first
  expect(store.datasets.map(d=>d.id)).toEqual(['latest'])
 })
 it('an in-flight list cannot erase an upload that completed afterwards',async()=>{
  const old=deferred<unknown>();vi.mocked(api).mockReturnValueOnce(old.promise).mockResolvedValueOnce({id:'new',source_type:'user'})
  const store=useWorkspaceStore();store.datasets=[{id:'old',source_type:'user'}];const request=store.refreshDatasets();await store.importDataset('new',{});old.resolve([]);await request
  expect(store.datasets.map(d=>d.id)).toEqual(['new','old']);expect(store.resources.datasets.status).toBe('ready')
 })
 it('a disposed workspace rejects late writes',async()=>{
  const late=deferred<unknown>();vi.mocked(api).mockReturnValueOnce(late.promise);const store=useWorkspaceStore();store.datasets=[{id:'old',source_type:'user'}];const request=store.refreshDatasets();store.dispose();late.resolve([]);await request;expect(store.datasets[0].id).toBe('old')
 })
 it('a failed bootstrap refresh preserves an already usable workspace',async()=>{
  const store=useWorkspaceStore();store.ready=true;store.boot={workspace:{name:'known'}};store.datasets=[{id:'old',source_type:'user'}];vi.mocked(api).mockRejectedValueOnce(Error('bootstrap offline'));await store.initialize()
  expect(store.ready).toBe(true);expect(store.boot.workspace).toEqual({name:'known'});expect(store.datasets[0].id).toBe('old');expect(store.error).toBe('bootstrap offline')
 })
 it('ignores a corrupt stored tab instead of rendering an unknown panel',()=>{
  localStorage.setItem('geoai-v11-panel-tab',JSON.stringify({bad:true}));expect(useWorkspaceStore().tab).toBe('applications')
 })
 it('a cancelled resource is not left spinning if reconnect fails',async()=>{
  const old=deferred<unknown>();vi.mocked(api).mockReturnValueOnce(old.promise).mockRejectedValueOnce(Error('bootstrap offline'))
  const store=useWorkspaceStore();store.resources.datasets={status:'ready',updatedAt:'2026-01-01T00:00:00Z',error:''}
  const pending=store.refreshDatasets();await store.initialize();expect(store.resources.datasets.status).toBe('ready')
  old.resolve([]);await pending;expect(store.resources.datasets.status).toBe('ready');store.dispose()
 })
})
