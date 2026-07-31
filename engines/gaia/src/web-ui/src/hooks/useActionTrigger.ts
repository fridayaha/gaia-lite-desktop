/**
 * useActionTrigger — 分析→行动闭环 hook（graph-reasoning-frontend-design-v2.md §1.2）。
 *
 * 列出适用于当前选中对象类型的 Action，预填 rid 触发执行，
 * 执行成功后通过 onApplied 回调让上层 read-your-writes 刷新节点属性。
 *
 * 复用现有 ExecuteActionDialog + executeAction API，不重造轮子。
 */
import { useCallback, useEffect, useState } from 'react';
import { listActionTypes } from '../api/client';
import type { ActionTypeRecord, ObjectTypeSummary } from '../types';

export function useActionTrigger(ontology: string, selectedOt: ObjectTypeSummary | null) {
  const [actions, setActions] = useState<ActionTypeRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [execAction, setExecAction] = useState<ActionTypeRecord | null>(null);

  // 加载适用于该对象类型的 Action（affected_object_type_id 匹配）。
  useEffect(() => {
    if (!selectedOt) {
      setActions([]);
      return;
    }
    setLoading(true);
    listActionTypes(ontology)
      .then((all) =>
        setActions(all.filter((a) => a.affected_object_type_id === selectedOt.id)),
      )
      .catch(() => setActions([]))
      .finally(() => setLoading(false));
  }, [ontology, selectedOt]);

  /** 可执行操作：status=ACTIVE 且目标非 VIRTUAL（对齐 ObjectDetailPanel 规则）。 */
  const applicableActions = actions.filter(
    (a) => a.status === 'ACTIVE' && selectedOt?.storage_type !== 'VIRTUAL',
  );

  /** 触发某个 Action（打开 ExecuteActionDialog）。 */
  const trigger = useCallback((action: ActionTypeRecord) => {
    setExecAction(action);
  }, []);

  /** 关闭对话框。 */
  const close = useCallback(() => {
    setExecAction(null);
  }, []);

  return {
    applicableActions,
    loading,
    execAction,
    trigger,
    close,
  };
}
