<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../services/api'
import { useWorkspaceStore } from '../stores/workspace'
import type { Entity } from '../types/api'
const emit = defineEmits<{ data: [] }>()
const store = useWorkspaceStore(), selected = ref<Entity | null>(null), endpoint = ref(''), protocol = ref('REST API'), busy = ref(false), error = ref('')
function edit(item: Entity) { selected.value=item;endpoint.value=typeof item.endpoint==='string'?item.endpoint:'';protocol.value=typeof item.protocol==='string'?item.protocol:'REST API';error.value='' }
async function save() {
 if(!selected.value||busy.value)return
 busy.value=true;error.value=''
 try {
  const item=await api<Entity>('/integrations/'+encodeURIComponent(selected.value.id),{method:'POST',body:{endpoint:endpoint.value.trim(),protocol:protocol.value}})
  if(store.boot?.integrations)store.boot.integrations=store.boot.integrations.map(x=>x.id===item.id?{...x,...item}:x)
  selected.value=null;store.toast('接口草案已保存；尚未连接或授权')
 }catch(e){error.value=e instanceof Error?e.message:'保存未完成'}finally{busy.value=false}
}
</script>
<template><section><div class="heading"><div><h1>系统集成</h1><p>地图服务、公开来源和业务接口按授权接入，保存地址不代表已取得数据。</p></div></div><div class="panel vue-form"><h2>数据与服务配置</h2><p>在数据页面管理天地图服务、共用来源、观测资源及接入草案。</p><button class="btn primary" @click="emit('data')">打开数据与服务</button></div><form v-if="selected" class="panel vue-form" @submit.prevent="save"><h2>{{selected.name}} · 接口草案</h2><label class="field">服务地址<input v-model="endpoint" type="url" maxlength="1000" required placeholder="https://example.gov.cn/service"></label><label class="field">协议<select v-model="protocol"><option>REST API</option><option>REST</option><option>OGC WMS / WMTS</option><option>WMS</option><option>WMTS</option><option>GeoJSON</option></select></label><p>地址中不要包含密码、token或密钥。登记不会访问该地址。</p><p v-if="error" class="vue-error" role="alert">{{error}}</p><button class="btn primary" :disabled="busy">{{busy?'保存中…':'保存草案'}}</button><button class="btn" type="button" @click="selected=null">取消</button></form><div class="integration-grid"><article v-for="item in store.boot?.integrations || []" :key="item.id" class="panel vue-form"><h2>{{item.name}}</h2><p>{{item.status}}</p><p>{{item.description}}</p><button class="btn" @click="edit(item)">配置接口草案</button></article></div></section></template>
