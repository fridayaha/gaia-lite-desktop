<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useUser } from "./utils/hook";
import { PureTableBar } from "@/components/RePureTableBar";
import { useRenderIcon } from "@/components/ReIcon/src/hooks";

import Role from "~icons/ri/admin-line";
import Password from "~icons/ri/lock-password-line";
import More from "~icons/ep/more-filled";
import Delete from "~icons/ep/delete";
import EditPen from "~icons/ep/edit-pen";
import AddFill from "~icons/ri/add-circle-line";
import SearchLine from "~icons/ri/search-line";
import LockUnlockLine from "~icons/ri/lock-unlock-line";
import MailVerifyLine from "~icons/ri/mail-line";
import PhoneVerifyLine from "~icons/ri/phone-line";

defineOptions({
  name: "SystemUser"
});

const { t } = useI18n();
const tableRef = ref();

const {
  form,
  loading,
  columns,
  dataList,
  selectedNum,
  pagination,
  buttonClass,
  deviceDetection,
  onSearch,
  onbatchDel,
  openDialog,
  handleUpdate,
  handleDelete,
  handleUnlock,
  handleVerifyEmail,
  handleVerifyPhone,
  handleReset,
  handleRole,
  handleSizeChange,
  onSelectionCancel,
  handleCurrentChange,
  handleSelectionChange
} = useUser(tableRef);

function onSearchClear() {
  form.is_active = "";
  onSearch();
}

function onStatusChange() {
  onSearch();
}

function onKeyupEnter() {
  onSearch();
}
</script>

<template>
  <div class="main">
    <DocsLink to="system.html#user" />

    <PureTableBar
      :title="t('system.user.title')"
      :columns="columns"
      @refresh="onSearch"
    >
      <template #buttons>
        <div class="w-full flex flex-wrap items-center justify-between gap-3">
          <el-button
            type="primary"
            :icon="useRenderIcon(AddFill)"
            @click="openDialog()"
          >
            {{ t("system.user.create") }}
          </el-button>
          <div class="flex items-center gap-3 flex-wrap">
            <el-select
              v-model="form.is_active"
              :placeholder="t('system.user.filter.allStatus')"
              clearable
              style="width: 120px"
              @change="onStatusChange"
            >
              <el-option :label="t('common.status.enabled')" value="true" />
              <el-option :label="t('common.status.disabled')" value="false" />
            </el-select>
            <el-input
              v-model="form.username"
              style="width: 260px"
              :placeholder="t('system.user.filter.searchPlaceholder')"
              clearable
              @keyup.enter="onKeyupEnter"
              @clear="onSearch"
            >
              <template #suffix>
                <el-icon class="el-input__icon">
                  <SearchLine v-show="form.username.length === 0" />
                </el-icon>
              </template>
            </el-input>
          </div>
        </div>
      </template>
      <template v-slot="{ size, dynamicColumns }">
        <div
          v-if="selectedNum > 0"
          v-motion-fade
          class="bg-(--el-fill-color-light) w-full h-11.5 mb-2 pl-4 flex items-center"
        >
          <div class="flex-auto">
            <span
              style="font-size: var(--el-font-size-base)"
              class="text-[rgba(42,46,54,0.5)] dark:text-[rgba(220,220,242,0.5)]"
            >
              {{ t("system.user.selected", { count: selectedNum }) }}
            </span>
            <el-button type="primary" text @click="onSelectionCancel">
              {{ t("system.user.cancelSelect") }}
            </el-button>
          </div>
          <el-popconfirm :title="t('system.user.confirmBatchDelete')" @confirm="onbatchDel">
            <template #reference>
              <el-button type="danger" text class="mr-1!">
                {{ t("system.user.batchDelete") }}
              </el-button>
            </template>
          </el-popconfirm>
        </div>
        <pure-table
          ref="tableRef"
          row-key="id"
          adaptive
          :adaptiveConfig="{ offsetBottom: 108 }"
          align-whole="center"
          table-layout="auto"
          :loading="loading"
          :size="size"
          :data="dataList"
          :columns="dynamicColumns"
          :pagination="{ ...pagination, size }"
          :header-cell-style="{
            background: 'var(--el-fill-color-light)',
            color: 'var(--el-text-color-primary)'
          }"
          @selection-change="handleSelectionChange"
          @page-size-change="handleSizeChange"
          @page-current-change="handleCurrentChange"
        >
          <template #operation="{ row }">
            <el-button
              class="reset-margin"
              link
              type="primary"
              :size="size"
              :icon="useRenderIcon(EditPen)"
              @click="openDialog('edit', row)"
            >
              {{ t("common.action.edit") }}
            </el-button>
            <el-button
              class="reset-margin"
              link
              type="primary"
              :size="size"
              :icon="useRenderIcon(Delete)"
              @click="handleDelete(row)"
            >
              {{ t("common.action.delete") }}
            </el-button>
            <el-dropdown>
              <el-button
                class="ml-3! mt-0.5!"
                link
                type="primary"
                :size="size"
                :icon="useRenderIcon(More)"
                @click="handleUpdate(row)"
              />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-if="row.is_locked">
                    <el-button
                      :class="buttonClass"
                      link
                      type="primary"
                      :size="size"
                      :icon="useRenderIcon(LockUnlockLine)"
                      @click="handleUnlock(row)"
                    >
                      {{ t("system.user.locked.unlockLabel") }}
                    </el-button>
                  </el-dropdown-item>
                  <el-dropdown-item v-if="row.email && !row.email_verified">
                    <el-button
                      :class="buttonClass"
                      link
                      type="primary"
                      :size="size"
                      :icon="useRenderIcon(MailVerifyLine)"
                      @click="handleVerifyEmail(row)"
                    >
                      {{ t("system.user.msg.verifyEmail") }}
                    </el-button>
                  </el-dropdown-item>
                  <el-dropdown-item v-if="row.phone && !row.phone_verified">
                    <el-button
                      :class="buttonClass"
                      link
                      type="primary"
                      :size="size"
                      :icon="useRenderIcon(PhoneVerifyLine)"
                      @click="handleVerifyPhone(row)"
                    >
                      {{ t("system.user.msg.verifyPhone") }}
                    </el-button>
                  </el-dropdown-item>
                  <el-dropdown-item>
                    <el-button
                      :class="buttonClass"
                      link
                      type="primary"
                      :size="size"
                      :icon="useRenderIcon(Password)"
                      @click="handleReset(row)"
                    >
                      {{ t("system.user.pwd.resetLabel") }}
                    </el-button>
                  </el-dropdown-item>
                  <el-dropdown-item>
                    <el-button
                      :class="buttonClass"
                      link
                      type="primary"
                      :size="size"
                      :icon="useRenderIcon(Role)"
                      @click="handleRole(row)"
                    >
                      {{ t("system.user.role.assignLabel") }}
                    </el-button>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </pure-table>
      </template>
    </PureTableBar>
  </div>
</template>

<style lang="scss" scoped>
:deep(.el-dropdown-menu__item i) {
  margin: 0;
}

:deep(.el-button:focus-visible) {
  outline: none;
}

.main-content {
  margin: 24px 24px 0 !important;
}

.search-form {
  :deep(.el-form-item) {
    margin-bottom: 12px;
  }
}
</style>
