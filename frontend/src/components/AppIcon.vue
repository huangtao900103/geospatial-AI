<script setup lang="ts">
defineProps<{ name: string }>()
const paths: Record<string,string> = { map:'m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6zM9 3v15m6-12v15', layers:'m12 3 10 5-10 5L2 8l10-5zM2 12l10 5 10-5M2 16l10 5 10-5', clock:'M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', data:'M3 6c0-4 18-4 18 0s-18 4-18 0zm0 0v12c0 4 18 4 18 0V6M3 12c0 4 18 4 18 0', grid:'M3 3h6v6H3zM15 3h6v6h-6zM3 15h6v6H3zM15 15h6v6h-6z' }
</script>
<template><svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="paths[name] || paths.layers" /></svg></template>
