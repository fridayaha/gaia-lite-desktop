/**
 * 对象选择器（ObjectReference 参数录入用，ADR Action Mutation Mapping P1）。
 *
 * 服务端搜索 combobox：用户聚焦输入框时立即用空查询拉前 N 条候选，
 * 输入关键词后 debounce 搜索（按 pk/title/searchProperties 模糊匹配）。
 *
 * 使用 React Aria 的 async combobox 模式（items 受控动态集合 + loadingState
 * + allowsEmptyCollection + 受控 isOpen），解决"async items 到达后 popover
 * 不打开"的已知问题（react-spectrum#5234）。
 *
 * 后端走 /objects/textsql（带 WHERE LIKE），不再前端全量加载——彻底解决
 * "超过 50 条看不见"的问题。
 *
 * 受控组件：value=主键字符串，onChange(主键)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { searchObjects, getObjectType } from '../api/client';
import { ComboBox, type ComboBoxOption } from './ui/ComboBox';
import type { LoadedObject } from '../types';

export interface ObjectPickerProps {
  ontology: string;
  /** 对象类型 api_name（不含 ontology 前缀）。 */
  objectType: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  /** 主键属性 api_name（用于显示与回填）。未知时按对象类型 primary_key 推断。 */
  primaryKeyHint?: string;
  /** 搜索时匹配的属性列表（默认 [pk, title]）。P1: 由 ActionTypeParameter
   *  search_properties 配置驱动，扩展搜索范围。 */
  searchProperties?: string[];
  /** 返回结果中额外携带的属性（供悬停预览用，P1）。默认只取 pk+title。 */
  previewProperties?: string[];
}

/** Debounce a rapidly-changing value. Returns the lagged value. */
function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

export function ObjectPicker({
  ontology,
  objectType,
  value,
  onChange,
  disabled,
  primaryKeyHint,
  searchProperties,
  previewProperties,
}: ObjectPickerProps) {
  const [primaryKey, setPrimaryKey] = useState<string>(primaryKeyHint ?? '');
  const [titleProp, setTitleProp] = useState<string>('');
  const [objects, setObjects] = useState<LoadedObject[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inputText, setInputText] = useState('');
  // Whether the popover is open (drives search + placeholder). Maintained via
  // onOpenChange (React Aria reports open/close) + onFocus fallback. NOT
  // passed as a controlled prop — react-aria-components ComboBox has no
  // isOpen prop; we let it manage open state internally and just observe.
  const [isOpen, setIsOpen] = useState(false);
  const debouncedQuery = useDebounced(inputText, 300);

  // Load object type metadata (pk/title) once.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const ot = await getObjectType(ontology, objectType);
        if (cancelled) return;
        const pk = ot.primary_key || primaryKeyHint || 'id';
        const title = ot.title_property || pk;
        setPrimaryKey(pk);
        setTitleProp(title);
      } catch {
        if (!cancelled) setError('加载对象类型元数据失败');
      }
    })();
    return () => {
      cancelled = false;
    };
  }, [ontology, objectType, primaryKeyHint]);

  const effectiveSearchProps = useMemo(() => {
    if (searchProperties && searchProperties.length > 0) return searchProperties;
    return primaryKey ? [primaryKey, titleProp].filter(Boolean) : [];
  }, [searchProperties, primaryKey, titleProp]);

  const selectProps = useMemo(() => {
    const s = new Set<string>();
    if (primaryKey) s.add(primaryKey);
    if (titleProp) s.add(titleProp);
    for (const p of previewProperties ?? []) s.add(p);
    return [...s];
  }, [primaryKey, titleProp, previewProperties]);

  // Server search (fires when isOpen and metadata loaded, on debounced query).
  const reqId = useRef(0);
  const doSearch = useCallback(
    async (query: string) => {
      if (!primaryKey) return;
      const id = ++reqId.current;
      setLoading(true);
      setError(null);
      try {
        const rows = await searchObjects(ontology, objectType, query, effectiveSearchProps, {
          limit: 20,
          properties: selectProps,
        });
        if (id === reqId.current) setObjects(rows);
      } catch (err) {
        if (id === reqId.current) {
          setObjects([]);
          setError('搜索失败，可直接输入主键');
        }
      } finally {
        if (id === reqId.current) setLoading(false);
      }
    },
    [ontology, objectType, primaryKey, effectiveSearchProps, selectProps],
  );

  useEffect(() => {
    if (!isOpen) return;
    void doSearch(debouncedQuery);
  }, [isOpen, debouncedQuery, doSearch]);
  function labelFor(o: LoadedObject): string {
    const pkVal = String(o[primaryKey]);
    const titleVal = titleProp !== primaryKey ? o[titleProp] : null;
    return titleVal != null && titleVal !== '' ? `${String(titleVal)} (#${pkVal})` : `#${pkVal}`;
  }

  const options: ComboBoxOption[] = useMemo(
    () => objects.map((o) => ({ value: String(o[primaryKey]), label: labelFor(o) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [objects, primaryKey, titleProp],
  );

  const initialInputText = useMemo(() => {
    if (!value) return '';
    const hit = options.find((o) => o.value === value);
    return hit ? hit.label : value;
  }, [value, options]);

  const placeholder = loading
    ? '搜索中…'
    : error
      ? '搜索失败，可直接输入主键'
      : isOpen
        ? `搜索${effectiveSearchProps.length > 0 ? `（${effectiveSearchProps.join(' / ')}）` : ''}…`
        : '点击搜索对象';
  return (
    <ComboBox
      options={options}
      allowsCustomValue
      allowsEmptyCollection
      menuTrigger="focus"
      loadingState={loading ? 'filtering' : 'idle'}
      value={value}
      onChange={(v) => {
        onChange(v);
        if (v) setIsOpen(false);
      }}
      defaultInputValue={initialInputText}
      onInputChange={(text) => {
        setInputText(text);
      }}
      onOpenChange={(open) => {
        setIsOpen(open);
      }}
      onFocus={() => {
        // Re-pick: clear an existing value so search fires afresh.
        if (value) {
          onChange('');
          setInputText('');
        }
        setIsOpen(true);
      }}
      onBlur={() => {
        setIsOpen(false);
        // Commit a free-form typed pk (allowsCustomValue path).
        if (inputText && !options.some((o) => o.value === inputText || o.label === inputText)) {
          onChange(inputText);
        }
      }}
      placeholder={placeholder}
      disabled={disabled}
      aria-label={`选择 ${objectType} 对象`}
      inputClassName="text-xs"
      // Server-side search already filters; disable client-side re-filtering.
      defaultFilter={() => true}
      getFilterText={(o) => `${o.label} ${o.value}`}
    />
  );
}
