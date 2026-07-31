<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessageBox } from "element-plus";
import dayjs from "dayjs";
import More2Fill from "~icons/ri/more-2-fill";
import FlashlightLine from "~icons/ri/flashlight-line";
import { IconifyIconOffline } from "@/components/ReIcon";
import type { Workspace } from "@/api/manager/skill-engine";

defineOptions({ name: "SkillCard" });

const props = defineProps<{ workspace: Workspace }>();
const emit = defineEmits<{
  (e: "open", id: string): void;
  (e: "delete", id: string): void;
}>();

const { t } = useI18n();

const accent = "#6d5efc";

const createdLabel = computed(() =>
  props.workspace.createdAt
    ? dayjs(props.workspace.createdAt).format("MM-DD")
    : "-",
);

async function onDelete() {
  try {
    await ElMessageBox.confirm(
      t("hub.studio.card.delete") + `：${props.workspace.name}？`,
      t("hub.studio.card.delete"),
      { type: "warning" },
    );
    emit("delete", props.workspace.id);
  } catch {
    /* cancelled */
  }
}

function goDetail() {
  emit("open", props.workspace.id);
}
</script>

<template>
  <div class="skill-card" @click="goDetail">
    <div class="skill-card__inner bg-bg_color">
      <!-- 头部：头像 + 操作 -->
      <el-row justify="space-between" align="middle">
        <div
          class="skill-card__avatar"
          :style="{ background: accent + '18', color: accent }"
        >
          <span class="skill-card__avatar-text">
            {{ workspace.name.charAt(0).toUpperCase() }}
          </span>
        </div>
        <div class="skill-card__actions" @click.stop>
          <el-dropdown trigger="click">
            <IconifyIconOffline :icon="More2Fill" class="skill-card__more-btn" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="goDetail">
                  {{ t("hub.studio.card.open") }}
                </el-dropdown-item>
                <el-dropdown-item divided @click="onDelete">
                  <span class="text-red-500">{{ t("common.action.delete") }}</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-row>

      <!-- 名称 -->
      <div class="skill-card__name-row">
        <p class="skill-card__name text-text_color_primary">{{ workspace.name }}</p>
      </div>

      <!-- 描述 -->
      <p class="skill-card__desc text-text_color_regular">
        {{ workspace.description || t("agent.noDescription") }}
      </p>

      <!-- 底部：类型 + 创建时间 -->
      <div class="skill-card__footer">
        <div class="skill-card__meta">
          <span class="type-chip">
            <FlashlightLine width="14" height="14" />
            <span class="type-name">{{ t("menus.pureSkillStudio") }}</span>
          </span>
        </div>
        <div class="skill-card__info text-text_color_secondary">
          {{ createdLabel }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.skill-card {
  display: flex;
  flex-direction: column;
  margin-bottom: 8px;
  overflow: hidden;
  cursor: pointer;
  border-radius: 4px;
  transition: box-shadow 0.2s;
}

.skill-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.skill-card__inner {
  flex: 1;
  min-height: 115px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
}

.skill-card__avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  flex-shrink: 0;
}

.skill-card__avatar-text {
  font-size: 15px;
  font-weight: 600;
  font-family: var(--el-font-family);
}

.skill-card__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.skill-card__more-btn {
  font-size: 18px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
}

.skill-card__more-btn:hover {
  color: var(--el-color-primary);
}

.skill-card__name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 4px;
}

.skill-card__name {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.skill-card__desc {
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

.skill-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--el-border-color-light);
}

.skill-card__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
  overflow: hidden;
}

.type-chip {
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

.type-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.skill-card__info {
  font-size: 11px;
  white-space: nowrap;
  flex-shrink: 0;
}
</style>
