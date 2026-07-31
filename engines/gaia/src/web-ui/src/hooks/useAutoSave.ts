/**
 * useAutoSave — 自动保存 hook（design.md §14.3 F-10）。
 *
 * - isDirty 变化后 debounce 2s → 触发 save
 * - 页面关闭时 sendBeacon 兜底
 * - 第一次保存时创建管道，后续保存创建新版本
 */
import { useCallback, useEffect, useRef } from 'react';
import { createPipeline, savePipelineVersion } from '../api/client';
import type { PipelineIR, PipelineResponse } from '../types/pipeline';

interface AutoSaveOptions {
  /** 当前管道对象（null=未创建）。 */
  pipeline: PipelineResponse | null;
  /** serialized IR。 */
  serializeIR: () => PipelineIR;
  /** 是否脏（有未保存改动）。 */
  isDirty: boolean;
  /** 更新保存后的管道对象。 */
  onPipelineSaved: (p: PipelineResponse) => void;
  /** 自动保存已触发/完成后的额外回调。 */
  onSaved?: () => void;
  /** 是否启用（editing 模式下）。 */
  enabled?: boolean;
  /** debounce 延迟（毫秒）。 */
  delay?: number;
  /**
   * 外部手动保存进行中的 ref（同步可读）。
   * 当 current=true 时，doSave 立即跳过，避免和手动保存产生双 PATCH 竞态。
   * 用 ref 而非 state：state 更新要等 React 渲染才生效，无法阻止已到期的 timer。
   */
  manualSavingRef?: React.MutableRefObject<boolean>;
}

export function useAutoSave({
  pipeline,
  serializeIR,
  isDirty,
  onPipelineSaved,
  onSaved,
  enabled = true,
  delay = 2000,
  manualSavingRef,
}: AutoSaveOptions) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savingRef = useRef(false);
  const lastSavedIRRef = useRef<string>('');

  const doSave = useCallback(async () => {
    // 双重锁：自身正在保存 OR 外部手动保存进行中 → 跳过
    // 这是修复双 PATCH 竞态的核心：ref 是同步可读的，不等 React 渲染
    if (savingRef.current) return;
    if (manualSavingRef?.current) return;
    savingRef.current = true;
    try {
      const ir = serializeIR();
      const irJson = JSON.stringify(ir);
      // 跳过与上一次保存相同的 IR（避免 isDirty 循环导致重复 PATCH）
      if (irJson === lastSavedIRRef.current) {
        savingRef.current = false;
        return;
      }
      lastSavedIRRef.current = irJson;
      if (pipeline) {
        const version = await savePipelineVersion(pipeline.api_name, ir, '自动保存');
        onPipelineSaved({ ...pipeline, current_version_number: version.version_number });
      } else {
        // 新管道：从 Sink 节点的 config.extra.dataset 取输出数据集名
        const sinkNode = ir.nodes.find((n) => n.type === 'Sink');
        const sinkDs =
          (sinkNode?.config.extra?.dataset as string | undefined) ?? 'default_dataset';
        const p = await createPipeline({
          api_name: `pipeline_${Date.now().toString(36)}`,
          display_name: '新管道',
          sink_dataset_api_name: sinkDs,
          graph: ir,
        });
        onPipelineSaved(p);
      }
      onSaved?.();
    } catch (err) {
      console.error('自动保存失败', err);
    } finally {
      savingRef.current = false;
    }
  }, [pipeline, serializeIR, onSaved]);

  // debounce 自动保存
  useEffect(() => {
    if (!enabled || !isDirty) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(doSave, delay);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [isDirty, enabled, delay, doSave]);

  // beforeunload 兜底：提示用户有未保存改动（sendBeacon 无法发 PATCH/POST body 给 REST API，
  // 且 auto-save 端点不存在；debounce 2s 已覆盖大部分场景，这里只做离开提示）
  useEffect(() => {
    if (!enabled) return;
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isDirty, enabled]);
}
