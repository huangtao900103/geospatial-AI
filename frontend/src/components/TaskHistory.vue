<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../services/api'
import { atlasCatalog, list, question, analysis, isRecord, ContractError } from '../services/contracts'
import type { Analysis, AtlasSummary, QuestionSummary } from '../types/api'
const props=defineProps<{active:boolean}>()
const emit=defineEmits<{question:[id:string];atlas:[id:string,version:string];scene:[result:Analysis]}>()
const search=ref(''),filter=ref('all'),questions=ref<QuestionSummary[]>([]),products=ref<AtlasSummary[]>([]),scenes=ref<Analysis[]>([])
const loading=ref(false),errors=ref<string[]>([]),updated=ref(''),records=ref<number>(),everLoaded=ref(false)
let version=0,controller:AbortController|undefined,disposed=false
const labels:Record<string,string>={processing:'处理中',answered:'已完成',needs_data:'待补条件',unsupported:'暂不支持',failed:'未完成',cancelled:'已取消'}
const match=(value:string)=>value.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())
const filteredQuestions=computed(()=>questions.value.filter(q=>(filter.value==='all'||filter.value==='active'&&q.status==='processing'||filter.value==='complete'&&q.status==='answered'||filter.value==='issues'&&['needs_data','unsupported','failed','cancelled'].includes(q.status))&&match([q.question,q.application_ref?.name,q.answer?.title,q.answer?.source_year].join(' '))))
const filteredProducts=computed(()=>['all','complete'].includes(filter.value)?products.value.filter(p=>match([p.title,p.period_label,p.scope_label].join(' '))):[])
const filteredScenes=computed(()=>['all','complete'].includes(filter.value)?scenes.value.filter(s=>match([s.name,s.id].join(' '))):[])
function date(value?:string){return value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'时间未提供'}
async function refresh(){
 if(disposed||!props.active)return
 const id=++version;controller?.abort();controller=new AbortController();loading.value=true
 const state=await Promise.allSettled(['/assistant/questions','/agriculture-atlas','/scene-workflows'].map(path=>api<unknown>(path,{signal:controller?.signal})))
 if(disposed||id!==version)return
 const failures:string[]=[]
 state.forEach((response,index)=>{
  try{
   if(response.status==='rejected')throw response.reason
   if(index===0)questions.value=list(response.value,question,'问答任务')
   else if(index===1){const catalog=atlasCatalog(response.value);products.value=catalog.products;records.value=catalog.production_record_count}
   else{if(!isRecord(response.value))throw new ContractError('台账任务');scenes.value=list(response.value.runs,analysis,'台账任务')}
  }catch(e){failures.push(['分析任务','区域成果','台账任务'][index]+'：'+(e instanceof Error?e.message:'读取失败'))}
 })
 errors.value=failures;everLoaded.value=true;loading.value=false
 if(!failures.length)updated.value=new Date().toISOString()
}
watch(()=>props.active,value=>{if(value)void refresh();else{++version;controller?.abort();loading.value=false}})
onMounted(()=>{if(props.active)void refresh()})
const timer=setInterval(()=>{if(props.active&&!loading.value&&questions.value.some(q=>q.status==='processing'))void refresh()},2500)
onBeforeUnmount(()=>{disposed=true;++version;controller?.abort();clearInterval(timer)})
</script>
<template><section class="vue-task-history"><header><h3>任务与成果</h3><p>查找分析任务、台账统计与按版本归档的区域成果。</p></header><div class="task-search-row"><label class="sr-only" for="vue-task-search">查找任务与成果</label><input id="vue-task-search" v-model="search" type="search" placeholder="按名称、地区或年份查找"><button class="btn" :disabled="loading" @click="refresh">{{loading?'更新中…':'刷新任务'}}</button></div><div class="task-filter-row" role="group" aria-label="任务状态"><button v-for="item in [{id:'all',label:'全部'},{id:'active',label:'进行中'},{id:'complete',label:'已完成'},{id:'issues',label:'需处理'}]" :key="item.id" :aria-pressed="filter===item.id" @click="filter=item.id">{{item.label}}</button></div><div v-if="errors.length" class="resource-notice failed" role="alert"><div><b>部分任务暂未更新，已有内容保留</b><p v-for="error in errors" :key="error">{{error}}</p></div></div><small v-if="updated" class="task-updated">上次完整读取 {{date(updated)}}（北京时间）</small><section v-if="filteredProducts.length" class="vue-history-group"><h4>区域成果 · {{filteredProducts.length}}个视图</h4><p v-if="records!==undefined">目录共{{records}}份生产记录；不同视图可能共享原生源。</p><button v-for="p in filteredProducts" :key="p.id" class="vue-history-row" @click="emit('atlas',p.id,p.product_sha256)"><b>{{p.title}}</b><small>{{p.period_label}} · {{p.scope_label}}</small><small v-if="p.quality?.status==='below_target'" class="source-blocker">低覆盖参考：{{p.quality.warning}}</small><span>{{p.layer_count===0?'查看资料':'查看成果与地图'}}</span></button></section><section v-if="filteredScenes.length" class="vue-history-group"><h4>台账与核查</h4><button v-for="s in filteredScenes" :key="s.id" class="vue-history-row" @click="emit('scene',s)"><b>{{s.name || s.id}}</b><span>查看地图</span></button></section><section v-if="filteredQuestions.length" class="vue-history-group"><h4>分析任务 · {{filteredQuestions.length}}</h4><button v-for="q in filteredQuestions" :key="q.id" class="vue-history-row" @click="emit('question',q.id)"><b>{{q.application_ref?.name || q.question}}</b><small v-if="q.application_ref">{{q.question}}</small><small>{{date(q.created_at)}}（北京时间） · {{labels[q.status]}}</small></button></section><p v-if="everLoaded&&!errors.length&&!filteredProducts.length&&!filteredQuestions.length&&!filteredScenes.length" class="vue-data-empty">没有匹配任务或成果，可调整筛选或开始新的分析。</p></section></template>
