import editForm from "../form.vue";
import { message } from "@/utils/message";
import { addDialog, closeDialog } from "@/components/ReDialog";
import { deviceDetection } from "@pureadmin/utils";
import { i18n } from "@/plugins/i18n";
import type { AgentInstanceResponse } from "@/api/manager/agentInstances";
import type { FormItemProps } from "./types";
import {
  getInstancesApi,
  createInstanceApi,
  updateInstanceApi,
  publishInstanceApi,
  offlineInstanceApi,
  deleteInstanceApi,
  cloneInstanceApi
} from "@/api/manager/agentInstances";
import { getDefinitionsApi } from "@/api/manager/agentDefinitions";
import { getResourcePoolsApi } from "@/api/manager/resourcePools";
import { getUserGroupsApi } from "@/api/manager/userGroups";
import { h, ref, reactive, computed, onMounted } from "vue";
import { dialogStore } from "@/components/ReDialog";

const t = i18n.global.t as unknown as (
  key: string,
  named?: Record<string, unknown>
) => string;

/** 模块级弹窗开启 — 列表页与详情页共用 */
export function openInstanceDialog(
  mode: "create" | "edit",
  data?: Partial<FormItemProps>,
  onSuccess?: () => void
) {
  const formRef = ref();
  const isEdit = mode === "edit";
  /** 按当前 step 刷新底部按钮：上一步 disabled / 下一步 label（最后一步显示"确定"）。
   *  maxStep 随 definition 选择动态变化（Dify=2，非 Dify=1），故 refreshFooter 不带参，
   *  直接读 dialogStore 里的 reactive options + formRef 实时值。
   *  注意：必须通过 dialogStore.value[idx] 拿 reactive proxy，不能用模块级原始对象引用，
   *  否则 mutation footerButtons[i].label 不触发模板 re-render（按钮文字不刷新）。 */
  const dialogKey = ref(0);

  const refreshFooter = () => {
    // dialogStore 是 ref，value 是 reactive 数组；数组元素是 reactive proxy。
    // 通过 proxy 修改 label 才能触发 index.vue 模板 re-render。
    const options = dialogStore.value[dialogKey.value];
    const btns = options?.footerButtons;
    const form = formRef.value;
    if (btns && btns.length >= 3) {
      const step = form?.getCurrentStep() ?? 0;
      const maxStep = form?.getMaxStep() ?? 1;
      btns[1].disabled = step === 0; // 上一步
      btns[2].label = step < maxStep ? t("common.action.next") : t("common.action.ok");
    }
  };

  // 并行加载定义 / 资源池 / 用户组（目标组候选）
  Promise.all([
    getDefinitionsApi({ page: 1, page_size: 100 }).catch(() => ({ items: [] })),
    getResourcePoolsApi({ page: 1, page_size: 100 }).catch(() => ({ items: [] })),
    getUserGroupsApi().catch(() => [])
  ]).then(([defsRes, poolsRes, groups]) => {
    const allDefinitions = (defsRes?.items || []) as any[];
    const allResourcePools = (poolsRes?.items || []) as any[];
    const allGroups = ((groups || []) as any[]).map((g: any) => ({
      id: g.id,
      name: g.name
    }));

    addDialog({
      title: isEdit ? t("instance.editTitle") : t("instance.create"),
      props: {
        formInline: {
          title: mode,
          id: data?.id,
          name: data?.name ?? "",
          description: data?.description ?? "",
          definition_id: data?.definition_id ?? "",
          version_id: data?.version_id ?? "",
          resource_pool_id: data?.resource_pool_id ?? "",
          group_id: data?.group_id ?? "",
          dify_config: data?.dify_config,
          runtime_config: data?.runtime_config,
          allDefinitions,
          allVersions: [],
          allResourcePools,
          allGroups
        }
      },
      width: "55%",
      draggable: true,
      fullscreen: deviceDetection(),
      fullscreenIcon: true,
      closeOnClickModal: false,
      contentRenderer: () =>
        h(editForm, {
          ref: formRef,
          formInline: null,
          onStepConfigChange: () => refreshFooter()
        }),
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
            refreshFooter();
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
        refreshFooter();
      }
    });

    // 记录当前 dialog 在 dialogStore 的索引，供 refreshFooter 通过 reactive proxy 改按钮 label
    dialogKey.value = dialogStore.value.length - 1;
    // dialog 初始渲染后刷新一次按钮（maxStep/step 已定）
    requestAnimationFrame(() => refreshFooter());
  });
}

export function useInstance() {
  const allInstances = ref<AgentInstanceResponse[]>([]);
  const loading = ref(true);

  const searchText = ref("");
  const statusFilter = ref("");
  const engineFilter = ref("");

  // Client-side pagination
  const pagination = reactive({
    total: 0,
    pageSize: 12,
    currentPage: 1,
    background: true
  });

  const filteredInstances = computed(() => {
    let list = allInstances.value;
    if (searchText.value) {
      const q = searchText.value.toLowerCase();
      list = list.filter(
        a =>
          a.name.toLowerCase().includes(q) ||
          a.description?.toLowerCase().includes(q)
      );
    }
    if (statusFilter.value) {
      list = list.filter(a => a.status === statusFilter.value);
    }
    if (engineFilter.value) {
      list = list.filter(a => a.engine_type === engineFilter.value);
    }
    return list;
  });

  const pagedList = computed(() => {
    const start = pagination.pageSize * (pagination.currentPage - 1);
    const end = start + pagination.pageSize;
    return filteredInstances.value.slice(start, end);
  });

  const publishedCount = computed(
    () => filteredInstances.value.filter(a => a.status === "PUBLISHED").length
  );
  const draftCount = computed(
    () => filteredInstances.value.filter(a => a.status === "DRAFT").length
  );
  const offlineCount = computed(
    () => filteredInstances.value.filter(a => a.status === "OFFLINE").length
  );

  async function onSearch() {
    loading.value = true;
    try {
      const res = await getInstancesApi({ page: 1, page_size: 100 });
      allInstances.value = res.items || [];
      pagination.total = res.total || 0;
    } catch {
      message(t("instance.msg.fetchListFailed"), { type: "error" });
    } finally {
      loading.value = false;
    }
  }

  function handleSizeChange(val: number) {
    pagination.pageSize = val;
    pagination.currentPage = 1;
  }

  function handleCurrentChange(val: number) {
    pagination.currentPage = val;
  }

  function openDialog(mode: "create" | "edit" = "create", row?: AgentInstanceResponse) {
    if (mode === "edit" && row) {
      openInstanceDialog(
        "edit",
        {
          id: row.id,
          name: row.name,
          description: row.description,
          definition_id: row.definition_id,
          version_id: row.version_id ?? "",
          resource_pool_id: row.resource_pool_id,
          group_id: row.group_id,
          dify_config: row.dify_config,
          runtime_config: row.runtime_config
        },
        onSearch
      );
    } else {
      openInstanceDialog("create", undefined, onSearch);
    }
  }

  async function handlePublish(row: AgentInstanceResponse) {
    try {
      await publishInstanceApi(row.id);
      message(t("instance.msg.onlined"), { type: "success" });
      onSearch();
    } catch (err: any) {
      message(err?.response?.data?.detail || t("instance.msg.onlinedFailed"), {
        type: "error"
      });
    }
  }

  async function handleOffline(row: AgentInstanceResponse) {
    try {
      await offlineInstanceApi(row.id);
      message(t("instance.msg.offlined"), { type: "success" });
      onSearch();
    } catch (err: any) {
      message(err?.response?.data?.detail || t("instance.msg.offlineFailed"), {
        type: "error"
      });
    }
  }

  async function handleDelete(row: AgentInstanceResponse) {
    try {
      await deleteInstanceApi(row.id);
      message(t("instance.msg.deleted", { name: row.name }), { type: "success" });
      await onSearch();
      if (pagedList.value.length === 0 && pagination.currentPage > 1) {
        pagination.currentPage -= 1;
      }
    } catch (err: any) {
      message(
        err?.response?.data?.detail || t("instance.msg.deleteFailed"),
        { type: "error" }
      );
    }
  }

  async function handleClone(row: AgentInstanceResponse) {
    try {
      await cloneInstanceApi(row.id);
      message(t("instance.msg.cloneOk"), { type: "success" });
      onSearch();
    } catch (err: any) {
      message(err?.response?.data?.detail || t("instance.msg.cloneFailed"), {
        type: "error"
      });
    }
  }

  onMounted(onSearch);

  return {
    loading,
    allInstances,
    filteredInstances,
    pagedList,
    pagination,
    searchText,
    statusFilter,
    engineFilter,
    publishedCount,
    draftCount,
    offlineCount,
    onSearch,
    openDialog,
    handlePublish,
    handleOffline,
    handleDelete,
    handleClone,
    handleSizeChange,
    handleCurrentChange
  };
}
