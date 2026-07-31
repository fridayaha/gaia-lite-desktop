/**
 * NodeConfigModal — 节点配置弹窗。
 *
 * 双击画布节点打开，内嵌 NodeConfigPanel。
 *
 * 设计依据（参考 n8n Node Detail View 范式 + ADR-013 React Aria Modal）：
 * - 配置表单较重（Filter 多条件 / Join 多键 / Aggregate 分组+聚合），右侧 320px 抽屉放不下，
 *   且会与 Schema/历史/JSON 抢位置 → 改为居中弹窗，给配置更大空间
 * - 画布右侧始终保留辅助面板（Schema/历史/JSON），配置走弹窗，互不干扰
 * - 弹窗宽度自适应配置复杂度（min 560 / max 720），高于右侧抽屉的 320px
 * - React Aria Modal 提供焦点陷阱 + ESC 关闭 + 遮罩点击关闭 + 焦点回归
 */
import { Modal } from '../Modal';
import { NodeConfigPanel } from './NodeConfigPanel';
import type { IRNode, IREdge, Schema } from '../../types/pipeline';
import type { DatasetGovernance } from '../../types';

type DatasetOption = Pick<DatasetGovernance, 'api_name' | 'display_name'>;

interface NodeConfigModalProps {
  /** 要编辑的节点；为 null 时弹窗关闭。 */
  node: IRNode | null;
  datasets: DatasetOption[];
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (nodeId: string, updates: Partial<IRNode>) => void;
  onClose: () => void;
}

export function NodeConfigModal({
  node,
  datasets,
  nodeSchemas,
  irEdges,
  onChange,
  onClose,
}: NodeConfigModalProps) {
  return (
    <Modal
      open={node !== null}
      onClose={onClose}
      ariaLabel={`编辑节点配置：${node?.label ?? ''}`}
      panelClassName="min-w-[560px] max-w-[720px] w-[720px] p-0"
    >
      {node && (
        <div className="flex max-h-[80vh] flex-col">
          <NodeConfigPanel
            node={node}
            datasets={datasets}
            nodeSchemas={nodeSchemas}
            irEdges={irEdges}
            onChange={onChange}
            onClose={onClose}
          />
        </div>
      )}
    </Modal>
  );
}
