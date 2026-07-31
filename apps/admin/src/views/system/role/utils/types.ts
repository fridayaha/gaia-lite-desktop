interface FormItemProps {
  id?: string;
  /** 用于判断是`新增`还是`修改` */
  title?: string;
  /** 角色名称 */
  name: string;
  /** 角色描述 */
  description: string;
}
interface FormProps {
  formInline: FormItemProps;
}

export type { FormItemProps, FormProps };
