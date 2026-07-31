interface FormItemProps {
  id?: string;
  /** 用于判断是`新增`还是`修改` */
  title: string;
  username: string;
  real_name: string;
  password: string;
  email: string;
  phone: string;
  is_active: boolean;
}

interface FormProps {
  formInline: FormItemProps;
  userId?: string;
}

interface RoleFormItemProps {
  username: string;
  /** 角色列表 */
  roleOptions: any[];
  /** 选中的角色列表 */
  ids: string[];
}
interface RoleFormProps {
  formInline: RoleFormItemProps;
}

export type { FormItemProps, FormProps, RoleFormItemProps, RoleFormProps };
