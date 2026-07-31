import { useEffect, useState } from 'react';
import { createLinkType, listObjectTypeSummaries } from '../api/client';
import { formatError } from '../lib/formatError';
import { useToast } from '../hooks/useToast';
import { useAsyncAction } from '../hooks/useAsyncAction';
import { Modal } from './Modal';
import { TextInput } from './ui/TextField';
import { Select, SelectOption } from './ui/Select';
import type { ObjectTypeSummary, Cardinality } from '../types';

interface CreateLinkDialogProps {
  open: boolean;
  ontologyName: string;
  /** The source object type (the relation originates from here). */
  sourceObjectType: ObjectTypeSummary;
  onClose: () => void;
  /** Called after a successful create so the parent can refresh. */
  onCreated?: () => void;
}

/**
 * Create an object-to-object relation (LinkType).
 *
 * This is the entry point that moved out of the CreateObjectWizard (the
 * wizard was trimmed to 3 steps — dataset → properties → review). Relations
 * need a target object that already exists, so they're configured from the
 * object detail panel after the object is created, mirroring how Palantir
 * Foundry manages link types separately from object-type creation.
 *
 * Backed by `POST /ontologies/{ontology}/link-types`. api_name is derived
 * server-side from display_name (camelCase); the user only supplies a
 * display name, target object, and cardinality.
 */
export function CreateLinkDialog({
  open,
  ontologyName,
  sourceObjectType,
  onClose,
  onCreated,
}: CreateLinkDialogProps) {
  const { show: showToast } = useToast();
  const action = useAsyncAction();

  const [displayName, setDisplayName] = useState('');
  const [targetId, setTargetId] = useState('');
  const [cardinality, setCardinality] = useState<Cardinality>('ONE');
  const [objectTypes, setObjectTypes] = useState<ObjectTypeSummary[]>([]);
  const [loadingTargets, setLoadingTargets] = useState(false);

  // Load candidate target object types (exclude the source itself — a
  // self-relation is rarely meaningful and the wizard's old UI disallowed it).
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    async function loadTargets() {
      setLoadingTargets(true);
      try {
        const ots = await listObjectTypeSummaries(ontologyName);
        if (cancelled) return;
        setObjectTypes(ots.filter((o) => o.id !== sourceObjectType.id));
      } catch (err) {
        if (!cancelled) showToast('加载对象列表失败：' + formatError(err), 'error');
      } finally {
        if (!cancelled) setLoadingTargets(false);
      }
    }
    void loadTargets();
    return () => {
      cancelled = true;
    };
  }, [open, ontologyName, sourceObjectType.id, showToast]);

  const reset = () => {
    setDisplayName('');
    setTargetId('');
    setCardinality('ONE');
  };

  const canSubmit = displayName.trim() !== '' && targetId !== '' && !action.loading;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    const result = await action.run(() =>
      createLinkType(ontologyName, {
        display_name: displayName.trim(),
        source_object_type_id: sourceObjectType.id,
        target_object_type_id: targetId,
        cardinality,
        direction: 'OUTGOING',
      }),
    );
    if (!result) {
      showToast(formatError(action.error, '创建关系失败'), 'error');
      return;
    }
    showToast(`关系 "${displayName.trim()}" 创建成功`, 'success');
    reset();
    onCreated?.();
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!action.loading) {
          reset();
          onClose();
        }
      }}
      ariaLabel="添加关系"
    >
      <div className="dialog">
        <h2>添加关系</h2>
        <p className="mb-4 text-[13px] text-text-muted">
          定义「{sourceObjectType.display_name}」与其他对象的关系
        </p>

        <div className="form-group">
          <label className="form-label">显示名称 *</label>
          <TextInput
            inputClassName="form-input"
            value={displayName}
            onChange={setDisplayName}
            placeholder="如：所属客户"
          />
          <span className="form-hint">API name 由后端从显示名推导（camelCase）</span>
        </div>

        <div className="form-group">
          <label className="form-label">指向对象 *</label>
          <Select
            inputClassName="form-select"
            value={targetId}
            onChange={setTargetId}
            placeholder={loadingTargets ? '加载中…' : '-- 选择对象 --'}
            aria-label="指向对象"
          >
            <SelectOption value="" label="-- 选择对象 --" />
            {objectTypes.map((o) => (
              <SelectOption key={o.id} value={o.id} label={o.display_name} />
            ))}
          </Select>
        </div>

        <div className="form-group">
          <label className="form-label">映射类型</label>
          <Select
            inputClassName="form-select"
            value={cardinality}
            onChange={(v) => setCardinality(v as Cardinality)}
            aria-label="映射类型"
          >
            <SelectOption value="ONE" label="一对一 (1:1)" />
            <SelectOption value="MANY" label="多对一 (N:1)" />
          </Select>
        </div>

        <div className="dialog-actions">
          <button
            type="button"
            className="btn"
            onClick={() => {
              reset();
              onClose();
            }}
            disabled={action.loading}
          >
            取消
          </button>
          <button
            type="button"
            className={`btn btn-primary ${action.loading ? 'is-loading' : ''}`}
            onClick={handleSubmit}
            disabled={!canSubmit}
          >
            {action.loading ? '创建中…' : '创建关系'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
