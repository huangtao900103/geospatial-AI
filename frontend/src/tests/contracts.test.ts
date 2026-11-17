import { describe, expect, it } from 'vitest'
import { analysis, atlasCatalog, dataset, entity, list, question, sourceCatalog, task } from '../services/contracts'
describe('runtime payload contracts',()=>{
 it('rejects malformed question states and unpinned atlas entries',()=>{
  expect(()=>question({id:'q',question:'query',status:'invented'})).toThrow()
  expect(()=>atlasCatalog({products:[{id:'p',title:'product',status:'ready',product_sha256:''}]})).toThrow()
  expect(atlasCatalog({products:[],production_record_count:0}).products).toEqual([])
 })
 it('keeps real empty lists distinct from absent or malformed lists',()=>{
  expect(list([],entity,'records')).toEqual([])
  for(const value of [null,{},'[]',undefined])expect(()=>list(value,entity,'records')).toThrow('返回格式')
 })
 it('rejects duplicated and empty identifiers',()=>{
  expect(()=>list([{id:'same'},{id:'same'}],entity,'records')).toThrow()
  expect(()=>entity({id:''})).toThrow()
 })
 it('validates user data without stripping frozen metadata',()=>{
  const value={id:'d',source_type:'user',feature_count:0,sha256:'version',geojson:{type:'FeatureCollection',features:[]}}
  expect(dataset(value)).toEqual(value)
  expect(()=>dataset({...value,feature_count:-1})).toThrow()
  expect(()=>dataset({...value,geojson:{type:'Polygon'}})).toThrow()
 })
 it('does not accept a fabricated task state or an invalid result envelope',()=>{
  expect(()=>task({id:'t',status:'pretend_success'})).toThrow()
  expect(()=>task({id:'t',status:'succeeded',result:[]})).toThrow()
  expect(task({id:'t',status:'failed',result:null}).status).toBe('failed')
  expect(analysis({id:'a',summary:{area:0,unknown:null}}).summary).toEqual({area:0,unknown:null})
 })
 it('keeps declared and connected years separate and rejects inconsistent years',()=>{
  const source={id:'rice',name:'rice',source_type:'landcover_raster',stages:{registered:true,catalog_searchable:false,pixel_readable:true,analysis_ready:true,connection_status:'adapter_ready_on_demand'},coverage:{temporal:{available_years:[2015,2024],connected_years:[2024]}}}
  expect(sourceCatalog({sources:[source]}).sources[0].coverage?.temporal?.connected_years).toEqual([2024])
  expect(()=>sourceCatalog({sources:[{...source,coverage:{temporal:{available_years:[2015],connected_years:[2024]}}}]})).toThrow()
  expect(()=>sourceCatalog({sources:[{...source,stages:{...source.stages,analysis_ready:'yes'}}]})).toThrow()
 })
})
