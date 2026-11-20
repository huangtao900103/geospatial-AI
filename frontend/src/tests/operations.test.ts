import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../services/api'
import { opsApi } from '../services/operations'
import { parseRoute } from '../services/navigation'
vi.mock('../services/api',()=>({api:vi.fn()}))
beforeEach(()=>vi.clearAllMocks())
describe('agricultural business boundary',()=>{
 it('routes to the same map rather than a disconnected dashboard',()=>expect(parseRoute('#/map/operations')).toEqual({view:'map',tab:'operations'}))
 it('rejects malformed successful lists instead of treating them as empty',async()=>{
  vi.mocked(api).mockResolvedValueOnce({registries:{}})
  await expect(opsApi('/registries')).rejects.toThrow('返回格式')
  vi.mocked(api).mockResolvedValueOnce({items:[],total:0,has_more:false})
  expect(await opsApi('/objects')).toEqual({items:[],total:0,has_more:false})
 })
 it('does not accept invalid revisions or missing row identifiers',async()=>{
  for(const value of [{id:'t',revision:0},{items:[{name:'no identifier'}]},{items:[],total:-1}]){vi.mocked(api).mockResolvedValueOnce(value);await expect(opsApi('/tasks')).rejects.toThrow()}
 })
})
