<script setup lang="ts">
import { computed } from 'vue'
import { useWorkspaceStore, resourceNames } from '../stores/workspace'
import type { ResourceKey } from '../types/api'
const props=defineProps<{resource:ResourceKey}>(),store=useWorkspaceStore()
const state=computed(()=>store.resources[props.resource])
const time=computed(()=>state.value.updatedAt?new Date(state.value.updatedAt).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'')
</script>
<template><div v-if="state.status==='error' || state.status==='loading'" class="resource-notice" :class="{failed:state.status==='error'}" :role="state.status==='error'?'alert':'status'"><div><b>{{resourceNames[resource]}}{{state.status==='loading'?'正在更新':'暂未更新'}}</b><p v-if="state.status==='error'">{{state.error}}</p><small v-if="time">显示上次读取内容 · {{time}}（北京时间）</small><small v-else>{{state.status==='loading'?'正在读取，请稍候。':'未取得数据，不能据此判断没有记录。'}}</small></div><button v-if="state.status==='error'" class="btn" @click="store.refreshResource(resource)">重试{{resourceNames[resource]}}</button></div></template>
