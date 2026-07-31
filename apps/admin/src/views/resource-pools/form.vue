<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useI18n } from "vue-i18n";
import { formRules } from "./utils/rule";
import { FormProps } from "./utils/types";
import { message } from "@/utils/message";
import { createResourcePoolApi, updateResourcePoolApi } from "@/api/manager/resourcePools";

const props = withDefaults(defineProps<FormProps>(), {
  formInline: () => ({
    title: "create",
    name: "",
    description: "",
    group_id: "",
    allGroups: [],
    isPlatformAdmin: false,
    min_cpu: "100m",
    max_cpu: "2",
    min_memory: "256Mi",
    max_memory: "2Gi",
    min_replicas: 1,
    max_replicas: 5,
    auto_recycle: true,
    idle_suspend_minutes: 30,
    idle_destroy_hours: 24,
    max_sessions_per_pod: 20
  })
});

const { t } = useI18n();
const ruleFormRef = ref();
const newFormInline = ref({ ...props.formInline });
const activeStep = ref(0);
const isEdit = computed(() => newFormInline.value.title === "edit");

/** 是否显示归属选择：编辑模式不可改组；创建模式平台管理员必显，组用户仅多组显（单组自动填） */
const showGroupSelect = computed(
  () =>
    !isEdit.value &&
    (newFormInline.value.isPlatformAdmin ||
      (newFormInline.value.allGroups || []).length !== 1)
);
/** 归属候选：平台管理员多一个"平台共享"选项（group_id="") */
const groupOptions = computed(() => {
  const opts: { id: string; name: string }[] = [];
  if (newFormInline.value.isPlatformAdmin) {
    opts.push({ id: "", name: t("engine.form.field.sharedPool") });
  }
  opts.push(...(newFormInline.value.allGroups || []));
  return opts;
});

const cpuOptions = ["100m", "200m", "500m", "1", "2", "4"];
const memoryOptions = ["128Mi", "256Mi", "512Mi", "1Gi", "2Gi", "4Gi", "8Gi"];

watch(
  () => props.formInline,
  val => {
    newFormInline.value = { ...val };
    activeStep.value = 0;
  },
  { deep: true }
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

  // Not last step → advance
  if (activeStep.value < 2) {
    activeStep.value++;
    return false;
  }

  // Last step → submit
  try {
    const payload: any = { ...newFormInline.value };
    // Remove UI-only fields before sending
    delete payload.title;
    delete payload.id;
    delete payload.allGroups;
    delete payload.isPlatformAdmin;
    // group_id 空字符串 → null（平台共享池）
    payload.group_id = payload.group_id || null;

    if (!isEdit.value) {
      await createResourcePoolApi(payload);
    } else {
      await updateResourcePoolApi(newFormInline.value.id!, payload);
    }

    message(t(isEdit.value ? "engine.msg.updated" : "engine.msg.created"), { type: "success" });
    return true;
  } catch (err: any) {
    message(err?.response?.data?.detail || t("engine.msg.operationFailed"), { type: "error" });
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
      <el-step :title="t('engine.form.step.basic')" />
      <el-step :title="t('engine.form.step.pod')" />
      <el-step :title="t('engine.form.step.recycle')" />
    </el-steps>

    <!-- ======== Step 1: 基本信息 ======== -->
    <div v-show="activeStep === 0" class="form-section">
      <el-form-item
        v-if="showGroupSelect"
        :label="t('engine.form.field.targetGroup')"
        prop="group_id"
      >
        <el-select
          v-model="newFormInline.group_id"
          :placeholder="t('engine.form.field.targetGroupPlaceholder')"
          filterable
          style="width: 100%"
        >
          <el-option
            v-for="g in groupOptions"
            :key="g.id || 'shared'"
            :label="g.name"
            :value="g.id"
          />
        </el-select>
        <p v-if="newFormInline.isPlatformAdmin" class="text-gray-400 text-xs mt-1">
          {{ t("engine.form.field.sharedPoolHint") }}
        </p>
      </el-form-item>

      <el-form-item :label="t('engine.form.field.name')" prop="name">
        <el-input
          v-model="newFormInline.name"
          clearable
          :placeholder="t('engine.form.field.namePlaceholder')"
          maxlength="128"
        />
      </el-form-item>

      <el-form-item :label="t('engine.form.field.description')">
        <el-input
          v-model="newFormInline.description"
          type="textarea"
          :rows="2"
          :placeholder="t('engine.form.field.descriptionPlaceholder')"
        />
      </el-form-item>
    </div>

    <!-- ======== Step 2: Pod 资源配置 ======== -->
    <div v-show="activeStep === 1" class="form-section">
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item :label="t('engine.form.field.minCpu')">
            <el-select v-model="newFormInline.min_cpu" style="width:100%">
              <el-option v-for="v in cpuOptions" :key="v" :label="v" :value="v" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item :label="t('engine.form.field.maxCpu')">
            <el-select v-model="newFormInline.max_cpu" style="width:100%">
              <el-option v-for="v in cpuOptions" :key="v" :label="v" :value="v" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>

      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item :label="t('engine.form.field.minMemory')">
            <el-select v-model="newFormInline.min_memory" style="width:100%">
              <el-option v-for="v in memoryOptions" :key="v" :label="v" :value="v" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item :label="t('engine.form.field.maxMemory')">
            <el-select v-model="newFormInline.max_memory" style="width:100%">
              <el-option v-for="v in memoryOptions" :key="v" :label="v" :value="v" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>

      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item :label="t('engine.form.field.minReplicas')">
            <el-input-number v-model="newFormInline.min_replicas" :min="1" :max="20" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item :label="t('engine.form.field.maxReplicas')">
            <el-input-number v-model="newFormInline.max_replicas" :min="1" :max="50" />
          </el-form-item>
        </el-col>
      </el-row>

      <el-form-item :label="t('engine.form.field.maxProfiles')">
        <el-input-number v-model="newFormInline.max_sessions_per_pod" :min="1" :max="100" />
        <span class="text-gray-400 text-xs ml-2">{{ t("engine.form.field.maxProfilesHint") }}</span>
      </el-form-item>
    </div>

    <!-- ======== Step 3: 资源回收策略 ======== -->
    <div v-show="activeStep === 2" class="form-section">
      <el-form-item :label="t('engine.form.field.autoRecycle')">
        <el-switch v-model="newFormInline.auto_recycle" />
        <span class="text-gray-400 text-xs ml-2">
          {{ newFormInline.auto_recycle ? t('engine.form.field.autoRecycleOn') : t('engine.form.field.autoRecycleOff') }}
        </span>
      </el-form-item>

      <template v-if="newFormInline.auto_recycle">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item :label="t('engine.form.field.idleSuspend')">
              <el-input-number v-model="newFormInline.idle_suspend_minutes" :min="5" :max="1440" />
              <span class="text-gray-400 text-xs ml-2">{{ t("engine.form.field.idleSuspendHint") }}</span>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item :label="t('engine.form.field.idleDestroy')">
              <el-input-number v-model="newFormInline.idle_destroy_hours" :min="1" :max="720" />
              <span class="text-gray-400 text-xs ml-2">{{ t("engine.form.field.idleDestroyHint") }}</span>
            </el-form-item>
          </el-col>
        </el-row>
      </template>
    </div>
  </el-form>
</template>

<style scoped>
.form-section {
  min-height: 200px;
}
</style>
