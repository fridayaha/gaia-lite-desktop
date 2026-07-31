const CAPABILITY_CONFIG: Record<string, { icon: string; label: string }> = {
  explore: { icon: '📋', label: '浏览 Schema' },
  batch_sync: { icon: '🔄', label: '新建同步' },
  cdc: { icon: '⚡', label: 'CDC 同步' },
  file_sync: { icon: '📁', label: '文件同步' },
  streaming_sync: { icon: '📨', label: '流式接入' },
  virtual_table: { icon: '👻', label: '虚拟表' },
};

interface CapabilityBarProps {
  capabilities: string[];
  onExplore?: () => void;
  onCreateSync?: () => void;
  onCreateCdc?: () => void;
  onCreateFileSync?: () => void;
  onCreateStream?: () => void;
  onCreateVirtualTable?: () => void;
}

const HANDLER_MAP: Record<string, string> = {
  explore: 'onExplore',
  batch_sync: 'onCreateSync',
  cdc: 'onCreateCdc',
  file_sync: 'onCreateFileSync',
  streaming_sync: 'onCreateStream',
  virtual_table: 'onCreateVirtualTable',
};

export function CapabilityBar({ capabilities, ...handlers }: CapabilityBarProps) {
  if (!capabilities || capabilities.length === 0) return null;

  return (
    <div className="capability-bar">
      {capabilities.map((cap) => {
        const config = CAPABILITY_CONFIG[cap];
        if (!config) return null;
        const handlerKey = HANDLER_MAP[cap] as keyof typeof handlers;
        const handler = handlers[handlerKey] as (() => void) | undefined;
        return (
          <button key={cap} className="capability-btn" onClick={handler} disabled={!handler}>
            {config.icon} {config.label}
          </button>
        );
      })}
    </div>
  );
}
