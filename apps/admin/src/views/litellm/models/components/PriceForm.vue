<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { priceFormRules } from "../utils/rule";
import type { PriceFormProps } from "../utils/types";

const props = withDefaults(defineProps<PriceFormProps>(), {
  formInline: () => ({
    model_id: "",
    model_name: "",
    input_cost_per_1m_tokens: null,
    output_cost_per_1m_tokens: null
  })
});

const { t } = useI18n();
const ruleFormRef = ref();
const newFormInline = ref(props.formInline);

function getRef() {
  return ruleFormRef.value;
}

defineExpose({ getRef });
</script>

<template>
  <div>
    <div class="mb-3 text-sm">
      <span class="text-gray-500">{{ t("litellm.model.field.priceTarget") }}：</span>
      <span class="font-bold">{{ newFormInline.model_name }}</span>
    </div>
    <el-form
      ref="ruleFormRef"
      :model="newFormInline"
      :rules="priceFormRules"
      label-position="top"
    >
      <el-form-item :label="t('litellm.model.dialog.inputPrice')" prop="input_cost_per_1m_tokens">
        <el-input-number
          v-model="newFormInline.input_cost_per_1m_tokens"
          :precision="6"
          :step="0.01"
          :min="0"
          controls-position="right"
          style="width: 200px"
        />
        <span class="ml-2 text-gray-500 text-xs">USD / 1M tokens</span>
      </el-form-item>
      <el-form-item :label="t('litellm.model.dialog.outputPrice')" prop="output_cost_per_1m_tokens">
        <el-input-number
          v-model="newFormInline.output_cost_per_1m_tokens"
          :precision="6"
          :step="0.01"
          :min="0"
          controls-position="right"
          style="width: 200px"
        />
        <span class="ml-2 text-gray-500 text-xs">USD / 1M tokens</span>
      </el-form-item>
    </el-form>
    <el-alert type="info" :closable="false" :title="t('litellm.model.tip.priceHint')" />
  </div>
</template>
