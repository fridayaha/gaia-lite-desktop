<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ReText } from "@/components/ReText";
import Profile from "./components/Profile.vue";
import { ref, onBeforeMount, computed } from "vue";
import SecurityLog from "./components/SecurityLog.vue";
import { useGlobal, deviceDetection, isAllEmpty } from "@pureadmin/utils";
import { useDataThemeChange } from "@/layout/hooks/useDataThemeChange";
import LaySidebarTopCollapse from "@/layout/components/lay-sidebar/components/SidebarTopCollapse.vue";
import { useUserStoreHook } from "@/store/modules/user";
import DefaultAvatar from "@/assets/user.jpg";

import leftLine from "~icons/ri/arrow-left-s-line";
import LogoutCircleRLine from "~icons/ri/logout-circle-r-line";
import ProfileIcon from "~icons/ri/user-3-line";
import SecurityLogIcon from "~icons/ri/window-line";

defineOptions({
  name: "AccountSettings"
});

const router = useRouter();
const { t } = useI18n();
const isOpen = ref(deviceDetection() ? false : true);
const { $storage } = useGlobal<GlobalPropertiesApi>();
onBeforeMount(() => {
  useDataThemeChange().dataThemeChange($storage.layout?.themeMode);
});

// aside 头像 + 昵称直接读 store，使 Profile.vue 更新头像/昵称后 aside 响应式刷新。
// avatar 为空时用本地默认头像兜底，与 navbar 右上角头像行为一致。
const userStore = useUserStoreHook();
const userInfo = computed(() => ({
  avatar: isAllEmpty(userStore.avatar) ? DefaultAvatar : userStore.avatar,
  username: userStore.username || "",
  nickname: userStore.nickname || ""
}));
const panes = computed(() => [
  {
    key: "profile",
    label: t("account.tabs.profile"),
    icon: ProfileIcon,
    component: Profile
  },
  {
    key: "securityLog",
    label: t("account.tabs.securityLog"),
    icon: SecurityLogIcon,
    component: SecurityLog
  }
]);
const witchPane = ref("profile");
</script>

<template>
  <el-container class="h-full">
    <DocsLink to="account-settings.html" />
    <el-aside
      v-if="isOpen"
      class="pure-account-settings overflow-hidden px-2 dark:bg-(--el-bg-color)! border-r border-(--pure-border-color)"
      :width="deviceDetection() ? '180px' : '240px'"
    >
      <el-menu :default-active="witchPane" class="pure-account-settings-menu">
        <div
          class="h-12.5! text-(--pure-theme-menu-text) cursor-pointer text-sm transition-all duration-300 ease-in-out hover:scale-105 will-change-transform transform-gpu origin-center hover:text-base! hover:text-(--pure-theme-menu-title-hover)!"
          @click="router.go(-1)"
        >
          <div
            class="h-full flex items-center px-(--el-menu-base-level-padding)"
          >
            <IconifyIconOffline :icon="leftLine" />
            <span class="ml-2">{{ t("account.back") }}</span>
          </div>
        </div>
        <div
          class="h-12.5! text-(--pure-theme-menu-text) cursor-pointer text-sm transition-all duration-300 ease-in-out hover:scale-105 will-change-transform transform-gpu origin-center hover:text-base! hover:text-(--pure-theme-menu-title-hover)!"
          @click="userStore.logOut()"
        >
          <div
            class="h-full flex items-center px-(--el-menu-base-level-padding)"
          >
            <IconifyIconOffline :icon="LogoutCircleRLine" />
            <span class="ml-2">{{ t("buttons.pureLoginOut") }}</span>
          </div>
        </div>
        <div class="flex items-center ml-8 my-4">
          <el-avatar :size="48" :src="userInfo.avatar" />
          <div class="ml-4 flex flex-col max-w-32.5">
            <ReText class="font-bold self-baseline!">
              {{ userInfo.nickname }}
            </ReText>
            <ReText class="self-baseline!" type="info">
              {{ userInfo.username }}
            </ReText>
          </div>
        </div>
        <el-menu-item
          v-for="item in panes"
          :key="item.key"
          :index="item.key"
          @click="
            () => {
              witchPane = item.key;
              if (deviceDetection()) {
                isOpen = !isOpen;
              }
            }
          "
        >
          <div class="flex items-center z-10">
            <el-icon><IconifyIconOffline :icon="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </div>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <LaySidebarTopCollapse
        v-if="deviceDetection()"
        class="px-0"
        :is-active="isOpen"
        @toggleClick="isOpen = !isOpen"
      />
      <component
        :is="panes.find(item => item.key === witchPane).component"
        :class="[!deviceDetection() && 'ml-30']"
      />
    </el-main>
  </el-container>
</template>

<style lang="scss">
.pure-account-settings {
  background: var(--pure-theme-menu-bg) !important;
}

.pure-account-settings-menu {
  background-color: transparent;
  border: none;

  .el-menu-item {
    height: 48px !important;
    color: var(--pure-theme-menu-text);
    background-color: transparent !important;
    transition: color 0.2s;

    &:hover {
      color: var(--pure-theme-menu-title-hover) !important;
    }

    &.is-active {
      color: #fff !important;

      &:hover {
        color: #fff !important;
      }

      &::before {
        position: absolute;
        inset: 0 8px;
        clear: both;
        margin: 4px 0;
        content: "";
        background: var(--el-color-primary);
        border-radius: 3px;
      }
    }
  }
}
</style>

<style lang="scss" scoped>
body[layout] {
  .el-menu--vertical .is-active {
    color: #fff !important;
    transition: color 0.2s;

    &:hover {
      color: #fff !important;
    }
  }
}
</style>
