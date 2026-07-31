interface ModelFormItem {
  /** 用于判断是`新建`还是`修改` */
  title: "create" | "edit";
  model_name: string;
  model: string;
  api_key: string;
  api_base: string;
  custom_llm_provider: string;
  context_length: number | null;
}

interface ModelFormProps {
  formInline: ModelFormItem;
}

interface PriceFormItem {
  model_id: string;
  model_name: string;
  input_cost_per_1m_tokens: number | null;
  output_cost_per_1m_tokens: number | null;
}

interface PriceFormProps {
  formInline: PriceFormItem;
}

export type { ModelFormItem, ModelFormProps, PriceFormItem, PriceFormProps };
