import type { MapTab, View } from '../types/api'
export function parseRoute(hash: string): { view: View; tab?: MapTab } {
 const parts = hash.replace(/^#\/?/, '').split('/')
 const aliases: Record<string, View> = { overview: 'map', map: 'map', data: 'data', workflow: 'production', production: 'production', area: 'area', lab: 'vector', vector: 'vector', connections: 'integrations', integrations: 'integrations' }
 const tab = ['map', 'applications', 'history', 'chat', 'operations'].includes(parts[1]) ? parts[1] as MapTab : undefined
 return { view: aliases[parts[0]] || 'map', tab }
}
export function routeHash(view: View, tab?: MapTab) { return '#/' + view + (view === 'map' && tab ? '/' + tab : '') }
