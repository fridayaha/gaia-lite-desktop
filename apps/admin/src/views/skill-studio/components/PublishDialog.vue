<script setup lang="ts">
import { ref, computed } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import {
  publishApi,
  type PublishResult,
} from "@/api/manager/skill-engine";

defineOptions({ name: "PublishDialog" });

const props = defineProps<{
  modelValue: boolean;
  workspaceId: string;
  manifestVersion?: string;
}>();
const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "published", itemId: string): void;
}>();
const { t } = useI18n();

const publishing = ref(false);
const result = ref<PublishResult | null>(null);

const open = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});

const riskType = computed(() => {
  const r = result.value?.scan?.riskLevel ?? "";
  return r;
});

function riskText(level: string): string {
  switch (level) {
    case "low":
      return t("hub.studio.detail.riskLow");
    case "medium":
      return t("hub.studio.detail.riskMedium");
    case "high":
      return t("hub.studio.detail.riskHigh");
    case "blocking":
      return t("hub.studio.detail.riskBlocking");
    default:
      return level || "—";
  }
}

function riskTagType(level: string): "success" | "warning" | "danger" | "info" {
  switch (level) {
    case "low":
      return "success";
    case "medium":
      return "warning";
    case "high":
    case "blocking":
      return "danger";
    default:
      return "info";
  }
}

async function onPublish() {
  publishing.value = true;
  result.value = null;
  try {
    const res = await publishApi(props.workspaceId);
    result.value = res;
    emit("published", res.itemId);
    ElMessage.success(t("hub.studio.detail.published"));
  } catch (err) {
    const msg = (err as { msg?: string; message?: string })?.msg ?? (err as Error)?.message;
    ElMessage.error(`${t("hub.studio.msg.saveFailed")}: ${msg ?? ""}`);
  } finally {
    publishing.value = false;
  }
}

function reset() {
  result.value = null;
}
</script>

<template>
  <el-dialog
    v-model="open"
    :title="t('hub.studio.detail.publish')"
    width="520px"
    :close-on-click-modal="false"
    append-to-body
    @close="reset"
  >
    <div class="pub-body">
      <div class="pub-hint">{{ t("hub.studio.detail.publishHint") }}</div>
      <div class="pub-version">
        <span class="k">{{ t("hub.studio.detail.version") }}</span>
        <span class="v">{{ manifestVersion || "—" }}</span>
      </div>

      <el-button
        v-if="!result"
        type="primary"
        :loading="publishing"
        @click="onPublish"
      >
        {{ t("hub.studio.detail.publish") }}
      </el-button>

      <div v-if="result" class="pub-result">
        <div class="risk-row">
          <span class="k">{{ t("hub.studio.detail.riskLevel") }}</span>
          <el-tag :type="riskTagType(riskType)" size="small" effect="dark">
            {{ riskText(riskType) }}
          </el-tag>
          <span class="findings">
            {{ t("hub.studio.detail.findings") }}: {{ result.scan?.findingsCount ?? 0 }}
          </span>
        </div>
        <div v-if="result.scan && result.scan.findings.length" class="findings-list">
          <div
            v-for="(f, i) in result.scan.findings.slice(0, 10)"
            :key="i"
            class="finding"
          >
            <span class="f-sev">{{ (f as any).severity ?? "—" }}</span>
            <span class="f-path">{{ (f as any).file_path ?? (f as any).risk_type ?? "—" }}</span>
          </div>
        </div>
        <div v-if="riskType === 'blocking'" class="blocking-hint">
          {{ t("hub.studio.detail.blockingHint") }}
        </div>
        <el-button type="primary" plain @click="open = false">
          {{ t("hub.studio.detail.done") }}
        </el-button>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.pub-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.pub-hint {
  font-size: 12.5px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
.pub-version {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}
.pub-version .k {
  color: var(--el-text-color-secondary);
}
.pub-version .v {
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
  font-weight: 600;
}
.pub-result {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.risk-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.risk-row .k {
  color: var(--el-text-color-secondary);
}
.findings {
  margin-left: auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.findings-list {
  max-height: 160px;
  overflow: auto;
  border: 1px solid var(--ss-line, #e5e7eb);
  border-radius: 6px;
}
.finding {
  display: flex;
  gap: 8px;
  padding: 4px 8px;
  font-size: 12px;
  font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
  border-bottom: 1px solid var(--ss-line, #f0f0f0);
}
.finding:last-child {
  border-bottom: none;
}
.f-sev {
  color: #dc2626;
  flex-shrink: 0;
  width: 60px;
}
.f-path {
  color: var(--ss-ink, #1f2430);
  word-break: break-all;
}
.blocking-hint {
  font-size: 12.5px;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.08);
  padding: 6px 10px;
  border-radius: 6px;
}
</style>
