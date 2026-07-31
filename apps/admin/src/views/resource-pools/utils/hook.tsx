import editForm from "../form.vue";
import { message } from "@/utils/message";
import { addDialog, closeDialog } from "@/components/ReDialog";
import { i18n } from "@/plugins/i18n";
import { deviceDetection } from "@pureadmin/utils";
import type { ResourcePoolResponse } from "@/api/manager/resourcePools";
import type { FormItemProps } from "./types";
import {
  getResourcePoolsApi,
  getResourcePoolApi,
  deleteResourcePoolApi,
  cloneResourcePoolApi
} from "@/api/manager/resourcePools";
import { getUserGroupsApi } from "@/api/manager/userGroups";
import { useUserStoreHook } from "@/store/modules/user";
import { h, ref, reactive, computed, onMounted } from "vue";

const t = i18n.global.t as unknown as (
  key: string,
  named?: Record<string, unknown>
) => string;

/** Module-level dialog opener — can be called from detail page too */
export async function openResourcePoolDialog(
  mode: "create" | "edit",
  data?: Partial<FormItemProps>,
  onSuccess?: () => void
) {
  const formRef = ref();
  const isEdit = mode === "edit";
  const stepLabels: Record<number, string> = {
    0: t("common.action.next"),
    1: t("common.action.next"),
    2: t("common.action.ok")
  };

  // 加载目标组候选 + 判断平台管理员（决定能否选"平台共享"）
  const roles = useUserStoreHook().roles || [];
  const isPlatformAdmin = roles.includes("平台管理员") || roles.includes("系统管理员");
  let allGroups: { id: string; name: string }[] = [];
  let defaultGroupId = "";
  try {
    const groups = await getUserGroupsApi();
    allGroups = (groups || []).map((g: any) => ({ id: g.id, name: g.name }));
    if (!isPlatformAdmin && allGroups.length === 1) {
      defaultGroupId = allGroups[0].id; // 组用户单组自动归属本组
    }
  } catch {
    // 加载失败不阻塞，按无组处理
  }

  /** 按当前 step 刷新底部按钮：上一步 disabled / 下一步 label */
  const refreshFooter = (options: any, step: number) => {
    const btns = options?.footerButtons;
    if (btns && btns.length >= 3) {
      btns[1].disabled = step === 0; // 上一步
      btns[2].label = stepLabels[step] || t("common.action.ok");
    }
  };

  addDialog({
    title: t("engine.dialogTitle", {
      title: isEdit ? t("common.action.edit") : t("common.action.add")
    }),
    props: {
      formInline: {
        title: mode,
        id: data?.id,
        name: data?.name ?? "",
        description: data?.description ?? "",
        group_id: data?.group_id ?? defaultGroupId,
        allGroups,
        isPlatformAdmin,
        min_cpu: data?.min_cpu ?? "100m",
        max_cpu: data?.max_cpu ?? "2",
        min_memory: data?.min_memory ?? "256Mi",
        max_memory: data?.max_memory ?? "2Gi",
        min_replicas: data?.min_replicas ?? 1,
        max_replicas: data?.max_replicas ?? 5,
        auto_recycle: data?.auto_recycle ?? true,
        idle_suspend_minutes: data?.idle_suspend_minutes ?? 30,
        idle_destroy_hours: data?.idle_destroy_hours ?? 24,
        max_sessions_per_pod: data?.max_sessions_per_pod ?? 20
      }
    },
    width: "55%",
    draggable: true,
    fullscreen: deviceDetection(),
    fullscreenIcon: true,
    closeOnClickModal: false,
    contentRenderer: () => h(editForm, { ref: formRef, formInline: null }),
    footerButtons: [
      {
        label: t("common.action.cancel"),
        text: true,
        bg: true,
        btnClick: ({ dialog: { options, index } }) => {
          closeDialog(options, index);
        }
      },
      {
        label: t("common.action.prev"),
        text: true,
        bg: true,
        disabled: true,
        btnClick: ({ dialog: { options } }) => {
          const form = formRef.value;
          if (!form) return;
          form.prevStep();
          refreshFooter(options, form.getCurrentStep());
        }
      },
      {
        label: t("common.action.next"),
        type: "primary",
        text: true,
        bg: true,
        btnClick: ({ dialog: { options, index } }) => {
          if (options?.beforeSure) {
            options.beforeSure(
              () => closeDialog(options, index),
              { options, index, closeLoading: () => {} }
            );
          }
        }
      }
    ],
    beforeSure: async (done, { options }) => {
      const form = formRef.value;
      if (!form) return;

      const submitted = await form.submitStep();
      if (submitted) {
        done();
        onSuccess?.();
        return;
      }

      // Update button label for next step
      refreshFooter(options, form.getCurrentStep());
    }
  });
}

export function useResourcePool() {
  const allPools = ref<ResourcePoolResponse[]>([]);
  const loading = ref(true);

  const searchText = ref("");

  const pagination = reactive({
    total: 0,
    pageSize: 12,
    currentPage: 1,
    background: true
  });

  const filteredPools = computed(() => {
    let list = allPools.value;
    if (searchText.value) {
      const q = searchText.value.toLowerCase();
      list = list.filter(
        i =>
          i.name.toLowerCase().includes(q) ||
          i.description?.toLowerCase().includes(q)
      );
    }
    return list;
  });

  const pagedList = computed(() => {
    const start = pagination.pageSize * (pagination.currentPage - 1);
    const end = start + pagination.pageSize;
    return filteredPools.value.slice(start, end);
  });

  const totalCount = computed(() => filteredPools.value.length);
  const autoRecycleCount = computed(() => filteredPools.value.filter(i => i.auto_recycle).length);
  const manualCount = computed(() => filteredPools.value.filter(i => !i.auto_recycle).length);

  async function onSearch() {
    loading.value = true;
    try {
      const res = await getResourcePoolsApi({ page: 1, page_size: 100 });
      allPools.value = res.items || [];
      pagination.total = res.total || 0;
    } catch (e) {
      console.error("Load resource pools failed:", e);
    } finally {
      loading.value = false;
    }
  }

  function handleCurrentChange(page: number) {
    pagination.currentPage = page;
  }

  function handleSizeChange(size: number) {
    pagination.pageSize = size;
    pagination.currentPage = 1;
  }

  // ── CRUD Handlers ──

  /** Open create dialog */
  function openDialog(): void;
  /** Open edit dialog with existing pool data (shallow, fetches full data internally) */
  function openDialog(mode: "edit", row: ResourcePoolResponse): void;
  function openDialog(mode: "create" | "edit" = "create", row?: ResourcePoolResponse) {
    if (mode === "edit" && row?.id) {
      // Fetch full data for edit
      getResourcePoolApi(row.id)
        .then(data => {
          openResourcePoolDialog("edit", data, onSearch);
        })
        .catch((e: any) => {
          message(t("engine.msg.loadDataFailed", { detail: e?.message || e }), { type: "error" });
        });
    } else {
      openResourcePoolDialog("create", undefined, onSearch);
    }
  }

  async function handleClone(pool: ResourcePoolResponse) {
    try {
      await cloneResourcePoolApi(pool.id);
      message(t("engine.msg.cloneOk"), { type: "success" });
      await onSearch();
    } catch (e: any) {
      message(t("engine.msg.cloneFailed", { detail: e?.message || e }), { type: "error" });
    }
  }

  async function handleDelete(pool: ResourcePoolResponse) {
    try {
      await deleteResourcePoolApi(pool.id);
      message(t("engine.msg.deleteOk"), { type: "success" });
      await onSearch();
    } catch (e: any) {
      message(t("engine.msg.deleteFailed", { detail: e?.response?.data?.detail || e?.message || e }), { type: "error" });
    }
  }

  onMounted(onSearch);

  return {
    allPools, loading, searchText,
    pagination, filteredPools, pagedList,
    totalCount, autoRecycleCount, manualCount,
    onSearch, handleCurrentChange, handleSizeChange,
    openDialog, handleClone, handleDelete
  };
}
