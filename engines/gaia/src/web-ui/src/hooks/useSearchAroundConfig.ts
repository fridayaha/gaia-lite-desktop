/**
 * useSearchAroundConfig — 多步 Search Around 配置 hook（design-v2 §1.1）。
 *
 * 链式构建嵌套 ObjectSet IR：static(起始集) → searchAround(link1) → searchAround(link2)...
 * 每步可配关系类型/方向/跳数/属性过滤，支持预览命中数量（防星爆）。
 */
import { useCallback, useState } from 'react';
import type { GraphFilter, ObjectSetIR } from '../types';
import { traverseLink } from '../api/graph';

export interface SearchAroundStep {
  id: string;
  linkType: string;
  direction: 'forward' | 'reverse';
  maxHops: number;
  filters: GraphFilter[];
  previewCount?: number;
  previewing?: boolean;
}

export interface SearchAroundConfig {
  startVids: string[];
  steps: SearchAroundStep[];
}

let stepIdCounter = 0;
function nextStepId(): string {
  stepIdCounter += 1;
  return `step-${stepIdCounter}`;
}

export function useSearchAroundConfig() {
  const [startVids, setStartVids] = useState<string[]>([]);
  const [steps, setSteps] = useState<SearchAroundStep[]>([]);

  /** 设置起始对象集（从画布选中节点传入）。 */
  const setStart = useCallback((rids: string[]) => {
    setStartVids(rids);
  }, []);

  /** 添加一个新跳（链式，起始集为上一跳结果）。 */
  const addStep = useCallback(() => {
    setSteps((prev) => [
      ...prev,
      {
        id: nextStepId(),
        linkType: '',
        direction: 'forward',
        maxHops: 1,
        filters: [],
      },
    ]);
  }, []);

  /** 更新某跳配置。 */
  const updateStep = useCallback((id: string, patch: Partial<SearchAroundStep>) => {
    setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  }, []);

  /** 移除某跳（及其后所有跳，因为链断了）。 */
  const removeStep = useCallback((id: string) => {
    setSteps((prev) => {
      const idx = prev.findIndex((s) => s.id === id);
      if (idx < 0) return prev;
      return prev.slice(0, idx); // 移除该跳及之后
    });
  }, []);

  /** 预览某跳命中数量（调 traverse_link 单跳 count，防星爆）。 */
  const previewStep = useCallback(
    async (ontology: string, id: string) => {
      const step = steps.find((s) => s.id === id);
      if (!step || !step.linkType || startVids.length === 0) return;
      setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, previewing: true } : s)));
      try {
        // 预览：从起始集（或上一跳结果集，简化为首跳用 startVids）单跳遍历
        const result = await traverseLink(ontology, {
          link_type: step.linkType,
          source_keys: startVids,
          direction: step.direction,
          include_source_mapping: false,
        });
        const count = result.target_objects?.length ?? 0;
        setSteps((prev) =>
          prev.map((s) => (s.id === id ? { ...s, previewCount: count, previewing: false } : s)),
        );
      } catch {
        setSteps((prev) =>
          prev.map((s) => (s.id === id ? { ...s, previewing: false, previewCount: undefined } : s)),
        );
      }
    },
    [steps, startVids],
  );

  /** 构建完整 ObjectSet IR（嵌套 searchAround）。 */
  const buildIR = useCallback((): ObjectSetIR | null => {
    if (startVids.length === 0 || steps.length === 0) return null;
    // 过滤掉未配 linkType 的跳
    const validSteps = steps.filter((s) => s.linkType);
    if (validSteps.length === 0) return null;
    let ir: ObjectSetIR = { type: 'static', objects: startVids };
    for (const step of validSteps) {
      ir = {
        type: 'searchAround',
        object_set: ir,
        link: step.linkType,
        direction: step.direction === 'forward' ? 'out' : 'in',
        hops: [1, step.maxHops],
        filters: step.filters.length > 0 ? step.filters : undefined,
      };
    }
    return ir;
  }, [startVids, steps]);

  /** 重置配置。 */
  const reset = useCallback(() => {
    setStartVids([]);
    setSteps([]);
  }, []);

  return {
    startVids,
    steps,
    setStart,
    addStep,
    updateStep,
    removeStep,
    previewStep,
    buildIR,
    reset,
  };
}
