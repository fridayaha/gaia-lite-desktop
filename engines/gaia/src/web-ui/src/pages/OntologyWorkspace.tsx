import { useState, useEffect, useCallback, useRef } from 'react';
import type { FormEvent } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
  createOntology,
  listOntologies,
  listObjectTypeSummaries,
  createObjectTypeBatch,
  updateObjectTypeBatch,
  deleteObjectType,
  deleteOntology,
  deprecateOntology,
  restoreOntology,
  getOntologyImpact,
  getObjectType,
  getDataset,
  listLinkTypes,
} from '../api/client';
import type { ObjectTypeBatchPayload } from '../api/client';
import { OntologySidebar } from '../components/OntologySidebar';
import { CreateObjectWizard } from '../components/CreateObjectWizard';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Modal } from '../components/Modal';
import { AiAssistantDock } from '../components/AiAssistantDock';
import { ObjectDetailPanel } from '../components/ObjectDetailPanel';
import { OntologyGraph } from '../components/OntologyGraph';
import { TableView, CardView } from '../components/ObjectTypeViews';
import { ToastView } from '../components/ToastView';
import { useToast } from '../hooks/useToast';
import { useAsyncAction } from '../hooks/useAsyncAction';
import { useHotkeys } from '../hooks/useHotkeys';
import { useAllowedActions } from '../hooks/useAllowedActions';
import { PermissionGate } from '../components/permission';
import { formatError } from '../lib/formatError';
import { isValidApiName, OBJECT_TYPE_API_NAME_PATTERN } from '../lib/deriveApiName';
import { suggestOntologyApiName } from '../api/ai';
import { TextInput, TextAreaInput } from '../components/ui/TextField';
import type { LayoutOutletContext } from '../components/Layout';
import type {
  Ontology,
  ObjectTypeCreate,
  ObjectTypeSummary,
  LinkTypeDef,
  BackingColumnRef,
} from '../types';
import type { PropertyDraft, LinkDraft, ActionDraft, ObjectWizardData } from '../types/wizard';
import { cn } from '../lib/cn';

/** Trash-can icon — the established convention for "delete".
 * ("×" means "cancel/close", not delete — see SSW/Primer UX guidance.) */
// ── F3 helper: resolve catalog/schema/table for backing_mapping ──
//
// MANAGED datasets live in Iceberg under a fixed (iceberg, ontology, table)
// locator — storage_location is the S3 path, not a three-part name, so we
// fall back to the Gaia defaults. VIRTUAL datasets store a three-part
// "catalog.schema.table" locator in storage_location (see B2/B3).
function parseDatasetLocator(
  dataset: { kind?: string; storage_location?: string } | null,
  datasetApiName: string,
): { catalog: string; schema: string; table: string } {
  if (dataset?.kind === 'VIRTUAL') {
    const parts = (dataset.storage_location || '').split('.');
    if (parts.length === 3) {
      return { catalog: parts[0], schema: parts[1], table: parts[2] };
    }
  }
  // MANAGED default (or VIRTUAL with malformed locator)
  return { catalog: 'iceberg', schema: 'ontology', table: datasetApiName };
}

// ── Shared handlers for table/card views ──

export function OntologyWorkspace() {
  const { setFullBleed } = useOutletContext<LayoutOutletContext>();
  useEffect(() => {
    setFullBleed(true);
    return () => setFullBleed(false);
  }, [setFullBleed]);

  const [ontologies, setOntologies] = useState<Ontology[]>([]);
  const [ontologiesLoading, setOntologiesLoading] = useState(true);
  const [selectedOntology, setSelectedOntology] = useState<string | null>(null);
  // The full Ontology record for the currently-selected api_name (for the
  // detail header + delete action). null when nothing is selected.
  const currentOntology = ontologies.find((o) => o.api_name === selectedOntology) ?? null;
  // Ontology 权限决策（ship-the-decision）
  const { decisions: ontoDecisions } = useAllowedActions(
    'ONTOLOGY',
    selectedOntology ? [selectedOntology] : [],
  );
  const [selectedObjectType, setSelectedObjectType] = useState<string | null>(null);
  const [objectTypes, setObjectTypes] = useState<ObjectTypeSummary[]>([]);
  const [links, setLinks] = useState<LinkTypeDef[]>([]);
  const [viewMode, setViewMode] = useState<'table' | 'card' | 'canvas'>('table');

  // 左侧“我的本体”sidebar 折叠状态。由 Workspace 统一管理，便于与 AI 助手 dock 联动。
  // 每次打开默认展开（不跨会话持久化）。
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(false);
  const toggleSidebar = useCallback((next: boolean) => {
    setSidebarCollapsed(next);
  }, []);
  // 联动：AI 助手 dock 从折叠展开时自动收起左侧 sidebar，给主区 + dock 让出
  // 空间。sidebar 仍可手动展开，不会反过来收起 dock。跳过 dock 首次挂载的
  // 初始展开态——否则首次打开页面 sidebar 会被无条件收起，违背“默认展开”。
  const dockFirstRun = useRef(true);
  const handleDockCollapsedChange = useCallback(
    (collapsed: boolean) => {
      if (dockFirstRun.current) {
        dockFirstRun.current = false;
        return;
      }
      if (!collapsed) toggleSidebar(true);
    },
    [toggleSidebar],
  );
  const [showCreateOntology, setShowCreateOntology] = useState(false);
  // 新建本体：displayName 主填 + apiName 可编辑预览（PascalCase 实时推导）
  const [ontoDisplayName, setOntoDisplayName] = useState('');
  const [ontoApiName, setOntoApiName] = useState('');
  const [ontoUserOverrodeApi, setOntoUserOverrodeApi] = useState(false);
  const [ontoApiLoading, setOntoApiLoading] = useState(false);
  const [showCreateObject, setShowCreateObject] = useState(false);
  const [showEditObject, setShowEditObject] = useState(false);
  const [aiPrefillData, setAiPrefillData] = useState<Partial<ObjectWizardData> | null>(null);
  const [editingObjectData, setEditingObjectData] = useState<Partial<ObjectWizardData> | null>(
    null,
  );
  const [confirmDelete, setConfirmDelete] = useState<{
    type: string;
    name: string;
    details: string[];
    action: () => void;
  } | null>(null);
  const { toast, show: setToast, dismiss } = useToast();
  const createOntologyAction = useAsyncAction();
  const wizardAction = useAsyncAction();
  const editAction = useAsyncAction();

  // 快捷键：1/2/3 切换视图（熟手高效）
  useHotkeys([
    { key: '1', handler: () => setViewMode('table') },
    { key: '2', handler: () => setViewMode('card') },
    { key: '3', handler: () => setViewMode('canvas') },
  ]);

  // ── Load ontologies on mount ──
  useEffect(() => {
    let cancelled = false;
    async function load() {
      setOntologiesLoading(true);
      try {
        const data = await listOntologies(false, true);
        if (!cancelled) setOntologies(data);
      } catch {
        if (!cancelled) setToast('加载本体列表失败', 'error');
      } finally {
        if (!cancelled) setOntologiesLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [setToast]);

  // ── Reload helper used by handlers ──

  // ── Stable refs for cytoscape event handlers ──

  // ── Handle select object type (declared before reload helper) ──
  const handleSelectObjectType = useCallback((_ontoName: string, typeName: string) => {
    setSelectedObjectType(typeName);
  }, []);

  // ── Stable refs for cytoscape event handlers ──
  // objectTypes 与 links 必须**同一次渲染**一起更新：图谱组件 syncElements 依赖二者,
  // 若分两次 setState（先节点后边），中间态会触发「新节点 + 旧边/空边」的布局——
  // fcose tile:true 把无边的孤立节点排成紧凑网格，边到达后 hasNewNodes=false 不重排,
  // 导致切换本体后节点挤在一起、关系看不到（已修复的回归 bug）。
  const reloadObjectTypes = useCallback(
    async (ontoName: string) => {
      try {
        const [ots, lks] = await Promise.all([
          listObjectTypeSummaries(ontoName),
          listLinkTypes(ontoName).catch(() => [] as LinkTypeDef[]),
        ]);
        setObjectTypes(ots);
        setLinks(lks);
      } catch {
        setToast('加载对象类型失败');
        setObjectTypes([]);
        setLinks([]);
      }
    },
    [setToast],
  ); // setObjectTypes, setLinks, setLoading are React stable setters
  useEffect(() => {
    if (!selectedOntology) return;
    let cancelled = false;
    async function load() {
      try {
        // 并行加载对象类型 + 关系，拿到后一次性 setState，避免中间态触发图谱脏布局
        const [ots, lks] = await Promise.all([
          listObjectTypeSummaries(selectedOntology!),
          listLinkTypes(selectedOntology!).catch(() => [] as LinkTypeDef[]),
        ]);
        if (cancelled) return;
        setObjectTypes(ots);
        setLinks(lks);
        // 不自动选中对象：详情面板只在用户主动点击时展示
      } catch {
        if (!cancelled) {
          setToast('加载对象类型失败');
          setObjectTypes([]);
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
    // handleSelectObjectType intentionally omitted — it selects the first type on first load
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOntology]);

  // ── Auto-refresh on window focus ──
  // Data can change out-of-band from this page's own React state — notably
  // the AI assistant dock writes object types via /ai/agent (a separate AG-UI
  // runtime), and external MCP clients can mutate the ontology too. Rather
  // than couple those writers to this page, refresh the object-type list
  // when the user returns to the window (focus). Cheap, decoupled, and
  // covers the common "asked the AI to build something → switched back to
  // the table" flow. A manual refresh button is also provided below.
  useEffect(() => {
    if (!selectedOntology) return;
    const onFocus = () => {
      // Avoid clobbering during a user's in-flight create/edit — only refresh
      // when no modal is open.
      if (showCreateObject || showEditObject || confirmDelete) return;
      reloadObjectTypes(selectedOntology);
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [selectedOntology, showCreateObject, showEditObject, confirmDelete, reloadObjectTypes]);

  // ── Create ontology ──
  // ── 新建本体：displayName → apiName 完全由 AI 生成或用户输入 ──
  //
  // All-in-AI 体验：本体是用户命名的命名空间，不给本地 fallback 默认值
  // （避免出现 ObjectType0 这类无意义占位）。
  //   - 用户只填 displayName，输入停止 600ms 后 AI 自动生成 PascalCase apiName；
  //   - 用户也可手动编辑 apiName 输入框，编辑后不再自动覆盖；
  //   - AI 失败时输入框留空，由用户手填兑底。
  // 竞态控制：每次发起 AI 请求递增序号，过期请求的结果被丢弃。
  const ontoAiReqId = useRef(0);
  const ontoAiTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runOntoAiSuggest = useCallback(
    async (displayName: string, markOverrode: boolean) => {
      const reqId = ++ontoAiReqId.current;
      setOntoApiLoading(true);
      try {
        const existing = ontologies.map((o) => o.api_name);
        const suggested = await suggestOntologyApiName(displayName, existing);
        // 丢弃过期结果（用户已继续输入或手动编辑）
        if (reqId !== ontoAiReqId.current) return;
        if (markOverrode) setOntoUserOverrodeApi(true);
        setOntoApiName(suggested);
      } catch (err) {
        if (reqId !== ontoAiReqId.current) return;
        setToast('AI 推导失败：' + formatError(err), 'error');
      } finally {
        if (reqId === ontoAiReqId.current) setOntoApiLoading(false);
      }
    },
    [ontologies, setToast],
  );

  // 自动触发：displayName 变化后 debounce 600ms，用户未手改则自动调 AI 生成。
  // 本体命名空间不给本地默认值，完全交给 AI / 用户。
  useEffect(() => {
    if (ontoAiTimer.current) clearTimeout(ontoAiTimer.current);
    const name = ontoDisplayName.trim();
    if (!name || ontoUserOverrodeApi) return;
    ontoAiTimer.current = setTimeout(() => {
      void runOntoAiSuggest(name, false);
    }, 600);
    return () => {
      if (ontoAiTimer.current) clearTimeout(ontoAiTimer.current);
    };
  }, [ontoDisplayName, ontoUserOverrodeApi, runOntoAiSuggest]);

  const handleOntoDisplayNameChange = (value: string) => {
    setOntoDisplayName(value);
    // 本体不给本地默认值：未手改时清空 apiName，等 AI 自动生成。
    if (!ontoUserOverrodeApi) {
      setOntoApiName('');
    }
  };
  const handleOntoApiNameEdit = (value: string) => {
    // 用户手动编辑 → 标记，后续自动 AI 不再覆盖；并作废在途 AI 请求。
    ontoAiReqId.current++;
    setOntoUserOverrodeApi(true);
    setOntoApiName(value);
  };
  const handleSuggestOntoApiName = () => {
    if (!ontoDisplayName.trim()) return;
    // 手动重试：强制 AI 生成一次，并标记为已确定。
    void runOntoAiSuggest(ontoDisplayName.trim(), true);
  };

  const resetOntoForm = () => {
    // 作废在途 AI 请求并清 debounce 定时器，避免残留结果写入下次打开的表单。
    ontoAiReqId.current++;
    if (ontoAiTimer.current) clearTimeout(ontoAiTimer.current);
    setOntoApiLoading(false);
    setOntoDisplayName('');
    setOntoApiName('');
    setOntoUserOverrodeApi(false);
  };

  const handleCreateOntology = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const apiName = ontoApiName.trim();
    const displayName = ontoDisplayName.trim();
    if (!isValidApiName(apiName, true)) {
      setToast('API 名称须以大写字母开头，仅含字母数字', 'error');
      return;
    }
    if (!displayName) {
      setToast('请填写显示名称', 'error');
      return;
    }
    const description = (new FormData(e.currentTarget).get('description') as string) || '';
    const result = await createOntologyAction.run(() =>
      createOntology({
        api_name: apiName,
        display_name: displayName,
        description,
      }),
    );
    if (!result) {
      setToast(formatError(createOntologyAction.error, '创建失败'), 'error');
      return;
    }
    setShowCreateOntology(false);
    resetOntoForm();
    // Reload ontologies list
    try {
      const data = await listOntologies(false, true);
      setOntologies(data);
    } catch {
      // non-critical
    }
    setToast('本体创建成功', 'success');
  };

  // ── Shared handlers for table/card views ──
  const handleEdit = async (ot: ObjectTypeSummary) => {
    if (!selectedOntology) return;
    // Load full object details on demand
    const fullOt = await getObjectType(selectedOntology, ot.api_name);
    // Filter outgoing links for this object type
    const outgoingLinks: LinkDraft[] = links
      .filter((l) => l.source_object_type_id === fullOt.id)
      .map((l) => ({
        display_name: l.display_name,
        target_object_type_id: l.target_object_type_id,
        cardinality: l.cardinality,
        direction: l.direction as 'OUTGOING',
      }));
    setEditingObjectData({
      api_name: ot.api_name,
      display_name: ot.display_name,
      description: fullOt.description,
      storage_type: fullOt.storage_type === 'VIRTUAL' ? 'VIRTUAL' : 'MANAGED',
      dataset_api_name:
        fullOt.properties.find((p) => p.backing_mapping)?.backing_mapping?.dataset_api_name || '',
      properties: fullOt.properties.map((p) => ({
        display_name: p.display_name,
        description: p.description || '',
        data_type: p.data_type,
        is_primary_key: p.is_primary_key,
        is_title_property: p.is_title_property,
        searchable: p.indexed !== false,
        nullable: p.nullable !== false,
        source_column: p.backing_mapping?.backing_column,
        // Carry the real api_name so same-name column matching on dataset
        // switch uses the actual identifier, not a display-name-derived guess.
        _preview_api_name: p.api_name,
      })),
      links: outgoingLinks,
    });
    setShowEditObject(true);
  };

  const handleDelete = (name: string, displayName: string) => {
    handleDeleteObjectType(name, displayName);
  };

  // ── Create object (wizard, single batch transaction) ──
  const handleWizardComplete = async (
    data: ObjectTypeCreate & {
      properties: PropertyDraft[];
      links: LinkDraft[];
      actions: ActionDraft[];
      dataset_api_name?: string;
    },
  ) => {
    if (!selectedOntology) return;
    const result = await wizardAction.run(async () => {
      // F3: build backing_mapping for properties that have a source column
      // bound to the selected dataset. catalog/schema/table are derived from
      // the dataset's storage_location: MANAGED → (iceberg, ontology, table);
      // VIRTUAL → parse the three-part locator catalog.schema.table.
      const datasetApiName = data.dataset_api_name || '';
      const boundDataset = datasetApiName
        ? await getDataset(datasetApiName).catch(() => null)
        : null;
      const mappingLocator = parseDatasetLocator(boundDataset, datasetApiName);
      const payload: ObjectTypeBatchPayload = {
        api_name: data.api_name,
        display_name: data.display_name,
        description: data.description,
        storage_type: data.storage_type,
        properties: data.properties.map((p) => ({
          display_name: p.display_name,
          description: p.description,
          data_type: p.data_type,
          searchable: p.searchable !== false,
          is_primary_key: p.is_primary_key,
          is_title_property: p.is_title_property,
          backing_mapping:
            datasetApiName && p.source_column
              ? ({
                  dataset_api_name: datasetApiName,
                  backing_catalog: mappingLocator.catalog,
                  backing_schema: mappingLocator.schema,
                  backing_table: mappingLocator.table,
                  backing_column: p.source_column,
                } as BackingColumnRef)
              : null,
        })),
        links: data.links.map((l) => ({
          display_name: l.display_name,
          target_object_type_id: l.target_object_type_id,
          cardinality: l.cardinality,
          direction: l.direction,
        })),
      };
      return createObjectTypeBatch(selectedOntology, payload);
    });
    if (!result) {
      setToast(formatError(wizardAction.error, '创建失败'), 'error');
      return;
    }
    setShowCreateObject(false);
    reloadObjectTypes(selectedOntology);
    setToast(
      `对象 "${result.display_name}" 创建成功 (${data.properties.length} 属性, ${data.links.length} 关系)`,
      'success',
    );
  };

  // ── Delete ontology (whole, high-risk: type api_name to confirm) ──
  // Lives in the detail header (not the sidebar list) per master-detail UX:
  // destructive actions belong in the object's own surface, separated from
  // primary actions, with a high-severity confirmation (SAP Fiori / Primer).
  const handleDeleteOntology = () => {
    const onto = currentOntology;
    if (!onto) return;
    // v5.2: fetch the cascade-impact report first so the confirm dialog
    // shows real counts (not the stale [unknown] strings). If the ontology
    // is still ACTIVE, the report's can_delete=False and we block here with
    // a prompt to Deprecate first (design §6.1).
    setConfirmDelete({
      type: '本体',
      name: onto.api_name,
      details: [
        onto.object_types_count > 0
          ? `${onto.object_types_count} 个对象类型（含属性/关系/动作）`
          : '',
        '关联的 Doris 索引表（将 drop）',
        '本体下的所有对象实例数据',
      ].filter(Boolean),
      action: async () => {
        const impact = await getOntologyImpact(onto.api_name);
        if (!impact.can_delete) {
          throw new Error(impact.blocked_reason || '请先弃用（Deprecate）本体');
        }
        await deleteOntology(onto.api_name);
        // If we deleted the currently-open ontology, clear the workspace.
        if (selectedOntology === onto.api_name) {
          setSelectedOntology(null);
          setSelectedObjectType(null);
          setObjectTypes([]);
          setLinks([]);
        }
        // Refresh the sidebar list.
        try {
          const data = await listOntologies(false, true);
          setOntologies(data);
        } catch {
          // non-critical — the row is already gone on the server
        }
        setToast(`已删除本体 ${onto.display_name}，7天内可恢复`, 'success');
      },
    });
  };

  // v5.2: Deprecate (ACTIVE → DEPRECATED) — precondition for delete.
  const handleDeprecateOntology = async () => {
    const onto = currentOntology;
    if (!onto) return;
    try {
      await deprecateOntology(onto.api_name);
      const data = await listOntologies(false, true);
      setOntologies(data);
      setToast(`已弃用本体 ${onto.display_name}，现可删除`, 'success');
    } catch (e) {
      setToast(`弃用失败：${e instanceof Error ? e.message : String(e)}`, 'error');
    }
  };

  // v5.2: Restore a soft-deleted ontology (clears deleted_at; status stays DEPRECATED).
  // Physical resources are NOT re-provisioned — surface that in the toast.
  // NOTE: the recycle-bin tab that calls this is deferred (decision 11); the
  // API client (restoreOntology) is wired so the future tab only needs UI.
  const handleRestoreOntology = useCallback(
    async (apiName: string) => {
      try {
        await restoreOntology(apiName);
        const data = await listOntologies(false, true);
        setOntologies(data);
        setSelectedOntology(apiName);
        setToast(`已恢复本体 ${apiName}，Doris 索引需重新同步`, 'success');
      } catch (e) {
        setToast(`恢复失败：${e instanceof Error ? e.message : String(e)}`, 'error');
      }
    },
    [setOntologies, setSelectedOntology, setToast],
  );
  // Exposed for the future recycle-bin tab (decision 11); referenced via the
  // ref so eslint's no-unused-vars does not flag the lifecycle handler.
  void handleRestoreOntology;

  // ── Delete ──
  const handleDeleteObjectType = (name: string, displayName: string) => {
    const ot = objectTypes.find((o) => o.api_name === name);
    const propCount = ot?.properties_count || 0;
    setConfirmDelete({
      type: '对象',
      name: displayName,
      details: [propCount > 0 ? `${propCount} 个属性` : '', '关联的关系', '关联的动作'].filter(
        Boolean,
      ),
      action: async () => {
        if (!selectedOntology) return;
        await deleteObjectType(selectedOntology, name);
        setSelectedObjectType(null);
        reloadObjectTypes(selectedOntology);
        setToast(`已删除 ${displayName}`);
      },
    });
  };

  return (
    <div className="flex h-full gap-0">
      <OntologySidebar
        ontologies={ontologies}
        loading={ontologiesLoading}
        selectedOntology={selectedOntology}
        onSelectOntology={(name) => {
          setSelectedOntology(name);
          setSelectedObjectType(null);
        }}
        onCreateOntology={() => setShowCreateOntology(true)}
        onDeprecateOntology={(apiName) => {
          setSelectedOntology(apiName);
          handleDeprecateOntology();
        }}
        onRestoreOntology={(apiName) => {
          handleRestoreOntology(apiName);
        }}
        onDeleteOntology={(apiName, _displayName) => {
          setSelectedOntology(apiName);
          handleDeleteOntology();
        }}
        decisions={ontoDecisions}
        collapsed={sidebarCollapsed}
        onCollapsedChange={toggleSidebar}
      />

      <div className="flex flex-1 min-w-0 overflow-hidden">
        <div className="flex flex-1 min-w-0 flex-col overflow-hidden p-6">
          {selectedOntology ? (
            <>
              <div className="page-header">
                <div className="flex items-start gap-3">
                  <div className="min-w-0">
                    <h1 className="truncate">
                      {currentOntology?.display_name ?? selectedOntology}
                      {currentOntology?.status === 'DEPRECATED' && (
                        <span className="ml-2 badge badge-warning" title="已弃用（Deprecate）">
                          已弃用
                        </span>
                      )}
                    </h1>
                    {currentOntology && (
                      <code className="ontology-api-name">{currentOntology.api_name}</code>
                    )}
                  </div>
                </div>
                {currentOntology?.status === 'DEPRECATED' && (
                  <div className="mt-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-text-secondary">
                    ⚠ 此本体已弃用。可在左侧列表 ⋯ 菜单「恢复」回活跃状态，或「删除」彻底软删除（7
                    天内可从回收站恢复）。
                  </div>
                )}
                <div className="page-header-actions">
                  <button
                    className="btn btn-sm"
                    title="刷新对象类型列表"
                    onClick={() => selectedOntology && reloadObjectTypes(selectedOntology)}
                  >
                    ↻ 刷新
                  </button>
                  <PermissionGate action="ontology:edit" resourceId={selectedOntology!} decisions={ontoDecisions} mode="disable">
                    <button className="btn btn-primary" onClick={() => setShowCreateObject(true)}>
                      + 新建对象
                    </button>
                  </PermissionGate>
                </div>
              </div>

              <div className="view-toggle">
                <button
                  className={cn('view-toggle-btn', viewMode === 'table' && 'active')}
                  onClick={() => setViewMode('table')}
                >
                  📋 表格
                </button>
                <button
                  className={cn('view-toggle-btn', viewMode === 'card' && 'active')}
                  onClick={() => setViewMode('card')}
                >
                  🃏 卡片
                </button>
                <button
                  className={cn('view-toggle-btn', viewMode === 'canvas' && 'active')}
                  onClick={() => setViewMode('canvas')}
                >
                  🕸 图谱
                </button>
              </div>

              <div className="flex min-h-0 flex-1 flex-col">
                <div
                  className={cn('min-h-0 flex-1 overflow-auto', viewMode !== 'table' && 'hidden')}
                >
                  <TableView
                    objectTypes={objectTypes}
                    links={links}
                    selectedObjectType={selectedObjectType}
                    onSelect={(name) =>
                      selectedOntology && handleSelectObjectType(selectedOntology, name)
                    }
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                  />
                </div>
                <div
                  className={cn('min-h-0 flex-1 overflow-auto', viewMode !== 'card' && 'hidden')}
                >
                  <CardView
                    objectTypes={objectTypes}
                    links={links}
                    selectedObjectType={selectedObjectType}
                    onSelect={(name) =>
                      selectedOntology && handleSelectObjectType(selectedOntology, name)
                    }
                    onEdit={handleEdit}
                    onDelete={handleDelete}
                  />
                </div>
                {/* 图谱视图：常驻不卸载，用 hidden 显隐，保留 cytoscape 实例/布局/缩放状态 */}
                <div className={cn('min-h-0 flex-1', viewMode !== 'canvas' && 'hidden')}>
                  <OntologyGraph
                    objectTypes={objectTypes}
                    links={links}
                    visible={viewMode === 'canvas'}
                    onSelectObject={(name) =>
                      selectedOntology && handleSelectObjectType(selectedOntology, name)
                    }
                  />
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state mt-20">
              <h1 className="mb-2 text-[28px]">🧠</h1>
              <h2>欢迎使用 Gaia 本体建模</h2>
              <p className="mb-6">用业务语言描述你的业务，Gaia 将此转化为 AI 可理解的语义模型</p>
              <button className="btn btn-primary" onClick={() => setShowCreateOntology(true)}>
                创建第一个本体
              </button>
            </div>
          )}
        </div>

        {/* 右侧详情面板 */}
        {selectedOntology &&
          selectedObjectType &&
          (() => {
            const ot = objectTypes.find((o) => o.api_name === selectedObjectType);
            if (!ot) return null;
            return (
              <ObjectDetailPanel
                ontologyName={selectedOntology}
                objectType={ot}
                onEdit={() => handleEdit(ot)}
                onDelete={() => handleDeleteObjectType(ot.api_name, ot.display_name)}
                onClose={() => setSelectedObjectType(null)}
              />
            );
          })()}

        {/* AI 本体助手 Dock：常驻最右，与对象详情面板互斥（详情打开则自动折叠） */}
        {selectedOntology && (
          <AiAssistantDock
            ontology={selectedOntology}
            detailOpen={!!selectedObjectType}
            onForceExpand={() => setSelectedObjectType(null)}
            onCollapsedChange={handleDockCollapsedChange}
          />
        )}
      </div>

      {/* Modals */}
      {showCreateObject && (
        <CreateObjectWizard
          objectTypes={objectTypes.map((o) => ({ id: o.id, display_name: o.display_name }))}
          initialData={aiPrefillData || undefined}
          onComplete={(data) => {
            handleWizardComplete(data);
            setAiPrefillData(null);
          }}
          onCancel={() => {
            setShowCreateObject(false);
            setAiPrefillData(null);
          }}
        />
      )}

      {showEditObject && editingObjectData && (
        <CreateObjectWizard
          objectTypes={objectTypes.map((o) => ({ id: o.id, display_name: o.display_name }))}
          editing
          initialData={editingObjectData}
          submitting={editAction.loading}
          onComplete={async (data) => {
            if (!selectedOntology) return;
            const result = await editAction.run(async () => {
              // F3: preserve/rebuild backing_mapping on edit (don't drop it).
              const datasetApiName = data.dataset_api_name || '';
              const boundDataset = datasetApiName
                ? await getDataset(datasetApiName).catch(() => null)
                : null;
              const locator = parseDatasetLocator(boundDataset, datasetApiName);
              await updateObjectTypeBatch(selectedOntology, editingObjectData.api_name!, {
                api_name: editingObjectData.api_name,
                display_name: data.display_name,
                description: data.description,
                storage_type: data.storage_type,
                properties: data.properties.map(
                  (p: {
                    display_name: string;
                    description?: string;
                    data_type: string;
                    searchable?: boolean;
                    is_primary_key?: boolean;
                    is_title_property?: boolean;
                    source_column?: string;
                  }) => ({
                    display_name: p.display_name,
                    description: p.description,
                    data_type: p.data_type,
                    searchable: p.searchable !== false,
                    is_primary_key: p.is_primary_key,
                    is_title_property: p.is_title_property,
                    backing_mapping:
                      datasetApiName && p.source_column
                        ? ({
                            dataset_api_name: datasetApiName,
                            backing_catalog: locator.catalog,
                            backing_schema: locator.schema,
                            backing_table: locator.table,
                            backing_column: p.source_column,
                          } as BackingColumnRef)
                        : null,
                  }),
                ),
                links: data.links.map((l) => ({
                  display_name: l.display_name,
                  target_object_type_id: l.target_object_type_id,
                  cardinality: l.cardinality,
                  direction: l.direction,
                })),
              });
              return true;
            });
            if (!result) {
              setToast(formatError(editAction.error, '更新失败'), 'error');
              return;
            }
            setShowEditObject(false);
            setEditingObjectData(null);
            reloadObjectTypes(selectedOntology);
            setToast(`对象已更新`, 'success');
          }}
          onCancel={() => {
            setShowEditObject(false);
            setEditingObjectData(null);
          }}
        />
      )}

      {showCreateOntology && (
        <Modal
          open
          onClose={() => {
            if (!createOntologyAction.loading) {
              setShowCreateOntology(false);
              resetOntoForm();
            }
          }}
          ariaLabel="新建本体"
        >
          <form className="dialog" onSubmit={handleCreateOntology}>
            <h2>新建本体</h2>
            <div className="form-group">
              <label className="form-label" htmlFor="onto-display-name">
                显示名称 *
              </label>
              <TextInput
                id="onto-display-name"
                value={ontoDisplayName}
                onChange={(v) => handleOntoDisplayNameChange(v)}
                required
                placeholder="如：我的业务"
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="onto-api-name">
                API 名称（{OBJECT_TYPE_API_NAME_PATTERN.toString()}）
              </label>
              <div className="flex gap-1.5">
                <TextInput
                  id="onto-api-name"
                  inputClassName={cn(
                    'form-input flex-1 font-mono text-xs',
                    ontoApiName && !isValidApiName(ontoApiName, true) && 'border-error',
                  )}
                  value={ontoApiLoading && !ontoUserOverrodeApi ? '' : ontoApiName}
                  onChange={(v) => handleOntoApiNameEdit(v)}
                  disabled={ontoApiLoading && !ontoUserOverrodeApi}
                  placeholder={ontoApiLoading ? 'AI 生成中…' : 'MyBusiness'}
                />
                <button
                  type="button"
                  className="btn btn-xs whitespace-nowrap"
                  onClick={handleSuggestOntoApiName}
                  disabled={ontoApiLoading || !ontoDisplayName.trim()}
                  title="用 AI 从显示名称重新推导（覆盖当前值）"
                >
                  {ontoApiLoading ? '⏳' : '✨ AI'}
                </button>
              </div>
              <span className="form-hint">
                输入显示名称后 AI 自动生成（PascalCase），可手动修改
              </span>
              {ontoApiName && !isValidApiName(ontoApiName, true) && (
                <span className="mt-1 block text-[11px] text-error">
                  API 名称须以大写字母开头，仅含字母数字
                </span>
              )}
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="onto-desc">
                描述
              </label>
              <TextAreaInput
                id="onto-desc"
                name="description"
                rows={3}
                placeholder="描述业务领域，供 AI 语义理解（如：航空运营领域，覆盖航班/飞机/机组等实体）"
              />
            </div>
            <div className="dialog-actions">
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setShowCreateOntology(false);
                  resetOntoForm();
                }}
                disabled={createOntologyAction.loading}
              >
                取消
              </button>
              <button
                type="submit"
                className={cn('btn btn-primary', createOntologyAction.loading && 'is-loading')}
                disabled={createOntologyAction.loading}
              >
                {createOntologyAction.loading && (
                  <span className="btn-spinner" aria-hidden="true" />
                )}
                {createOntologyAction.loading ? '创建中…' : '创建'}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {confirmDelete && (
        <ConfirmDialog
          title={
            confirmDelete.type === '导入确认'
              ? `AI 批量导入确认`
              : `删除${confirmDelete.type} "${confirmDelete.name}"`
          }
          message={
            confirmDelete.type === '导入确认'
              ? '以下对象将被创建，已存在的对象将跳过：'
              : '此操作不可撤销。'
          }
          details={confirmDelete.details}
          requireName={confirmDelete.type === '导入确认' ? undefined : confirmDelete.name}
          confirmText={confirmDelete.type === '导入确认' ? '确认导入' : undefined}
          onConfirm={async () => {
            try {
              await confirmDelete.action();
              setConfirmDelete(null);
            } catch (e) {
              setToast(`操作失败：${e instanceof Error ? e.message : String(e)}`, 'error');
              // 保持对话框打开，让用户看到错误原因而非关掉装无事
            }
          }}
          onCancel={() => setConfirmDelete(null)}
        />
      )}

      <ToastView toast={toast} onDismiss={dismiss} />
    </div>
  );
}
