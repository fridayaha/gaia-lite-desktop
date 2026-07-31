<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useDark } from "@vueuse/core";
import { ElMessageBox } from "element-plus";
import { MdEditor } from "md-editor-v3";
import "md-editor-v3/lib/style.css";
import FileTextLine from "~icons/ri/file-text-line";
import MagicLine from "~icons/ri/magic-line";
import ArrowDownLine from "~icons/ri/arrow-down-s-line";
import QuestionLine from "~icons/ri/question-line";

defineOptions({ name: "PersonaEditor" });

const props = withDefaults(
  defineProps<{
    modelValue: string;
    placeholder?: string;
    height?: string;
  }>(),
  {
    placeholder: "",
    height: "420px"
  }
);

const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
}>();

const { t, locale } = useI18n();
const isDark = useDark();

const editorLanguage = computed<"zh-CN" | "en-US">(() =>
  locale.value.startsWith("zh") ? "zh-CN" : "en-US"
);

const value = computed({
  get: () => props.modelValue || "",
  set: v => emit("update:modelValue", v)
});

// ── 人设 (SOUL.md) 辅助：模板 / 样例 / 说明 ──
const SOUL_TEMPLATE = `# 角色
<一句话定位这个智能体的身份与职责>

# 核心能力
-
-

# 行为准则
-

# 语气与风格
-

# 约束
-
`;

const SOUL_SAMPLES: Record<string, string> = {
  code: `# 角色
你是一名资深全栈工程师，擅长 Python、TypeScript 与云原生开发，协助用户编写、审查与调试代码。

# 核心能力
- 读懂大型代码库，定位问题并给出最小改动方案
- 编写清晰、可维护、带类型注解的代码
- 解释复杂技术时类比生动、由浅入深

# 行为准则
- 修改前先复述用户意图，确认后再动手
- 给出代码时同步说明关键设计取舍
- 不确定时如实说明，不编造 API 或库的行为

# 语气与风格
- 简洁专业，少废话
- 主动指出潜在风险与边界情况

# 约束
- 不输出未经验证的命令行操作
- 涉及敏感信息（密钥/凭据）时提醒用户脱敏
`,
  service: `# 角色
你是某产品的客服助手，耐心、专业地解答用户咨询并引导解决问题。

# 核心能力
- 准确理解用户问题，必要时追问澄清
- 基于已知政策与知识库作答，不臆测
- 复杂问题分步骤引导

# 行为准则
- 先共情再解决，语气友好
- 超出范围的问题如实告知，并指明正确渠道
- 不承诺无法兑现的补偿或时效

# 语气与风格
- 亲切、简短、有温度
- 多用肯定句，少用否定与命令

# 约束
- 不泄露内部流程与系统细节
- 涉及账户安全时优先引导官方渠道
`,
  knowledge: `# 角色
你是企业知识库问答助手，基于检索到的资料精准回答用户问题。

# 核心能力
- 快速提炼资料要点，结构化作答
- 区分「资料明确说明」与「需推断」的内容
- 跨多篇资料整合信息

# 行为准则
- 回答附带资料来源引用
- 资料未覆盖时明确告知，不编造
- 追问以补全关键上下文

# 语气与风格
- 客观、准确、有条理
- 适度使用列表与标题增强可读性

# 约束
- 不输出与问题无关的资料内容
- 对矛盾资料如实呈现并标注
`
};

const personaHelpVisible = ref(false);

function fillTemplate() {
  value.value = SOUL_TEMPLATE;
}

async function fillSample(key: string) {
  const content = SOUL_SAMPLES[key];
  if (!content) return;
  if (value.value && value.value.trim()) {
    try {
      await ElMessageBox.confirm(
        t("agent.form.field.personaConfirmReplace"),
        t("agent.form.field.personaSample"),
        {
          confirmButtonText: t("common.action.confirm"),
          cancelButtonText: t("common.action.cancel"),
          type: "warning"
        }
      );
    } catch {
      return;
    }
  }
  value.value = content;
}
</script>

<template>
  <div class="w-full">
    <div class="flex items-center gap-2 mb-2 flex-wrap">
      <el-button size="small" plain @click="fillTemplate">
        <el-icon class="mr-1"><FileTextLine /></el-icon>
        {{ t("agent.form.field.personaTemplate") }}
      </el-button>
      <el-dropdown trigger="click" @command="fillSample">
        <el-button size="small" plain>
          <el-icon class="mr-1"><MagicLine /></el-icon>
          {{ t("agent.form.field.personaSample") }}
          <el-icon class="ml-1"><ArrowDownLine /></el-icon>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="code">{{ t("agent.form.field.personaSampleCode") }}</el-dropdown-item>
            <el-dropdown-item command="service">{{ t("agent.form.field.personaSampleService") }}</el-dropdown-item>
            <el-dropdown-item command="knowledge">{{ t("agent.form.field.personaSampleKnowledge") }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button size="small" text @click="personaHelpVisible = !personaHelpVisible">
        <el-icon class="mr-1"><QuestionLine /></el-icon>
        {{ t("agent.form.field.personaHelp") }}
      </el-button>
    </div>
    <el-collapse-transition>
      <div v-show="personaHelpVisible" class="persona-help">
        {{ t("agent.form.field.personaHelpText") }}
      </div>
    </el-collapse-transition>
    <MdEditor
      v-model="value"
      :theme="isDark ? 'dark' : 'light'"
      :language="editorLanguage"
      :preview="true"
      :height="height"
      :placeholder="placeholder || t('agent.form.field.systemPromptPlaceholder')"
      :toolbars-exclude="['github', 'save', 'pageFullscreen', 'catalog']"
      style="border-radius: 8px; overflow: hidden"
    />
  </div>
</template>

<style scoped>
.persona-help {
  margin-bottom: 8px;
  padding: 10px 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
  white-space: pre-wrap;
}
</style>
