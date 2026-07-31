<script setup lang="ts">
import { ref, computed } from "vue";
import { useI18n } from "vue-i18n";
import { useDark } from "@vueuse/core";
import { MdPreview } from "md-editor-v3";
import "md-editor-v3/lib/preview.css";
import type { AgentDefinitionResponse } from "@/api/manager/agentDefinitions";
import { updateDefinitionApi } from "@/api/manager/agentDefinitions";
import PersonaEditor from "../components/PersonaEditor.vue";
import { message } from "@/utils/message";

defineOptions({ name: "DefinitionPersonaTab" });

const props = defineProps<{
  definition: AgentDefinitionResponse;
}>();

const emit = defineEmits<{
  (e: "updated"): void;
}>();

const { t } = useI18n();
const isDark = useDark();

/** persona_config / model_settings 可能是 string 或 object，统一解出 system_prompt */
function pickSystemPrompt(cfg: Record<string, any> | string | undefined): string {
  if (!cfg) return "";
  const obj =
    typeof cfg === "string"
      ? (() => {
          try {
            return JSON.parse(cfg);
          } catch {
            return {};
          }
        })()
      : cfg;
  return obj?.system_prompt ?? "";
}

const systemPrompt = computed(() => {
  const persona = pickSystemPrompt(props.definition?.persona_config as any);
  if (persona) return persona;
  return pickSystemPrompt(props.definition?.model_settings as any);
});

// ── 内联编辑 ──
const editing = ref(false);
const saving = ref(false);
const draftPrompt = ref("");

function startEdit() {
  draftPrompt.value = systemPrompt.value;
  editing.value = true;
}

function cancelEdit() {
  editing.value = false;
  draftPrompt.value = "";
}

async function saveEdit() {
  if (!props.definition) return;
  saving.value = true;
  try {
    const d = props.definition;
    const ms = (d.model_settings as any) || {};
    const litellm = ms?.litellm || {};
    // 镜像 form.vue submitStep：persona_config + model_settings 同步 system_prompt，
    // 其余字段用当前 definition 原值回填，避免覆盖。
    const payload = {
      name: d.name,
      group_id: d.group_id,
      description: d.description,
      avatar_color: d.avatar_color,
      engine_type: d.engine_type,
      persona_config: { system_prompt: draftPrompt.value },
      model_settings: {
        system_prompt: draftPrompt.value,
        litellm: {
          model_group: litellm.model_group || litellm.model || "",
          model: litellm.model_group || litellm.model || ""
        }
      },
      skill_config: d.skill_config || {},
      memory_config: d.memory_config || {}
    };
    await updateDefinitionApi(d.id, payload);
    message(t("definition.msg.editOk"), { type: "success" });
    editing.value = false;
    emit("updated");
  } catch (err: any) {
    message(err?.response?.data?.detail || t("common.msg.operationFailed"), { type: "error" });
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="persona-tab">
    <div class="persona-header">
      <div class="persona-header-row">
        <span class="persona-header-title">{{ t("agent.form.field.systemPrompt") }}</span>
        <div v-if="!editing" class="persona-header-actions">
          <el-button type="primary" plain size="small" @click="startEdit">
            {{ t("common.action.edit") }}
          </el-button>
        </div>
        <div v-else class="persona-header-actions">
          <el-button size="small" :loading="saving" type="success" @click="saveEdit">
            {{ t("common.action.save") }}
          </el-button>
          <el-button size="small" @click="cancelEdit">
            {{ t("common.action.cancel") }}
          </el-button>
        </div>
      </div>
      <span class="persona-header-hint">{{ t("agent.form.field.personaHelpText") }}</span>
    </div>

    <!-- 编辑态 -->
    <PersonaEditor
      v-if="editing"
      v-model="draftPrompt"
      height="520px"
    />

    <!-- 查看态：markdown 渲染 -->
    <div v-else-if="systemPrompt" class="persona-preview">
      <MdPreview :model-value="systemPrompt" :theme="isDark ? 'dark' : 'light'" />
    </div>
    <el-empty v-else :description="t('agent.config.notSet')" />
  </div>
</template>

<style scoped>
.persona-tab {
  margin-bottom: 20px;
}

.persona-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 16px;
}

.persona-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.persona-header-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.persona-header-actions {
  display: flex;
  gap: 8px;
}

.persona-header-hint {
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-placeholder);
  white-space: pre-wrap;
}

.persona-preview {
  padding: 4px 8px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  max-height: 65vh;
  overflow-y: auto;
}
</style>
