<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { View } from '../types/api'
import type { WorkspaceRuntime } from '../adapters/runtime'
import { useWorkspaceStore } from '../stores/workspace'
const props = defineProps<{ view: View; runtime: WorkspaceRuntime }>()
const host = ref<HTMLElement>(), store = useWorkspaceStore()
let generation = 0
async function mount() {
 const id = ++generation; props.runtime.deactivate(); await nextTick()
 if (id !== generation || !host.value) return
 try { await props.runtime.mount(props.view, host.value) } catch (error) { if (id === generation) store.toast(error instanceof Error ? error.message : '页面暂未加载') }
}
onMounted(mount); watch(() => props.view, mount)
onBeforeUnmount(() => { ++generation; props.runtime.deactivate() })
</script>
<template><section ref="host" class="engine-host" :data-workspace-view="view" /></template>
