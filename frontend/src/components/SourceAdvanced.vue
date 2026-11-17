<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { WorkspaceRuntime } from '../adapters/runtime'
import { useWorkspaceStore } from '../stores/workspace'
const props=defineProps<{runtime:WorkspaceRuntime;tab:'library'|'search'}>(),host=ref<HTMLElement>(),store=useWorkspaceStore()
let generation=0
async function mount(){const id=++generation;await nextTick();if(id!==generation)return;if(host.value)try{await props.runtime.mountSourceAdvanced(props.tab,host.value)}catch(e){if(id===generation)store.toast(e instanceof Error?e.message:'影像资源暂未读取')}}
onMounted(mount);watch(()=>props.tab,mount)
onBeforeUnmount(()=>{++generation})
</script>
<template><section ref="host" class="source-advanced-host"/></template>
