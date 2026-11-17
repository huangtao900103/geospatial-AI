<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useWorkspaceStore } from '../stores/workspace'
import type { MapTab, View } from '../types/api'
import UploadDialog from './UploadDialog.vue'
import CommandPalette from './CommandPalette.vue'
const emit = defineEmits<{ navigate: [view: View, tab?: MapTab]; uploaded: [id: string]; action: [name: string, target: HTMLElement] }>()
const store = useWorkspaceStore(), element = ref<HTMLDialogElement>()
let previousFocus: HTMLElement | null = null
const title = computed(() => store.dialog?.title || ({upload:'上传数据',command:'查找功能',tools:'专业工具',guide:'使用指南'}[store.overlay || 'guide']))
function close() { store.dialog = null; store.overlay = null }
const shown = computed(() => !!store.dialog || !!store.overlay)
watch(shown, async (value) => {
 await nextTick(); const dialog = element.value; if (!dialog) return
 if (value && !dialog.open) { previousFocus = document.activeElement as HTMLElement; dialog.showModal(); dialog.querySelector<HTMLElement>('input,select,textarea,button')?.focus() }
 if (!value && dialog.open) { dialog.close(); previousFocus?.focus() }
})
function click(event: MouseEvent) {
 const target = event.target as HTMLElement
 const action = target.closest<HTMLElement>('[data-action]'); if (action) { event.preventDefault(); if (action.dataset.action === 'close-dialog') close(); else emit('action', action.dataset.action || '', action) }
 if (target === element.value) { const box = target.getBoundingClientRect(); if (event.clientX<box.left || event.clientX>box.right || event.clientY<box.top || event.clientY>box.bottom) close() }
}
function navigate(view: View, tab?: MapTab) { close(); emit('navigate', view, tab) }
onBeforeUnmount(() => { element.value?.close() })
</script>
<template><dialog id="dialog" ref="element" aria-labelledby="dialog-title" @cancel.prevent="close" @close="close" @click="click"><button class="dialog-close" aria-label="关闭弹窗" @click="close">×</button><h2 id="dialog-title">{{title}}</h2><template v-if="store.dialog"><div v-html="store.dialog.html"/><div v-if="store.dialog.actions" class="dialog-actions" v-html="store.dialog.actions"/></template><UploadDialog v-else-if="store.overlay==='upload'" @uploaded="id=>emit('uploaded',id)"/><CommandPalette v-else-if="store.overlay==='command'" @navigate="navigate"/><div v-else-if="store.overlay==='tools'" class="professional-tools-grid"><button @click="navigate('production')">遥感处理</button><button @click="navigate('area')">全市历史核算</button><button @click="navigate('vector')">矢量数据分析</button><button @click="navigate('data')">数据与服务配置</button><button @click="navigate('integrations')">系统集成</button></div><section v-else-if="store.overlay==='guide'" class="vue-guide"><p>在一张图中定位范围、分析数据并查看结果。</p><ol><li>搜索地点并明确选择分析范围。</li><li>选择行业专题，或输入包含区域、年份和目标的需求。</li><li>查看任务状态、有效覆盖和定量结果。</li><li>成果上图后可核查像元、查看来源并下载。</li></ol><p>公开年度分类用于历史参考，底图不参与光谱或面积计算。</p><button class="btn primary" @click="navigate('map','applications')">开始分析</button></section></dialog></template>
