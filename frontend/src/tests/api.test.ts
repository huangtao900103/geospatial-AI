import { describe, expect, it, vi } from 'vitest'
import { createApiClient, ApiError } from '../services/api'
describe('typed API boundary', () => {
 it('preserves zero and null and submits same-origin JSON', async () => {
  const transport=vi.fn().mockResolvedValue(new Response(JSON.stringify({area:0,unknown:null})))
  const result=await createApiClient(transport)('/metrics',{method:'POST',body:{dataset_id:'d'}})
  expect(result).toEqual({area:0,unknown:null});expect(transport).toHaveBeenCalledWith('/api/metrics',expect.objectContaining({method:'POST',body:'{"dataset_id":"d"}'}))
 })
 it('retains server failure code without creating a substitute result', async () => {
  const client=createApiClient(vi.fn().mockResolvedValue(new Response(JSON.stringify({error:{code:'VERSION_CHANGED',message:'版本变化'}}),{status:409})))
  await expect(client('/analysis/a')).rejects.toMatchObject({code:'VERSION_CHANGED',status:409,message:'版本变化'})
 })
 it('rejects external and double-prefixed paths before network access', async () => {
  const transport=vi.fn(),client=createApiClient(transport)
  for(const path of ['https://example.test','//example.test','/api/analysis'])await expect(client(path)).rejects.toBeInstanceOf(ApiError)
  expect(transport).not.toHaveBeenCalled()
 })
 it('reports malformed responses as errors',async()=>{
  await expect(createApiClient(vi.fn().mockResolvedValue(new Response('not json')))('/health')).rejects.toMatchObject({code:'INVALID_RESPONSE'})
 })
 it('propagates caller cancellation',async()=>{
  const controller=new AbortController();controller.abort()
  const transport=vi.fn((_url,init)=>Promise.reject(init.signal.aborted?new DOMException('aborted','AbortError'):Error('bad')))
  await expect(createApiClient(transport)('/health',{signal:controller.signal})).rejects.toMatchObject({code:'CANCELLED'})
 })
})
