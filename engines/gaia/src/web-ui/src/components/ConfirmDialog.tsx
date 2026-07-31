import { useState } from 'react';
import { cn } from '../lib/cn';
import { Modal } from './Modal';
import { TextInput } from './ui/TextField';

export interface ImpactItem {
  resource_type: string;
  api_name: string;
  effect: string;
}

const RESOURCE_LABELS: Record<string, string> = {
  sync_task: '同步任务',
  object_type: '对象类型',
  dataset: '数据集',
  link_type: '关系类型',
  datasource: '数据源',
};

const EFFECT_LABELS: Record<string, string> = {
  CASCADE_DELETE: '级联删除',
  SET_NULL: '清空引用',
  ORPHANED: '成为孤立引用',
};

/** Severity drives the confirmation UX:
 *   LOW → simple "sure?" dialog
 *   MEDIUM → list affected resources
 *   HIGH → type name to confirm
 * Auto-selected: HIGH if requireName is set, else MEDIUM if impacts/details exist, else LOW.
 */
function inferSeverity(
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | undefined,
  impacts: ImpactItem[] | undefined,
  details: string[] | undefined,
  requireName: string | undefined,
): 'LOW' | 'MEDIUM' | 'HIGH' {
  if (severity) return severity;
  if (requireName) return 'HIGH';
  if ((impacts && impacts.length > 0) || (details && details.length > 0)) return 'MEDIUM';
  return 'LOW';
}

interface ConfirmDialogProps {
  /** 控制显隐。未传时（legacy 行为）默认 open=true。 */
  open?: boolean;
  severity?: 'LOW' | 'MEDIUM' | 'HIGH';
  title: string;
  message: string;
  /** Impact items (new API) — structured affected resources. */
  impacts?: ImpactItem[];
  /** [legacy] details — simple string list, converted to impacts internally. */
  details?: string[];
  /** For HIGH severity, the user must type this exact string to confirm. */
  requireName?: string;
  confirmText?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * 确认对话框（ADR-013 Phase 3）。基于统一 Modal 底座 + AlertDialog 语义：
 * role="alertdialog" 让屏幕阅读器立即播报，焦点陷阱 / ESC / 遮罩点击由
 * React Aria ModalOverlay 统一处理。IME 安全的确认输入用 TextInput。
 */
export function ConfirmDialog({
  open = true,
  severity: explicitSeverity,
  title,
  message,
  impacts,
  details,
  requireName,
  confirmText,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const severity = inferSeverity(explicitSeverity, impacts, details, requireName);
  const [input, setInput] = useState('');

  const canConfirm = severity === 'HIGH' && requireName ? input === requireName : true;

  // Merge legacy details into structured impacts for display
  const displayImpacts: ImpactItem[] =
    impacts ||
    details?.map((d) => ({
      resource_type: 'unknown',
      api_name: d,
      effect: 'CASCADE_DELETE',
    })) ||
    [];

  return (
    <Modal open={open} onClose={onCancel} ariaLabel={title} closeOnOverlay>
      <div role="alertdialog" aria-modal="true" aria-label={title}>
        <h2>
          {severity === 'HIGH' && '⚠️ '}
          {severity === 'MEDIUM' && '⚡ '}
          {title}
        </h2>
        <p className="mb-3 text-sm text-text-secondary">{message}</p>

        {/* MEDIUM / HIGH: list affected resources */}
        {displayImpacts.length > 0 && (
          <div className="mb-3 rounded-md bg-bg p-3 text-[13px]">
            <strong className="text-text-secondary">
              {severity === 'HIGH' ? '⛔ 将影响以下资源:' : '将同时影响:'}
            </strong>
            <ul className="mt-2 flex list-none flex-col gap-1.5 pl-4">
              {displayImpacts.map((item, i) => (
                <li key={i} className="flex items-center gap-2 text-xs">
                  <span
                    className={cn(
                      item.effect === 'ORPHANED'
                        ? 'text-error'
                        : item.effect === 'CASCADE_DELETE'
                          ? 'text-warning'
                          : 'text-text-muted',
                    )}
                  >
                    {EFFECT_LABELS[item.effect] || item.effect}
                  </span>
                  <span className="text-text-muted">
                    [{RESOURCE_LABELS[item.resource_type] || item.resource_type}]
                  </span>
                  <code className="font-mono text-[11px] text-text">{item.api_name}</code>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* HIGH: type to confirm */}
        {severity === 'HIGH' && requireName && (
          <div className="form-group">
            <label className="form-label" htmlFor="confirm-name-input">
              输入 <strong className="text-error">{requireName}</strong> 确认删除:
            </label>
            <TextInput
              id="confirm-name-input"
              inputClassName="form-input form-input-mono"
              value={input}
              onChange={setInput}
              placeholder={requireName}
            />
          </div>
        )}

        <div className="dialog-actions">
          <button className="btn" onClick={onCancel}>
            取消
          </button>
          <button
            className={severity === 'HIGH' ? 'btn btn-danger' : 'btn btn-primary'}
            onClick={onConfirm}
            disabled={!canConfirm}
          >
            {confirmText || (severity === 'HIGH' ? '确认删除' : '确定')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
