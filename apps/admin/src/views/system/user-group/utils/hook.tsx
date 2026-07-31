import dayjs from "dayjs";
import editForm from "../form.vue";
import memberForm from "../member.vue";
import { message } from "@/utils/message";
import { addDialog } from "@/components/ReDialog";
import { i18n } from "@/plugins/i18n";
import { deviceDetection } from "@pureadmin/utils";
import type { FormItemProps } from "../utils/types";
import {
  getUserGroupsApi,
  createUserGroupApi,
  updateUserGroupApi,
  deleteUserGroupApi,
  getUserGroupApi
} from "@/api/manager/userGroups";
import { getUsersApi } from "@/api/manager/users";
import { type Ref, h, ref, reactive, onMounted } from "vue";

export function useUserGroup() {
  const t = i18n.global.t as unknown as (
    key: string,
    named?: Record<string, unknown>
  ) => string;
  const dataList = ref([]);
  const loading = ref(true);
  const formRef = ref();
  const columns: TableColumnList = [
    {
      label: t("system.userGroup.col.name"),
      prop: "name",
      minWidth: 130
    },
    {
      label: t("system.userGroup.col.code"),
      prop: "code",
      minWidth: 120
    },
    {
      label: t("system.userGroup.col.description"),
      prop: "description",
      minWidth: 200
    },
    {
      label: t("system.userGroup.col.memberCount"),
      prop: "member_count",
      minWidth: 80
    },
    {
      label: t("system.userGroup.col.createdAt"),
      prop: "created_at",
      minWidth: 160,
      formatter: ({ created_at }) =>
        dayjs(created_at).format("YYYY-MM-DD HH:mm:ss")
    },
    {
      label: t("system.userGroup.col.operation"),
      fixed: "right",
      width: 240,
      slot: "operation"
    }
  ];

  async function onSearch() {
    loading.value = true;
    try {
      const groups = await getUserGroupsApi();
      dataList.value = groups || [];
    } catch {
      message(t("system.userGroup.msg.fetchFailed"), { type: "error" });
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
      title: t("system.userGroup.dialogTitle", {
        title: isEdit ? t("common.action.edit") : t("common.action.add")
      }),
      props: {
        formInline: {
          title: mode,
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
              await createUserGroupApi({
                name: curData.name,
                description: curData.description
              });
            } else {
              await updateUserGroupApi(row!.id, {
                name: curData.name,
                description: curData.description
              });
            }
            message(t(isEdit ? "system.userGroup.msg.updated" : "system.userGroup.msg.created"), {
              type: "success"
            });
            done();
            onSearch();
          } catch (err: any) {
            message(err?.response?.data?.detail || t("system.userGroup.msg.operationFailed"), {
              type: "error"
            });
          }
        });
      }
    });
  }

  async function handleDelete(row) {
    try {
      await deleteUserGroupApi(row.id);
      message(t("system.userGroup.msg.deleted", { name: row.name }), { type: "success" });
      onSearch();
    } catch (err: any) {
      message(err?.response?.data?.detail || t("system.userGroup.msg.deleteFailed"), {
        type: "error"
      });
    }
  }

  /** 管理成员 */
  async function handleMembers(row) {
    let currentMemberIds: string[] = [];
    let allUsers: any[] = [];

    try {
      // Fetch current group members
      const groupDetail = await getUserGroupApi(row.id);
      currentMemberIds = (groupDetail.members || []).map(m => m.id);
      // Fetch all users for selection
      const userRes = await getUsersApi({ page: 1, page_size: 100 });
      allUsers = (userRes?.items || []).map(u => ({
        id: u.id,
        username: u.username,
        email: u.email
      }));
    } catch {
      message(t("system.userGroup.member.fetchFailed"), { type: "error" });
      return;
    }

    addDialog({
      title: t("system.userGroup.member.manageTitle", { name: row.name }),
      props: {
        formInline: {
          groupName: row.name,
          currentMemberIds,
          allUsers
        }
      },
      width: "600px",
      draggable: true,
      fullscreen: deviceDetection(),
      fullscreenIcon: true,
      closeOnClickModal: false,
      contentRenderer: () => h(memberForm),
      beforeSure: async (done, { options }) => {
        const formData = options.props.formInline as any;
        try {
          await updateUserGroupApi(row.id, {
            member_ids: formData.currentMemberIds
          });
          message(t("system.userGroup.member.updated", { name: row.name }), { type: "success" });
          done();
          onSearch();
        } catch (err: any) {
          message(err?.response?.data?.detail || t("system.userGroup.member.updateFailed"), {
            type: "error"
          });
        }
      }
    });
  }

  onMounted(() => {
    onSearch();
  });

  return {
    loading,
    columns,
    dataList,
    onSearch,
    resetForm,
    openDialog,
    handleDelete,
    handleMembers
  };
}
