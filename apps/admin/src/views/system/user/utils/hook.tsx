import "./reset.css";
import dayjs from "dayjs";
import roleForm from "../form/role.vue";
import editForm from "../form/index.vue";
import { zxcvbn, getPasswordStrengthHint } from "@/utils/zxcvbn";
import { message } from "@/utils/message";
import { usePublicHooks } from "../../hooks";
import { addDialog } from "@/components/ReDialog";
import { i18n } from "@/plugins/i18n";
import type { PaginationProps } from "@pureadmin/table";
import type { FormItemProps, RoleFormItemProps } from "../utils/types";
import {
  getKeyList,
  isAllEmpty,
  deviceDetection
} from "@pureadmin/utils";
import {
  getUsersApi,
  createUserApi,
  updateUserApi,
  deleteUserApi,
  getUserApi,
  getUserProfilesApi,
  unlockUserApi,
  initiateEmailVerifyApi,
  initiatePhoneVerifyApi,
  verifyUserEmailApi,
  verifyUserPhoneApi
} from "@/api/manager/users";
import { getRolesApi } from "@/api/manager/roles";
import {
  ElForm,
  ElInput,
  ElFormItem,
  ElProgress,
  ElMessageBox
} from "element-plus";
import {
  type Ref,
  h,
  ref,
  toRaw,
  watch,
  computed,
  reactive,
  onMounted
} from "vue";

export function useUser(tableRef: Ref) {
  const t = i18n.global.t as unknown as (
    key: string,
    named?: Record<string, unknown>
  ) => string;
  const form = reactive({
    username: "",
    is_active: ""
  });
  const formRef = ref();
  const ruleFormRef = ref();
  const dataList = ref([]);
  const loading = ref(true);
  const switchLoadMap = ref({});
  const { switchStyle } = usePublicHooks();
  const selectedNum = ref(0);
  const pagination = reactive<PaginationProps>({
    total: 0,
    pageSize: 10,
    currentPage: 1,
    background: true
  });
  const columns: TableColumnList = [
    {
      label: t("system.user.col.username"),
      prop: "username",
      minWidth: 130
    },
    {
      label: t("system.user.col.realName"),
      prop: "real_name",
      minWidth: 120,
      cellRenderer: ({ row }) => row.real_name || "-"
    },
    {
      label: t("system.user.col.phone"),
      prop: "phone",
      minWidth: 130,
      cellRenderer: ({ row }) =>
        row.phone ? (
          <div class="flex items-center gap-1">
            <span>{row.phone}</span>
            {row.phone_verified ? (
              <el-tag size="small" type="success">
                {t("system.user.msg.verified")}
              </el-tag>
            ) : (
              <el-tag size="small" type="info">
                {t("system.user.msg.unverified")}
              </el-tag>
            )}
          </div>
        ) : (
          <el-tag size="small" type="info">
            {t("system.user.msg.notBound")}
          </el-tag>
        )
    },
    {
      label: t("system.user.col.email"),
      prop: "email",
      minWidth: 200,
      cellRenderer: ({ row }) =>
        row.email ? (
          <div class="flex items-center gap-1">
            <span class="truncate">{row.email}</span>
            {row.email_verified ? (
              <el-tag size="small" type="success">
                {t("system.user.msg.verified")}
              </el-tag>
            ) : (
              <el-tag size="small" type="info">
                {t("system.user.msg.unverified")}
              </el-tag>
            )}
          </div>
        ) : (
          <el-tag size="small" type="info">
            {t("system.user.msg.notBound")}
          </el-tag>
        )
    },
    {
      label: t("system.user.col.roles"),
      prop: "roles",
      minWidth: 150,
      cellRenderer: ({ row }) => {
        const roles = row.roles || [];
        return roles.length > 0
          ? roles.map((r: string) => (
              <el-tag key={r} size="small" class="mr-1">
                {r}
              </el-tag>
            ))
          : "-";
      }
    },
    {
      label: t("system.user.col.status"),
      prop: "is_active",
      minWidth: 90,
      cellRenderer: scope => (
        <el-switch
          size={scope.props.size === "small" ? "small" : "default"}
          loading={switchLoadMap.value[scope.index]?.loading}
          v-model={scope.row.is_active}
          active-text={t("common.status.enabled")}
          inactive-text={t("common.status.disabled")}
          inline-prompt
          style={switchStyle.value}
          onChange={() => onChange(scope as any)}
        />
      )
    },
    {
      label: t("system.user.col.locked"),
      prop: "is_locked",
      minWidth: 120,
      cellRenderer: ({ row }) => {
        if (!row.is_locked) {
          return <span class="text-gray-400">—</span>;
        }
        const remaining = row.locked_remaining_seconds || 0;
        const minutes = Math.max(1, Math.ceil(remaining / 60));
        return (
          <el-tag size="small" type="danger" effect="dark">
            {t("system.user.locked.badge", { minutes })}
          </el-tag>
        );
      }
    },
    {
      label: t("system.user.col.createdAt"),
      minWidth: 160,
      prop: "created_at",
      formatter: ({ created_at }) =>
        dayjs(created_at).format("YYYY-MM-DD HH:mm:ss")
    },
    {
      label: t("system.user.col.lastLogin"),
      minWidth: 200,
      prop: "last_login_at",
      cellRenderer: ({ row }) => {
        if (!row.last_login_at) {
          return <span class="text-gray-400">—</span>;
        }
        return (
          <div>
            <div>{dayjs(row.last_login_at).format("YYYY-MM-DD HH:mm:ss")}</div>
            <div class="text-xs text-gray-500">{row.last_login_ip || "-"}</div>
          </div>
        );
      }
    },
    {
      label: t("system.user.col.operation"),
      fixed: "right",
      width: 180,
      slot: "operation"
    }
  ];
  const buttonClass = computed(() => {
    return [
      "h-5!",
      "reset-margin",
      "text-gray-500!",
      "dark:text-white!",
      "dark:hover:text-primary!"
    ];
  });
  const pwdForm = reactive({
    newPwd: ""
  });
  const pwdProgress = computed(() => [
    { color: "#e74242", text: t("system.user.pwd.strength.veryWeak") },
    { color: "#EFBD47", text: t("system.user.pwd.strength.weak") },
    { color: "#ffa500", text: t("system.user.pwd.strength.fair") },
    { color: "#1bbf1b", text: t("system.user.pwd.strength.strong") },
    { color: "#008000", text: t("system.user.pwd.strength.veryStrong") }
  ]);
  const curScore = ref();
  const roleOptions = ref([]);

  async function onChange({ row, index }) {
    const action = row.is_active ? t("common.status.enabled") : t("common.status.disabled");
    try {
      await ElMessageBox.confirm(
        t("system.user.confirm.statusContent", { action, name: row.username }),
        t("system.user.confirm.statusTitle"),
        {
          confirmButtonText: t("system.user.confirm.ok"),
          cancelButtonText: t("common.action.cancel"),
          type: "warning",
          draggable: true
        }
      );
    } catch {
      row.is_active = !row.is_active;
      return;
    }

    switchLoadMap.value[index] = Object.assign(
      {},
      switchLoadMap.value[index],
      { loading: true }
    );
    try {
      await updateUserApi(row.id, { is_active: row.is_active });
      message(t("system.user.msg.statusChanged"), { type: "success" });
    } catch {
      row.is_active = !row.is_active;
      message(t("system.user.msg.statusChangeFailed"), { type: "error" });
    } finally {
      switchLoadMap.value[index] = Object.assign(
        {},
        switchLoadMap.value[index],
        { loading: false }
      );
    }
  }

  function handleUpdate(row) {
    console.log(row);
  }

  async function handleDelete(row) {
    try {
      // 先查该用户在多少个实例上有独立会话空间，确认框据此动态提示。
      const { count } = await getUserProfilesApi(row.id);
      const content =
        count > 0
          ? t("system.user.confirmDeleteWithProfiles", {
              name: row.username,
              count
            })
          : t("system.user.confirmDelete", { name: row.username });
      await ElMessageBox.confirm(content, t("common.tip"), {
        confirmButtonText: t("common.action.delete"),
        cancelButtonText: t("common.action.cancel"),
        type: "warning",
        draggable: true
      });
    } catch {
      // 用户取消确认框 → 静默
      return;
    }
    try {
      await deleteUserApi(row.id);
      message(t("system.user.msg.deleted", { name: row.username }), { type: "success" });
      onSearch();
    } catch (err: any) {
      console.error("delete user failed:", err?.response?.data?.detail || err);
      message(t("system.user.msg.deleteFailed"), { type: "error" });
    }
  }

  async function handleUnlock(row) {
    try {
      await ElMessageBox.confirm(
        t("system.user.confirm.unlockContent", { name: row.username }),
        t("system.user.confirm.unlockTitle"),
        {
          confirmButtonText: t("common.action.confirm"),
          cancelButtonText: t("common.action.cancel"),
          type: "warning",
          draggable: true
        }
      );
    } catch {
      return;
    }
    try {
      await unlockUserApi(row.id);
      message(t("system.user.msg.unlocked", { name: row.username }), { type: "success" });
      onSearch();
    } catch (err: any) {
      console.error("unlock user failed:", err?.response?.data?.detail || err);
      message(t("system.user.msg.unlockFailed"), { type: "error" });
    }
  }

  // 0.8.110 邮箱/手机认证 — admin 发起 + 输入验证码两步合并到一个 dialog
  async function handleVerifyChannel(
    row: any,
    channel: "email" | "phone"
  ) {
    const isEmail = channel === "email";
    const target = isEmail ? row.email : row.phone;
    if (!target) {
      message(
        t(isEmail ? "system.user.msg.userNoEmail" : "system.user.msg.userNoPhone"),
        { type: "warning" }
      );
      return;
    }
    if (isEmail ? row.email_verified : row.phone_verified) {
      message(t("system.user.msg.alreadyVerified"), { type: "info" });
      return;
    }

    // 步骤 1：发起认证 → 后端发码给用户
    try {
      if (isEmail) {
        await initiateEmailVerifyApi(row.id);
      } else {
        await initiatePhoneVerifyApi(row.id);
      }
    } catch (err: any) {
      console.error("initiate verify failed:", err?.response?.data?.detail || err);
      message(t("system.user.msg.sendFailed"), { type: "error" });
      return;
    }

    // 步骤 2：弹 dialog 让 admin 输入 6 位 code
    const codeRef = ref("");
    addDialog({
      title: t(
        isEmail
          ? "system.user.verifyEmailTitle"
          : "system.user.verifyPhoneTitle"
      ),
      width: "40%",
      draggable: true,
      closeOnClickModal: false,
      contentRenderer: () =>
        h(
          "div",
          { class: "py-4" },
          [
            h(
              "p",
              { class: "mb-4 text-sm text-gray-600" },
              t("system.user.msg.codeSent", { target })
            ),
            h(ElInput, {
              placeholder: t("system.user.msg.verifyCodePlaceholder"),
              modelValue: codeRef.value,
              "onUpdate:modelValue": (val: string) => (codeRef.value = val),
              maxlength: 6,
              class: "text-center text-2xl tracking-widest"
            })
          ]
        ),
      beforeSure: async done => {
        if (codeRef.value.length !== 6) {
          message(t("system.user.msg.verifyCodePlaceholder"), {
            type: "warning"
          });
          return;
        }
        try {
          if (isEmail) {
            await verifyUserEmailApi(row.id, codeRef.value);
          } else {
            await verifyUserPhoneApi(row.id, codeRef.value);
          }
          message(t("system.user.msg.verifySuccess"), { type: "success" });
          done();
          onSearch();
        } catch (err: any) {
          console.error("verify channel failed:", err?.response?.data?.detail || err);
          message(t("system.user.msg.verifyFailed"), { type: "error" });
        }
      }
    });
  }

  function handleVerifyEmail(row: any) {
    return handleVerifyChannel(row, "email");
  }

  function handleVerifyPhone(row: any) {
    return handleVerifyChannel(row, "phone");
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
    selectedNum.value = val.length;
    tableRef.value.setAdaptive();
  }

  function onSelectionCancel() {
    selectedNum.value = 0;
    tableRef.value.getTableRef().clearSelection();
  }

  function onbatchDel() {
    const curSelected = tableRef.value.getTableRef().getSelectionRows();
    message(t("system.user.msg.batchDeleted", { ids: getKeyList(curSelected, "id") }), {
      type: "success"
    });
    tableRef.value.getTableRef().clearSelection();
    onSearch();
  }

  async function onSearch() {
    loading.value = true;
    try {
      const params: Record<string, any> = {
        page: pagination.currentPage,
        page_size: pagination.pageSize
      };
      if (form.username) params.search = form.username;
      if (form.is_active !== "") params.is_active = form.is_active === "true";

      const res = await getUsersApi(params);
      dataList.value = res.items;
      pagination.total = res.total;
      pagination.pageSize = res.page_size;
      pagination.currentPage = res.page;
    } catch {
      message(t("system.user.msg.fetchFailed"), { type: "error" });
    } finally {
      loading.value = false;
    }
  }

  const resetForm = formEl => {
    if (!formEl) return;
    formEl.resetFields();
    form.is_active = "";
    onSearch();
  };

  function openDialog(mode: "create" | "edit" = "create", row?: FormItemProps) {
    const isEdit = mode === "edit";
    addDialog({
      title: t("system.user.dialogTitle", {
        title: isEdit ? t("common.action.edit") : t("common.action.add")
      }),
      props: {
        formInline: {
          title: mode,
          username: row?.username ?? "",
          real_name: row?.real_name ?? "",
          password: "",
          email: row?.email ?? "",
          phone: row?.phone ?? "",
          is_active: row?.is_active ?? true
        }
      },
      width: "40%",
      draggable: true,
      fullscreen: deviceDetection(),
      fullscreenIcon: true,
      closeOnClickModal: false,
      contentRenderer: () => h(editForm, {
        ref: formRef,
        formInline: {
          id: row?.id ?? "",
          title: mode,
          username: row?.username ?? "",
          real_name: row?.real_name ?? "",
          password: "",
          email: row?.email ?? "",
          phone: row?.phone ?? "",
          is_active: row?.is_active ?? true,
        },
        userId: row?.id ?? "",
      }),
      beforeSure: async (done, { options }) => {
        const FormRef = formRef.value.getRef();
        const curData = options.props.formInline as FormItemProps;
        FormRef.validate(async valid => {
          if (!valid) return;
          try {
            if (!isEdit) {
              await createUserApi({
                username: curData.username,
                real_name: curData.real_name || undefined,
                email: curData.email,
                phone: curData.phone || undefined,
                password: curData.password
              });
            } else {
              await updateUserApi(row!.id, {
                username: curData.username,
                real_name: curData.real_name || undefined,
                email: curData.email,
                phone: curData.phone || undefined,
                password: curData.password || undefined,
                is_active: curData.is_active
              });
              // 业务用户绑定随「确定」一起保存（best-effort，失败不阻断用户更新）
              try {
                await formRef.value?.saveBusinessBinding?.(true);
              } catch {
                /* 业务绑定保存失败不阻断 */
              }
            }
            message(t(isEdit ? "system.user.msg.updated" : "system.user.msg.created"), {
              type: "success"
            });
            done();
            onSearch();
          } catch (err: any) {
            console.error("save user failed:", err?.response?.data?.detail || err);
            message(t("system.user.msg.operationFailed"), { type: "error" });
          }
        });
      }
    });
  }

  watch(
    pwdForm,
    ({ newPwd }) =>
      (curScore.value = isAllEmpty(newPwd) ? -1 : zxcvbn(newPwd).score)
  );

  /** 重置密码 */
  function handleReset(row) {
    addDialog({
      title: t("system.user.pwd.resetTitle", { name: row.username }),
      width: "30%",
      draggable: true,
      closeOnClickModal: false,
      fullscreen: deviceDetection(),
      contentRenderer: () => (
        <>
          <ElForm ref={ruleFormRef} model={pwdForm}>
            <ElFormItem
              prop="newPwd"
              rules={[
                {
                  required: true,
                  message: t("system.user.pwd.newPwdRequired"),
                  trigger: "blur"
                },
                {
                  min: 8,
                  message: t("system.user.pwd.newPwdWeak"),
                  trigger: "blur"
                },
                {
                  validator: (_rule, value, callback) => {
                    if (!value) {
                      callback();
                      return;
                    }
                    const score = zxcvbn(value).score;
                    if (score < 3) {
                      const hint = getPasswordStrengthHint(value) || t("system.user.form.rule.passwordStrength");
                      callback(new Error(hint));
                    } else {
                      callback();
                    }
                  },
                  trigger: "blur"
                }
              ]}
            >
              <ElInput
                clearable
                show-password
                type="password"
                v-model={pwdForm.newPwd}
                placeholder={t("system.user.pwd.newPwdPlaceholder")}
              />
            </ElFormItem>
          </ElForm>
          <div class="my-4 flex">
            {pwdProgress.value.map(({ color, text }, idx) => (
              <div
                class="w-[19vw]"
                style={{ marginLeft: idx !== 0 ? "4px" : 0 }}
              >
                <ElProgress
                  striped
                  striped-flow
                  duration={curScore.value === idx ? 6 : 0}
                  percentage={curScore.value >= idx ? 100 : 0}
                  color={color}
                  stroke-width={10}
                  show-text={false}
                />
                <p
                  class="text-center"
                  style={{ color: curScore.value === idx ? color : "" }}
                >
                  {text}
                </p>
              </div>
            ))}
          </div>
        </>
      ),
      closeCallBack: () => (pwdForm.newPwd = ""),
      beforeSure: async done => {
        ruleFormRef.value.validate(async valid => {
          if (!valid) return;
          try {
            await updateUserApi(row.id, { password: pwdForm.newPwd });
            message(t("system.user.pwd.resetOk", { name: row.username }), {
              type: "success"
            });
            done();
          } catch (err: any) {
            console.error("reset password failed:", err?.response?.data?.detail || err);
            message(t("system.user.pwd.resetFailed"), { type: "error" });
          }
        });
      }
    });
  }

  /** 分配角色 */
  async function handleRole(row) {
    // 获取用户当前角色
    let userRoleIds: string[] = [];
    try {
      const userDetail = await getUserApi(row.id);
      const roles = userDetail.roles || [];
      // Match role names back to role objects
      if (roleOptions.value.length > 0) {
        userRoleIds = roleOptions.value
          .filter((r: any) => roles.includes(r.name))
          .map((r: any) => r.id);
      }
    } catch {
      // If fetch fails, start with empty selection
    }

    addDialog({
      title: t("system.user.role.assignTitle", { name: row.username }),
      props: {
        formInline: {
          username: row?.username ?? "",
          roleOptions: roleOptions.value ?? [],
          ids: userRoleIds
        }
      },
      width: "400px",
      draggable: true,
      fullscreen: deviceDetection(),
      fullscreenIcon: true,
      closeOnClickModal: false,
      contentRenderer: () => h(roleForm),
      beforeSure: async (done, { options }) => {
        const curData = options.props.formInline as RoleFormItemProps;
        try {
          await updateUserApi(row.id, { role_ids: curData.ids });
          message(t("system.user.role.assignOk", { name: row.username }), { type: "success" });
          done();
          onSearch();
        } catch (err: any) {
          console.error("assign role failed:", err?.response?.data?.detail || err);
          message(t("system.user.role.assignFailed"), { type: "error" });
        }
      }
    });
  }

  onMounted(async () => {
    onSearch();
    try {
      const roles = await getRolesApi();
      roleOptions.value = roles || [];
    } catch {
      // Silently handle error
    }
  });

  return {
    form,
    loading,
    columns,
    dataList,
    selectedNum,
    pagination,
    buttonClass,
    deviceDetection,
    onSearch,
    resetForm,
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
  };
}
