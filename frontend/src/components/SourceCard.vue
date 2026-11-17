<script setup lang="ts">
import { computed } from 'vue'
import type { DataSource } from '../types/api'
const props=defineProps<{source:DataSource}>();const emit=defineEmits<{detail:[id:string]}>()
const state=computed(()=>props.source.stages)
const years=computed(()=>props.source.coverage?.temporal)
const resolution=computed(()=>props.source.resolution?.description || '以提供方文件为准')
const coverage=computed(()=>props.source.coverage?.spatial?.scope_note || '以具体范围实际读取为准')
const license=computed(()=>props.source.license?.name==='not_verified'?'许可待核实':props.source.license?.name || '按提供方许可')
</script>
<template><article class="vue-source-card"><header><div><small>{{source.provider || '提供方待确认'}}</small><h2>{{source.name}}</h2></div><span class="source-status" :class="{ready:state.analysis_ready}">{{state.analysis_ready?'具备分析适配':state.catalog_searchable?'目录可检索':'仅登记 · 待接入'}}</span></header><p>{{source.semantics?.join(' · ') || '数据用途见来源详情'}}</p><dl><div><dt>覆盖范围</dt><dd>{{coverage}}</dd></div><div><dt>声明年份</dt><dd>{{years?.available_years?.join('、') || years?.product_year || (years?.start ? years.start+'至'+(years.end||'持续更新'):'按目录或文件时间核实')}}</dd></div><div v-if="source.platform_shared"><dt>已适配年份</dt><dd>{{years?.connected_years?.join('、') || '尚未接通'}}</dd></div><div><dt>分辨率</dt><dd>{{resolution}}</dd></div><div><dt>许可</dt><dd>{{license}}{{source.license?.verified?'':' · 使用条件待核实'}}</dd></div></dl><p v-if="source.platform_shared" class="shared-source-note"><b>共用数据源 · 按需调用</b><br>提交分析后读取所需范围；收录年份不代表所有年份均可分析。</p><p v-if="source.integration?.blocker" class="source-blocker">{{source.integration.blocker}}</p><footer><span>目录{{state.catalog_searchable?'可检索':'未接 / 不适用'}} · 像元{{state.pixel_applicable===false?'不适用':state.pixel_readable?'可读取':'未接通'}}</span><button class="btn" @click="emit('detail',source.id)">数据详情与边界</button></footer></article></template>
