<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { editFormRules } from "../utils/rule";
import type { KeyEditFormProps } from "../utils/types";

const props = withDefaults(defineProps<KeyEditFormProps>(), {
  formInline: () => ({
    max_budget: undefined,
    budget_duration: "",
    rpm_limit: undefined,
    tpm_limit: undefined,
    duration: ""
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
  <el-form
    ref="ruleFormRef"
    :model="newFormInline"
    :rules="editFormRules"
    label-position="top"
  >
    <el-form-item :label="t('litellm.key.field.maxBudget')" prop="max_budget">
      <el-input-number
        v-model="newFormInline.max_budget"
        :min="0"
        :precision="2"
        controls-position="right"
        style="width: 100%"
      />
    </el-form-item>
    <el-form-item :label="t('litellm.key.field.budgetDuration')" prop="budget_duration">
      <el-input
        v-model="newFormInline.budget_duration"
        :placeholder="t('litellm.key.field.budgetDurationPlaceholder')"
      />
    </el-form-item>
    <el-form-item :label="t('litellm.key.field.rpm')" prop="rpm_limit">
      <el-input-number
        v-model="newFormInline.rpm_limit"
        :min="0"
        controls-position="right"
        style="width: 100%"
      />
    </el-form-item>
    <el-form-item :label="t('litellm.key.field.tpm')" prop="tpm_limit">
      <el-input-number
        v-model="newFormInline.tpm_limit"
        :min="0"
        controls-position="right"
        style="width: 100%"
      />
    </el-form-item>
    <el-form-item :label="t('litellm.key.field.duration')" prop="duration">
      <el-input
        v-model="newFormInline.duration"
        :placeholder="t('litellm.key.field.durationPlaceholder')"
      />
    </el-form-item>
  </el-form>
</template>
