import { isRecord, validateResponse } from './contracts'
import type { ApiClient, RequestOptions } from '../types/api'
export class ApiError extends Error {
 constructor(message: string, public code: string, public status: number) { super(message); this.name = 'ApiError' }
}
export function createApiClient(transport: typeof fetch = fetch): ApiClient {
 return async <T>(path: string, options: RequestOptions = {}): Promise<T> => {
  if (!path.startsWith('/') || path.startsWith('//') || /^\/api(?:\/|$)/.test(path)) throw new ApiError('接口路径不合法', 'INVALID_PATH', 0)
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) controller.abort()
  const timeout = setTimeout(abort, path === '/data-sources/search' ? 45000 : path === '/integrations/tianditu/check' ? 25000 : path.startsWith('/public/') ? 18000 : 12000)
  try {
   const response = await transport('/api' + path, { method: options.method || 'GET', signal: controller.signal,
    headers: options.body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: options.body === undefined ? undefined : JSON.stringify(options.body) })
   let value: unknown
   try { value = await response.json() } catch { throw new ApiError('本地接口未返回有效数据', 'INVALID_RESPONSE', response.status) }
   if (!response.ok) {
    const error = isRecord(value) && isRecord(value.error) ? value.error : {}
    throw new ApiError(typeof error.message === 'string' ? error.message : '请求未完成，请重试', typeof error.code === 'string' ? error.code : 'REQUEST_FAILED', response.status)
   }
   return validateResponse(path, options.method || 'GET', value) as T
  } catch (error) {
   if (error instanceof Error && error.name === 'AbortError') throw new ApiError(options.signal?.aborted ? '请求已取消' : '请求超时，请稍后重试；未生成替代数据', options.signal?.aborted ? 'CANCELLED' : 'TIMEOUT', 0)
   throw error
  } finally { clearTimeout(timeout); options.signal?.removeEventListener('abort', abort) }
 }
}
export const api = createApiClient()
