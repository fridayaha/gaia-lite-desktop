<template>
  <div>
    <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-bottom:16px">
      <el-input v-model="filters.keyword" placeholder="关键词" clearable style="width:200px" />
      <el-select v-model="filters.type" placeholder="类型" clearable style="width:120px">
        <el-option label="Agent" value="agent" />
        <el-option label="MCP" value="mcp" />
        <el-option label="Skill" value="skill" />
        <el-option label="Tool" value="tool" />
      </el-select>
      <el-select v-model="filters.status" placeholder="状态" clearable style="width:130px">
        <el-option label="草稿" value="draft" />
        <el-option label="待审核" value="pending_review" />
        <el-option label="已发布" value="published" />
        <el-option label="已驳回" value="rejected" />
        <el-option label="已禁用" value="disabled" />
        <el-option label="已归档" value="archived" />
      </el-select>
      <el-select v-model="filters.risk_level" placeholder="风险等级" clearable style="width:130px">
        <el-option label="低" value="low" />
        <el-option label="中" value="medium" />
        <el-option label="高" value="high" />
        <el-option label="阻断" value="blocking" />
      </el-select>
      <el-select v-model="filters.source_type" placeholder="来源" clearable style="width:120px">
        <el-option label="预置" value="preset" />
        <el-option label="手动" value="manual" />
        <el-option label="上传" value="upload" />
      </el-select>
      <el-button type="primary" @click="fetchData">搜索</el-button>
      <el-button @click="createVisible = true">创建能力</el-button>
      <el-button @click="initPresetsData">初始化预置</el-button>
      <el-button @click="triggerImport" :loading="importing">导入能力包</el-button>
      <input
        ref="fileInput"
        type="file"
        accept=".json,.yaml,.yml,.zip"
        style="display:none"
        @change="onFileSelected"
      />
    </div>

    <el-alert v-if="msg" :title="msg" :type="msgType" closable @close="msg=''" style="margin-bottom:12px" />

    <el-table :data="items" stripe v-loading="loading">
      <el-table-column prop="name" label="名称" min-width="180">
        <template #default="{row}">
          <router-link :to="`/items/${row.id}`">{{ row.name }}</router-link>
        </template>
      </el-table-column>
      <el-table-column prop="type" label="类型" width="80" />
      <el-table-column prop="status" label="状态" width="110">
        <template #default="{row}">
          <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="risk_level" label="风险" width="80">
        <template #default="{row}">
          <el-tag :type="riskType(row.risk_level)">{{ row.risk_level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="source_type" label="来源" width="80" />
      <el-table-column prop="discoverable" label="可发现" width="80">
        <template #default="{row}">{{ row.discoverable ? '是' : '否' }}</template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="page"
      v-model:page-size="limit"
      :total="total"
      :page-sizes="[10,20,50]"
      layout="total,sizes,prev,pager,next"
      @change="fetchData"
      style="margin-top:16px;justify-content:flex-end"
    />

    <el-dialog v-model="createVisible" title="创建能力" width="500px">
      <el-form :model="createForm" label-width="90px">
        <el-form-item label="名称">
          <el-input v-model="createForm.name" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="createForm.type">
            <el-option label="Agent" value="agent" />
            <el-option label="MCP" value="mcp" />
            <el-option label="Skill" value="skill" />
            <el-option label="Tool" value="tool" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createForm.description" type="textarea" />
        </el-form-item>
        <el-form-item label="行业">
          <el-input v-model="createForm.industry" />
        </el-form-item>
        <el-form-item label="场景">
          <el-input v-model="createForm.scenario" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="doCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchItems, createItem, initPresets, importPackage } from '../api/hub.js'

const items = ref([])
const total = ref(0)
const loading = ref(false)
const page = ref(1)
const limit = ref(20)
const msg = ref('')
const msgType = ref('info')
const fileInput = ref(null)
const importing = ref(false)

const filters = reactive({
  keyword: '', type: '', status: '', risk_level: '', source_type: '',
})

const createVisible = ref(false)
const createForm = reactive({
  name: '', type: 'tool', description: '', industry: '', scenario: '',
})

function statusType(s) {
  const map = { draft: 'info', pending_review: 'warning', published: 'success', rejected: 'danger', disabled: 'danger', archived: '' }
  return map[s] || ''
}

function riskType(r) {
  const map = { low: 'success', medium: 'warning', high: 'danger', blocking: 'danger' }
  return map[r] || ''
}

async function fetchData() {
  loading.value = true
  try {
    const params = {
      skip: (page.value - 1) * limit.value,
      limit: limit.value,
      ...filters,
    }
    const res = await fetchItems(params)
    items.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function doCreate() {
  try {
    await createItem({ ...createForm, source_type: 'manual' })
    ElMessage.success('创建成功')
    createVisible.value = false
    createForm.name = ''; createForm.description = ''; createForm.industry = ''; createForm.scenario = ''
    fetchData()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function initPresetsData() {
  try {
    const res = await initPresets()
    ElMessage.success(`预置初始化完成：创建 ${res.created}，跳过 ${res.skipped}`)
    fetchData()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function triggerImport() {
  fileInput.value?.click()
}

async function onFileSelected(e) {
  const file = e.target.files?.[0]
  if (!file) return
  importing.value = true
  try {
    const res = await importPackage(file)
    const warnings = res.warnings || []
    if (warnings.length > 0) {
      ElMessage.warning(`导入成功（${res.name} ${res.version}），但有 ${warnings.length} 条警告，详见浏览器控制台`)
      console.warn('Import warnings:', warnings)
    } else {
      ElMessage.success(`导入成功：${res.name} ${res.version}（${res.status}）`)
    }
    fetchData()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    importing.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

onMounted(fetchData)
</script>
