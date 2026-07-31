<script setup lang="ts">
import { ref, computed } from "vue";
import { useI18n } from "vue-i18n";

const { t } = useI18n();

const props = withDefaults(
  defineProps<{
    formInline: {
      groupName: string;
      currentMemberIds: string[];
      allUsers: Array<{ id: string; username: string; email: string }>;
    };
  }>(),
  {
    formInline: () => ({
      groupName: "",
      currentMemberIds: [],
      allUsers: []
    })
  }
);

const newFormInline = ref(props.formInline);
const searchQuery = ref("");

const filteredUsers = computed(() => {
  if (!searchQuery.value) return newFormInline.value.allUsers;
  const q = searchQuery.value.toLowerCase();
  return newFormInline.value.allUsers.filter(
    u =>
      u.username.toLowerCase().includes(q) ||
      u.email.toLowerCase().includes(q)
  );
});

function toggleMember(userId: string) {
  const idx = newFormInline.value.currentMemberIds.indexOf(userId);
  if (idx >= 0) {
    newFormInline.value.currentMemberIds.splice(idx, 1);
  } else {
    newFormInline.value.currentMemberIds.push(userId);
  }
}

function isMember(userId: string): boolean {
  return newFormInline.value.currentMemberIds.includes(userId);
}
</script>

<template>
  <div class="member-manager">
    <div class="mb-3">
      <span class="text-sm text-gray-500">
        {{ t("system.userGroup.member.selected", { count: newFormInline.currentMemberIds.length }) }}
      </span>
    </div>
    <el-input
      v-model="searchQuery"
      :placeholder="t('system.userGroup.member.searchPlaceholder')"
      clearable
      class="mb-3"
    />
    <div class="user-list max-h-80 overflow-y-auto border rounded">
      <div
        v-for="user in filteredUsers"
        :key="user.id"
        class="user-item flex items-center justify-between px-3 py-2 hover:bg-gray-50 cursor-pointer border-b last:border-b-0"
        @click="toggleMember(user.id)"
      >
        <div class="flex items-center gap-2">
          <el-checkbox
            :model-value="isMember(user.id)"
            @click.stop="toggleMember(user.id)"
          />
          <div>
            <div class="text-sm font-medium">{{ user.username }}</div>
            <div class="text-xs text-gray-400">{{ user.email }}</div>
          </div>
        </div>
        <el-tag v-if="isMember(user.id)" size="small" type="success">
          {{ t("system.userGroup.member.joined") }}
        </el-tag>
      </div>
      <div
        v-if="filteredUsers.length === 0"
        class="text-center text-gray-400 py-8"
      >
        {{ t("system.userGroup.member.empty") }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.user-item {
  transition: background-color 0.2s;
}
.user-item:hover {
  background-color: var(--el-fill-color-light);
}
</style>
