<script setup lang="ts">
import { ref, computed } from "vue";
import { useI18n } from "vue-i18n";
import { makeFormRules } from "../utils/rule";
import type { ModelFormProps } from "../utils/types";

const props = withDefaults(defineProps<ModelFormProps>(), {
  formInline: () => ({
    title: "create",
    model_name: "",
    model: "",
    api_key: "",
    api_base: "",
    custom_llm_provider: "",
    context_length: null
  })
});

const { t } = useI18n();
const ruleFormRef = ref();
const newFormInline = ref(props.formInline);
const isEdit = computed(() => newFormInline.value.title === "edit");
const rules = computed(() => makeFormRules(newFormInline.value.title));

/** 已知供应商（可自定义输入其它） */
const providerOptions = [
  "openai",
  "anthropic",
  "azure",
  "gemini",
  "vertex_ai",
  "bedrock",
  "mistral",
  "cohere",
  "deepseek",
  "dashscope",
  "zhipu"
];

function getRef() {
  return ruleFormRef.value;
}

defineExpose({ getRef });
</script>

<template>
  <el-form
    ref="ruleFormRef"
    :model="newFormInline"
    :rules="rules"
    label-position="top"
  >
    <el-form-item :label="t('litellm.model.field.name')" prop="model_name">
      <el-input
        v-model="newFormInline.model_name"
        :placeholder="t('litellm.model.field.namePlaceholder')"
        :disabled="isEdit"
      />
      <div class="text-xs text-gray-400 mt-1">{{ t("litellm.model.field.nameHint") }}</div>
    </el-form-item>
    <el-form-item :label="t('litellm.model.field.upstream')" prop="model">
      <el-input v-model="newFormInline.model" :placeholder="t('litellm.model.field.upstreamPlaceholder')" />
      <div class="text-xs text-gray-400 mt-1">{{ t("litellm.model.field.upstreamHint") }}</div>
    </el-form-item>
    <el-form-item :label="t('litellm.model.field.apiKey')" prop="api_key">
      <el-input
        v-model="newFormInline.api_key"
        type="password"
        show-password
        :placeholder="t('litellm.model.field.apiKeyPlaceholder')"
      />
      <div class="text-xs text-gray-400 mt-1">{{ t("litellm.model.field.apiKeyHint") }}</div>
    </el-form-item>
    <el-form-item :label="t('litellm.model.field.apiBase')">
      <el-input v-model="newFormInline.api_base" :placeholder="t('litellm.model.field.apiBasePlaceholder')" />
    </el-form-item>
    <el-form-item :label="t('litellm.model.field.customProvider')">
      <el-select
        v-model="newFormInline.custom_llm_provider"
        :placeholder="t('litellm.model.field.customProviderPlaceholder')"
        filterable
        allow-create
        default-first-option
        clearable
        style="width: 100%"
      >
        <el-option v-for="p in providerOptions" :key="p" :label="p" :value="p" />
      </el-select>
    </el-form-item>
    <el-form-item :label="t('litellm.model.field.contextLength')">
      <el-input-number
        v-model="newFormInline.context_length"
        :min="1000"
        :step="1000"
        controls-position="right"
        style="width: 100%"
      />
      <div class="text-xs text-gray-400 mt-1">{{ t("litellm.model.field.contextLengthHint") }}</div>
    </el-form-item>
  </el-form>
</template>
