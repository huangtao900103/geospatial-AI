<script setup lang="ts">
import { computed, getCurrentInstance, markRaw, onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import AppIcon from './components/AppIcon.vue'
import EngineWorkspace from './components/EngineWorkspace.vue'
import WorkspaceDialog from './components/WorkspaceDialog.vue'
import DataWorkspace from './components/DataWorkspace.vue'
import ResourceNotice from './components/ResourceNotice.vue'
import VectorAnalysis from './components/VectorAnalysis.vue'
import IntegrationsView from './components/IntegrationsView.vue'
import type { WorkspaceRuntime } from './adapters/runtime'
import { useWorkspaceStore } from './stores/workspace'
import { parseRoute, routeHash } from './services/navigation'
import type { Analysis, MapTab, View } from './types/api'
const appContext=getCurrentInstance()?.appContext
const store = useWorkspaceStore()
function navigate(view: View, tab?: MapTab) {
 store.sidebar=false; store.view=view; if(tab)store.setTab(tab)
 const hash=routeHash(view,view==='map'?store.tab:undefined); if(location.hash!==hash)location.hash=hash
}
const runtime=shallowRef<WorkspaceRuntime>()
const navigation: {label:string;icon:string;view:View;tab?:MapTab}[]=[{label:'地图',icon:'map',view:'map',tab:'map'},{label:'专题分析',icon:'layers',view:'map',tab:'applications'},{label:'任务与成果',icon:'clock',view:'map',tab:'history'},{label:'数据',icon:'data',view:'data'}]
const currentTitle=computed(()=>store.view==='map'?({map:'地图',applications:'专题分析',history:'任务与成果',chat:'分析详情'}[store.tab]):({data:'数据',production:'遥感处理',area:'全市历史核算',vector:'矢量数据分析',integrations:'系统集成'}[store.view]))
function active(item:typeof navigation[number]) {return store.view===item.view && (item.view!=='map'||store.tab===item.tab)}
function reconnect(){if(!runtime.value)location.reload();else void store.initialize()}
function routeChanged(){const route=parseRoute(location.hash);store.view=route.view;if(route.tab)store.setTab(route.tab)}
function keydown(event:KeyboardEvent){if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();store.dialog=null;store.overlay='command'}if(event.key==='Escape')store.sidebar=false}
async function uploaded(id:string){try{await runtime.value?.showDataset(id)}catch(e){store.toast(e instanceof Error?e.message:'数据已上传，上图暂未完成')}}
async function action(name:string,target:HTMLElement){try{await runtime.value?.action(name,target)}catch(e){store.toast(e instanceof Error?e.message:'操作未完成')}}
async function showAnalysis(result:Analysis){try{await runtime.value?.showAnalysis(result)}catch(e){store.toast(e instanceof Error?e.message:'成果上图未完成')}}
watch(()=>store.tab,tab=>{if(store.view==='map')runtime.value?.setTab(tab)})
watch(()=>store.datasets,()=>runtime.value?.refreshUserData())
onMounted(async()=>{routeChanged();window.addEventListener('hashchange',routeChanged);window.addEventListener('keydown',keydown);try{const module=await import('./adapters/runtime');runtime.value=markRaw(new module.WorkspaceRuntime(navigate,appContext));await store.initialize()}catch(e){store.error=e instanceof Error?e.message:'页面模块加载失败'}})
onBeforeUnmount(()=>{window.removeEventListener('hashchange',routeChanged);window.removeEventListener('keydown',keydown);runtime.value?.destroy();store.dispose()})
</script>
<template>
 <a class="skip" href="#main">跳到主要内容</a>
 <aside id="sidebar" class="sidebar" :class="{open:store.sidebar}">
  <a class="brand" href="#/map/applications" aria-label="时空智能工作台"><span class="brand-mark"><svg viewBox="0 0 42 42"><path d="M21 3 39 13 21 23 3 13Z M3 21 21 31 39 21 M3 29 21 39 39 29" fill="none" stroke="currentColor" stroke-width="2.5"/></svg></span><span>时空智能<b>工作台</b><small>地图 · 数据 · 分析</small></span></a>
  <nav id="nav" aria-label="主导航"><button v-for="item in navigation" :key="item.label" class="nav-link" :class="{active:active(item)}" :aria-current="active(item)?'page':undefined" @click="navigate(item.view,item.tab)"><AppIcon :name="item.icon"/><span>{{item.label}}</span></button><div class="nav-spacer"/><button class="nav-link nav-professional" @click="store.overlay='tools'"><AppIcon name="grid"/><span>专业工具</span></button></nav>
 </aside>
 <button v-if="store.sidebar" class="vue-nav-backdrop" aria-label="关闭导航" @click="store.sidebar=false"/>
 <div class="shell"><header class="topbar"><div class="topbar-left"><button class="icon-button menu-toggle" aria-label="打开导航" :aria-expanded="store.sidebar" aria-controls="sidebar" @click="store.sidebar=!store.sidebar">☰</button><a class="topbar-brand" href="#/map/applications"><span>时空智能</span><b>工作台</b><small>地图 · 数据 · 分析</small></a><span class="breadcrumb">工作空间 <span>/</span> <b>{{currentTitle}}</b></span></div><div class="topbar-right"><button class="command-trigger" aria-label="查找功能" @click="store.overlay='command'">⌕ <span>查找功能</span><kbd>⌘ K</kbd></button><span class="environment"><i/>本地工作空间</span><button class="icon-button" aria-label="使用指南" @click="store.overlay='guide'">?</button><span class="topbar-divider"/><span class="avatar small-avatar">林</span></div></header>
 <main id="main" tabindex="-1"><section v-if="!store.ready" class="initial-loading"><span v-if="store.loading" class="spinner"/><h2>{{store.error?'工作空间暂未连接':'正在打开工作台'}}</h2><p>{{store.error||'加载数据目录、任务与应用配置…'}}</p><button v-if="store.error" class="btn primary" @click="reconnect">重新连接</button></section><template v-else><div v-if="store.error" class="workspace-refresh-error" role="alert">{{store.error}}；已有内容保留。<button class="btn" @click="reconnect">重新连接</button></div><details v-if="store.view!=='data' && store.issues.length" class="workspace-resource-issues"><summary>{{store.issues.length}}类资源暂未更新 · 查看与重试</summary><ResourceNotice v-for="key in store.issues" :key="key" :resource="key"/></details><DataWorkspace v-if="store.view==='data' && runtime" :runtime="runtime"/><VectorAnalysis v-else-if="store.view==='vector'" @show="showAnalysis"/><IntegrationsView v-else-if="store.view==='integrations'" @data="navigate('data')"/><EngineWorkspace v-else-if="runtime" :view="store.view" :runtime="runtime"/></template></main>
 <footer><span><i class="status-dot"/><span id="service-status">{{store.error?'服务暂未连接':store.ready?'本地服务运行正常':'本地服务连接中'}}</span><span class="footer-dot">·</span>来源与适用范围见成果说明</span><span>时空智能工作台 <b>2026</b></span></footer></div>
 <div id="toast" class="toast" :class="{show:!!store.notice}" role="status" aria-live="polite">{{store.notice}}</div>
 <WorkspaceDialog @navigate="navigate" @uploaded="uploaded" @action="action"/>
</template>
