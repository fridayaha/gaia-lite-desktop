import { useCallback, useRef, useState } from 'react';
import pLimit from 'p-limit';
import {
  listSyncTasks,
  startSyncTask,
  stopSyncTask,
  deleteSyncTask,
  createSyncTask,
  refreshSyncTask,
  refreshAllSyncTasks,
  registerDataset,
  exploreDataSource,
  describeTable,
  sampleData as sampleDataApi,
  ApiError,
} from '../api/client';
import type { SyncTask, TableInfo } from '../types';
import type { SyncConfig } from '../components/SyncModeSelector';

type ExploreResult = Awaited<ReturnType<typeof exploreDataSource>>;

/** 结构化探索错误：区分「数据源不可达」「查询引擎不可用」「其他」。 */
export interface ExploreError {
  /** 后端稳定错误码：DATASOURCE_UNREACHABLE / TRINO_UNAVAILABLE / 其他或 undefined */
  code?: string;
  /** 面向用户的可读消息（后端 detail 首句） */
  message: string;
}

/**
 * 并发限流：浏览器对同域 HTTP/1.1 约 6 个并发连接。限 4 留余量给
 * 其他请求（探索/样本数据/权限等）。避免一次打开几十个任务详情
 * 时打爆连接池导致页面卡死。
 */
const REFRESH_CONCURRENCY = 4;
const refreshLimit = pLimit(REFRESH_CONCURRENCY);

interface UseDataSourceReturn {
  syncTasks: SyncTask[];
  explore: ExploreResult | null;
  exploreLoading: boolean;
  exploreError: ExploreError | null;
  columnDetails: Record<string, TableInfo>;
  columnLoading: Record<string, boolean>;
  sample: { rows: Record<string, unknown>[] } | null;
  sampleLoading: boolean;
  sampleError: string | null;
  loadSyncTasks: (dsApiName: string) => Promise<void>;
  reloadSyncTasks: () => Promise<void>;
  fetchSyncTasksFor: (dsApiName: string) => Promise<void>;
  startSync: (task: SyncTask) => Promise<void>;
  stopSync: (task: SyncTask) => Promise<void>;
  removeSync: (task: SyncTask) => Promise<void>;
  createSyncTask: (
    dsApiName: string,
    tableName: string,
    config: SyncConfig,
    displayName?: string,
  ) => Promise<void>;
  fetchExplore: (dsApiName: string) => Promise<void>;
  fetchColumns: (dsApiName: string, database: string, tableName: string) => Promise<void>;
  fetchSample: (dsApiName: string, database: string, tableName: string) => Promise<void>;
}

/**
 * 数据源操作 hook —— 收口 DataConnections 与 DataSourceDetail 的重复逻辑。
 * 每个 hook 实例绑定一个数据源上下文（syncTasks 等按 dsApiName 读写）。
 */
export function useDataSource(initialSyncTasks: SyncTask[] = []): UseDataSourceReturn {
  const [syncTasks, setSyncTasks] = useState<SyncTask[]>(initialSyncTasks);
  const [explore, setExplore] = useState<ExploreResult | null>(null);
  const [exploreLoading, setExploreLoading] = useState(false);
  const [exploreError, setExploreError] = useState<ExploreError | null>(null);
  const [columnDetails, setColumnDetails] = useState<Record<string, TableInfo>>({});
  const [columnLoading, setColumnLoading] = useState<Record<string, boolean>>({});
  const [sample, setSample] = useState<{
    rows: Record<string, unknown>[];
  } | null>(null);
  const [sampleLoading, setSampleLoading] = useState(false);
  const [sampleError, setSampleError] = useState<string | null>(null);

  // 轻量加载：只调 listSyncTasks（纯 PG 查询，零 SeaTunnel 调用），
  // 用于 mount 时填充 syncTasks 让 tab 计数正确。不触发 reconcile。
  const loadSyncTasks = useCallback(async (dsApiName: string) => {
    try {
      const tasks = await listSyncTasks(dsApiName);
      setSyncTasks(tasks);
    } catch {
      setSyncTasks([]);
    }
  }, []);

  const reloadSyncTasks = useCallback(async () => {
    // 调用方需传 dsApiName，见 fetchSyncTasksFor
  }, []);

  // 防重入：同一数据源的 fetchSyncTasksFor 在途时不重复触发（应对
  // React StrictMode dev 双 mount 及快速重复点击）。用 ref 持有当前
  // 在途的 Promise，新调用复用而非重发。
  const inflightRef = useRef<Promise<void> | null>(null);

  const fetchSyncTasksFor = useCallback(async (dsApiName: string) => {
    // 已有在途请求 → 复用，避免 StrictMode 双调用 / 快速重复点击重发。
    if (inflightRef.current) return inflightRef.current;

    const run = async () => {
      try {
        // P1：先拿 PG 存的 status 立即渲染——终态即真相，不需等外部系统。
        // 这一步让列表瞬间出现，用户不用盯着空白等 SeaTunnel 响应。
        const tasks = await listSyncTasks(dsApiName);
        setSyncTasks(tasks);

        // P4：用后端批量端点一次 reconcile 全部任务（2 次 SeaTunnel 调用
        // 替代 N×2 次）。后台更新 PG 后用返回值刷新列表。
        // 失败则保留 PG 原值（best-effort，不阻塞渲染）。
        try {
          const refreshed = await refreshAllSyncTasks(dsApiName);
          setSyncTasks(refreshed);
          return;
        } catch {
          // 批量端点不可用（旧后端）或 SeaTunnel 宕 — 退回单条限流路径。
        }

        // P1+P2 退退路径：只对非终态（RUNNING）任务限流 refresh。
        // 终态（FINISHED/STOPPED/CANCELED/FAILED）写入后即真相，无后台
        // 机制让其复活，无需 reconcile。
        const nonTerminal = tasks.filter((t) => t.status === 'RUNNING');
        if (nonTerminal.length === 0) return;
        const refreshed = await Promise.all(
          nonTerminal.map((t) =>
            refreshLimit(() =>
              refreshSyncTask(t.api_name).catch(() => t),
            ),
          ),
        );
        setSyncTasks((prev) => {
          const byName = new Map(refreshed.map((r) => [r.api_name, r]));
          return prev.map((t) => byName.get(t.api_name) ?? t);
        });
      } catch {
        setSyncTasks([]);
      }
    };

    const p = run().finally(() => {
      inflightRef.current = null;
    });
    inflightRef.current = p;
    return p;
  }, []);

  const startSync = useCallback(async (task: SyncTask) => {
    // Use the returned task (status=RUNNING) to update the single row
    // in place. We intentionally do NOT call fetchSyncTasksFor here —
    // it requires the datasource *api_name*, but SyncTask only carries
    // data_source_id, so calling it with the id would 404 and wipe the
    // list. The list refreshes its SeaTunnel-truth status on next load
    // via fetchSyncTasksFor's per-task refreshSyncTask calls.
    const updated = await startSyncTask(task.api_name);
    setSyncTasks((prev) => prev.map((t) => (t.api_name === task.api_name ? updated : t)));
    // Poll SeaTunnel for the real terminal state shortly after — a
    // full_snapshot BATCH job often finishes within seconds.
    setTimeout(() => {
      refreshSyncTask(task.api_name)
        .then((r) =>
          setSyncTasks((prev) => prev.map((t) => (t.api_name === task.api_name ? r : t))),
        )
        .catch(() => undefined);
    }, 4000);
  }, []);

  const stopSync = useCallback(async (task: SyncTask) => {
    const updated = await stopSyncTask(task.api_name);
    setSyncTasks((prev) => prev.map((t) => (t.api_name === task.api_name ? updated : t)));
  }, []);

  const removeSync = useCallback(async (task: SyncTask) => {
    await deleteSyncTask(task.api_name);
    setSyncTasks((prev) => prev.filter((t) => t.api_name !== task.api_name));
  }, []);

  const createOneSync = useCallback(
    async (dsApiName: string, tableName: string, cfg: SyncConfig, displayName?: string) => {
      const datasetApiName = cfg.target_dataset || `${tableName}_raw`;
      const task = await createSyncTask(dsApiName, {
        api_name: `${dsApiName}_sync_${tableName}`,
        source_config: {
          table: tableName,
          incremental_column: cfg.incremental_column || undefined,
        },
        target_dataset_api_name: datasetApiName,
        sync_mode: cfg.sync_mode,
        transaction_type: cfg.transaction_type,
      });
      setSyncTasks((prev) => [...prev, task]);
      try {
        await registerDataset({
          api_name: datasetApiName,
          // 优先用源表中文名/注释作数据集显示名，拿不到时退化成表名
          // （不再用 api_name 兜底——那只是个标识符，没有业务语义）。
          display_name: displayName || tableName,
          data_source_api_name: dsApiName,
        });
      } catch {
        /* dataset 已存在则忽略 */
      }
    },
    [],
  );

  const fetchExplore = useCallback(async (dsApiName: string) => {
    setExploreLoading(true);
    setExploreError(null);
    try {
      const r = await exploreDataSource(dsApiName);
      setExplore(r);
    } catch (err) {
      setExplore(null);
      // 从后端结构化错误响应提取 code + 面向用户的消息首句
      let code: string | undefined;
      let message: string;
      if (err instanceof ApiError) {
        code = err.code;
        // detail 可能含一大段 Java 堆栈，只取首句（第一个换行/中文句号前）给用户
        const raw = err.detail || err.message;
        message = raw.split(/[\n。]/)[0].trim() || raw.slice(0, 120);
      } else {
        message = (err as Error).message || '探索失败';
      }
      setExploreError({ code, message });
    } finally {
      setExploreLoading(false);
    }
  }, []);

  const fetchColumns = useCallback(
    async (dsApiName: string, database: string, tableName: string) => {
      if (columnDetails[tableName] || columnLoading[tableName]) return;
      setColumnLoading((p) => ({ ...p, [tableName]: true }));
      try {
        const info = await describeTable(dsApiName, database, tableName);
        setColumnDetails((p) => ({ ...p, [tableName]: info }));
      } catch {
        setColumnDetails((p) => ({
          ...p,
          [tableName]: {
            name: tableName,
            schema: database,
            columns: [],
            row_count_estimate: null,
          } as TableInfo,
        }));
      } finally {
        setColumnLoading((p) => ({ ...p, [tableName]: false }));
      }
    },
    [columnDetails, columnLoading],
  );

  const fetchSample = useCallback(
    async (dsApiName: string, database: string, tableName: string) => {
      setSampleLoading(true);
      setSampleError(null);
      try {
        const rows = await sampleDataApi(dsApiName, database, tableName, 100);
        // 列信息由调用方从 columnDetails（响应式）读取，不在此快照——
        // 避免 fetchColumns/fetchSample 并发竞态导致列丢失。
        setSample({ rows });
      } catch (err) {
        setSampleError((err as Error).message);
        setSample({ rows: [] });
      } finally {
        setSampleLoading(false);
      }
    },
    [columnDetails],
  );

  return {
    syncTasks,
    explore,
    exploreLoading,
    exploreError,
    columnDetails,
    columnLoading,
    sample,
    sampleLoading,
    sampleError,
    loadSyncTasks,
    reloadSyncTasks,
    fetchSyncTasksFor,
    startSync,
    stopSync,
    removeSync,
    createSyncTask: createOneSync,
    fetchExplore,
    fetchColumns,
    fetchSample,
  };
}
