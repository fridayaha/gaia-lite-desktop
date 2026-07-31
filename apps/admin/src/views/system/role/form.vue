<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { formRules } from "./utils/rule";
import { FormProps } from "./utils/types";

const props = withDefaults(defineProps<FormProps>(), {
  formInline: () => ({
    name: "",
    description: ""
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
    :rules="formRules"
    label-width="82px"
  >
    <el-form-item :label="t('system.role.nameForm')" prop="name">
      <el-input
        v-model="newFormInline.name"
        clearable
        :placeholder="t('system.role.namePlaceholder')"
      />
    </el-form-item>

    <el-form-item :label="t('system.role.descForm')" prop="description">
      <el-input
        v-model="newFormInline.description"
        :placeholder="t('system.role.descPlaceholder')"
        type="textarea"
      />
    </el-form-item>
  </el-form>
</template>
