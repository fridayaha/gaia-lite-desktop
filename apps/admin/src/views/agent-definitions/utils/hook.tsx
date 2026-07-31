import editForm from "../form.vue";
import { message } from "@/utils/message";
import { addDialog, closeDialog } from "@/components/ReDialog";
import { deviceDetection } from "@pureadmin/utils";
import { i18n } from "@/plugins/i18n";
import { ElMessageBox } from "element-plus";
import type { AgentDefinitionResponse } from "@/api/manager/agentDefinitions";
import type { FormItemProps } from "../utils/types";
import {
  getDefinitionsApi,
  createDefinitionApi,
  updateDefinitionApi,
  deleteDefinitionApi,
  publishDefinitionApi
} from "@/api/manager/agentDefinitions";
import { getUserGroupsApi } from "@/api/manager/userGroups";
import { h, ref, reactive, computed, onMounted } from "vue";

export function useDefinition() {
  const t = i18n.global.t as unknown as (
    key: string,
    named?: Record<string, unknown>
  ) => string;
  const allDefinitions = ref<AgentDefinitionResponse[]>([]);
  const allGroups = ref<{ id: string; name: string }[]>([]);
  const loading = ref(true);
  const formRef = ref();

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

  const filteredDefinitions = computed(() => {
    let list = allDefinitions.value;
    if (searchText.value) {
      const q = searchText.value.toLowerCase();
      list = list.filter(
        d =>
          d.name.toLowerCase().includes(q) ||
          d.description?.toLowerCase().includes(q)
      );
    }
    if (statusFilter.value) {
      list = list.filter(d => d.status === statusFilter.value);
    }
    if (engineFilter.value) {
      list = list.filter(d => d.engine_type === engineFilter.value);
    }
    return list;
  });

  const pagedList = computed(() => {
    const start = pagination.pageSize * (pagination.currentPage - 1);
    const end = start + pagination.pageSize;
    return filteredDefinitions.value.slice(start, end);
  });

  const publishedCount = computed(
    () => filteredDefinitions.value.filter(d => d.status === "PUBLISHED").length
  );
  const draftCount = computed(
    () => filteredDefinitions.value.filter(d => d.status === "DRAFT").length
  );

  const statusOptions = [
    { value: "DRAFT", label: t("common.status.draft"), color: "#f59e0b" },
    { value: "PUBLISHED", label: t("common.status.published"), color: "#00a870" }
  ];

  async function fetchList() {
    loading.value = true;
    try {
      const res = await getDefinitionsApi({ page: 1, page_size: 100 });
      allDefinitions.value = res.items || [];
      pagination.total = res.total || 0;
    } catch {
      message(t("agent.msg.fetchListFailed"), { type: "error" });
    } finally {
      loading.value = false;
    }
  }

  async function onSearch() {
    pagination.currentPage = 1;
    await fetchList();
  }

  function handleSizeChange(val: number) {
    pagination.pageSize = val;
    pagination.currentPage = 1;
  }

  function handleCurrentChange(val: number) {
    pagination.currentPage = val;
  }

  function openDialog(mode: "create" | "edit" = "create", row?: AgentDefinitionResponse) {
    const isEdit = mode === "edit";
    const modelSettings = (row as any)?.model_settings || {};
    const personaConfig = (row as any)?.persona_config || {};
    const modelGroup: string = modelSettings?.litellm?.model_group ?? "";
    const systemPrompt: string =
      personaConfig?.system_prompt ?? modelSettings?.system_prompt ?? "";

    /** 按当前 step 刷新底部按钮：上一步 disabled / 下一步 label */
    const refreshFooter = (options: any, step: number, lastStep: number) => {
      const btns = options?.footerButtons;
      if (btns && btns.length >= 3) {
        btns[1].disabled = step === 0; // 上一步
        btns[2].label = step < lastStep ? t("common.action.next") : t("common.action.ok");
      }
    };

    // 定义层两步表单：0 基本信息 → 1 人设+模型
    const LAST_STEP = 1;

    addDialog({
      title: isEdit ? t("agent.editTitle") : t("definition.create"),
      props: {
        formInline: {
          title: mode,
          id: (row as any)?.id,
          name: row?.name ?? "",
          description: row?.description ?? "",
          avatar_color: (row as any)?.avatar_color ?? "#386bf5",
          engine_type: (row as any)?.engine_type ?? "HERMES",
          group_id: (row as any)?.group_id ?? "",
          persona_config: personaConfig,
          model_settings: modelSettings,
          skill_config: (row as any)?.skill_config ?? {},
          memory_config: (row as any)?.memory_config ?? {},
          modelGroup,
          system_prompt: systemPrompt,
          allGroups: allGroups.value
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
            refreshFooter(options, form.getCurrentStep(), LAST_STEP);
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
          onSearch();
          return;
        }
        refreshFooter(options, form.getCurrentStep(), LAST_STEP);
      }
    });
  }

  async function handlePublishVersion(row: AgentDefinitionResponse) {
    try {
      const { value } = await ElMessageBox.prompt(
        t("definition.version.changeLog"),
        t("definition.publishVersion"),
        {
          confirmButtonText: t("common.action.publish"),
          cancelButtonText: t("common.action.cancel"),
          inputType: "textarea",
          inputPlaceholder: t("definition.version.changeLog")
        }
      );
      await publishDefinitionApi(row.id, { change_log: value || "" });
      message(t("definition.msg.published"), { type: "success" });
      fetchList();
    } catch (err: any) {
      if (err === "cancel" || err?.toString?.().includes("cancel")) return;
      message(err?.response?.data?.detail || t("agent.msg.publishFailed"), { type: "error" });
    }
  }

  async function handleDelete(row: AgentDefinitionResponse) {
    try {
      await deleteDefinitionApi(row.id);
      message(t("definition.msg.deleted", { name: row.name }), { type: "success" });
      await fetchList();
      if (pagedList.value.length === 0 && pagination.currentPage > 1) {
        pagination.currentPage -= 1;
      }
    } catch (err: any) {
      message(err?.response?.data?.detail || t("agent.msg.deleteFailed"), { type: "error" });
    }
  }

  onMounted(() => {
    onSearch();
    getUserGroupsApi()
      .then(groups => {
        allGroups.value = (groups || []).map((g: any) => ({ id: g.id, name: g.name }));
      })
      .catch(() => {});
  });

  return {
    loading,
    allDefinitions,
    filteredDefinitions,
    pagedList,
    pagination,
    searchText,
    statusFilter,
    engineFilter,
    publishedCount,
    draftCount,
    onSearch,
    openDialog,
    handlePublishVersion,
    handleDelete,
    handleSizeChange,
    handleCurrentChange
  };
}
