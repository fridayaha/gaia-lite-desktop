import { ref, reactive, computed, onMounted, type Ref } from "vue";
import { h } from "vue";
import { useI18n } from "vue-i18n";
import { message } from "@/utils/message";
import { ElMessageBox } from "element-plus";
import { addDialog } from "@/components/ReDialog";
import ModelForm from "../components/ModelForm.vue";
import PriceForm from "../components/PriceForm.vue";
import type { ModelFormItem, PriceFormItem } from "./types";
import {
  getModelsApi,
  createModelApi,
  updateModelApi,
  updateModelPriceApi,
  deleteModelApi
} from "@/api/manager/litellm";

export function useModels(_tableRef?: Ref) {
  const { t } = useI18n();
  const loading = ref(false);
  const searchText = ref("");
  const models = ref<any[]>([]);
  const formRef = ref();
  const priceFormRef = ref();

  /** 推断供应商：优先 custom_llm_provider，否则取上游 model 前缀 */
  function providerOf(row: any): string {
    const params = row?.litellm_params || {};
    if (params.custom_llm_provider) return params.custom_llm_provider;
    const m = params.model || "";
    return m.includes("/") ? m.split("/")[0] : "—";
  }

  async function loadModels() {
    loading.value = true;
    try {
      const res = await getModelsApi();
      models.value = res.items;
    } catch (err: any) {
      console.error("load models failed:", err?.response?.data?.detail || err);
      message(t("litellm.model.msg.loadFailed"), { type: "error" });
      models.value = [];
    } finally {
      loading.value = false;
    }
  }

  const filteredModels = computed(() => {
    const kw = searchText.value.trim().toLowerCase();
    if (!kw) return models.value;
    return models.value.filter(
      m =>
        (m.model_name || "").toLowerCase().includes(kw) ||
        (m.litellm_params?.model || "").toLowerCase().includes(kw) ||
        providerOf(m).toLowerCase().includes(kw)
    );
  });

  const stats = computed(() => {
    const total = models.value.length;
    const providers = new Set(models.value.map(providerOf).filter(p => p && p !== "—")).size;
    return { total, providers };
  });

  const pagination = reactive({ pageSize: 10, currentPage: 1, background: true });
  const pagedList = computed(() => {
    const start = pagination.pageSize * (pagination.currentPage - 1);
    return filteredModels.value.slice(start, start + pagination.pageSize);
  });
  function handleSizeChange(val: number) {
    pagination.pageSize = val;
    pagination.currentPage = 1;
  }
  function handleCurrentChange(val: number) {
    pagination.currentPage = val;
  }

  // ── 新建/编辑（addDialog + ModelForm） ──
  function openDialog(mode: "create" | "edit", row?: any) {
    const isEdit = mode === "edit";
    const formInline: ModelFormItem = isEdit
      ? {
          title: "edit",
          model_name: row.model_name || "",
          model: row.litellm_params?.model || "",
          api_key: "",
          api_base: row.litellm_params?.api_base || "",
          custom_llm_provider: row.litellm_params?.custom_llm_provider || (providerOf(row) !== "—" ? providerOf(row) : ""),
          context_length: row.model_info?.context_length ?? null
        }
      : {
          title: "create",
          model_name: "",
          model: "",
          api_key: "",
          api_base: "",
          custom_llm_provider: "",
          context_length: null
        };
    addDialog({
      title: isEdit ? t("litellm.model.dialogEditTitle") : t("litellm.model.dialogTitle"),
      width: "560px",
      draggable: true,
      closeOnClickModal: false,
      contentRenderer: () => h(ModelForm, { ref: formRef, formInline }),
      beforeSure: async done => {
        const FormRef = formRef.value.getRef();
        await FormRef.validate(async (valid: boolean) => {
          if (!valid) return;
          try {
            if (!isEdit) {
              await createModelApi({
                model_name: formInline.model_name,
                model: formInline.model,
                api_key: formInline.api_key,
                api_base: formInline.api_base || undefined,
                custom_llm_provider: formInline.custom_llm_provider || undefined,
                context_length: formInline.context_length ?? undefined
              });
              message(t("litellm.model.msg.created"), { type: "success" });
            } else {
              await updateModelApi(row.model_info?.id || row.model_id || row.model_name, {
                model: formInline.model,
                api_key: formInline.api_key || undefined,
                api_base: formInline.api_base || undefined,
                custom_llm_provider: formInline.custom_llm_provider || undefined,
                context_length: formInline.context_length ?? undefined
              });
              message(t("litellm.model.msg.updated"), { type: "success" });
            }
            done();
            await loadModels();
          } catch (err: any) {
            console.error("save model failed:", err?.response?.data?.detail || err);
            message(t(isEdit ? "litellm.model.msg.updateFailed" : "litellm.model.msg.createFailed"), {
              type: "error"
            });
          }
        });
      }
    });
  }

  function openCreate() {
    openDialog("create");
  }
  function openEdit(row: any) {
    openDialog("edit", row);
  }

  async function handleDelete(row: any) {
    const id = row.model_info?.id || row.model_id || row.model_name;
    try {
      await ElMessageBox.confirm(
        t("litellm.model.msg.confirmDelete", { name: row.model_name }),
        t("litellm.model.msg.deleteTitle"),
        {
          type: "warning",
          confirmButtonText: t("common.action.delete"),
          cancelButtonText: t("common.action.cancel")
        }
      );
    } catch {
      return;
    }
    try {
      await deleteModelApi(id);
      message(t("litellm.model.msg.deleted"), { type: "success" });
      await loadModels();
    } catch (err: any) {
      console.error("delete model failed:", err?.response?.data?.detail || err);
      message(t("litellm.model.msg.deleteFailed"), { type: "error" });
    }
  }

  // ── 编辑价格（addDialog + PriceForm） ──
  function openEditPrice(row: any) {
    const formInline: PriceFormItem = {
      model_id: row.model_info?.id || row.model_id || row.model_name,
      model_name: row.model_name || "",
      input_cost_per_1m_tokens:
        row.input_cost_per_1m_tokens == null ? null : Number(row.input_cost_per_1m_tokens),
      output_cost_per_1m_tokens:
        row.output_cost_per_1m_tokens == null ? null : Number(row.output_cost_per_1m_tokens)
    };
    addDialog({
      title: t("litellm.model.action.editPrice"),
      width: "500px",
      draggable: true,
      closeOnClickModal: false,
      contentRenderer: () => h(PriceForm, { ref: priceFormRef, formInline }),
      beforeSure: async done => {
        const FormRef = priceFormRef.value.getRef();
        await FormRef.validate(async (valid: boolean) => {
          if (!valid) return;
          try {
            await updateModelPriceApi(formInline.model_id, {
              input_cost_per_1m_tokens: formInline.input_cost_per_1m_tokens,
              output_cost_per_1m_tokens: formInline.output_cost_per_1m_tokens
            });
            message(t("litellm.model.msg.priceUpdated"), { type: "success" });
            done();
            await loadModels();
          } catch (err: any) {
            console.error("update price failed:", err?.response?.data?.detail || err);
            message(t("litellm.model.msg.priceUpdateFailed"), { type: "error" });
          }
        });
      }
    });
  }

  onMounted(loadModels);

  return {
    loading,
    searchText,
    filteredModels,
    stats,
    pagination,
    pagedList,
    providerOf,
    handleSizeChange,
    handleCurrentChange,
    openCreate,
    openEdit,
    openEditPrice,
    handleDelete
  };
}
