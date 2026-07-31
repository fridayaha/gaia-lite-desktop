interface FormItemProps {
  id?: string;
  /** 用于判断是`新增`还是`修改` */
  title: string;
  /** 用户组名称 */
  name: string;
  /** 用户组描述 */
  description: string;
}

interface FormProps {
  formInline: FormItemProps;
}

export type { FormItemProps, FormProps };
