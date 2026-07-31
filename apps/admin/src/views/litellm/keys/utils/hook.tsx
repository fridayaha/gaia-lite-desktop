import { ref, reactive, computed, onMounted, type Ref } from "vue";
import { h } from "vue";
import { useI18n } from "vue-i18n";
import { message } from "@/utils/message";
import { ElMessageBox } from "element-plus";
import { addDialog } from "@/components/ReDialog";
import EditForm from "../components/EditForm.vue";
import type { KeyEditFormItem } from "./types";
import {
  getKeysApi,
  updateKeyApi,
  deleteKeyApi,
  blockKeyApi,
  unblockKeyApi
} from "@/api/manager/litellm";
import { getUserGroupsApi } from "@/api/manager/userGroups";

export function useKeys(_tableRef?: Ref) {
  const { t } = useI18n();
  const loading = ref(false);
  const groups = ref<any[]>([]);
  const groupFilter = ref<string>("");
  const keys = ref<any[]>([]);
  const searchText = ref("");
  const formRef = ref();

  async function loadGroups() {
    try {
      groups.value = await getUserGroupsApi();
    } catch (err: any) {
      console.error("load groups failed:", err?.response?.data?.detail || err);
      message(t("litellm.key.msg.loadGroupsFailed"), { type: "error" });
    }
  }

  async function loadKeys() {
    loading.value = true;
    try {
      const res = await getKeysApi(groupFilter.value || undefined);
      keys.value = res.items;
    } catch (err: any) {
      console.error("load keys failed:", err?.response?.data?.detail || err);
      message(t("litellm.key.msg.loadFailed"), { type: "error" });
      keys.value = [];
    } finally {
      loading.value = false;
    }
  }

  function groupName(teamId: string): string {
    if (!teamId) return "—";
    if (teamId === "default") return t("litellm.platformDefault");
    const g = groups.value.find(x => x.id === teamId);
    return g?.name || teamId;
  }

  /** 所属智能体名：后端 list_keys 已按 metadata.instance_id 富化 agent_name；
   *  非 per-instance key（手动创建）无 agent_name → 显示 — */
  function agentName(row: any): string {
    return row?.agent_name || "—";
  }

  const filteredKeys = computed(() => {
    const kw = searchText.value.trim().toLowerCase();
    if (!kw) return keys.value;
    return keys.value.filter(k => {
      return (
        agentName(k).toLowerCase().includes(kw) ||
        groupName(k.team_id).toLowerCase().includes(kw) ||
        (k.key_alias || "").toLowerCase().includes(kw)
      );
    });
  });

  const stats = computed(() => {
    const total = keys.value.length;
    const blocked = keys.value.filter(k => k.blocked === true).length;
    return { total, normal: total - blocked, blocked };
  });

  const pagination = reactive({ pageSize: 10, currentPage: 1, background: true });
  const pagedList = computed(() => {
    const start = pagination.pageSize * (pagination.currentPage - 1);
    return filteredKeys.value.slice(start, start + pagination.pageSize);
  });
  function handleSizeChange(val: number) {
    pagination.pageSize = val;
    pagination.currentPage = 1;
  }
  function handleCurrentChange(val: number) {
    pagination.currentPage = val;
  }

  // ── 封禁/解封 ──
  async function handleToggleBlock(row: any) {
    const blocked = row.blocked === true;
    try {
      if (blocked) {
        await ElMessageBox.confirm(t("litellm.key.msg.confirmUnblock"), t("litellm.key.action.unblock"), {
          type: "info",
          confirmButtonText: t("litellm.key.action.unblock"),
          cancelButtonText: t("common.action.cancel")
        });
      } else {
        await ElMessageBox.confirm(t("litellm.key.msg.confirmBlock"), t("litellm.key.blockTitle"), {
          type: "warning",
          confirmButtonText: t("litellm.key.action.block"),
          cancelButtonText: t("common.action.cancel")
        });
      }
    } catch {
      return;
    }
    try {
      if (blocked) {
        await unblockKeyApi(row.token);
        message(t("litellm.key.msg.unblocked"), { type: "success" });
      } else {
        await blockKeyApi(row.token);
        message(t("litellm.key.msg.blocked"), { type: "success" });
      }
      await loadKeys();
    } catch (err: any) {
      console.error("toggle block failed:", err?.response?.data?.detail || err);
      message(t("litellm.key.msg.operationFailed"), { type: "error" });
    }
  }

  async function handleDelete(row: any) {
    try {
      await ElMessageBox.confirm(t("litellm.key.msg.confirmRevoke"), t("litellm.key.msg.revokeTitle"), {
        type: "warning",
        confirmButtonText: t("litellm.key.action.revoke"),
        cancelButtonText: t("common.action.cancel")
      });
    } catch {
      return;
    }
    try {
      await deleteKeyApi(row.token);
      message(t("litellm.key.msg.revoked"), { type: "success" });
      await loadKeys();
    } catch (err: any) {
      console.error("revoke key failed:", err?.response?.data?.detail || err);
      message(t("litellm.key.msg.revokeFailed"), { type: "error" });
    }
  }

  // ── 预算/限速编辑（addDialog + EditForm） ──
  function openEdit(row: any) {
    const formInline: KeyEditFormItem = {
      max_budget: row.max_budget ?? undefined,
      budget_duration: row.budget_duration || "",
      rpm_limit: row.rpm_limit ?? undefined,
      tpm_limit: row.tpm_limit ?? undefined,
      duration: row.duration || ""
    };
    addDialog({
      title: t("litellm.key.dialogEditTitle"),
      width: "500px",
      draggable: true,
      closeOnClickModal: false,
      contentRenderer: () => h(EditForm, { ref: formRef, formInline }),
      beforeSure: async done => {
        const FormRef = formRef.value.getRef();
        await FormRef.validate(async (valid: boolean) => {
          if (!valid) return;
          try {
            await updateKeyApi(row.token, {
              max_budget: formInline.max_budget,
              budget_duration: formInline.budget_duration || undefined,
              rpm_limit: formInline.rpm_limit,
              tpm_limit: formInline.tpm_limit,
              duration: formInline.duration || undefined
            } as any);
            message(t("litellm.key.msg.updated"), { type: "success" });
            done();
            await loadKeys();
          } catch (err: any) {
            console.error("update key failed:", err?.response?.data?.detail || err);
            message(t("litellm.key.msg.updateFailed"), { type: "error" });
          }
        });
      }
    });
  }

  onMounted(async () => {
    await loadGroups();
    await loadKeys();
  });

  return {
    loading,
    groups,
    groupFilter,
    keys,
    searchText,
    filteredKeys,
    stats,
    pagination,
    pagedList,
    groupName,
    agentName,
    handleSizeChange,
    handleCurrentChange,
    handleToggleBlock,
    handleDelete,
    openEdit,
    loadKeys
  };
}
