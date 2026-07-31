/**
 * OntologyContextSelector — 图探索页的"当前本体"上下文选择器。
 *
 * 设计依据（ADR-013 + PatternFly Context Selector 模式）：
 *  - 本体是"应用上下文"（switch context），不是普通筛选器。选择器的视觉
 *    层级要高于一般控件，让用户一眼知道"现在在哪个本体"。
 *  - 可扩展性：本体数量从 demo 的 3 个增长到生产的几十上百个，原生 <select>
 *    撑不住（无搜索、无富信息）。用 ComboBox（带搜索的 dropdown）一套组件
 *    无缝过渡——3 个时搜索是锦上添花，50 个时是救命必需。
 *  - 富信息选项：每项显示中文名 + 英文 api_name + 对象数 + 描述，帮用户切换
 *    前判断"这个本体有什么"，不用切过去才知道（ts4nfdi EntitySelectWidget
 *    需求：lightweight access to contextual ontology information）。
 *  - 搜索匹配中文名 + 英文名 + 描述（用户可能记任一个）。
 *
 * 基于 components/ui/ComboBox（React Aria Components）。本体列表是本地全量
 * 数据（非 async），ComboBox 封装会走静态集合模式：React Aria 自己用
 * defaultFilter 过滤 + 选中后自动同步 input 到选中项 label，无需调用方管
 * query 状态。
 */
import { ComboBox } from './ui/ComboBox';
import type { Ontology } from '../types';

interface OntologyContextSelectorProps {
  /** 全量本体列表（父组件已加载）。 */
  ontologies: Ontology[];
  /** 当前选中的本体 api_name。 */
  value: string;
  /** 切换本体回调（传 api_name）。 */
  onChange: (apiName: string) => void;
  /** 是否禁用（切本体过程中等）。 */
  disabled?: boolean;
}

export function OntologyContextSelector({
  ontologies,
  value,
  onChange,
  disabled,
}: OntologyContextSelectorProps) {
  const options = ontologies.map((o) => ({
    value: o.api_name,
    label: `${o.display_name} (${o.api_name})`,
    content: (
      <div className="flex flex-col gap-0.5 py-0.5">
        <div className="flex items-center gap-2">
          <span className="font-medium text-text">{o.display_name}</span>
          <span className="text-xs text-text-muted">{o.api_name}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <span>{o.object_types_count} 类对象</span>
          {o.description && (
            <>
              <span>·</span>
              <span className="truncate">{o.description}</span>
            </>
          )}
        </div>
      </div>
    ),
  }));

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-base leading-none" aria-hidden>
        🧠
      </span>
      {/* key={value}：选中后重建 ComboBox，重置 React Aria 内部状态。
          解决 client 模式下「选中后 input 显示 label，再打开被 defaultFilter
          过滤光」的问题——重建后 defaultInputValue 重新初始化，打开时显示全部。 */}
      <ComboBox
        key={value}
        aria-label="选择本体"
        options={options}
        value={value}
        onChange={onChange}
        disabled={disabled}
        defaultInputValue={
          options.find((o) => o.value === value)?.label ?? ''
        }
        // 搜索匹配中文名 + 英文 api_name + 描述（用户可能记任一个）。
        // ComboBox 封装在本地数据模式下用此函数驱动 React Aria 的 defaultFilter。
        getFilterText={(o) => {
          const ont = ontologies.find((x) => x.api_name === o.value);
          return ont ? `${ont.display_name} ${ont.api_name} ${ont.description}` : o.label;
        }}
        inputClassName="min-w-[180px] font-medium"
        placeholder="搜索本体…"
        menuTrigger="focus"
      />
    </div>
  );
}
