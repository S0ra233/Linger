<script setup lang="ts">
import { mayLeave } from '../ui-state'
async function reload() { if (await mayLeave()) window.location.reload() }
</script>
<template>
  <section class="panel" role="alert">
    <h2>页面资源加载失败</h2>
    <p>请检查连接；服务更新后可能需要重新加载。</p>
    <el-button @click="reload">重新加载</el-button>
  </section>
</template>
