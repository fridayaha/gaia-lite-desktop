import dayjs from "dayjs";
import editForm from "../form.vue";
import { message } from "@/utils/message";
import { addDialog } from "@/components/ReDialog";
import { i18n } from "@/plugins/i18n";
import type { FormItemProps } from "../utils/types";
import type { PaginationProps } from "@pureadmin/table";
import { deviceDetection } from "@pureadmin/utils";
import {
  getRolesApi,
  createRoleApi,
  updateRoleApi,
  deleteRoleApi,
  getRoleApi,
  getAllPermissionsApi
} from "@/api/manager/roles";
import type { PermissionResponse } from "@/api/manager/roles";
import { type Ref, reactive, ref, onMounted, h, watch } from "vue";

const t = i18n.global.t as unknown as (
  key: string,
  named?: Record<string, unknown>
) => string;

function buildPermissionTree(permissions: PermissionResponse[]) {
  const grouped: Record<string, any[]> = {};
  for (const p of permissions) {
    if (!grouped[p.resource_type]) {
      grouped[p.resource_type] = [];
    }
    grouped[p.resource_type].push({
      id: p.id,
      name: p.name,
      children: []
    });
  }
  return Object.entries(grouped).map(([type, children]) => ({
    id: type,
    name: t(`system.role.resource.${type}`) || type,
    children
  }));
}

export function useRole(treeRef: Ref) {
  const form = reactive({
    name: ""
  });
  const allRoles = ref<any[]>([]);
  const curRow = ref();
  const formRef = ref();
  const dataList = ref([]);
  const treeIds = ref<string[]>([]);
  const treeData = ref([]);
  const isShow = ref(false);
  const loading = ref(true);
  const treeSearchValue = ref();
  const isExpandAll = ref(false);
  const isSelectAll = ref(false);
  const treeProps = {
    value: "id",
    label: "name",
    children: "children"
  };
  const pagination = reactive<PaginationProps>({
    total: 0,
    pageSize: 10,
    currentPage: 1,
    background: true
  });
  const columns: TableColumnList = [
    {
      label: t("system.role.col.name"),
      prop: "name"
    },
    {
      label: t("system.role.col.description"),
      prop: "description",
      minWidth: 160
    },
    {
      label: t("system.role.col.permissionCodes"),
      prop: "permission_codes",
      minWidth: 160,
      cellRenderer: ({ row }) => {
        const codes = row.permission_codes || [];
        return codes.length > 0
          ? codes
              .slice(0, 3)
              .map((c: string) => (
                <el-tag key={c} size="small" class="mr-1">
                  {c}
                </el-tag>
              ))
          : "-";
      }
    },
    {
      label: t("system.role.col.userCount"),
      prop: "user_count",
      minWidth: 80
    },
    {
      label: t("system.role.col.createdAt"),
      prop: "created_at",
      minWidth: 160,
      formatter: ({ created_at }) =>
        dayjs(created_at).format("YYYY-MM-DD HH:mm:ss")
    },
    {
      label: t("system.role.col.operation"),
      fixed: "right",
      width: 210,
      slot: "operation"
    }
  ];

  async function handleDelete(row) {
    try {
      await deleteRoleApi(row.id);
      message(t("system.role.msg.deleted", { name: row.name }), { type: "success" });
      onSearch();
    } catch (err: any) {
      message(err?.response?.data?.detail || t("system.role.msg.deleteFailed"), { type: "error" });
    }
  }

  async function handleSizeChange(val: number) {
    pagination.pageSize = val;
    pagination.currentPage = 1;
    await onSearch();
  }

  async function handleCurrentChange(val: number) {
    pagination.currentPage = val;
    await onSearch();
  }

  function handleSelectionChange(val) {
    console.log("handleSelectionChange", val);
  }

  async function onSearch() {
    loading.value = true;
    try {
      let roles = allRoles.value;
      if (allRoles.value.length === 0) {
        roles = await getRolesApi();
        allRoles.value = roles;
      }

      let filtered = roles;
      if (form.name) {
        filtered = roles.filter(r => r.name.includes(form.name));
      }

      pagination.total = filtered.length;
      const start = (pagination.currentPage - 1) * pagination.pageSize;
      dataList.value = filtered.slice(start, start + pagination.pageSize);
    } catch {
      message(t("system.role.msg.fetchFailed"), { type: "error" });
    } finally {
      loading.value = false;
    }
  }

  const resetForm = formEl => {
    if (!formEl) return;
    formEl.resetFields();
    onSearch();
  };

  function openDialog(mode: "create" | "edit" = "create", row?: FormItemProps) {
    const isEdit = mode === "edit";
    addDialog({
      title: t("system.role.dialogTitle", {
        title: isEdit ? t("common.action.edit") : t("common.action.add")
      }),
      props: {
        formInline: {
          name: row?.name ?? "",
          description: row?.description ?? ""
        }
      },
      width: "40%",
      draggable: true,
      fullscreen: deviceDetection(),
      fullscreenIcon: true,
      closeOnClickModal: false,
      contentRenderer: () => h(editForm, { ref: formRef, formInline: null }),
      beforeSure: async (done, { options }) => {
        const FormRef = formRef.value.getRef();
        const curData = options.props.formInline as FormItemProps;
        FormRef.validate(async valid => {
          if (!valid) return;
          try {
            if (!isEdit) {
              await createRoleApi({
                name: curData.name,
                description: curData.description
              });
            } else {
              await updateRoleApi(row!.id, {
                name: curData.name,
                description: curData.description
              });
            }
            message(t(isEdit ? "system.role.msg.updated" : "system.role.msg.created"), {
              type: "success"
            });
            allRoles.value = []; // Force re-fetch on next search
            done();
            onSearch();
          } catch (err: any) {
            message(err?.response?.data?.detail || t("system.role.msg.operationFailed"), {
              type: "error"
            });
          }
        });
      }
    });
  }

  /** 菜单权限 */
  async function handleMenu(row?: any) {
    if (row?.id) {
      curRow.value = row;
      isShow.value = true;
      try {
        const roleDetail = await getRoleApi(row.id);
        const checkedCodes = roleDetail.permission_codes || [];
        const checkedIds = treeIds.value.length > 0
          ? treeIds.value
          : [];
        // Match codes to IDs
        if (allPermissions.length > 0) {
          const matchedIds = allPermissions
            .filter(p => checkedCodes.includes(p.code))
            .map(p => p.id);
          setTimeout(() => {
            treeRef.value?.setCheckedKeys(matchedIds);
          }, 100);
        }
      } catch {
        message(t("system.role.msg.permissionFetchFailed"), { type: "error" });
      }
    } else {
      curRow.value = null;
      isShow.value = false;
    }
  }

  /** 高亮当前权限选中行 */
  function rowStyle({ row: { id } }) {
    return {
      cursor: "pointer",
      background: id === curRow.value?.id ? "var(--el-fill-color-light)" : ""
    };
  }

  /** 菜单权限-保存 */
  async function handleSave() {
    try {
      // leafOnly=true：只保存叶子节点（真实权限点 UUID），过滤掉按 resource_type 分组的父节点（字符串 id）
      const checkedIds = treeRef.value.getCheckedKeys(true);
      await updateRoleApi(curRow.value.id, { permission_ids: checkedIds });
      message(t("system.role.msg.permissionSaved", { name: curRow.value.name }), {
        type: "success"
      });
      allRoles.value = [];
      onSearch();
    } catch (err: any) {
      message(err?.response?.data?.detail || t("system.role.msg.permissionSaveFailed"), {
        type: "error"
      });
    }
  }

  const onQueryChanged = (query: string) => {
    treeRef.value!.filter(query);
  };

  const filterMethod = (query: string, node) => {
    return (node.name || "").includes(query);
  };

  let allPermissions: PermissionResponse[] = [];

  onMounted(async () => {
    onSearch();
    try {
      allPermissions = await getAllPermissionsApi();
      treeData.value = buildPermissionTree(allPermissions);
      treeIds.value = allPermissions.map(p => p.id);
    } catch {
      // Handle silently
    }
  });

  watch(isExpandAll, val => {
    val
      ? treeRef.value.setExpandedKeys(treeIds.value)
      : treeRef.value.setExpandedKeys([]);
  });

  watch(isSelectAll, val => {
    val
      ? treeRef.value.setCheckedKeys(treeIds.value)
      : treeRef.value.setCheckedKeys([]);
  });

  return {
    form,
    isShow,
    curRow,
    loading,
    columns,
    rowStyle,
    dataList,
    treeData,
    treeProps,
    pagination,
    isExpandAll,
    isSelectAll,
    treeSearchValue,
    onSearch,
    resetForm,
    openDialog,
    handleMenu,
    handleSave,
    handleDelete,
    filterMethod,
    onQueryChanged,
    handleSizeChange,
    handleCurrentChange,
    handleSelectionChange
  };
}
