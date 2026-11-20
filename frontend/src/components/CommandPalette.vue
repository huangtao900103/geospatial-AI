<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MapTab, View } from '../types/api'
const emit = defineEmits<{ navigate: [view: View, tab?: MapTab] }>()
const query = ref('')
const commands: { name: string; hint: string; view: View; tab?: MapTab }[] = [
 {name:'农业业务',hint:'台账建档、防灾核查、任务归档与生产指导',view:'map',tab:'operations'},
 {name:'地图',hint:'搜索地点、选择范围与查看成果',view:'map',tab:'map'}, {name:'专题分析',hint:'按行业场景开展分析',view:'map',tab:'applications'}, {name:'任务与成果',hint:'查找已完成的分析与下载',view:'map',tab:'history'}, {name:'数据来源与上传',hint:'我的数据、公开来源与观测资产',view:'data'}, {name:'遥感处理',hint:'原生影像与光谱指数',view:'production'}, {name:'矢量数据分析',hint:'用户图层空间统计',view:'vector'}, {name:'全市历史核算',hint:'2021分类产品与仙桃参考界',view:'area'}]
const filtered = computed(() => commands.filter(c => (c.name+c.hint).includes(query.value.trim())))
</script>
<template><label class="sr-only" for="command-search">查找功能</label><input id="command-search" v-model="query" autofocus placeholder="搜索地图、数据或分析功能" @keydown.enter.prevent="filtered[0] && emit('navigate', filtered[0].view, filtered[0].tab)"><div class="vue-command-results"><button v-for="item in filtered" :key="item.name" class="command-item" @click="emit('navigate', item.view, item.tab)"><b>{{item.name}}</b><span>{{item.hint}}</span></button><p v-if="!filtered.length">没有匹配功能，请换个关键词。</p></div></template>
