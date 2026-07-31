<script setup lang="ts">
/**
 * SkillConfigPanel — 技能元数据 + config_params 配置弹窗。
 *
 * 两段：
 *  ① 元数据：name/version/type/engine/author/icon/industries/description → manifest.json
 *  ② 配置项 (config_params)：声明编辑器（name/label/type/secret/options）→ manifest.json；
 *     值录入（密钥→密码框+已配置徽章，加密存储；非密钥→输入框/select，明文存）。
 *
 * 保存时一次写 manifest.json（含 config_params）+ PUT config（值）。
 * 非密钥值变更后端触发 debug reload（重新镜像 + ${config.param} 替换）。
 */
import { ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  readFileApi,
  writeFileApi,
  listFilesApi,
  getConfigApi,
  saveConfigApi,
} from "@/api/manager/skill-engine";

defineOptions({ name: "SkillConfigPanel" });

const props = defineProps<{ modelValue: boolean; workspaceId: string }>();
const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "saved", payload: { skillChanged: boolean }): void;
}>();

const open = ref(props.modelValue);
watch(() => props.modelValue, (v) => {
  open.value = v;
  if (v) void load();
});
watch(open, (v) => emit("update:modelValue", v));

const loading = ref(false);
const saving = ref(false);
const hasManifest = ref(false);
// 完整 manifest（保存时与表单合并，保留其它字段）
let manifestFull: Record<string, any> = {};

const DEFAULTS = {
  name: "",
  version: "0.1.0",
  description: "",
  type: "skill",
  engine: "hermes",
  author: "",
  icon: "",
  industries: "",
};
const form = ref({ ...DEFAULTS });
const engineOptions = ["hermes", "openclaw"];

// ── config_params ──
type Param = {
  name: string;
  label: string;
  type: "string" | "number" | "boolean" | "select";
  secret: boolean;
  optionsStr: string; // 逗号分隔，仅 select 用
};
const params = ref<Param[]>([]);
const paramTypeOptions = ["string", "number", "boolean", "select"];
// 非密钥值（明文，prefill from getConfigApi）
const nonSecretValues = ref<Record<string, string>>({});
// 密钥输入（密码框，空 = 不改）
const secretInputs = ref<Record<string, string>>({});
// 已配置的密钥 key 集合（显「已配置」徽章）
const configuredSecrets = ref<Set<string>>(new Set());

function addParam() {
  params.value.push({ name: "", label: "", type: "string", secret: false, optionsStr: "" });
}
function removeParam(i: number) {
  params.value.splice(i, 1);
}

async function load() {
  loading.value = true;
  let exists = false;
  try {
    const tree = await listFilesApi(props.workspaceId);
    exists = (tree.files || []).some((f) => f.path === "manifest.json");
  } catch {
    /* list 失败回退到直接读 */
  }
  if (exists) {
    try {
      const res = await readFileApi(props.workspaceId, "manifest.json");
      manifestFull = JSON.parse(res.content);
      hasManifest.value = true;
      const industriesArr = Array.isArray(manifestFull.industries)
        ? manifestFull.industries
        : [];
      form.value = {
        name: manifestFull.name ?? DEFAULTS.name,
        version: manifestFull.version ?? DEFAULTS.version,
        description: manifestFull.description ?? DEFAULTS.description,
        type: manifestFull.type ?? DEFAULTS.type,
        engine: manifestFull.engine ?? DEFAULTS.engine,
        author: manifestFull.author ?? DEFAULTS.author,
        icon: manifestFull.icon ?? DEFAULTS.icon,
        industries: industriesArr.join(", "),
      };
      // config_params 声明
      params.value = (manifestFull.config_params ?? []).map((p: any) => ({
        name: p.name ?? "",
        label: p.label ?? "",
        type: p.type ?? "string",
        secret: p.secret === true,
        optionsStr: Array.isArray(p.options) ? p.options.join(", ") : "",
      }));
    } catch {
      manifestFull = {};
      hasManifest.value = false;
      form.value = { ...DEFAULTS };
      params.value = [];
    }
  } else {
    manifestFull = {};
    hasManifest.value = false;
    form.value = { ...DEFAULTS };
    params.value = [];
  }

  // 拉 config 值状态（密钥不返明文）
  nonSecretValues.value = {};
  secretInputs.value = {};
  configuredSecrets.value = new Set();
  try {
    const cfg = await getConfigApi(props.workspaceId);
    nonSecretValues.value = Object.fromEntries(
      Object.entries(cfg.configValues || {}).map(([k, v]) => [k, String(v)]),
    );
    configuredSecrets.value = new Set(cfg.configured || []);
  } catch {
    /* 未配置或接口不可用，忽略 */
  }
  loading.value = false;
}

async function onSave() {
  // 校验 config_params：name 不为空、不重复
  const named = params.value.filter((p) => p.name.trim());
  const names = named.map((p) => p.name.trim());
  if (new Set(names).size !== names.length) {
    ElMessage.warning("配置项 name 不可重复");
    return;
  }
  saving.value = true;
  try {
    // industries / icon 处理
    const industriesArr = form.value.industries
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const { industries: _industries, icon: _icon, ...rest } = form.value;
    const merged: Record<string, any> = { ...manifestFull, ...rest };
    if (form.value.icon.trim()) merged.icon = form.value.icon.trim();
    else delete merged.icon;
    if (industriesArr.length) merged.industries = industriesArr;
    else delete merged.industries;

    // config_params 声明写入 manifest
    merged.config_params = named.map((p) => {
      const out: Record<string, any> = {
        name: p.name.trim(),
        label: p.label.trim() || p.name.trim(),
        type: p.type,
      };
      if (p.secret) out.secret = true;
      if (p.type === "select") {
        out.options = p.optionsStr.split(",").map((s) => s.trim()).filter(Boolean);
      }
      return out;
    });

    await writeFileApi(props.workspaceId, "manifest.json", JSON.stringify(merged, null, 2));
    manifestFull = merged;
    hasManifest.value = true;

    // 保存 config 值（非密钥 + 密钥）
    const config: Record<string, unknown> = {};
    const credentials: Record<string, string> = {};
    for (const p of named) {
      const key = p.name.trim();
      if (p.secret) {
        if (secretInputs.value[key]) credentials[key] = secretInputs.value[key];
      } else {
        // 非密钥：提交当前录入值（空字符串也提交，由后端 type 校验）
        if (nonSecretValues.value[key] !== undefined) {
          config[key] = nonSecretValues.value[key];
        }
      }
    }
    if (Object.keys(config).length || Object.keys(credentials).length) {
      try {
        const res = await saveConfigApi(props.workspaceId, { config, credentials });
        configuredSecrets.value = new Set(res.configured || []);
      } catch (e: any) {
        // config 保存失败不阻断 manifest 保存，但提示
        ElMessage.error(e?.message || "配置值保存失败，请重试");
      }
    }
    // 清空密钥输入框（已保存或未改）
    secretInputs.value = {};

    ElMessage.success("已保存");
    open.value = false;
    emit("saved", { skillChanged: false });
  } catch {
    ElMessage.error("保存失败，请重试");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <el-dialog
    v-model="open"
    title="技能配置"
    width="640px"
    :close-on-click-modal="false"
    append-to-body
  >
    <div v-loading="loading">
      <el-form :model="form" label-width="72px" label-position="right">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="技能名称，如 chart-skill" />
        </el-form-item>
        <el-form-item label="版本">
          <el-input v-model="form.version" placeholder="0.1.0" />
        </el-form-item>
        <el-form-item label="类型">
          <el-input v-model="form.type" disabled title="技能类型固定为 skill" />
        </el-form-item>
        <el-form-item label="引擎">
          <el-select v-model="form.engine">
            <el-option v-for="e in engineOptions" :key="e" :label="e" :value="e" />
          </el-select>
        </el-form-item>
        <el-form-item label="作者">
          <el-input v-model="form.author" placeholder="技能作者，如 skilldev" />
        </el-form-item>
        <el-form-item label="图标">
          <el-input v-model="form.icon" placeholder="留空用默认图标，如 ri:bar-chart-2-line" />
        </el-form-item>
        <el-form-item label="行业">
          <el-input v-model="form.industries" placeholder="多个用逗号分隔，如 零售, 金融" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="技能用途 / 触发场景" />
        </el-form-item>
      </el-form>

      <el-divider content-position="left">配置项 (config_params)</el-divider>
      <div class="cfg-hint">
        声明技能需要的配置/密钥。密钥（secret:true）加密存储，运行时技能脚本通过
        <code>http://localhost:8004/secret?skill=&lt;name&gt;&amp;key=&lt;param&gt;</code> 取明文；
        非密钥在 SKILL.md 里用 <code>${'$'}{config.param}</code> 引用。
      </div>

      <div v-for="(p, i) in params" :key="i" class="param-row">
        <el-input v-model="p.name" placeholder="name" class="param-name" />
        <el-input v-model="p.label" placeholder="label" class="param-label" />
        <el-select v-model="p.type" class="param-type">
          <el-option v-for="t in paramTypeOptions" :key="t" :label="t" :value="t" />
        </el-select>
        <el-tooltip content="密钥：加密存储，运行时解密" placement="top">
          <el-switch v-model="p.secret" class="param-secret" />
        </el-tooltip>
        <el-button text type="danger" @click="removeParam(i)">删</el-button>
        <el-input
          v-if="p.type === 'select'"
          v-model="p.optionsStr"
          placeholder="选项，逗号分隔"
          class="param-options"
        />
      </div>
      <el-button text type="primary" @click="addParam">+ 新增配置项</el-button>

      <!-- 值录入 -->
      <template v-if="params.some((p) => p.name.trim())">
        <el-divider content-position="left">值</el-divider>
        <el-form label-width="120px" label-position="right">
          <template v-for="(p, i) in params.filter((x) => x.name.trim())" :key="'v' + i">
            <el-form-item :label="p.label || p.name">
              <div class="value-row">
                <el-input
                  v-if="p.secret"
                  v-model="secretInputs[p.name.trim()]"
                  type="password"
                  show-password
                  :placeholder="configuredSecrets.has(p.name.trim()) ? '已配置（留空不改）' : '输入密钥值'"
                  class="value-input"
                />
                <el-select
                  v-else-if="p.type === 'select'"
                  v-model="nonSecretValues[p.name.trim()]"
                  :placeholder="`选择 ${p.label || p.name}`"
                  class="value-input"
                >
                  <el-option
                    v-for="opt in p.optionsStr.split(',').map((s) => s.trim()).filter(Boolean)"
                    :key="opt"
                    :label="opt"
                    :value="opt"
                  />
                </el-select>
                <el-input
                  v-else
                  v-model="nonSecretValues[p.name.trim()]"
                  :placeholder="`输入 ${p.label || p.name}`"
                  class="value-input"
                />
                <el-tag v-if="p.secret && configuredSecrets.has(p.name.trim())" type="success" size="small">
                  已配置
                </el-tag>
              </div>
            </el-form-item>
          </template>
        </el-form>
      </template>

      <div v-if="!hasManifest" class="hint">未找到 manifest.json，保存后将创建。</div>
    </div>
    <template #footer>
      <el-button @click="open = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hint,
.cfg-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
  line-height: 1.6;
}
.cfg-hint code {
  background: var(--el-fill-color-light);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}
.param-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}
.param-name { width: 120px; }
.param-label { width: 140px; }
.param-type { width: 110px; }
.param-options { width: 100%; margin-top: 4px; }
.value-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.value-input { flex: 1; }
</style>
