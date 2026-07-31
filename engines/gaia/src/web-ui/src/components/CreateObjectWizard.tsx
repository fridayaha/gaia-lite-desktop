import { useState, useEffect, useCallback, useRef, useMemo, Fragment } from 'react';
import { useFieldId } from '../hooks/useFormId';
import { useDraft } from '../hooks/useDraft';
import { useToast } from '../hooks/useToast';
import { cn } from '../lib/cn';
import { formatError } from '../lib/formatError';
import { trinoTypeToDataType } from '../lib/typeMapping';
import { checkTypeCompatibility, autoMatchByColumnName } from '../lib/columnMapping';
import { deriveApiName, isValidApiName } from '../lib/deriveApiName';
import { listDatasets, getDatasetSchema } from '../api/client';
import { scaffoldObjectType, suggestObjectTypeApiName, type ScaffoldResult } from '../api/ai';
import { Modal } from './Modal';
import { TextInput, TextAreaInput } from './ui/TextField';
import { Select, SelectOption } from './ui/Select';
import type { DataType, DatasetGovernance, ObjectTypeCreate } from '../types';
import type {
  PropertyDraft,
  LinkDraft,
  ActionDraft,
  ObjectWizardData,
  DatasetSchemaColumn,
} from '../types/wizard';

// ── Constants ──

const DATA_TYPES: { value: DataType; label: string }[] = [
  { value: 'STRING', label: 'String' },
  { value: 'INTEGER', label: 'Integer' },
  { value: 'LONG', label: 'Long' },
  { value: 'BOOLEAN', label: 'Boolean' },
  { value: 'FLOAT', label: 'Float' },
  { value: 'DOUBLE', label: 'Double' },
  { value: 'DECIMAL', label: 'Decimal' },
  { value: 'DATE', label: 'Date' },
  { value: 'TIMESTAMP', label: 'Timestamp' },
];

const CREATE_STEPS = [
  { num: 1, id: 'datasource', title: '选择数据集', desc: '绑定数据来源或暂不关联' },
  { num: 2, id: 'properties', title: '配置属性', desc: 'AI 已生成，确认/微调字段结构' },
  { num: 3, id: 'review', title: '审核并创建', desc: '确认所有配置' },
];

const EDIT_STEPS = [
  { num: 1, id: 'overview', title: '基础信息与数据集', desc: '对象元数据与数据集绑定' },
  { num: 2, id: 'properties', title: '配置属性', desc: '确认/微调字段与源列映射' },
  { num: 3, id: 'review', title: '审核并更新', desc: '确认所有配置' },
];

type StorageType = 'MANAGED' | 'VIRTUAL';

interface CreateObjectWizardProps {
  objectTypes?: { id: string; display_name: string }[];
  initialData?: Partial<ObjectWizardData>;
  editing?: boolean;
  /** 提交进行中（由父组件传入），禁用提交按钮并显示 loading。 */
  submitting?: boolean;
  onComplete: (
    data: ObjectTypeCreate & {
      properties: PropertyDraft[];
      links: LinkDraft[];
      actions: ActionDraft[];
      dataset_api_name?: string;
    },
  ) => void;
  onCancel: () => void;
}

// ── Helpers ──

/** Live-derived apiName preview for a property (display only; backend derives
 *  the real one). Uses the bound dataset column as a fallback source when the
 *  display name is non-ASCII (e.g. Chinese). */
function previewApiName(p: PropertyDraft, existingCount: number): string {
  return deriveApiName(p.display_name, {
    pascal: false,
    backingColumn: p.source_column,
    fallbackPrefix: 'property',
    existingCount,
  });
}

// ── Component ──

export function CreateObjectWizard({
  initialData,
  editing,
  submitting,
  onComplete,
  onCancel,
}: CreateObjectWizardProps) {
  const STEPS = editing ? EDIT_STEPS : CREATE_STEPS;
  const [activeStep, setActiveStep] = useState(0);
  // Track visited steps using state. Initialized with step 0 as visited.
  const [completed, setCompleted] = useState(new Set<number>([0]));

  const goToStep = (step: number) => {
    setActiveStep(step);
    setCompleted((prev) => {
      if (prev.has(step)) return prev;
      return new Set([...prev, step]);
    });
  };

  // Form state
  const [displayName, setDisplayName] = useState(initialData?.display_name || '');
  const [apiName, setApiName] = useState(initialData?.api_name || '');
  // All-in-AI apiName: display name 变化后自动调 AI 生成 PascalCase apiName
  // （不给 ObjectType0 本地 fallback 占位）。scaffold 也会填充 apiName。
  // 用户手动编辑 apiName 输入框 → userOverrodeApi=true，后续不再自动覆盖。
  const [userOverrodeApi, setUserOverrodeApi] = useState(!!initialData?.api_name);
  const [apiNameLoading, setApiNameLoading] = useState(false);
  const apiNameReqId = useRef(0);
  const apiNameTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [description, setDescription] = useState(initialData?.description || '');
  const [storageType, setStorageType] = useState<StorageType>(
    initialData?.storage_type === 'VIRTUAL' ? 'VIRTUAL' : 'MANAGED',
  );
  // PK / title tracked by property INDEX (properties no longer carry api_name;
  // the backend derives it from display_name / backing_column).
  const [primaryKeyIndex, setPrimaryKeyIndex] = useState<number>(() => {
    const idx = (initialData?.properties ?? []).findIndex((p) => p.is_primary_key);
    return idx >= 0 ? idx : -1;
  });
  const [titlePropIndex, setTitlePropIndex] = useState<number>(() => {
    const props = initialData?.properties ?? [];
    const pkIdx = props.findIndex((p) => p.is_primary_key);
    const idx = props.findIndex((p, i) => p.is_title_property && i !== pkIdx);
    return idx >= 0 ? idx : -1;
  });
  const [datasetApiName, setDatasetApiName] = useState(initialData?.dataset_api_name || '');
  // skipDataset default: creation defaults to false (show the dataset list
  // so the user can pick a dataset to drive BuildWith scaffolding without
  // first unchecking a box). Edit mode is ALWAYS false — an unbound object
  // opened for editing should show the dataset list so the user can add a
  // binding (the "暂不关联" checkbox is create-mode only).
  const [skipDataset, setSkipDataset] = useState(
    editing ? false : (initialData?.skip_dataset ?? false),
  );
  const [datasetSchema, setDatasetSchema] = useState<DatasetSchemaColumn[] | null>(
    initialData?.dataset_schema ?? null,
  );
  const [properties, setProperties] = useState<PropertyDraft[]>(initialData?.properties || []);
  const [links] = useState<LinkDraft[]>(initialData?.links || []);
  const [actions] = useState<ActionDraft[]>(initialData?.actions || []);

  // Datasets catalog state (F1)
  const [datasets, setDatasets] = useState<DatasetGovernance[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(false);
  const [datasetsError, setDatasetsError] = useState<string | null>(null);
  const [datasetSearch, setDatasetSearch] = useState('');
  const [schemaLoading, setSchemaLoading] = useState(false);
  // BuildWith: AI scaffolding state. Triggered after a dataset is selected;
  // streams a complete ObjectType structure (metadata + properties + keys)
  // that the user confirms/tweaks in Step 2. See
  // docs/design/buildwith-object-scaffolding.md.
  const [scaffoldLoading, setScaffoldLoading] = useState(false);
  const [scaffoldError, setScaffoldError] = useState<string | null>(null);
  // Race control: increment to invalidate in-flight scaffold streams.
  const scaffoldReqId = useRef(0);
  const { show: showToast } = useToast();

  // Load datasets catalog on mount (F1 replaces MOCK_DATASETS).
  useEffect(() => {
    let cancelled = false;
    async function loadDatasets() {
      setDatasetsLoading(true);
      setDatasetsError(null);
      try {
        const list = await listDatasets();
        if (!cancelled) setDatasets(list);
      } catch (err) {
        if (!cancelled) setDatasetsError(formatError(err, '加载数据集列表失败'));
      } finally {
        if (!cancelled) setDatasetsLoading(false);
      }
    }
    loadDatasets();
    return () => {
      cancelled = true;
    };
  }, []);

  // F1: when a dataset is selected, fetch its schema (B3 dispatches by kind).
  const loadDatasetSchema = useCallback(
    async (apiName: string): Promise<DatasetSchemaColumn[] | null> => {
      setSchemaLoading(true);
      try {
        const s = await getDatasetSchema(apiName);
        const cols = s.columns ?? [];
        setDatasetSchema(cols);
        return cols;
      } catch (err) {
        setDatasetSchema([]);
        showToast('拉取数据集列失败：' + formatError(err), 'error');
        return null;
      } finally {
        setSchemaLoading(false);
      }
    },
    [showToast],
  );

  // 编辑模式：initialData 带 dataset_api_name 时，自动加载该数据集 schema，
  // 否则源列 Select 选项为空，即使 property.source_column 有值也显示不出来。
  useEffect(() => {
    if (editing && initialData?.dataset_api_name) {
      void loadDatasetSchema(initialData.dataset_api_name);
    }
    // 仅挂载时跑一次，避免反复拉取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 编辑态：用户切换数据集后，对现有属性做同名匹配更新 source_column
  // （不重新生成属性，保留用户的属性结构）。在 handleSelectDataset 的
  // schema 加载回调里执行（cols 与 apiName 同步），不用 effect（effect 会
  // 拿到上一次的 stale schema）。匹配基准用 initialData 的原始 source_column。
  const originalSourceColumns = useMemo(() => {
    const m: Record<string, string> = {};
    for (const p of initialData?.properties ?? []) {
      const api = p._preview_api_name;
      if (api && p.source_column) m[api] = p.source_column;
    }
    return m;
  }, [initialData]);

  // Filter datasets by storage_type → kind (F1 kind filter).
  const wantedKind: 'MANAGED' | 'VIRTUAL' = storageType === 'VIRTUAL' ? 'VIRTUAL' : 'MANAGED';
  // Edit mode: the dataset the object was originally bound to (for the
  // "当前绑定" badge and the dataset-changed indicator).
  const initialDatasetApiName = (editing ? initialData?.dataset_api_name : '') || '';
  const datasetChangedInWizard = editing && datasetApiName !== initialDatasetApiName;
  const candidateDatasets = datasets.filter((d) => (d.kind || 'MANAGED') === wantedKind);
  const filteredCandidates = datasetSearch.trim()
    ? candidateDatasets.filter(
        (d) =>
          d.api_name.toLowerCase().includes(datasetSearch.toLowerCase()) ||
          (d.display_name || '').toLowerCase().includes(datasetSearch.toLowerCase()),
      )
    : candidateDatasets;

  // When storage_type changes, clear an incompatible selection (F1).
  // Done in the setStorageType wrapper (not an effect) to avoid cascading renders.
  const changeStorageType = (next: StorageType) => {
    setStorageType(next);
    const nextKind: 'MANAGED' | 'VIRTUAL' = next === 'VIRTUAL' ? 'VIRTUAL' : 'MANAGED';
    const currentKind = datasets.find((d) => d.api_name === datasetApiName)?.kind || 'MANAGED';
    if (datasetApiName && currentKind !== nextKind) {
      setDatasetApiName('');
      setDatasetSchema(null);
      // VIRTUAL objects can't defer binding — also clear skip flag.
      if (next === 'VIRTUAL') setSkipDataset(false);
    }
    if (next === 'VIRTUAL') setSkipDataset(false);
  };

  function handleSelectDataset(apiName: string) {
    setDatasetApiName(apiName);
    setSkipDataset(false);
    void loadDatasetSchema(apiName).then((cols) => {
      // Edit mode: schema loaded for the NEW dataset — match source columns
      // now (cols and apiName are in sync, unlike a useEffect that may fire
      // with a stale schema from a previous selection).
      if (!editing || !cols || cols.length === 0) return;
      const matched = autoMatchByColumnName(
        properties.map((p) => ({
          api_name: p._preview_api_name || previewApiName(p, 0),
          source_column: originalSourceColumns[p._preview_api_name || ''] ?? null,
        })),
        cols,
      );
      setProperties((prev) =>
        prev.map((p) => {
          const apiN = p._preview_api_name || previewApiName(p, 0);
          const col = matched[apiN] ?? '';
          return { ...p, source_column: col || undefined };
        }),
      );
    });
  }

  // BuildWith: scaffold an ObjectType from the selected dataset's schema.
  // Streams a complete structure (metadata + properties + keys) from /ai/scaffold
  // and patches each frame onto wizard state. Deterministic fields (data_type,
  // nullable, source_column) are filled from the dataset schema client-side —
  // the LLM is never asked to guess them. On failure, falls back to the
  // deterministic skeleton (handleGenerateFromDataset) so the user always has
  // an editable structure.
  const triggerScaffold = useCallback(
    (apiName: string, display_name: string, columns: DatasetSchemaColumn[]) => {
      if (!columns.length) return;
      const reqId = ++scaffoldReqId.current;
      setScaffoldLoading(true);
      setScaffoldError(null);

      // Reset prior scaffold-derived state so stale data doesn't linger.
      // Mark apiName as overridden so the displayName-driven AI suggest doesn't
      // race with scaffold's own apiName fill; the user can still re-run via ✨ AI.
      apiNameReqId.current++;
      setDisplayName('');
      setApiName('');
      setUserOverrodeApi(true);
      setDescription('');
      setProperties([]);
      setPrimaryKeyIndex(-1);
      setTitlePropIndex(-1);

      async function run() {
        try {
          for await (const frame of scaffoldObjectType({
            dataset_api_name: apiName,
            dataset_display_name: display_name,
            storage_type: storageType,
            columns: columns.map((c) => ({ name: c.name, type: c.type, nullable: c.nullable })),
          })) {
            if (reqId !== scaffoldReqId.current) return; // stale
            if ('error' in frame) {
              setScaffoldError(frame.error);
              applyDeterministicSkeleton(columns, reqId);
              return;
            }
            applyScaffoldFrame(frame, columns, reqId);
          }
        } catch (err) {
          if (reqId !== scaffoldReqId.current) return;
          setScaffoldError(formatError(err));
          applyDeterministicSkeleton(columns, reqId);
        } finally {
          if (reqId === scaffoldReqId.current) setScaffoldLoading(false);
        }
      }
      void run();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- applyDeterministicSkeleton is stable enough (only closes over setters); adding it would re-create triggerScaffold every render
    [storageType],
  );

  // Patch one streamed ScaffoldResult frame onto wizard state. Deterministic
  // data_type/nullable are filled from the dataset schema (not the LLM).
  function applyScaffoldFrame(
    frame: ScaffoldResult,
    columns: DatasetSchemaColumn[],
    reqId: number,
  ) {
    if (reqId !== scaffoldReqId.current) return;
    const colByName = new Map(columns.map((c) => [c.name, c]));
    if (frame.display_name) setDisplayName(frame.display_name);
    if (frame.api_name) setApiName(frame.api_name);
    if (frame.description !== undefined) setDescription(frame.description);
    if (frame.properties && frame.properties.length > 0) {
      const drafts: PropertyDraft[] = frame.properties.map((p) => {
        const col = colByName.get(p.source_column);
        return {
          display_name: p.display_name,
          description: p.description || '',
          data_type: col ? trinoTypeToDataType(col.type) : 'STRING',
          is_primary_key: false, // resolved from primary_key_column below
          is_title_property: false, // resolved from title_column below
          searchable: p.searchable,
          nullable: col ? col.nullable : true,
          source_column: p.source_column,
        };
      });
      setProperties(drafts);
      // Map key column names → property indices.
      if (frame.primary_key_column) {
        const pkIdx = drafts.findIndex((p) => p.source_column === frame.primary_key_column);
        if (pkIdx >= 0) setPrimaryKeyIndex(pkIdx);
      }
      const titleCol = frame.title_column;
      if (titleCol) {
        const tIdx = drafts.findIndex((p) => p.source_column === titleCol);
        if (tIdx >= 0) setTitlePropIndex(tIdx);
      } else {
        // No title column → use PK as title (existing usePkAsTitle convention).
        setTitlePropIndex(-1);
      }
    }
  }

  // Auto-trigger scaffold once a dataset's schema loads (creation mode only —
  // editing keeps the user's prior config). Skip when the user chose to defer.
  // This is a responsive effect: schema is loaded asynchronously by
  // loadDatasetSchema, and scaffolding must fire when it arrives. The setState
  // calls happen inside triggerScaffold's async flow, not synchronously in the
  // effect body, but the linter can't see through the call — hence the disable.
  useEffect(() => {
    if (editing) return;
    if (!datasetApiName || skipDataset) return;
    if (!datasetSchema || datasetSchema.length === 0) return;
    const ds = datasets.find((d) => d.api_name === datasetApiName);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- responsive effect; setState happens inside triggerScaffold's async flow
    triggerScaffold(datasetApiName, ds?.display_name || datasetApiName, datasetSchema);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- full deps (datasets, triggerScaffold) would re-fire scaffold on every identity change
  }, [datasetApiName, datasetSchema, skipDataset, editing]);

  // Deterministic fallback skeleton (used when scaffold fails). PK/title
  // are LEFT EMPTY — required fields must not be guessed wrong; the user
  // picks them. Called from triggerScaffold's catch/error-frame path (not an
  // effect) so it doesn't trip react-hooks/set-state-in-effect.
  function applyDeterministicSkeleton(columns: DatasetSchemaColumn[], reqId: number) {
    if (reqId !== scaffoldReqId.current) return;
    const skeleton: PropertyDraft[] = columns.map((col) => ({
      display_name: col.name,
      description: '',
      data_type: trinoTypeToDataType(col.type),
      is_primary_key: false,
      is_title_property: false,
      searchable: false,
      nullable: col.nullable,
      source_column: col.name,
    }));
    setProperties(skeleton);
    setPrimaryKeyIndex(-1);
    setTitlePropIndex(-1);
    showToast('AI 推导失败，已生成基础结构，请手动补充主键和标题', 'info');
  }

  // 草稿自动保存已禁用：向导精简为 3 步后容错价值有限，且恢复提示打扰用户。
  // 保留 useDraft 调用（disabled key）以避免下游 draft.clear()/draft.set() 报错。
  const draftKey = '';
  const draft = useDraft<ObjectWizardData>('gaia:wizard:disabled', {} as ObjectWizardData);
  useEffect(() => {
    if (editing || !draftKey) return;
    if (displayName || apiName || properties.length > 0 || links.length > 0 || actions.length > 0) {
      draft.set({
        display_name: displayName,
        api_name: apiName,
        description,
        storage_type: storageType,
        dataset_api_name: datasetApiName,
        dataset_schema: datasetSchema ?? undefined,
        skip_dataset: skipDataset,
        // PK/title are resolved to per-property flags at submit; persist them
        // as flags on the properties so a restored draft keeps the selection.
        properties: properties.map((p, i) => ({
          ...p,
          is_primary_key: i === primaryKeyIndex,
          is_title_property: usePkAsTitle ? i === primaryKeyIndex : i === titlePropIndex,
        })),
        links,
        actions,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    displayName,
    apiName,
    description,
    storageType,
    primaryKeyIndex,
    titlePropIndex,
    datasetApiName,
    datasetSchema,
    skipDataset,
    properties,
    links,
    actions,
    editing,
  ]);

  // Validation state
  const [showErrors, setShowErrors] = useState(false);
  // Expanded property rows (by index) for the "more attributes" detail panel
  // (description / nullable). Keep the flat table scannable; details on demand.
  const [expandedProps, setExpandedProps] = useState<Set<number>>(new Set());
  const togglePropExpanded = (i: number) => {
    setExpandedProps((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  const fDisplayName = useFieldId('wiz-display-name');
  const fApiName = useFieldId('wiz-api-name');
  const fDescription = useFieldId('wiz-description');
  const fPrimaryKey = useFieldId('wiz-primary-key');
  const fTitleProp = useFieldId('wiz-title-prop');
  const handleCancel = () => {
    onCancel();
  };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onCancel]);

  // "使用主键作为默认标题"：true 时 titlePropIndex 跟随主键。
  const usePkAsTitle = false;
  const [newProp, setNewProp] = useState({
    display_name: '',
    data_type: 'STRING' as DataType,
    searchable: true,
  });

  // F2: source-column options come from the bound dataset's schema.
  const hasSchema = !!datasetSchema && datasetSchema.length > 0;
  const sourceColumns = datasetSchema ?? [];

  const canGoNext = () => {
    switch (activeStep) {
      case 0: {
        if (editing) {
          // Edit mode overview: metadata required, dataset optional (skip
          // to rebind later). api_name is read-only so it's always present.
          return displayName.trim() !== '';
        }
        // Create mode F1 validation
        if (storageType === 'MANAGED' && skipDataset) return true;
        return !!datasetApiName;
      }
      case 1: {
        if (displayName.trim() === '') return false;
        if (properties.length === 0) return false;
        if (primaryKeyIndex < 0) return false;
        if (!usePkAsTitle && titlePropIndex < 0) return false;
        // If a dataset is actually bound (not skipped), every property must
        // have a source_column — block Next here, not just at review.
        if (datasetApiName && !skipDataset && hasUnmapped) return false;
        return true;
      }
      case 2:
        return true;
    }
  };

  // Finish gate: if a dataset is bound, every property must have a
  // source_column (mirrors link_dataset's full-mapping invariant). Without
  // this, editing via the wizard would bypass link_dataset's strong checks
  // (updateObjectTypeBatch silently writes backing_mapping=null on unmapped
  // properties → silent data loss).
  const unmappedPropertyNames = properties
    .filter((p) => !p.source_column)
    .map((p) => p._preview_api_name || previewApiName(p, 0));
  const hasUnmapped = unmappedPropertyNames.length > 0;
  const datasetBound = !!datasetApiName && !skipDataset;
  const canFinish = !datasetBound || !hasUnmapped;

  const handleFinish = () => {
    if (!editing) draft.clear();
    // api_name is PascalCase, caller-supplied. primary_key / title_property
    // are derived by the backend from per-property is_primary_key /
    // is_title_property flags (Q2) — we omit them and let the backend resolve.
    onComplete({
      api_name: apiName,
      display_name: displayName,
      description,
      storage_type: storageType,
      properties: properties.map((p, i) => ({
        ...p,
        is_primary_key: i === primaryKeyIndex,
        is_title_property: usePkAsTitle ? i === primaryKeyIndex : i === titlePropIndex,
      })),
      links,
      actions,
      dataset_api_name: skipDataset ? '' : datasetApiName,
    });
  };

  const handleAddProperty = () => {
    if (!newProp.display_name.trim()) return;
    // F2: auto-map source column when display_name matches a dataset column.
    const autoSource =
      hasSchema && sourceColumns.some((c) => c.name === newProp.display_name)
        ? newProp.display_name
        : undefined;
    const p: PropertyDraft = {
      display_name: newProp.display_name,
      description: '',
      data_type: newProp.data_type,
      is_primary_key: false,
      is_title_property: false,
      searchable: newProp.searchable,
      nullable: true,
      source_column: autoSource,
    };
    const next = [...properties, p];
    setProperties(next);
    // First property becomes PK by default.
    if (primaryKeyIndex < 0) setPrimaryKeyIndex(next.length - 1);
    setNewProp({ display_name: '', data_type: 'STRING', searchable: true });
  };

  // F2: "从数据集生成属性" — bulk-generate properties from dataset columns.
  const handleGenerateFromDataset = () => {
    if (!hasSchema) return;
    const generated: PropertyDraft[] = sourceColumns.map((col) => ({
      display_name: col.name,
      description: '',
      data_type: trinoTypeToDataType(col.type),
      is_primary_key: false,
      is_title_property: false,
      searchable: false,
      nullable: true,
      source_column: col.name,
    }));
    setProperties(generated);
    // Promote the first column (or one named "id") to PK if none set.
    const idIdx = generated.findIndex((p) => p.source_column === 'id');
    setPrimaryKeyIndex(idIdx >= 0 ? idIdx : 0);
    setTitlePropIndex(-1);
    showToast(`已从数据集生成 ${generated.length} 个属性`, 'success');
  };

  // ── All-in-AI apiName 自动生成（与本体一致）──
  // displayName 变化后 debounce 600ms，用户未手改则自动调 AI 生成 PascalCase
  // apiName。scaffold 也会填 apiName；两者都不给 ObjectType0 本地占位。
  const runApiNameSuggest = useCallback(
    (name: string, markOverrode: boolean) => {
      const reqId = ++apiNameReqId.current;
      setApiNameLoading(true);
      suggestObjectTypeApiName(name, [])
        .then((suggested) => {
          if (reqId !== apiNameReqId.current) return;
          if (markOverrode) setUserOverrodeApi(true);
          setApiName(suggested);
        })
        .catch((err) => {
          if (reqId !== apiNameReqId.current) return;
          showToast('API 名称 AI 推导失败：' + formatError(err), 'error');
        })
        .finally(() => {
          if (reqId === apiNameReqId.current) setApiNameLoading(false);
        });
    },
    [showToast],
  );

  useEffect(() => {
    if (apiNameTimer.current) clearTimeout(apiNameTimer.current);
    const name = displayName.trim();
    if (!name || userOverrodeApi) return;
    apiNameTimer.current = setTimeout(() => {
      void runApiNameSuggest(name, false);
    }, 600);
    return () => {
      if (apiNameTimer.current) clearTimeout(apiNameTimer.current);
    };
  }, [displayName, userOverrodeApi, runApiNameSuggest]);

  // ── Metadata form (shared by create Step 1 and edit Step 0) ──
  // ``readonlyApi`` locks api_name (edit mode: renaming cascades to
  // Actions/Links/Doris table names, so it's locked).
  const renderMetadata = (readonlyApi: boolean) => (
    <div className="card mb-4 p-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="form-group">
          <label className="form-label" htmlFor={fDisplayName.id}>
            Display name *
          </label>
          <TextInput
            id={fDisplayName.id}
            inputClassName={cn(
              'form-input',
              showErrors && !displayName.trim() && 'border-error',
            )}
            value={displayName}
            onChange={(v) => {
              setDisplayName(v);
              if (!userOverrodeApi) {
                apiNameReqId.current++;
                setApiName('');
              }
              setShowErrors(false);
            }}
            placeholder="如：航班告警"
          />
          {showErrors && !displayName.trim() && (
            <div className="mt-1 text-[11px] text-error">请输入显示名称</div>
          )}
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor={fApiName.id}>
            API name（PascalCase）
          </label>
          {readonlyApi ? (
            <>
              <TextInput
                id={fApiName.id}
                inputClassName="form-input flex-1 cursor-not-allowed font-mono text-xs opacity-70"
                value={apiName}
                disabled
                readOnly
              />
              <span className="form-hint">编辑态不可更改（重命名会影响动作/关系/索引表）</span>
            </>
          ) : (
            <>
              <div className="flex gap-1.5">
                <TextInput
                  id={fApiName.id}
                  inputClassName={cn(
                    'form-input flex-1 font-mono text-xs',
                    apiName && !isValidApiName(apiName, true) && 'border-error',
                  )}
                  value={apiNameLoading && !userOverrodeApi ? '' : apiName}
                  onChange={(v) => {
                    apiNameReqId.current++;
                    setUserOverrodeApi(true);
                    setApiName(v);
                  }}
                  disabled={apiNameLoading && !userOverrodeApi}
                  placeholder={apiNameLoading ? 'AI 生成中…' : 'FlightAlert'}
                />
                <button
                  type="button"
                  className="btn btn-xs whitespace-nowrap"
                  onClick={() => {
                    if (displayName.trim())
                      void runApiNameSuggest(displayName.trim(), true);
                  }}
                  disabled={apiNameLoading || !displayName.trim()}
                  title="用 AI 从显示名称重新推导（覆盖当前值）"
                >
                  {apiNameLoading ? '⏳' : '✨ AI'}
                </button>
              </div>
              <span className="form-hint">
                输入显示名称后 AI 自动生成（PascalCase），可手动修改
              </span>
              {apiName && !isValidApiName(apiName, true) && (
                <span className="mt-1 block text-[11px] text-error">
                  API 名称须以大写字母开头，仅含字母数字
                </span>
              )}
            </>
          )}
        </div>
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor={fDescription.id}>
          Description
        </label>
        <TextAreaInput
          id={fDescription.id}
          inputClassName="form-input"
          value={description}
          onChange={setDescription}
          rows={2}
        />
      </div>
      {editing && (
        <div className="mt-3 rounded-md border border-border bg-bg px-3 py-2 text-[12px] text-text-secondary">
          <span className="font-semibold">存储类型：</span>
          <span className="ml-1">🔒 {storageType === 'VIRTUAL' ? '虚拟对象 VIRTUAL' : '托管对象 MANAGED'}</span>
          <span className="ml-2 text-text-muted">
            （创建后不可更改，如需切换请新建对象）
          </span>
        </div>
      )}
    </div>
  );

  // ── Render ──

  const storageTypeLabel = (st: StorageType) => (st === 'VIRTUAL' ? '虚拟对象' : '托管对象');

  // apiName 预览：用户已填或 AI 生成；不再显示 ObjectType0 本地 fallback 占位。
  const objectApiNamePreview = apiName || '';

  return (
    <Modal
      open
      onClose={onCancel}
      ariaLabel={editing ? '编辑对象' : '创建对象'}
      overlayClassName=""
      panelClassName="dialog max-h-[85vh] min-w-[900px] max-w-[1000px] overflow-hidden p-0"
    >
      <div className="flex max-h-[85vh] min-h-[520px] w-full">
        {/* ── Left: Step Sidebar ── */}
        <div className="flex w-[220px] flex-col border-r border-border bg-sidebar py-5">
          <div className="px-4 pb-3 text-sm font-semibold text-text">
            {editing ? '编辑对象' : '创建对象'}
          </div>
          <div className="px-4 pb-1 text-[11px] uppercase tracking-wider text-text-muted">
            Steps
          </div>
          {STEPS.map((step, i) => {
            const isActive = i === activeStep;
            const isDone = completed.has(i) && i < activeStep;
            return (
              <div
                key={step.id}
                onClick={() => goToStep(i)}
                className={cn(
                  'flex cursor-pointer items-start gap-2.5 border-l-[3px] px-4 py-2.5 transition-all',
                  isActive
                    ? 'border-accent bg-[var(--accent-bg)] text-accent-text'
                    : 'border-transparent text-text-muted',
                  !isActive && isDone && 'text-text-secondary',
                )}
              >
                <div
                  className={cn(
                    'mt-px flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-xs font-bold',
                    isActive
                      ? 'bg-accent text-white'
                      : isDone
                        ? 'bg-success text-white'
                        : 'bg-border text-text-muted',
                  )}
                >
                  {isDone ? '✓' : step.num}
                </div>
                <div>
                  <div className={cn('text-[13px]', isActive ? 'font-semibold' : 'font-normal')}>
                    {step.title}
                  </div>
                  <div className="text-[11px] opacity-60">{step.desc}</div>
                </div>
              </div>
            );
          })}
          <div className="flex-1" />
          <div className="px-4 text-[11px] text-text-muted">
            {editing ? '编辑模式' : '创建模式'}
          </div>
        </div>

        {/* ── Right: Content ── */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-auto p-5">
            {/* ── Step 0: Dataset Selection (create) / Overview (edit) ── */}
            {activeStep === 0 && (
              <div>
                <h2 className="mb-1">{STEPS[0].title}</h2>
                <p className="mb-4 text-[13px] text-text-muted">
                  {editing
                    ? '编辑对象元数据，并可切换数据集（不换可直接下一步）'
                    : '选择存储类型并绑定数据集，或选择暂不关联'}
                </p>

                {/* Edit mode: metadata form first (storage_type locked) */}
                {editing && renderMetadata(true)}

                {/* Edit mode: dataset section header */}
                {editing && (
                  <div className="mb-2 text-[11px] uppercase tracking-wide text-text-muted">
                    数据集
                  </div>
                )}

                {/* storage_type segmented control — create mode only (F1: 提顶) */}
                {!editing && (
                  <div className="mb-4 grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => changeStorageType('MANAGED')}
                      className={cn(
                        'rounded-md border p-3 text-left transition-all',
                        storageType === 'MANAGED'
                          ? 'border-accent bg-[var(--accent-bg)]'
                          : 'border-border hover:border-text-muted',
                      )}
                    >
                      <div className="text-[13px] font-semibold">托管对象 MANAGED</div>
                      <div className="mt-0.5 text-[11px] text-text-muted">数据落地 Iceberg，可写</div>
                    </button>
                    <button
                      type="button"
                      onClick={() => changeStorageType('VIRTUAL')}
                      className={cn(
                        'rounded-md border p-3 text-left transition-all',
                        storageType === 'VIRTUAL'
                          ? 'border-accent bg-[var(--accent-bg)]'
                          : 'border-border hover:border-text-muted',
                      )}
                    >
                      <div className="text-[13px] font-semibold">虚拟对象 VIRTUAL</div>
                      <div className="mt-0.5 text-[11px] text-text-muted">外部表代理，只读不落地</div>
                    </button>
                  </div>
                )}

                {/* “暂不关联” 仅创建态 MANAGED 可见 (F1) */}
                {!editing && storageType === 'MANAGED' && (
                  <label className="mb-3 flex cursor-pointer items-center gap-2">
                    <input
                      type="checkbox"
                      checked={skipDataset}
                      onChange={(e) => {
                        setSkipDataset(e.target.checked);
                        if (e.target.checked) {
                          setDatasetApiName('');
                          setDatasetSchema(null);
                        }
                      }}
                    />
                    <span className="text-[13px] text-text-secondary">
                      暂不关联（稍后可在对象详情补关联）
                    </span>
                  </label>
                )}

                {!skipDataset && (
                  <>
                    <div className="search-wrap mb-3">
                      <TextInput
                        inputClassName="search-input"
                        placeholder="搜索数据集..."
                        value={datasetSearch}
                        onChange={setDatasetSearch}
                      />
                    </div>
                    <div className="mb-3 overflow-hidden rounded-md border border-border">
                      {datasetsLoading ? (
                        <div className="px-3 py-6 text-center text-[12px] text-text-muted">
                          加载数据集…
                        </div>
                      ) : datasetsError ? (
                        <div className="px-3 py-4 text-center text-[12px] text-error">
                          {datasetsError}
                        </div>
                      ) : filteredCandidates.length === 0 ? (
                        <div className="px-3 py-6 text-center text-[12px] text-text-muted">
                          {storageType === 'VIRTUAL'
                            ? '暂无虚拟表，请先在数据源详情登记'
                            : '暂无托管表，请先创建同步任务'}
                        </div>
                      ) : (
                        filteredCandidates.map((ds) => (
                          <div
                            key={ds.api_name}
                            data-testid={`dataset-option-${ds.api_name}`}
                            onClick={() => handleSelectDataset(ds.api_name)}
                            className={cn(
                              'flex cursor-pointer items-center justify-between border-b border-border px-3 py-2.5 text-[13px]',
                              datasetApiName === ds.api_name && 'bg-[var(--accent-bg)]',
                            )}
                          >
                            <div>
                              <span className="text-text">
                                {ds.kind === 'VIRTUAL' ? '🔗' : '📊'} {ds.api_name}
                              </span>
                              <span className="ml-3 text-[11px] text-text-muted">
                                {ds.kind === 'VIRTUAL' ? '虚拟表' : '托管表'}
                                {ds.data_source_api_name ? ` · ${ds.data_source_api_name}` : ''}
                                {ds.row_count_estimate != null
                                  ? ` · ${ds.row_count_estimate.toLocaleString()} 行`
                                  : ''}
                              </span>
                            </div>
                            {datasetApiName === ds.api_name && (
                              <span className="text-[11px] text-accent-text">
                                {editing && ds.api_name === initialDatasetApiName
                                  ? '当前绑定'
                                  : editing
                                    ? '✓ 已切换'
                                    : '✓ 已选择'}
                              </span>
                            )}
                            {editing &&
                              datasetApiName !== ds.api_name &&
                              ds.api_name === initialDatasetApiName && (
                                <span className="text-[11px] text-text-muted">原绑定</span>
                              )}
                          </div>
                        ))
                      )}
                    </div>

                    {datasetApiName && (
                      <div className="rounded-md bg-bg p-2 text-[11px] text-text-muted">
                        {editing
                          ? datasetChangedInWizard
                            ? schemaLoading
                              ? '拉取新数据集列…'
                              : datasetSchema && datasetSchema.length > 0
                                ? `已加载 ${datasetSchema.length} 列，已按同名自动映射源列，下一步请确认/调整`
                                : '新数据集无列信息'
                            : schemaLoading
                              ? '拉取数据集列…'
                              : datasetSchema && datasetSchema.length > 0
                                ? `已加载 ${datasetSchema.length} 列，不换可直接下一步`
                                : ''
                          : schemaLoading
                            ? '拉取数据集列…'
                            : datasetSchema && datasetSchema.length > 0
                              ? scaffoldLoading
                                ? `已加载 ${datasetSchema.length} 列，AI 正在推导对象结构…`
                                : `已加载 ${datasetSchema.length} 列，下一步查看 AI 推导结果`
                              : datasetSchema
                                ? '数据集无列信息（可手动添加属性）'
                                : ''}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* ── Step 1: Properties (F2) ── */}
            {activeStep === 1 && (
              <div>
                <h2 className="mb-1">{STEPS[1].title}</h2>
                <p className="mb-4 text-[13px] text-text-muted">
                  确认/微调 AI 生成的对象结构与字段
                </p>
                {scaffoldLoading && (
                  <div className="mb-3 flex items-center gap-2 rounded-md border border-accent bg-[var(--accent-bg)] px-3 py-2 text-[13px] text-accent-text">
                    <span className="btn-spinner" aria-hidden="true" />
                    AI 正在从数据集推导对象结构…
                  </div>
                )}
                {scaffoldError && !scaffoldLoading && (
                  <div className="mb-3 rounded-md border border-warning bg-[color-mix(in_srgb,var(--color-warning)_12%,transparent)] px-3 py-2 text-[13px] text-text-secondary">
                    ⚠ AI 推导失败，已生成基础结构，请手动补充主键和标题
                  </div>
                )}

                {/* Metadata — create mode only (edit mode shows it on Step 0) */}
                {!editing && renderMetadata(false)}

                {/* Unmapped-properties warning (dataset bound): block Next. */}
                {datasetBound && hasUnmapped && (
                  <div className="mb-3 rounded-md border border-warning bg-[color-mix(in_srgb,var(--color-warning)_12%,transparent)] px-3 py-2 text-[13px] text-text-secondary">
                    ⚠ 还有 {unmappedPropertyNames.length} 个属性未映射源列，需全部映射后才能进入下一步。不需要的属性请删除。
                    <div className="mt-0.5 text-[11px] text-text-muted">
                      未映射：{unmappedPropertyNames.slice(0, 5).join(', ')}
                      {unmappedPropertyNames.length > 5 && ` …等 ${unmappedPropertyNames.length} 个`}
                    </div>
                  </div>
                )}

                {/* Properties Table (F2: + 源列 column) */}
                <div className="card overflow-hidden p-0">
                  <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
                    <span className="text-[13px] font-semibold">Properties</span>
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        className="btn btn-xs"
                        disabled={!hasSchema}
                        onClick={handleGenerateFromDataset}
                        title={hasSchema ? '把数据集列批量生成为属性' : '请先选择带列的数据集'}
                      >
                        ⚡ 从数据集生成属性
                      </button>
                      <span
                        className={cn(
                          'text-[11px]',
                          showErrors && properties.length === 0 ? 'text-error' : 'text-text-muted',
                        )}
                      >
                        {properties.length} defined
                      </span>
                    </div>
                  </div>
                  <table className="data-table mb-0">
                    <thead>
                      <tr>
                        <th className="w-[28px]"></th>
                        <th>Property name</th>
                        <th className="font-mono">API name（预览）</th>
                        <th className="w-[100px]">Type</th>
                        <th className="w-[140px]">源列</th>
                        <th className="w-[80px]">Searchable</th>
                        <th className="w-[50px]"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {properties.map((p, i) => {
                        const preview = previewApiName(p, i);
                        const isPk = i === primaryKeyIndex;
                        const isTitle = usePkAsTitle ? isPk : i === titlePropIndex;
                        const isExpanded = expandedProps.has(i);
                        return (
                          <Fragment key={i}>
                            <tr>
                              <td className="text-center">
                                <button
                                  type="button"
                                  className="text-text-muted hover:text-text"
                                  onClick={() => togglePropExpanded(i)}
                                  aria-label={isExpanded ? '收起属性详情' : '展开属性详情'}
                                  aria-expanded={isExpanded}
                                  title="展开/收起描述与可空设置"
                                >
                                  <span
                                    className={cn(
                                      'inline-block transition-transform text-[10px]',
                                      isExpanded && 'rotate-90',
                                    )}
                                  >
                                    ▶
                                  </span>
                                </button>
                              </td>
                              <td>
                                <TextInput
                                  inputClassName="form-input w-full px-2 py-[3px] text-xs"
                                  value={p.display_name}
                                  onChange={(v) => {
                                    const updated = [...properties];
                                    updated[i] = { ...p, display_name: v };
                                    setProperties(updated);
                                  }}
                                />
                              </td>
                              <td className="font-mono text-xs">
                                <div className="flex items-center gap-1">
                                  {isPk && (
                                    <span className="rounded-pill bg-[var(--accent-bg-strong)] px-[5px] py-px text-[10px] font-bold text-accent-text">
                                      PK
                                    </span>
                                  )}
                                  {isTitle && (
                                    <span className="rounded-pill bg-[var(--teal-bg)] px-[5px] py-px text-[10px] font-bold text-teal">
                                      Title
                                    </span>
                                  )}
                                  <span className="text-text-muted">{preview}</span>
                                </div>
                              </td>
                              <td>
                                <Select
                                  inputClassName="form-select w-full px-1.5 py-[3px] text-[11px]"
                                  value={p.data_type}
                                  onChange={(v) => {
                                    const updated = [...properties];
                                    updated[i] = { ...p, data_type: v as DataType };
                                    setProperties(updated);
                                  }}
                                  aria-label="数据类型"
                                >
                                  {DATA_TYPES.map((dt) => (
                                    <SelectOption
                                      key={dt.value}
                                      value={dt.value}
                                      label={dt.label}
                                    />
                                  ))}
                                </Select>
                              </td>
                              <td>
                                <Select
                                  inputClassName="form-select w-full px-1.5 py-[3px] text-[11px]"
                                  value={p.source_column ?? ''}
                                  disabled={!hasSchema}
                                  onChange={(v) => {
                                    const updated = [...properties];
                                    updated[i] = {
                                      ...p,
                                      source_column: v || undefined,
                                    };
                                    setProperties(updated);
                                  }}
                                  placeholder="—"
                                  aria-label="源列"
                                >
                                  <SelectOption value="" label="—" />
                                  {sourceColumns.map((c) => (
                                    <SelectOption
                                      key={c.name}
                                      value={c.name}
                                      label={`${c.name} (${c.type})`}
                                    />
                                  ))}
                                </Select>
                                {(() => {
                                  // Inline type-compat marker under the source-column select.
                                  // Only shows when a column is picked and its type is
                                  // warn/incompatible vs the property's data_type.
                                  if (!p.source_column) return null;
                                  const col = sourceColumns.find(
                                    (c) => c.name === p.source_column,
                                  );
                                  if (!col) return null;
                                  const v = checkTypeCompatibility(p.data_type, col.type);
                                  if (v === 'exact' || v === 'compatible') return null;
                                  return (
                                    <div
                                      className={cn(
                                        'mt-0.5 text-[9px]',
                                        v === 'incompatible' ? 'text-danger' : 'text-warning',
                                      )}
                                      title={`属性 ${p.data_type} ↔ 列 ${col.type}`}
                                    >
                                      {v === 'incompatible' ? '类型不兼容' : '类型可能有损'}
                                    </div>
                                  );
                                })()}
                              </td>
                              <td className="text-center">
                                <input
                                  type="checkbox"
                                  checked={p.searchable !== false}
                                  onChange={(e) => {
                                    const updated = [...properties];
                                    updated[i] = { ...p, searchable: e.target.checked };
                                    setProperties(updated);
                                  }}
                                  title="倒排索引核心开关：控制属性是否可被搜索/过滤"
                                />
                              </td>
                              <td>
                                <button
                                  className="btn btn-sm btn-danger px-1 text-[10px]"
                                  onClick={() => {
                                    const updated = properties.filter((_, j) => j !== i);
                                    // Re-anchor PK/title indices after removal.
                                    if (primaryKeyIndex === i) {
                                      setPrimaryKeyIndex(updated.length ? 0 : -1);
                                    } else if (primaryKeyIndex > i) {
                                      setPrimaryKeyIndex(primaryKeyIndex - 1);
                                    }
                                    if (titlePropIndex === i) {
                                      setTitlePropIndex(-1);
                                    } else if (titlePropIndex > i) {
                                      setTitlePropIndex(titlePropIndex - 1);
                                    }
                                    setProperties(updated);
                                  }}
                                  aria-label="删除属性"
                                >
                                  ✕
                                </button>
                              </td>
                            </tr>
                            {isExpanded && (
                              <tr className="bg-[var(--accent-bg)]">
                                <td colSpan={7} className="px-4 py-2">
                                  <div className="grid grid-cols-2 gap-3">
                                    <div className="form-group">
                                      <label className="form-label text-[11px]">
                                        描述 (description)
                                      </label>
                                      <TextAreaInput
                                        inputClassName="form-input text-xs"
                                        value={p.description}
                                        rows={2}
                                        onChange={(v) => {
                                          const updated = [...properties];
                                          updated[i] = { ...p, description: v };
                                          setProperties(updated);
                                        }}
                                        placeholder="该属性的业务含义，供 AI 语义理解"
                                      />
                                    </div>
                                    <div className="form-group">
                                      <label className="form-label text-[11px]">
                                        可空 (nullable)
                                      </label>
                                      <label className="flex cursor-pointer items-center gap-2 pt-1">
                                        <input
                                          type="checkbox"
                                          checked={p.nullable}
                                          onChange={(e) => {
                                            const updated = [...properties];
                                            updated[i] = { ...p, nullable: e.target.checked };
                                            setProperties(updated);
                                          }}
                                        />
                                        <span className="text-xs text-text-secondary">
                                          允许为空{p.nullable ? '' : '（非空）'}
                                        </span>
                                      </label>
                                      <div className="mt-1 text-[11px] text-text-muted">
                                        主键属性应非空。AI 推导的描述：{p.description || '（无）'}
                                      </div>
                                    </div>
                                  </div>
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>

                  {/* Add row */}
                  <div className="flex items-center gap-1.5 border-t border-border px-4 py-2">
                    <TextInput
                      inputClassName="form-input flex-1 px-2 py-1 text-xs"
                      placeholder="属性名称（如 FlightNo）"
                      value={newProp.display_name}
                      onChange={(v) => setNewProp({ ...newProp, display_name: v })}
                      onKeyDown={(e) => e.key === 'Enter' && handleAddProperty()}
                    />
                    <Select
                      inputClassName="form-select w-[100px] px-1.5 py-1 text-[11px]"
                      value={newProp.data_type}
                      onChange={(v) => setNewProp({ ...newProp, data_type: v as DataType })}
                      aria-label="数据类型"
                    >
                      {DATA_TYPES.map((dt) => (
                        <SelectOption key={dt.value} value={dt.value} label={dt.label} />
                      ))}
                    </Select>
                    <label className="flex cursor-pointer items-center gap-0.5 whitespace-nowrap text-[11px] text-text-muted">
                      <input
                        type="checkbox"
                        checked={newProp.searchable}
                        onChange={(e) => setNewProp({ ...newProp, searchable: e.target.checked })}
                      />
                      🔍
                    </label>
                    <button
                      className="btn btn-primary btn-sm whitespace-nowrap"
                      onClick={handleAddProperty}
                    >
                      + Add
                    </button>
                  </div>
                </div>
                {showErrors && properties.length === 0 && (
                  <div className="mt-2 text-xs text-error">至少需要添加一个属性</div>
                )}

                {/* Primary Key + Title (by property index) — 配置在属性之后：
                    主键/标题是从已定义属性中选出的，先有属性再选才符合认知顺序。 */}
                <div className="card mt-4 p-4">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="form-group">
                      <label className="form-label" htmlFor={fPrimaryKey.id}>
                        主键字段 (Primary Key) *
                      </label>
                      <Select
                        id={fPrimaryKey.id}
                        inputClassName={cn(
                          'form-select',
                          showErrors && primaryKeyIndex < 0 && 'border-error',
                        )}
                        value={String(primaryKeyIndex)}
                        onChange={(v) => {
                          setPrimaryKeyIndex(Number(v));
                          setShowErrors(false);
                        }}
                        placeholder="-- 选择主键 --"
                        aria-label="主键字段"
                      >
                        <SelectOption value="-1" label="-- 选择主键 --" />
                        {properties.map((p, i) => (
                          <SelectOption key={i} value={String(i)} label={p.display_name} />
                        ))}
                      </Select>
                      <div className="mt-1 text-[11px] text-text-muted">
                        唯一标识对象实例，不可重复、不可为空
                      </div>
                      {showErrors && primaryKeyIndex < 0 && (
                        <div className="mt-1 text-[11px] text-error">请选择主键字段</div>
                      )}
                    </div>
                    <div className="form-group">
                      <label className="form-label" htmlFor={fTitleProp.id}>
                        标题字段 (Title) *
                      </label>
                      <Select
                        id={fTitleProp.id}
                        inputClassName={cn(
                          'form-select',
                          showErrors && titlePropIndex < 0 && 'border-error',
                        )}
                        value={String(titlePropIndex)}
                        onChange={(v) => {
                          setTitlePropIndex(Number(v));
                          setShowErrors(false);
                        }}
                        placeholder="-- 选择标题字段 --"
                        aria-label="标题字段"
                      >
                        <SelectOption value="-1" label="-- 选择标题字段 --" />
                        {properties.map((p, i) =>
                          i === primaryKeyIndex ? null : (
                            <SelectOption key={i} value={String(i)} label={p.display_name} />
                          ),
                        )}
                      </Select>
                      <div className="mt-1 text-[11px] text-text-muted">
                        界面友好展示对象实例，不建议用主键
                      </div>
                      {showErrors && titlePropIndex < 0 && (
                        <div className="mt-1 text-[11px] text-error">请选择标题字段</div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* ── Step 2: Review ── */}
            {activeStep === 2 && (
              <div>
                <h2 className="mb-1">{STEPS[2].title}</h2>
                {hasUnmapped && datasetApiName ? (
                  <div className="card mb-4 border-warning bg-[color-mix(in_srgb,var(--color-warning)_12%,var(--color-bg))] p-3.5 text-[13px] text-text-secondary">
                    ⚠ 还有 {unmappedPropertyNames.length} 个属性未映射源列，禁止保存。请返回「配置属性」步为每个属性选择源列，或删除不需要的属性。
                    <div className="mt-1 text-[11px] text-text-muted">
                      未映射：{unmappedPropertyNames.slice(0, 5).join(', ')}
                      {unmappedPropertyNames.length > 5 && ` …等 ${unmappedPropertyNames.length} 个`}
                    </div>
                  </div>
                ) : (
                  <div className="card mb-4 border-success bg-[color-mix(in_srgb,var(--color-success)_10%,var(--color-bg))] p-3.5 text-[13px]">
                    ✅ 对象类型配置有效，可以{editing ? '更新' : '创建'}
                  </div>
                )}

                <div className="card p-4">
                  <div className="grid grid-cols-2 gap-2 text-[13px]">
                    <div className="text-text-muted">Display name</div>
                    <div className="font-semibold">{displayName || '-'}</div>
                    <div className="text-text-muted">API name</div>
                    <div className="font-mono text-xs">{objectApiNamePreview || '-'}</div>
                    <div className="text-text-muted">Storage type</div>
                    <div>{storageTypeLabel(storageType)}</div>
                    <div className="text-text-muted">Primary key</div>
                    <div className="font-mono text-xs">
                      {primaryKeyIndex >= 0
                        ? properties[primaryKeyIndex]?.display_name || '-'
                        : '-'}
                    </div>
                    <div className="text-text-muted">数据集</div>
                    <div className="font-mono text-xs">
                      {skipDataset || !datasetApiName ? (
                        <span className="text-warning">⚠ 未关联</span>
                      ) : (
                        datasetApiName
                      )}
                    </div>
                    <div className="text-text-muted">Properties</div>
                    <div>{properties.length}</div>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* ── Navigation Buttons ── */}
          <div className="flex items-center justify-between border-t border-border bg-sidebar px-5 py-3.5">
            <div>
              <button className="btn" onClick={handleCancel}>
                Cancel
              </button>
            </div>
            <div className="flex gap-2">
              {activeStep > 0 && (
                <button className="btn" onClick={() => goToStep(activeStep - 1)}>
                  ← Back
                </button>
              )}
              {activeStep < 2 ? (
                <button
                  className={cn('btn btn-primary', !canGoNext() && 'opacity-50')}
                  disabled={!canGoNext()}
                  onClick={() => {
                    if (canGoNext()) {
                      setShowErrors(false);
                      goToStep(activeStep + 1);
                    } else {
                      setShowErrors(true);
                    }
                  }}
                >
                  Next →
                </button>
              ) : (
                <button
                  className={cn('btn btn-primary', submitting && 'is-loading')}
                  onClick={handleFinish}
                  disabled={submitting || !canFinish}
                  title={!canFinish ? '存在未映射源列的属性，禁止保存' : undefined}
                >
                  {submitting && <span className="btn-spinner" aria-hidden="true" />}
                  {submitting
                    ? editing
                      ? 'Saving…'
                      : 'Creating…'
                    : editing
                      ? 'Save Changes'
                      : 'Create Object'}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
}
