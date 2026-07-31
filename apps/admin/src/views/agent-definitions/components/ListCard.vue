<script setup lang="ts">
import type { AgentDefinitionResponse } from "@/api/manager/agentDefinitions";
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import dayjs from "dayjs";
import More2Fill from "~icons/ri/more-2-fill";
import HermesLogo from "./icons/HermesLogo.vue";
import OpenClawLogo from "./icons/OpenClawLogo.vue";
import DifyLogo from "./icons/DifyLogo.vue";
import { IconifyIconOffline } from "@/components/ReIcon";

defineOptions({ name: "DefinitionCard" });
const router = useRouter();
const { t } = useI18n();

const props = defineProps<{
  definition: AgentDefinitionResponse;
}>();

const emit = defineEmits<{
  (e: "edit", definition: AgentDefinitionResponse): void;
  (e: "publish", definition: AgentDefinitionResponse): void;
  (e: "delete", definition: AgentDefinitionResponse): void;
}>();

function goDetail(d: AgentDefinitionResponse) {
  router.push(`/agent-definitions/detail/${d.id}`);
}

const statusConfig = computed<Record<string, { label: string; color: string }>>(() => ({
  DRAFT: { label: t("common.status.draft"), color: "#f59e0b" },
  PUBLISHED: { label: t("common.status.published"), color: "#00a870" }
}));

const engineDefaultColors: Record<string, string> = {
  HERMES: "#386bf5",
  OPENCLAW: "#e6a23c",
  DIFY: "#1FB6FF"
};

function getEngineColor(type: string): string {
  return engineDefaultColors[type] || "#909399";
}

function getAvatarColor(): string {
  return props.definition.avatar_color || "#386bf5";
}
</script>

<template>
  <div class="def-card" @click="goDetail(definition)">
    <div class="def-card__inner bg-bg_color">
      <!-- Header: Avatar + Status + Dropdown -->
      <el-row justify="space-between" align="middle">
        <div
          class="def-card__avatar"
          :style="{
            background: getAvatarColor() + '18',
            color: getAvatarColor()
          }"
        >
          <span class="def-card__avatar-text">
            {{ definition.name.charAt(0).toUpperCase() }}
          </span>
        </div>
        <div class="def-card__actions" @click.stop>
          <el-tag
            :color="statusConfig[definition.status]?.color"
            effect="dark"
            class="def-card__status-tag"
          >
            {{ statusConfig[definition.status]?.label || definition.status }}
          </el-tag>
          <el-dropdown trigger="click">
            <IconifyIconOffline
              :icon="More2Fill"
              class="def-card__more-btn"
            />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="emit('edit', definition)">
                  {{ t("common.action.edit") }}
                </el-dropdown-item>
                <el-dropdown-item
                  v-if="definition.has_unpublished_changes"
                  @click="emit('publish', definition)"
                >
                  {{ t("definition.publishVersion") }}
                </el-dropdown-item>
                <el-dropdown-item divided @click="emit('delete', definition)">
                  <span class="text-red-500">{{ t("common.action.delete") }}</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-row>

      <!-- Name + Version -->
      <div class="def-card__name-row">
        <p class="def-card__name text-text_color_primary">
          {{ definition.name }}
        </p>
        <el-tag
          v-if="definition.current_version_no"
          size="small"
          type="info"
          effect="plain"
          class="version-chip"
        >
          v{{ definition.current_version_no }}
        </el-tag>
        <el-tag
          v-if="definition.has_unpublished_changes"
          size="small"
          color="#f59e0b"
          effect="dark"
          class="unpublished-chip"
        >
          {{ t("definition.unpublishedChanges") }}
        </el-tag>
      </div>

      <!-- Description -->
      <p class="def-card__desc text-text_color_regular">
        {{ definition.description || t("agent.noDescription") }}
      </p>

      <!-- Footer: Engine type + Instance count + Creator -->
      <div class="def-card__footer">
        <div class="def-card__meta">
          <span class="engine-chip">
            <HermesLogo v-if="definition.engine_type === 'HERMES'" class="engine-mini-logo" />
            <OpenClawLogo v-else-if="definition.engine_type === 'OPENCLAW'" class="engine-mini-logo" />
            <DifyLogo v-else-if="definition.engine_type === 'DIFY'" class="engine-mini-logo" />
            <span class="engine-name">{{ definition.engine_type }}</span>
          </span>
        </div>
        <div class="def-card__info text-text_color_secondary">
          <span class="instance-count">
            {{ definition.instance_count ?? 0 }} {{ t("agent.instanceUnit") }}
          </span>
          <span class="mx-1">·</span>
          <span>{{ definition.creator_name }}</span>
          <span class="mx-1">·</span>
          <span>{{
            definition.created_at
              ? dayjs(definition.created_at).format("MM-DD")
              : "-"
          }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.def-card {
  display: flex;
  flex-direction: column;
  margin-bottom: 8px;
  overflow: hidden;
  cursor: pointer;
  border-radius: 4px;
  transition: box-shadow 0.2s;
}

.def-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.def-card__inner {
  flex: 1;
  min-height: 115px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
}

.def-card__avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  flex-shrink: 0;
}

.def-card__avatar-text {
  font-size: 15px;
  font-weight: 600;
  font-family: var(--el-font-family);
}

.def-card__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.def-card__status-tag {
  border: 0;
  height: 20px;
  line-height: 20px;
}

.def-card__more-btn {
  font-size: 18px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
}

.def-card__more-btn:hover {
  color: var(--el-color-primary);
}

.def-card__name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 4px;
}

.def-card__name {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.def-card__desc {
  display: -webkit-box;
  height: 28px;
  margin-bottom: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-line-clamp: 2;
  font-size: 11px;
  line-height: 14px;
  -webkit-box-orient: vertical;
}

.def-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--el-border-color-light);
}

.def-card__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
  overflow: hidden;
}

.engine-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
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
}

.engine-mini-logo {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.engine-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.version-chip {
  flex-shrink: 0;
}

.unpublished-chip {
  flex-shrink: 0;
  border: 0;
  height: 20px;
  line-height: 20px;
}

.def-card__info {
  font-size: 11px;
  white-space: nowrap;
  flex-shrink: 0;
}

.instance-count {
  font-weight: 500;
}
</style>
