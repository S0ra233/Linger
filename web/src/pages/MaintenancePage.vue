<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errorText, formatTime } from '../api'
import type { SystemInfo } from '../api'

interface BackupItem { name: string; bytes: number; created_at: number }
interface BackupJob {
  id: string; status: string; started_at: number; error: string | null; warning?: string
  result: { name: string; snapshot_at: number; file_count: number; source_bytes: number } | null
}
interface Maintenance {
  job: BackupJob | null; items: BackupItem[]; has_more: boolean
  disk: { total: number; free: number }; database_bytes: number; schema_version: number; key_source: string
}
const props = defineProps<{ refreshKey: number; timezone: string }>()
const data = ref<Maintenance | null>(null), info = ref<SystemInfo | null>(null)
const loading = ref(false), creating = ref(false), error = ref(''), tab = ref('backups')
const active = computed(() => data.value?.job?.status === 'running')
const jobLabels: Record<string, string> = { running: '正在备份', succeeded: '备份完成', failed: '备份失败', interrupted: '备份已中止' }
let timer: number | undefined, disposed = false
function bytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`
  return `${(value / 1024 ** 3).toFixed(2)} GiB`
}
function schedule() {
  window.clearTimeout(timer)
  if (!disposed && active.value) timer = window.setTimeout(() => void load(), 3000)
}
async function load() {
  if (loading.value || disposed) return
  loading.value = true
  try {
    // 两项各自保留真实结果；部署信息失败不掩盖备份结果。
    const results = await Promise.allSettled([
      api<Maintenance>('/maintenance'), api<SystemInfo>('/system/info'),
    ])
    if (disposed) return
    const [maintenance, system] = results
    if (maintenance.status === 'fulfilled') data.value = maintenance.value
    if (system.status === 'fulfilled') info.value = system.value
    error.value = results.filter(result => result.status === 'rejected')
      .map(result => errorText((result as PromiseRejectedResult).reason)).join('；')
  } finally { loading.value = false; schedule() }
}
async function createBackup() {
  if (creating.value || active.value || !data.value) return
  creating.value = true
  try {
    await ElMessageBox.confirm('备份包含聊天、图片和配置，不包含服务器主密钥。是否现在创建？', '创建备份', {
      confirmButtonText: '创建备份', cancelButtonText: '取消', type: 'info',
    })
    const job = await api<BackupJob>('/maintenance/backups', { method: 'POST' })
    if (!disposed && data.value) { data.value.job = job; error.value = ''; ElMessage.success('备份已开始，离开页面仍会继续') }
  } catch (cause) {
    if (cause !== 'cancel' && cause !== 'close' && !disposed) error.value = errorText(cause)
  } finally { creating.value = false; if (!disposed) await load() }
}
watch(() => props.refreshKey, () => void load())
onMounted(load)
onScopeDispose(() => { disposed = true; window.clearTimeout(timer) })
</script>

<template>
  <section class="panel maintenance-page">
    <div class="panel-heading"><h2>部署与维护</h2><el-button :loading="loading" @click="load">刷新</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="form-alert" />
    <el-skeleton v-if="loading && !data" :rows="5" animated />
    <template v-if="data">
      <div class="maintenance-facts">
        <div><span>数据库</span><strong>SQLite v{{ data.schema_version }}</strong></div>
        <div><span>主文件</span><strong>{{ bytes(data.database_bytes) }}</strong></div>
        <div><span>磁盘可用</span><strong>{{ bytes(data.disk.free) }}</strong></div>
        <div><span>主密钥来源</span><strong>{{ data.key_source === 'environment' ? '进程环境' : '独立文件' }}</strong></div>
      </div>
      <el-tabs v-model="tab" class="management-tabs">
        <el-tab-pane label="备份" name="backups">
          <div class="panel-heading"><span>数据库、已引用图片及导入原文</span><el-button type="primary" :loading="creating" :disabled="active || loading" @click="createBackup">创建备份</el-button></div>
          <el-alert title="备份不含主密钥。请另存原主密钥，并保护下载的私人资料。" type="info" :closable="false" class="form-alert" />
          <div v-if="data.job" class="maintenance-job" role="status">
            <el-tag :type="data.job.status === 'succeeded' ? 'success' : data.job.status === 'running' ? 'info' : 'danger'">{{ jobLabels[data.job.status] ?? data.job.status }}</el-tag>
            <span>{{ formatTime(data.job.started_at, timezone) }}</span>
            <span v-if="data.job.result">{{ data.job.result.file_count }} 个文件 · 快照 {{ formatTime(data.job.result.snapshot_at, timezone) }}</span>
            <span v-if="data.job.error" class="error-text">{{ data.job.error }}</span>
            <span v-if="data.job.warning" class="error-text">{{ data.job.warning }}</span>
          </div>
          <el-empty v-if="!data.items.length && !active && !error" description="还没有完整备份" :image-size="70" />
          <div v-if="data.items.length" class="table-scroll"><el-table :data="data.items">
            <el-table-column label="备份" min-width="290"><template #default="{ row }"><span class="backup-name">{{ row.name }}</span></template></el-table-column>
            <el-table-column label="创建时间" min-width="180"><template #default="{ row }">{{ formatTime(row.created_at, timezone) }}</template></el-table-column>
            <el-table-column label="大小" min-width="110"><template #default="{ row }">{{ bytes(row.bytes) }}</template></el-table-column>
            <el-table-column label="操作" width="90"><template #default="{ row }"><a class="inline-link" :href="`/api/v1/maintenance/backups/${encodeURIComponent(row.name)}/download`" download>下载</a></template></el-table-column>
          </el-table></div>
          <p v-if="data.has_more" class="muted">显示最近 50 份；更早备份保留在服务器 data/backups 中。</p>
        </el-tab-pane>
        <el-tab-pane label="部署信息" name="deployment">
          <dl v-if="info" class="detail-list">
            <div><dt>公开地址</dt><dd>{{ info.public_origin }}</dd></div>
            <div><dt>数据目录</dt><dd>{{ info.data_dir }}</dd></div>
            <div><dt>运行环境</dt><dd>{{ info.platform }} · Python {{ info.python }}</dd></div>
            <div><dt>安全 Cookie</dt><dd>{{ info.cookie_secure ? '已启用' : '未启用（本地 HTTP）' }}</dd></div>
            <div><dt>允许来源</dt><dd>{{ info.allowed_origins.join('、') }}</dd></div>
          </dl>
          <el-empty v-else description="部署信息尚未取得，请刷新" :image-size="70" />
          <div class="maintenance-notes"><p>同机：NapCat 通过本机地址或容器网络连接 RP。</p><p>分离：本地 NapCat 使用 QQ 接入页的 WSS 地址连接云端。</p><p>部署文件及操作说明：deploy/、docs/DEPLOYMENT.md。</p></div>
        </el-tab-pane>
        <el-tab-pane label="恢复" name="recovery">
          <div class="maintenance-notes"><p>先停服务，再使用原主密钥恢复到不存在的新目录。原目录保持保留。</p><pre class="maintenance-command">rp-server restore "备份.zip" --target "新数据目录" --key-file "原主密钥文件"</pre><p>恢复后所有生活线暂停，自动运行与后台生活关闭，旧待发计划取消。发送中改为结果未知，需要核对实际 QQ 记录。</p><p>确认后显式切换 RP_DATA_DIR；环境提供密钥时继续使用原 RP_MASTER_KEY。容器操作见部署说明。</p></div>
        </el-tab-pane>
      </el-tabs>
    </template>
  </section>
</template>
