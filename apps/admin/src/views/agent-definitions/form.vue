<script setup lang="ts">
import { ref, computed, watch, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { makeFormRules } from "./utils/rule";
import { FormProps } from "./utils/types";
import HermesLogo from "./components/icons/HermesLogo.vue";
import OpenClawLogo from "./components/icons/OpenClawLogo.vue";
import DifyLogo from "./components/icons/DifyLogo.vue";
import PersonaEditor from "./components/PersonaEditor.vue";
import { message } from "@/utils/message";
import {
  createDefinitionApi,
  updateDefinitionApi
} from "@/api/manager/agentDefinitions";
import { getModelGroupsApi, type LiteLLMModelGroup } from "@/api/manager/litellm";

const HERMES_DEFAULT_COLOR = "#386bf5";

/** 头像预设色（新建时随机选其一） */
const colorPresets = [
  HERMES_DEFAULT_COLOR, "#00a870", "#f59e0b", "#e6a23c",
  "#909399", "#9b59b6", "#e74c3c", "#1abc9c"
];

const { t } = useI18n();

const props = withDefaults(defineProps<FormProps>(), {
  formInline: () => ({
    title: "create",
    name: "",
    description: "",
    avatar_color: HERMES_DEFAULT_COLOR,
    engine_type: "HERMES",
    group_id: "",
    persona_config: {},
    model_settings: {},
    skill_config: {},
    memory_config: {},
    modelGroup: "",
    system_prompt: "",
    allGroups: []
  })
});

const ruleFormRef = ref();
const newFormInline = ref({ ...props.formInline });
const activeStep = ref(0);

const isEdit = computed(() => newFormInline.value.title === "edit");

const groupCount = computed(() => (newFormInline.value.allGroups || []).length);
/** 单组用户自动归属其唯一组，不显示目标组选择；多组(或 0)显示。编辑模式不可改组。 */
const showGroupSelect = computed(() => !isEdit.value && groupCount.value !== 1);

/** 单组用户：自动填 group_id */
watch(
  () => newFormInline.value.allGroups,
  groups => {
    if (groups && groups.length === 1 && !newFormInline.value.group_id) {
      newFormInline.value.group_id = groups[0].id;
    }
  },
  { immediate: true }
);

/** 校验规则 */
const formRules = makeFormRules();

/** 可选模型组列表（来自 LiteLLM 全局模型组） */
const modelGroupOptions = ref<LiteLLMModelGroup[]>([]);
const modelGroupLoading = ref(false);
/** 选中模型组的 context_length（自动回填），写入 config.yaml 跳过引擎模型探测 */
const contextLength = ref<number | null>(null);

/** 选用模型组时自动回填其 context_length */
function onModelGroupChange(group: string) {
  const g = modelGroupOptions.value.find(m => m.model_group === group);
  contextLength.value = g?.context_length ?? null;
}

async function loadModelGroups() {
  modelGroupLoading.value = true;
  try {
    const res = await getModelGroupsApi();
    modelGroupOptions.value = res.items;
    // 已选模型组但无 context_length（如旧 Agent）：从模型组自动回填
    if (newFormInline.value.modelGroup && contextLength.value == null) {
      onModelGroupChange(newFormInline.value.modelGroup);
    }
  } catch (err: any) {
    console.error("load model groups failed:", err?.response?.data?.detail || err);
    message(t("agent.form.msg.loadModelFailed"), { type: "warning" });
    modelGroupOptions.value = [];
  } finally {
    modelGroupLoading.value = false;
  }
}

onMounted(loadModelGroups);

watch(
  () => props.formInline,
  val => {
    newFormInline.value = { ...val };
    if (!newFormInline.value.modelGroup) {
      newFormInline.value.modelGroup = val.model_settings?.litellm?.model_group ?? "";
    }
    if (contextLength.value == null) {
      contextLength.value = val.model_settings?.litellm?.context_length ?? null;
    }
    if (!newFormInline.value.system_prompt) {
      newFormInline.value.system_prompt =
        val.persona_config?.system_prompt ?? val.model_settings?.system_prompt ?? "";
    }
    // 创建模式：头像颜色默认随机一个预设色（编辑模式保留回填值）
    if (newFormInline.value.title === "create") {
      newFormInline.value.avatar_color = colorPresets[Math.floor(Math.random() * colorPresets.length)];
    }
    // 单组用户自动填 group_id：此 watch 的 {...val} 会覆盖 allGroups 自动填充 watch
    // 设入的 group_id（val.group_id 为 ""），且 allGroups 数组引用不变不会触发后者，
    // 故在此处兜底再填一次，避免单组用户创建时 group_id 为空被后端 422。
    if (
      !isEdit.value &&
      newFormInline.value.allGroups?.length === 1 &&
      !newFormInline.value.group_id
    ) {
      newFormInline.value.group_id = newFormInline.value.allGroups[0].id;
    }
    activeStep.value = 0;
  },
  { deep: true, immediate: true }
);

function getRef() {
  return ruleFormRef.value;
}

function getCurrentStep() {
  return activeStep.value;
}

function prevStep() {
  if (activeStep.value > 0) activeStep.value--;
}

/** Validate current step, then advance or submit */
async function submitStep(): Promise<boolean> {
  const valid = await ruleFormRef.value.validate().catch(() => false);
  if (!valid) return false;

  // 定义层两步：0 基本信息 → 1 人设+模型
  if (activeStep.value < 1) {
    activeStep.value++;
    return false;
  }

  // 最后一步 → 提交 API
  try {
    const systemPrompt = newFormInline.value.system_prompt;
    const modelGroup = newFormInline.value.modelGroup;

    const personaConfig: Record<string, any> = {
      system_prompt: systemPrompt
    };
    const modelSettings: Record<string, any> = {
      system_prompt: systemPrompt,
      litellm: {
        model_group: modelGroup,
        model: modelGroup,
        context_length: contextLength.value ?? undefined
      }
    };

    // Dify 引擎定义：不附加 model_settings.dify 块（Dify 应用绑定已下沉到实例层 AgentInstance.dify_config）
    // 这里只保留 litellm 配置块（虽然 Dify 引擎不用 litellm，但保持结构一致，未来可移除）

    const payload: any = {
      name: newFormInline.value.name,
      group_id: newFormInline.value.group_id,
      description: newFormInline.value.description,
      avatar_color: newFormInline.value.avatar_color,
      engine_type: newFormInline.value.engine_type,
      persona_config: personaConfig,
      model_settings: modelSettings,
      skill_config: newFormInline.value.skill_config || {},
      memory_config: newFormInline.value.memory_config || {}
    };

    if (!isEdit.value) {
      await createDefinitionApi(payload);
    } else {
      await updateDefinitionApi(newFormInline.value.id!, payload);
    }

    message(t(isEdit.value ? "definition.msg.editOk" : "definition.msg.createOk"), { type: "success" });
    return true;
  } catch (err: any) {
    message(err?.response?.data?.detail || t("common.msg.operationFailed"), { type: "error" });
    return false;
  }
}

defineExpose({ getRef, getCurrentStep, submitStep, prevStep });
</script>

<template>
  <el-form
    ref="ruleFormRef"
    :model="newFormInline"
    :rules="formRules"
    label-width="100px"
    label-position="top"
  >
    <!-- Steps indicator -->
    <el-steps :active="activeStep" align-center class="mb-6">
      <el-step :title="t('agent.form.step.basic')" />
      <el-step :title="t('agent.form.step.persona')" />
    </el-steps>

    <!-- ======== Step 0: 基本信息 ======== -->
    <div v-if="activeStep === 0" class="form-section">
      <el-form-item
        v-if="showGroupSelect"
        :label="t('agent.form.field.targetGroup')"
        prop="group_id"
      >
        <el-select
          v-model="newFormInline.group_id"
          :placeholder="t('agent.form.field.targetGroupPlaceholder')"
          filterable
          style="width: 100%"
        >
          <el-option
            v-for="g in newFormInline.allGroups"
            :key="g.id"
            :label="g.name"
            :value="g.id"
          />
        </el-select>
      </el-form-item>
      <el-row :gutter="24">
        <el-col :span="12">
          <el-form-item :label="t('agent.form.field.name')" prop="name">
            <el-input
              v-model="newFormInline.name"
              clearable
              :placeholder="t('agent.form.field.namePlaceholder')"
            />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item :label="t('agent.form.field.avatarColor')" prop="avatar_color">
            <el-color-picker
              v-model="newFormInline.avatar_color"
              :predefine="colorPresets"
              color-format="hex"
              size="default"
            />
            <span class="text-gray-400 text-xs ml-2">{{ t("agent.form.field.avatarColorHint") }}</span>
          </el-form-item>
        </el-col>
      </el-row>

      <el-form-item :label="t('agent.form.field.description')" prop="description">
        <el-input
          v-model="newFormInline.description"
          :placeholder="t('agent.form.field.descriptionPlaceholder')"
          type="textarea"
          :rows="3"
        />
      </el-form-item>

      <!-- 引擎类型选择 -->
      <el-form-item :label="t('agent.detail.label.engineType')" prop="engine_type">
        <el-radio-group v-model="newFormInline.engine_type">
          <el-radio value="HERMES">
            <span class="engine-radio">
              <HermesLogo class="engine-radio-logo" />
              <span>Hermes</span>
            </span>
          </el-radio>
          <el-radio value="OPENCLAW">
            <span class="engine-radio">
              <OpenClawLogo class="engine-radio-logo" />
              <span>OpenClaw</span>
            </span>
          </el-radio>
          <el-radio value="DIFY">
            <span class="engine-radio">
              <DifyLogo class="engine-radio-logo" />
              <span>Dify</span>
            </span>
          </el-radio>
        </el-radio-group>
      </el-form-item>

      <!-- 模型配置（LiteLLM 统一网关）— 仅 Hermes/OpenClaw 显示 -->
      <template v-if="newFormInline.engine_type !== 'DIFY'">
        <el-divider content-position="left">{{ t("agent.form.field.modelConfig") }}</el-divider>
        <el-form-item :label="t('agent.form.field.modelGroup')" prop="modelGroup">
          <el-select
            v-model="newFormInline.modelGroup"
            :placeholder="t('agent.form.field.modelGroupPlaceholder')"
            filterable
            :loading="modelGroupLoading"
            style="width: 100%"
            @change="onModelGroupChange"
          >
            <el-option
              v-for="g in modelGroupOptions"
              :key="g.model_group"
              :label="g.provider ? `${g.model_group}（${g.provider}）` : g.model_group"
              :value="g.model_group"
            />
          </el-select>
          <p class="text-gray-400 text-xs mt-1">{{ t("agent.form.field.litellmHint") }}</p>
          <p v-if="contextLength" class="text-gray-400 text-xs">
            {{ t("agent.form.field.contextLength") }}：{{ contextLength }}
          </p>
        </el-form-item>
      </template>
    </div>

    <!-- ======== Step 1: 人设 (SOUL.md) ======== -->
    <div v-if="activeStep === 1" class="form-section">
      <el-form-item :label="t('agent.form.field.systemPrompt')" prop="system_prompt">
        <PersonaEditor v-model="newFormInline.system_prompt" height="440px" class="w-full" />
      </el-form-item>
    </div>
  </el-form>
</template>

<style scoped>
.form-section {
  min-height: 200px;
}

.engine-radio {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.engine-radio-logo {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}
</style>
