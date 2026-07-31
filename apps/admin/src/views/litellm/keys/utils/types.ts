interface KeyEditFormItem {
  max_budget?: number | undefined;
  budget_duration: string;
  rpm_limit?: number | undefined;
  tpm_limit?: number | undefined;
  duration: string;
}

interface KeyEditFormProps {
  formInline: KeyEditFormItem;
}

export type { KeyEditFormItem, KeyEditFormProps };
