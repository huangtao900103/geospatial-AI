<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '../services/api'
import { useWorkspaceStore } from '../stores/workspace'
import type { Analysis } from '../types/api'
const emit = defineEmits<{ show: [result: Analysis] }>()
const store = useWorkspaceStore(), selected = ref(''), algorithm = ref('spatial_summary'), distance = ref(1000), busy = ref(false), error = ref(''), result = ref<Analysis | null>(null)
const datasets = computed(() => store.datasets.filter(d => d.source_type === 'user'))
async function run() {
 if (!selected.value || busy.value) return
 busy.value = true; error.value = ''
 try {
  const data = await api<Analysis>('/analysis', {method:'POST',body:{dataset_id:selected.value,algorithm_id:algorithm.value,parameters:algorithm.value==='nearest'?{max_distance_m:distance.value}:{}}})
  result.value=data; store.analyses=[data,...store.analyses.filter(a=>a.id!==data.id)]
 } catch (e) { error.value=e instanceof Error?e.message:'分析未完成' } finally { busy.value=false }
}
</script>
<template><section class="vue-vector"><div class="heading"><div><h1>矢量数据分析</h1><p>对已上传的业务图层进行空间与属性统计。</p></div><button class="btn primary" @click="store.overlay='upload'">上传数据</button></div><form class="panel vue-form" @submit.prevent="run"><label class="field">用户图层<select v-model="selected" required><option value="">请选择已上传图层</option><option v-for="d in datasets" :key="d.id" :value="d.id">{{d.name}} · {{d.feature_count}}要素</option></select></label><label class="field">分析内容<select v-model="algorithm"><option value="spatial_summary">要素与几何统计</option><option value="nearest">点位最近邻</option><option value="ndvi">属性波段NDVI（需red/nir属性）</option></select></label><label v-if="algorithm==='nearest'" class="field">最大距离（米）<input v-model.number="distance" type="number" min="0" required></label><p v-if="!datasets.length">暂无用户图层，请先上传GeoJSON。</p><p v-if="error" class="vue-error" role="alert">{{error}}</p><button class="btn primary" :disabled="busy || !selected">{{busy?'正在分析…':'运行分析'}}</button></form><article v-if="result" class="panel vue-form"><h2>分析结果</h2><p>结果来源与计算条件已保存。</p><pre>{{JSON.stringify(result.summary,null,2)}}</pre><button class="btn primary" @click="emit('show',result)">成果上图</button><a class="btn" :href="'/api/analysis/'+encodeURIComponent(result.id)+'/result.geojson'" download>下载GeoJSON</a></article></section></template>
