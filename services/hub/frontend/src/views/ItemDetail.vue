<template>
  <div v-loading="loading">
    <el-button @click="$router.push('/items')" style="margin-bottom:16px">← 返回列表</el-button>

    <el-alert v-if="msg" :title="msg" :type="msgType" closable @close="msg=''" style="margin-bottom:12px" />

    <el-descriptions v-if="item" :column="3" border title="能力详情" style="margin-bottom:24px">
      <el-descriptions-item label="名称">{{ item.name }}</el-descriptions-item>
      <el-descriptions-item label="类型">{{ item.type }}</el-descriptions-item>
      <el-descriptions-item label="状态">
        <el-tag :type="statusType(item.status)">{{ item.status }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="当前发布版本风险">
        <el-tag :type="riskType(item.risk_level)">{{ item.status === 'published' ? item.risk_level : '未发布' }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="来源">{{ item.source_type }}</el-descriptions-item>
      <el-descriptions-item label="当前版本">{{ item.current_version_id || '无' }}</el-descriptions-item>
      <el-descriptions-item label="描述">{{ item.description || '-' }}</el-descriptions-item>
      <el-descriptions-item label="行业">{{ item.industry || '-' }}</el-descriptions-item>
      <el-descriptions-item label="场景">{{ item.scenario || '-' }}</el-descriptions-item>
      <el-descriptions-item label="可发现">{{ item.discoverable ? '是' : '否' }}</el-descriptions-item>
      <el-descriptions-item label="已有引用可访问">{{ item.allow_existing_references ? '是' : '否' }}</el-descriptions-item>
      <el-descriptions-item label="强制禁用">{{ item.force_disabled ? '是' : '否' }}</el-descriptions-item>
    </el-descriptions>

    <h3 style="margin-bottom:12px">资产操作</h3>
    <p style="color:#909399;margin:0 0 8px 0;font-size:13px">当前 PoC 以版本审核为主。创建版本后，请在版本列表中执行扫描、提交版本审核、审批和发布。</p>
    <el-space style="margin-bottom:24px">
      <el-button :disabled="!canItemAction('createVersion')" @click="createVerVisible = true">创建版本</el-button>
      <el-button type="warning" :disabled="!canItemAction('disable')" @click="doDisable">禁用</el-button>
      <el-button type="danger" :disabled="!canItemAction('archive')" @click="doArchive">归档</el-button>
      <el-button :disabled="!canItemAction('rollback')" @click="rollbackVisible = true">回滚</el-button>
      <el-button :disabled="item?.status !== 'published' || item?.risk_level === 'blocking'" @click="downloadManifest">下载 Manifest</el-button>
      <el-button @click="downloadExport">导出能力包</el-button>
    </el-space>

    <h3 style="margin-bottom:12px">能力关系</h3>
    <div v-if="relations.outgoing.length === 0 && relations.incoming.length === 0" style="color:#909399;margin-bottom:24px">暂无关系</div>
    <div v-else style="margin-bottom:24px">
      <div v-if="relations.outgoing.length > 0">
        <h4 style="margin:8px 0;font-size:14px">出向关系（outgoing）</h4>
        <el-table :data="relations.outgoing" stripe size="small">
          <el-table-column prop="relation_type" label="关系类型" width="120" />
          <el-table-column label="目标" min-width="160">
            <template #default="{row}">
              <template v-if="row.target_item">
                <router-link :to="`/items/${row.target_item.id}`">{{ row.target_item.name }}</router-link>
                <span style="color:#909399;font-size:12px;margin-left:6px">{{ row.target_item.type }}</span>
              </template>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column prop="relation_scope" label="范围" width="110" />
          <el-table-column label="必需" width="70">
            <template #default="{row}">{{ row.required ? '是' : '否' }}</template>
          </el-table-column>
        </el-table>
      </div>
      <div v-if="relations.incoming.length > 0" style="margin-top:12px">
        <h4 style="margin:8px 0;font-size:14px">入向关系（incoming）</h4>
        <el-table :data="relations.incoming" stripe size="small">
          <el-table-column prop="relation_type" label="关系类型" width="120" />
          <el-table-column label="来源" min-width="160">
            <template #default="{row}">
              <template v-if="row.source_item">
                <router-link :to="`/items/${row.source_item.id}`">{{ row.source_item.name }}</router-link>
                <span style="color:#909399;font-size:12px;margin-left:6px">{{ row.source_item.type }}</span>
              </template>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column prop="relation_scope" label="范围" width="110" />
          <el-table-column label="必需" width="70">
            <template #default="{row}">{{ row.required ? '是' : '否' }}</template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <h3 style="margin-bottom:12px">版本列表</h3>
    <el-table :data="versions" stripe>
      <el-table-column prop="version" label="版本" width="100" />
      <el-table-column prop="status" label="状态" width="130">
        <template #default="{row}">
          <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="risk_level" label="风险" width="80">
        <template #default="{row}">
          <el-tag :type="riskType(row.risk_level)">{{ row.risk_level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" min-width="120" />
      <el-table-column label="操作" min-width="400" fixed="right">
        <template #default="{row}">
          <el-button size="small" :disabled="!canVersionAction('scan', row.status)" @click="doScan(row.id)">扫描</el-button>
          <el-button size="small" :disabled="!canVersionAction('submitReview', row.status)" @click="doSubmitReview(row.id)">提交版本审核</el-button>
          <el-button size="small" type="success" :disabled="!canVersionAction('approve', row.status)" @click="doApprove(row.id)">审批通过</el-button>
          <el-button size="small" type="danger" :disabled="!canVersionAction('reject', row.status)" @click="doReject(row.id)">驳回</el-button>
          <el-button size="small" type="warning" :disabled="!canVersionAction('requestChange', row.status)" @click="doRequestChange(row.id)">要求修改</el-button>
          <el-button size="small" type="primary" :disabled="!canVersionAction('publish', row.status)" @click="doPublish(row.id)">发布版本</el-button>
          <el-button size="small" @click="doViewReport(row.id)">查看报告</el-button>
          <el-button size="small" @click="downloadVersionPackage(row.id)">下载包</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createVerVisible" title="创建版本" width="550px">
      <el-form label-width="100px">
        <el-form-item label="版本号">
          <el-input v-model="verForm.version" placeholder="如 1.0.0" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="verForm.description" />
        </el-form-item>
        <el-form-item label="manifest">
          <el-input v-model="verForm.manifest_json" type="textarea" :rows="2" placeholder='{"key":"value"}' />
        </el-form-item>
        <el-form-item label="config">
          <el-input v-model="verForm.config_json" type="textarea" :rows="2" placeholder='{"key":"value"}' />
        </el-form-item>
        <el-form-item label="permission">
          <el-input v-model="verForm.permission_json" type="textarea" :rows="2" placeholder='{"key":"value"}' />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVerVisible = false">取消</el-button>
        <el-button type="primary" @click="doCreateVersion">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rollbackVisible" title="回滚版本" width="400px">
      <el-form label-width="80px">
        <el-form-item label="目标版本">
          <el-select v-model="rollbackTarget" placeholder="选择版本">
            <el-option
              v-for="v in versions"
              :key="v.id"
              :label="v.version"
              :value="v.id"
              :disabled="v.id === item?.current_version_id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rollbackVisible = false">取消</el-button>
        <el-button type="primary" @click="doRollback">确定回滚</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="reportVisible" title="扫描报告" width="700px">
      <div v-if="reportError">
        <p style="color:#909399">{{ reportError }}</p>
      </div>
      <div v-else-if="scanReport">
        <p><strong>风险等级：</strong>
          <el-tag :type="riskType(scanReport.risk_level)">{{ scanReport.risk_level }}</el-tag>
        </p>
        <p><strong>扫描器版本：</strong>{{ scanReport.scanner_version || '-' }}</p>
        <p><strong>摘要：</strong></p>
        <pre style="max-width:100%;overflow:auto;background:#f5f7fa;padding:8px;font-size:12px;max-height:200px">{{ JSON.stringify(scanReport.summary, null, 2) }}</pre>
        <el-table :data="scanReport.findings" v-if="scanReport.findings?.length" stripe style="margin-top:12px">
          <el-table-column prop="risk_type" label="风险类型" width="140" />
          <el-table-column prop="severity" label="严重等级" width="90">
            <template #default="{row}">
              <el-tag :type="riskType(row.severity)">{{ row.severity }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="详情" min-width="200">
            <template #default="{row}">
              {{ row.evidence?.message || '-' }}
            </template>
          </el-table-column>
          <el-table-column prop="recommendation" label="建议" min-width="160" />
        </el-table>
        <p v-else style="margin-top:12px;color:#999">无安全发现</p>
      </div>
      <div v-else>加载中...</div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  fetchItem, fetchVersions, fetchItemRelations,
  scanVersion, getScanReport,
  submitReview, approveVersion, rejectVersion, requestChange, publishVersion,
  disableItem, archiveItem, rollbackItem, createVersion,
} from '../api/hub.js'

const props = defineProps({ id: String })
const route = useRoute()

const item = ref(null)
const versions = ref([])
const loading = ref(false)
const msg = ref('')
const msgType = ref('info')

const createVerVisible = ref(false)
const verForm = reactive({
  version: '', description: '', manifest_json: '', config_json: '', permission_json: '',
})

const rollbackVisible = ref(false)
const rollbackTarget = ref('')

const reportVisible = ref(false)
const scanReport = ref(null)
const reportError = ref('')

const relations = reactive({ outgoing: [], incoming: [] })

function statusType(s) {
  const map = { draft: 'info', pending_review: 'warning', approved: 'success', published: 'success', rejected: 'danger', deprecated: '', archived: '', disabled: 'danger', change_required: 'warning' }
  return map[s] || ''
}

function riskType(r) {
  const map = { low: 'success', medium: 'warning', high: 'danger', blocking: 'danger', critical: 'danger' }
  return map[r] || ''
}

function canItemAction(action) {
  const s = item.value?.status
  const rules = {
    disable: ['published'],
    archive: ['published', 'disabled'],
    rollback: ['published'],
    createVersion: ['draft', 'pending_review', 'published'],
  }
  return rules[action]?.includes(s) ?? false
}

function canVersionAction(action, status) {
  const rules = {
    scan: ['draft', 'change_required', 'pending_review'],
    submitReview: ['draft', 'change_required'],
    approve: ['pending_review'],
    reject: ['pending_review'],
    requestChange: ['pending_review'],
    publish: ['approved'],
  }
  return rules[action]?.includes(status) ?? false
}

function friendlyError(err) {
  const msg = err?.message || String(err)
  if (/invalid state transition/i.test(msg)) return '当前状态不允许该操作'
  if (/not found/i.test(msg) || /404/i.test(msg)) return '资源不存在或已被删除'
  return msg
}

async function refresh() {
  loading.value = true
  try {
    const id = props.id || route.params.id
    const [it, vers, rels] = await Promise.all([
      fetchItem(id),
      fetchVersions(id),
      fetchItemRelations(id).catch(() => ({ outgoing: [], incoming: [] })),
    ])
    item.value = it
    versions.value = vers
    relations.outgoing = rels.outgoing || []
    relations.incoming = rels.incoming || []
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function parseJSON(str) {
  if (!str || !str.trim()) return undefined
  return JSON.parse(str)
}

async function doCreateVersion() {
  try {
    await createVersion(item.value.id, {
      version: verForm.version,
      description: verForm.description || undefined,
      manifest_json: parseJSON(verForm.manifest_json),
      config_json: parseJSON(verForm.config_json),
      permission_json: parseJSON(verForm.permission_json),
    })
    ElMessage.success('版本创建成功')
    createVerVisible.value = false
    verForm.version = ''; verForm.description = ''; verForm.manifest_json = ''; verForm.config_json = ''; verForm.permission_json = ''
    refresh()
  } catch (e) {
    ElMessage.error(friendlyError(e))
  }
}

async function doScan(vid) {
  try {
    const r = await scanVersion(vid)
    ElMessage.success(`扫描完成：${r.risk_level}`)
    refresh()
  } catch (e) { ElMessage.error(friendlyError(e)) }
}

async function doSubmitReview(vid) {
  try { await submitReview(vid); ElMessage.success('已提交审核'); refresh() } catch (e) { ElMessage.error(friendlyError(e)) }
}

async function doApprove(vid) {
  try { await approveVersion(vid); ElMessage.success('审批通过'); refresh() } catch (e) { ElMessage.error(friendlyError(e)) }
}

async function doReject(vid) {
  try {
    const { value: comment } = await ElMessageBox.prompt('驳回意见（可选）', '驳回版本', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputValue: '',
    })
    await rejectVersion(vid, comment || '')
    ElMessage.success('已驳回')
    refresh()
  } catch {
    // user cancelled
  }
}

async function doRequestChange(vid) {
  try { await requestChange(vid); ElMessage.success('已要求修改'); refresh() } catch (e) { ElMessage.error(friendlyError(e)) }
}

async function doPublish(vid) {
  try { await publishVersion(vid); ElMessage.success('发布成功'); refresh() } catch (e) { ElMessage.error(friendlyError(e)) }
}

async function doViewReport(vid) {
  reportVisible.value = true
  scanReport.value = null
  reportError.value = ''
  try {
    scanReport.value = await getScanReport(vid)
  } catch {
    reportError.value = '当前版本暂无扫描报告，请先执行扫描'
  }
}

async function doDisable() {
  try { await disableItem(item.value.id); ElMessage.success('已禁用'); refresh() } catch (e) { ElMessage.error(friendlyError(e)) }
}

async function doArchive() {
  try { await archiveItem(item.value.id); ElMessage.success('已归档'); refresh() } catch (e) { ElMessage.error(friendlyError(e)) }
}

async function doRollback() {
  if (!rollbackTarget.value) { ElMessage.warning('请选择目标版本'); return }
  try {
    await rollbackItem(item.value.id, rollbackTarget.value)
    ElMessage.success('回滚成功')
    rollbackVisible.value = false
    refresh()
  } catch (e) { ElMessage.error(friendlyError(e)) }
}

function downloadManifest() {
  if (item.value?.id) window.open(`/api/runtime/capabilities/${item.value.id}/manifest`, '_blank')
}

function downloadExport() {
  if (item.value?.id) window.open(`/api/hub/exports/items/${item.value.id}`, '_blank')
}

function downloadVersionPackage(versionId) {
  if (item.value?.id) window.open(`/api/hub/exports/items/${item.value.id}/versions/${versionId}/package`, '_blank')
}

onMounted(() => {
  if (props.id || route.params.id) refresh()
})
</script>
