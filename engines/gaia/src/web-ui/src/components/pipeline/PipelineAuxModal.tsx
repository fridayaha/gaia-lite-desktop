/**
 * PipelineAuxModal — 管道辅助视图弹窗（Schema / 执行历史 / JSON）。
 *
 * 重构背景：原右侧常驻抽屉移除以最大化画布留白（用户需求），辅助视图改为
 * 由工具栏按钮按需弹出。本组件复用 PipelineAuxPanel 的三 tab 内容，
 * 包在 React Aria Modal 中（焦点陷阱 + ESC 关闭 + 遮罩关闭）。
 */
import { Modal } from '../Modal';
import { PipelineAuxPanel, type AuxTab } from './PipelineRightPanel';
import type {
  IRNode,
  IREdge,
  BuildResponse,
  ContractViolation,
} from '../../types/pipeline';

interface PipelineAuxModalProps {
  open: boolean;
  activeTab: AuxTab;
  onTabChange: (tab: AuxTab) => void;
  onClose: () => void;
  selectedNode: IRNode | null;
  irNodes: IRNode[];
  irEdges: IREdge[];
  builds: BuildResponse[];
  validationErrors: ContractViolation[];
  validationValid: boolean;
  pipelineStatus: string;
  jsonString: string;
  onJsonChange: (json: string) => void;
  onApplyJson: () => void;
}

export function PipelineAuxModal({
  open,
  activeTab,
  onTabChange,
  onClose,
  selectedNode,
  irNodes,
  irEdges,
  builds,
  validationErrors,
  validationValid,
  pipelineStatus,
  jsonString,
  onJsonChange,
  onApplyJson,
}: PipelineAuxModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      ariaLabel="管道辅助视图"
      panelClassName="min-w-[560px] max-w-[640px] w-[640px] p-0"
    >
      <div className="flex max-h-[80vh] flex-col">
        {/* 关闭按钮 */}
        <button
          onClick={onClose}
          className="absolute right-3 top-3 z-10 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          title="关闭"
        >
          ✕
        </button>
        <PipelineAuxPanel
          activeTab={activeTab}
          onTabChange={onTabChange}
          selectedNode={selectedNode}
          irNodes={irNodes}
          irEdges={irEdges}
          builds={builds}
          validationErrors={validationErrors}
          validationValid={validationValid}
          pipelineStatus={pipelineStatus}
          jsonString={jsonString}
          onJsonChange={onJsonChange}
          onApplyJson={onApplyJson}
        />
      </div>
    </Modal>
  );
}
