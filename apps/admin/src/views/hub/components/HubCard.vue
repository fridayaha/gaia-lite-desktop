<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { HubItem, HubItemType, RiskLevel } from "@/api/hub";

defineOptions({ name: "HubCard" });

const props = defineProps<{
  item: HubItem;
  subscribed?: boolean;
  subscribedAgents?: string[];
}>();

const emit = defineEmits<{ click: []; subscribe: [] }>();

const { t } = useI18n();

const typeColor: Record<HubItemType, string> = {
  agent: "success",
  skill: "warning",
  tool: "info",
  mcp: "danger",
};

const statusColor: Record<string, string> = {
  draft: "info",
  pending_review: "warning",
  approved: "",
  published: "success",
  rejected: "danger",
  disabled: "danger",
  archived: "warning",
};

const riskColor: Record<string, string> = {
  low: "success",
  medium: "warning",
  high: "danger",
  blocking: "danger",
};

const statusLabel = computed(() => t(`hub.status.${props.item.status}`));
const riskLabel = computed(() => props.item.risk_level ? t(`hub.risk.${props.item.risk_level}`) : "—");
const formatDate = computed(() => {
  const d = props.item.created_at;
  if (!d) return "";
  return d.slice(5, 10);
});

// 仅 skill 类型支持订阅（manager 侧只有 skills/install-from-hub 入口）
const canSubscribe = computed(() => props.item.type === "skill");
</script>

<template>
  <el-card shadow="never" class="hub-card" @click="emit('click')">
    <div class="hub-card__inner">
      <!-- Header: 类型 tag + 状态 tag + 精选标记 + 已订阅标记 -->
      <div class="hub-card__header">
        <div class="hub-card__tags-row">
          <el-tag size="small" :type="(typeColor[item.type as HubItemType] || '') as any" effect="light">
            {{ t(`hub.overview.${item.type}`) }}
          </el-tag>
          <el-tag size="small" :type="(statusColor[item.status] || '') as any" effect="plain">
            {{ statusLabel }}
          </el-tag>
          <el-tag v-if="item.featured" size="small" effect="dark" type="warning">
            {{ t("hub.featured") }}
          </el-tag>
          <el-tag v-if="subscribed" size="small" effect="dark" type="success">
            ✓ {{ t("hub.subscribe.short") }}
          </el-tag>
        </div>
      </div>

      <!-- Name -->
      <h4 class="hub-card__name">{{ item.name }}</h4>

      <!-- Description -->
      <p class="hub-card__desc">{{ item.description }}</p>

      <!-- 标签 -->
      <div v-if="item.tags?.length" class="hub-card__label-tags">
        <el-tag v-for="tag in item.tags.slice(0, 3)" :key="tag" size="small" effect="plain" class="mr-1">
          {{ tag }}
        </el-tag>
        <span v-if="item.tags.length > 3" class="text-xs text-[var(--el-text-color-placeholder)]">
          +{{ item.tags.length - 3 }}
        </span>
      </div>

      <!-- Footer: 风险 · 来源 · 日期 + 订阅按钮 -->
      <div class="hub-card__footer">
        <div class="hub-card__footer-info">
          <el-tag size="small" :type="(riskColor[item.risk_level as RiskLevel] || 'info') as any" effect="plain">
            {{ riskLabel }}
          </el-tag>
          <span class="hub-card__footer-sep">·</span>
          <span class="hub-card__footer-text">{{ t(`hub.sourceType.${item.source_type}`) || item.source_type }}</span>
          <span class="hub-card__footer-sep">·</span>
          <span class="hub-card__footer-text">{{ formatDate }}</span>
        </div>
        <el-button
          v-if="canSubscribe"
          size="small"
          :type="subscribed ? 'success' : 'primary'"
          :plain="!subscribed"
          @click.stop="emit('subscribe')"
        >
          {{ subscribed ? t("hub.subscribe.cancelSub") : t("hub.subscribe.short") }}
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.hub-card { border-radius: 8px; cursor: pointer; transition: all 0.2s; }
.hub-card:hover { box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06); }
.hub-card__inner { min-height: 150px; padding: 14px 16px; display: flex; flex-direction: column; }
.hub-card__header { margin-bottom: 6px; }
.hub-card__tags-row { display: flex; gap: 4px; flex-wrap: wrap; }
.hub-card__name { margin: 0 0 4px; font-size: 15px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hub-card__desc {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  flex: 1;
}
.hub-card__label-tags { display: flex; align-items: center; gap: 2px; flex-wrap: wrap; }
.hub-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--el-border-color-lighter);
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.hub-card__footer-info { display: flex; align-items: center; gap: 4px; }
.hub-card__footer-sep { color: var(--el-border-color); }
.hub-card__footer-text { color: var(--el-text-color-secondary); }
</style>
