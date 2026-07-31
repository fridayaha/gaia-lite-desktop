/**
 * 副作用配置子表单（ADR Action Mutation Mapping）。
 *
 * 按 effect.type 切换 config 子字段。受控组件。本期覆盖：
 *  - write_back：目标对象类型 + op（upsert/insert）—— Gaia 特有源表回写
 *  - webhook：URL + 方法 + 触发时机
 *  - notification：收件人 + 标题 + 正文 + 渠道（对标 Foundry，简化）
 *  - sub_action / kafka_topic：透传 config（高级，文本框）
 */
import { cn } from '../lib/cn';
import { TextInput, TextAreaInput } from './ui/TextField';
import { Select, SelectOption } from './ui/Select';
import type { ActionEffectConfig } from '../types';

export interface EffectConfigFormProps {
  effect: ActionEffectConfig;
  onChange: (effect: ActionEffectConfig) => void;
  /** 可选的目标对象类型列表（write_back 用）。 */
  objectTypeApiNames: string[];
}

export function EffectConfigForm({ effect, onChange, objectTypeApiNames }: EffectConfigFormProps) {
  const config = effect.config ?? {};

  function setConfig(patch: Record<string, unknown>) {
    onChange({ ...effect, config: { ...config, ...patch } });
  }

  return (
    <div className="flex flex-col gap-2">
      {/* 触发时机 + 条件 —— 所有副作用通用 */}
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-text-secondary">触发时机</span>
          <Select
            inputClassName="form-select text-xs"
            value={effect.trigger}
            onChange={(v) =>
              onChange({
                ...effect,
                trigger: v as ActionEffectConfig['trigger'],
              })
            }
            aria-label="触发时机"
          >
            <SelectOption value="AFTER_ONTOLOGY_CHANGE" label="变更后" />
            <SelectOption value="BEFORE_ONTOLOGY_CHANGE" label="变更前" />
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-text-secondary">条件（可选）</span>
          <TextInput
            inputClassName="form-input font-mono text-xs"
            value={effect.condition ?? ''}
            onChange={(v) => onChange({ ...effect, condition: v || null })}
            placeholder="如 is_urgent == true"
          />
        </label>
      </div>

      {effect.type === 'write_back' && (
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">目标对象类型</span>
            <Select
              inputClassName="form-select text-xs"
              value={(config.target_object_type as string) ?? ''}
              onChange={(v) => setConfig({ target_object_type: v })}
              placeholder="— 选择 —"
              aria-label="目标对象类型"
            >
              <SelectOption value="" label="— 选择 —" />
              {objectTypeApiNames.map((n) => (
                <SelectOption key={n} value={n} label={n} />
              ))}
            </Select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">操作</span>
            <Select
              inputClassName="form-select text-xs"
              value={(config.op as string) ?? 'upsert'}
              onChange={(v) => setConfig({ op: v })}
              aria-label="操作"
            >
              <SelectOption value="upsert" label="upsert（修改回写）" />
              <SelectOption value="insert" label="insert（新建回写）" />
            </Select>
          </label>
        </div>
      )}

      {effect.type === 'webhook' && (
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">URL</span>
            <TextInput
              inputClassName="form-input text-xs"
              value={(config.url as string) ?? ''}
              onChange={(v) => setConfig({ url: v })}
              placeholder="https://..."
            />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-text-secondary">方法</span>
              <Select
                inputClassName="form-select text-xs"
                value={(config.method as string) ?? 'POST'}
                onChange={(v) => setConfig({ method: v })}
                aria-label="方法"
              >
                <SelectOption value="POST" label="POST" />
                <SelectOption value="PUT" label="PUT" />
                <SelectOption value="PATCH" label="PATCH" />
              </Select>
            </label>
          </div>
        </div>
      )}

      {effect.type === 'notification' && (
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">收件人（用户 ID，逗号分隔）</span>
            <TextInput
              inputClassName="form-input text-xs"
              value={(config.recipients as string) ?? ''}
              onChange={(v) => setConfig({ recipients: v })}
              placeholder="user1, user2"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">标题</span>
            <TextInput
              inputClassName="form-input text-xs"
              value={(config.title as string) ?? ''}
              onChange={(v) => setConfig({ title: v })}
              placeholder="支持 {{{param}}} 引用参数"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">正文</span>
            <TextAreaInput
              inputClassName={cn('form-input text-xs', 'min-h-[60px] resize-y')}
              value={(config.content_template as string) ?? ''}
              onChange={(v) => setConfig({ content_template: v })}
              placeholder="支持 {{{param}}} 引用参数"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">渠道</span>
            <Select
              inputClassName="form-select text-xs"
              value={(config.channel as string) ?? 'IN_APP'}
              onChange={(v) => setConfig({ channel: v })}
              aria-label="渠道"
            >
              <SelectOption value="IN_APP" label="站内" />
              <SelectOption value="EMAIL" label="邮件" />
              <SelectOption value="IN_APP,EMAIL" label="站内 + 邮件" />
            </Select>
          </label>
        </div>
      )}

      {(effect.type === 'sub_action' || effect.type === 'kafka_topic') && (
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-text-secondary">配置 JSON</span>
          <TextAreaInput
            inputClassName="form-input min-h-[60px] resize-y font-mono text-xs"
            value={JSON.stringify(config, null, 2)}
            onChange={(v) => {
              try {
                onChange({ ...effect, config: JSON.parse(v) });
              } catch {
                /* 编辑中暂不报错，保存前校验 */
              }
            }}
          />
        </label>
      )}
    </div>
  );
}
