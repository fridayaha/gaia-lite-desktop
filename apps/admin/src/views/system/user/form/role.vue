<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import ReCol from "@/components/ReCol";
import { RoleFormProps } from "../utils/types";

const props = withDefaults(defineProps<RoleFormProps>(), {
  formInline: () => ({
    username: "",
    roleOptions: [],
    ids: []
  })
});

const { t } = useI18n();
const newFormInline = ref(props.formInline);
</script>

<template>
  <el-form :model="newFormInline">
    <el-row :gutter="30">
      <re-col>
        <el-form-item :label="t('system.user.roleForm.username')" prop="username">
          <el-input v-model="newFormInline.username" disabled />
        </el-form-item>
      </re-col>
      <re-col>
        <el-form-item :label="t('system.user.roleForm.roleList')" prop="ids">
          <el-select
            v-model="newFormInline.ids"
            :placeholder="t('system.user.form.selectPlaceholder')"
            class="w-full"
            clearable
            multiple
          >
            <el-option
              v-for="(item, index) in newFormInline.roleOptions"
              :key="index"
              :value="item.id"
              :label="item.name"
            >
              {{ item.name }}
            </el-option>
          </el-select>
        </el-form-item>
      </re-col>
    </el-row>
  </el-form>
</template>
