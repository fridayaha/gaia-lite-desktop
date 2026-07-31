/**
 * 统一错误文案翻译。HCI 依据：尼尔森「清晰的错误提示」+「贴近用户真实世界」——
 * 不直接展示后端堆栈/术语，给出可执行的解决方案。
 */

interface MappedError {
  pattern: RegExp;
  message: string;
}

/** 按顺序匹配，首个命中即返回。 */
const MAPPINGS: MappedError[] = [
  {
    pattern: /failed to fetch|networkerror|err_connection|network is down/i,
    message: '网络连接失败，请检查网络后重试',
  },
  {
    pattern: /401|unauthorized|未授权/i,
    message: '登录已失效，请刷新页面重新登录',
  },
  {
    pattern: /403|forbidden|权限|permission/i,
    message: '没有权限执行此操作，请联系管理员申请权限',
  },
  {
    pattern: /404|not found|不存在/i,
    message: '资源不存在或已被删除',
  },
  {
    // ADR Action Mutation Mapping §4.2: OCC 版本冲突专属文案(放在通用 409 前)
    pattern: /modified by another|OCC_CONFLICT|expected_version|版本冲突|已被他人修改/i,
    message: '对象已被他人修改，请刷新后重试',
  },
  {
    pattern: /409|conflict|唯一约束|already exist|已存在|冲突/i,
    message: '名称已存在或数据冲突，请检查后重试',
  },
  {
    pattern: /422|validation|参数|invalid/i,
    message: '输入参数有误，请检查表单填写',
  },
  {
    pattern: /timeout|超时/i,
    message: '请求超时，请稍后重试',
  },
];

export function formatError(err: unknown, fallback = '操作失败，请稍后重试'): string {
  if (!err) return fallback;
  const raw = err instanceof Error ? err.message : String(err);
  for (const m of MAPPINGS) {
    if (m.pattern.test(raw)) return m.message;
  }
  // 截断过长的后端堆栈
  const firstLine = raw.split('\n')[0].trim();
  if (firstLine.length > 120) return fallback;
  return firstLine || fallback;
}
