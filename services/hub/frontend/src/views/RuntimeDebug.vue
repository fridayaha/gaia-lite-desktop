<template>
  <div>
    <el-button @click="$router.push('/items')" style="margin-bottom:16px">← 返回列表</el-button>

    <h2 style="margin-bottom:16px">Runtime Discover 调试</h2>

    <el-card style="margin-bottom:16px">
      <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center">
        <el-select v-model="filters.type" placeholder="类型" clearable style="width:120px">
          <el-option label="Agent" value="agent" />
          <el-option label="MCP" value="mcp" />
          <el-option label="Skill" value="skill" />
          <el-option label="Tool" value="tool" />
        </el-select>
        <el-input v-model="filters.keyword" placeholder="关键词" clearable style="width:200px" />
        <el-select v-model="filters.risk_level_max" placeholder="最高风险" style="width:120px">
          <el-option label="低" value="low" />
          <el-option label="中" value="medium" />
          <el-option label="高" value="high" />
        </el-select>
        <el-input-number v-model="filters.limit" :min="1" :max="100" style="width:120px" />
        <el-button type="primary" @click="doDiscover">搜索</el-button>
      </div>
    </el-card>

    <el-card v-if="total !== null" style="margin-bottom:16px">
      <template #header>搜索结果（{{ total }}）</template>
      <el-table :data="items" stripe v-loading="discovering" @row-click="doResolve">
        <el-table-column prop="name" label="名称" min-width="160">
          <template #default="{row}">
            <a href="javascript:void(0)" @click="doResolve(row)">{{ row.name }}</a>
          </template>
        </el-table-column>
        <el-table-column prop="type" label="类型" width="80" />
        <el-table-column prop="version" label="版本" width="100" />
        <el-table-column prop="risk_level" label="风险" width="80">
          <template #default="{row}">
            <el-tag :type="riskType(row.risk_level)">{{ row.risk_level }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="resolveResult">
      <template #header>
        解析结果 <span style="color:#909399;font-size:13px">（{{ resolveResult.name }} {{ resolveResult.version }}）</span>
      </template>
      <div v-if="resolveError" style="color:#f56c6c;margin-bottom:12px">{{ resolveError }}</div>
      <pre style="max-width:100%;overflow:auto;background:#f5f7fa;padding:12px;font-size:12px;max-height:500px">{{
        JSON.stringify(resolveResult, null, 2)
      }}</pre>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { discoverCapabilities, resolveCapability } from '../api/hub.js'

const filters = reactive({
  type: '',
  keyword: '',
  risk_level_max: 'high',
  limit: 20,
})

const items = ref([])
const total = ref(null)
const discovering = ref(false)
const resolveResult = ref(null)
const resolveError = ref('')

function riskType(r) {
  const map = { low: 'success', medium: 'warning', high: 'danger', blocking: 'danger' }
  return map[r] || ''
}

async function doDiscover() {
  discovering.value = true
  try {
    const res = await discoverCapabilities({
      type: filters.type || undefined,
      keyword: filters.keyword || undefined,
      risk_level_max: filters.risk_level_max,
      limit: filters.limit,
      offset: 0,
    })
    items.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    discovering.value = false
  }
}

async function doResolve(row) {
  resolveError.value = ''
  try {
    resolveResult.value = await resolveCapability(row.id)
  } catch (e) {
    resolveError.value = e.message
    resolveResult.value = { error: e.message }
  }
}
</script>
