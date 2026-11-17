<script setup lang="ts">
import { ref } from 'vue'
import { useWorkspaceStore } from '../stores/workspace'
const emit = defineEmits<{ uploaded: [id: string] }>()
const store = useWorkspaceStore(), name = ref(''), error = ref(''), preview = ref(''), busy = ref(false)
const content = ref<unknown>(null)
let sequence = 0
async function select(event: Event) {
 const id = ++sequence; content.value = null; error.value = ''; preview.value = ''
 const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return
 try {
  if (file.size > 2 * 1024 * 1024) throw Error('文件超过2MiB，请减少要素数量')
  const geo = JSON.parse(await file.text())
  if (id !== sequence) return
  if (!geo || geo.type !== 'FeatureCollection' || !Array.isArray(geo.features)) throw Error('请选择GeoJSON FeatureCollection文件')
  if (geo.features.length < 1 || geo.features.length > 500) throw Error('要素数量须为1至500个')
  content.value = geo; if (!name.value) name.value = file.name.replace(/\.(geojson|json)$/i, '')
  preview.value = `已读取 ${file.name} · ${geo.features.length} 个要素，提交时继续校验坐标和几何。`
 } catch (e) { if (id === sequence) error.value = e instanceof Error ? e.message : '文件读取失败' }
}
async function submit() {
 if (busy.value || !content.value) return
 busy.value = true; error.value = ''
 try { const dataset = await store.importDataset(name.value.trim() || '上传图层', content.value); emit('uploaded', dataset.id); store.overlay = null }
 catch (e) { error.value = e instanceof Error ? e.message : '导入失败' } finally { busy.value = false }
}
</script>
<template><form @submit.prevent="submit"><p>上传WGS84 GeoJSON点、线或面，完成后在地图查看。</p><label class="field">图层名称<input v-model="name" maxlength="100" autocomplete="off"></label><label class="field">选择文件<input id="upload-file" type="file" accept=".geojson,.json,application/geo+json,application/json" :disabled="busy" @change="select"></label><p>{{ preview || '最多500个要素、5000个坐标点、2MiB；坐标顺序为经度、纬度。' }}</p><p v-if="error" class="vue-error" role="alert">{{ error }}</p><div class="dialog-actions"><button class="btn primary" type="submit" :disabled="!content || busy">{{ busy ? '正在上传…' : '上传并上图' }}</button></div></form></template>
