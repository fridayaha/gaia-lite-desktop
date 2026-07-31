<script setup lang="ts">
import type { AgentInstanceResponse } from "@/api/manager/agentInstances";
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import { copyTextToClipboard } from "@pureadmin/utils";
import dayjs from "dayjs";
import More2Fill from "~icons/ri/more-2-fill";
import Notification3Line from "~icons/ri/notification-3-line";
import FileCopyLine from "~icons/ri/file-copy-line";
import HermesLogo from "./icons/HermesLogo.vue";
import OpenClawLogo from "./icons/OpenClawLogo.vue";
import DifyLogo from "./icons/DifyLogo.vue";

defineOptions({ name: "AgentInstanceCard" });
const router = useRouter();
const { t } = useI18n();

const props = defineProps<{
  instance: AgentInstanceResponse;
}>();

const emit = defineEmits<{
  (e: "edit", instance: AgentInstanceResponse): void;
  (e: "publish", instance: AgentInstanceResponse): void;
  (e: "offline", instance: AgentInstanceResponse): void;
  (e: "clone", instance: AgentInstanceResponse): void;
  (e: "delete", instance: AgentInstanceResponse): void;
}>();

function goDetail() {
  router.push(`/agent-instances/detail/${props.instance.id}`);
}

function copyAgentId() {
  const ok = copyTextToClipboard(props.instance.id);
  if (ok) {
    ElMessage.success("Agent ID 已复制");
  } else {
    ElMessage.error("复制失败");
  }
}

const statusConfig = computed<Record<string, { label: string; color: string }>>(() => ({
  DRAFT: { label: t("common.status.draft"), color: "#f59e0b" },
  PUBLISHED: { label: t("instance.stats.published"), color: "#00a870" },
  OFFLINE: { label: t("common.status.offline"), color: "#909399" }
}));

const engineColors: Record<string, string> = {
  HERMES: "#386bf5",
  OPENCLAW: "#e6a23c"
};

function getEngineColor(type: string | null): string {
  return (type && engineColors[type]) || "#909399";
}

/** 访问范围标签映射 */
const scopeLabelMap = computed<Record<string, string>>(() => ({
  ALL: t("common.scope.allFull"),
  USER: t("common.scope.user"),
  USER_GROUP: t("common.scope.userGroup")
}));
</script>

<template>
  <div class="instance-card" @click="goDetail">
    <div class="instance-card__inner bg-bg_color">
      <!-- Header: Avatar + Status + Dropdown -->
      <el-row justify="space-between" align="middle">
        <div
          class="instance-card__avatar"
          :style="{
            background: getEngineColor(instance.engine_type) + '18',
            color: getEngineColor(instance.engine_type)
          }"
        >
          <HermesLogo
            v-if="instance.engine_type === 'HERMES'"
            class="instance-card__avatar-logo"
          />
          <OpenClawLogo
            v-else-if="instance.engine_type === 'OPENCLAW'"
            class="instance-card__avatar-logo"
          />
          <DifyLogo
            v-else-if="instance.engine_type === 'DIFY'"
            class="instance-card__avatar-logo"
          />
          <span v-else class="instance-card__avatar-text">
            {{ instance.name.charAt(0).toUpperCase() }}
          </span>
        </div>
        <div class="instance-card__actions" @click.stop>
          <el-tag
            :color="statusConfig[instance.status]?.color"
            effect="dark"
            class="instance-card__status-tag"
          >
            {{ statusConfig[instance.status]?.label || instance.status }}
          </el-tag>
          <el-dropdown trigger="click">
            <IconifyIconOffline
              :icon="More2Fill"
              class="instance-card__more-btn"
            />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="emit('edit', instance)">
                  {{ t("common.action.edit") }}
                </el-dropdown-item>
                <el-dropdown-item @click="emit('clone', instance)">
                  {{ t("common.action.clone") }}
                </el-dropdown-item>
                <el-dropdown-item
                  v-if="instance.status === 'DRAFT' || instance.status === 'OFFLINE'"
                  @click="emit('publish', instance)"
                >
                  {{ t("instance.online") }}
                </el-dropdown-item>
                <el-dropdown-item
                  v-if="instance.status === 'PUBLISHED'"
                  @click="emit('offline', instance)"
                >
                  {{ t("instance.offline") }}
                </el-dropdown-item>
                <el-dropdown-item divided @click="emit('delete', instance)">
                  <span class="text-red-500">{{ t("common.action.delete") }}</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-row>

      <!-- Name -->
      <p class="instance-card__name text-text_color_primary">
        {{ instance.name }}
      </p>

      <!-- Description -->
      <p class="instance-card__desc text-text_color_regular">
        {{ instance.description || t("instance.noDescription") }}
      </p>

      <!-- Meta: definition + version -->
      <div class="instance-card__meta-row">
        <span class="meta-label">{{ t("instance.detail.label.definition") }}:</span>
        <span class="meta-value">{{ instance.definition_name || "—" }}</span>
        <el-divider direction="vertical" />
        <span class="meta-label">{{ t("instance.detail.label.version") }}:</span>
        <span class="meta-value">{{ instance.version_no || "—" }}</span>
        <el-tooltip
          v-if="instance.has_newer_version"
          :content="t('instance.version.hasUpdate')"
          placement="top"
        >
          <el-icon class="version-badge-icon" @click.stop="goDetail">
            <Notification3Line />
          </el-icon>
        </el-tooltip>
      </div>

      <!-- 智能体ID（可用于链路追踪等页面过滤） -->
      <div class="instance-card__id-row">
        <span class="meta-label">智能体ID:</span>
        <el-tooltip :content="instance.id" placement="top" :hide-after="0">
          <span class="meta-value font-mono">{{ instance.id.slice(0, 8) }}…</span>
        </el-tooltip>
        <el-tooltip content="复制完整 ID" placement="top">
          <el-icon class="copy-icon" @click.stop="copyAgentId">
            <FileCopyLine />
          </el-icon>
        </el-tooltip>
      </div>

      <!-- Footer: ResourcePool + AccessScope + Creator + Time -->
      <div class="instance-card__footer">
        <div class="instance-card__meta">
          <span v-if="instance.resource_pool_id" class="pool-chip">
            {{ instance.resource_pool_name || instance.resource_pool_id.slice(0, 8) }}
          </span>
          <el-tag
            v-else-if="instance.engine_type === 'DIFY'"
            size="small"
            type="info"
            effect="plain"
          >
            {{ t("agent.difyExternalNoPool") }}
          </el-tag>
          <el-tag v-else size="small" type="danger" effect="plain">
            {{ t("agent.unboundEngine") }}
          </el-tag>
          <el-tag v-if="instance.group_name" size="small" effect="plain" class="scope-chip">
            {{ instance.group_name }}
          </el-tag>
        </div>
        <div class="instance-card__info text-text_color_secondary">
          <span>{{ instance.creator_name }}</span>
          <span class="mx-1">·</span>
          <span>{{
            instance.created_at ? dayjs(instance.created_at).format("MM-DD") : "-"
          }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.instance-card {
  display: flex;
  flex-direction: column;
  margin-bottom: 8px;
  overflow: hidden;
  cursor: pointer;
  border-radius: 4px;
  transition: box-shadow 0.2s;
}

.instance-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.instance-card__inner {
  flex: 1;
  min-height: 140px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
}

.instance-card__avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  flex-shrink: 0;
}

.instance-card__avatar-logo {
  width: 20px;
  height: 20px;
}

.instance-card__avatar-text {
  font-size: 15px;
  font-weight: 600;
  font-family: var(--el-font-family);
}

.instance-card__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.instance-card__status-tag {
  border: 0;
  height: 20px;
  line-height: 20px;
}

.instance-card__more-btn {
  font-size: 18px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
}

.instance-card__more-btn:hover {
  color: var(--el-color-primary);
}

.instance-card__name {
  margin: 8px 0 4px;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.3;
}

.instance-card__desc {
  display: -webkit-box;
  height: 28px;
  margin-bottom: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-line-clamp: 2;
  font-size: 11px;
  line-height: 14px;
  -webkit-box-orient: vertical;
}

.instance-card__meta-row {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.instance-card__id-row {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: auto;
  white-space: nowrap;
  overflow: hidden;
}

.copy-icon {
  cursor: pointer;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
  vertical-align: -2px;
}

.copy-icon:hover {
  color: var(--el-color-primary);
}

.meta-label {
  color: var(--el-text-color-placeholder);
}

.meta-value {
  color: var(--el-text-color-regular);
  font-weight: 500;
}

.version-badge-icon {
  margin-left: 4px;
  color: var(--el-color-danger);
  cursor: pointer;
  font-size: 15px;
  vertical-align: -2px;
  animation: version-bell-pulse 1.8s ease-in-out infinite;
}

@keyframes version-bell-pulse {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.25);
    opacity: 0.65;
  }
}

.instance-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--el-border-color-light);
}

.instance-card__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
  overflow: hidden;
}

.pool-chip {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  min-width: 0;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  border: 1px solid var(--el-border-color);
  background: var(--el-fill-color-blank);
  color: var(--el-text-color-regular);
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
  box-sizing: border-box;
  overflow: hidden;
  text-overflow: ellipsis;
}

.scope-chip {
  flex-shrink: 0;
}

.instance-card__info {
  font-size: 11px;
  white-space: nowrap;
  flex-shrink: 0;
}
</style>
