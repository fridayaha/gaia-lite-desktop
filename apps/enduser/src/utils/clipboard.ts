/**
 * 复制文本到剪贴板。门户走 HTTP 部署（非安全上下文）时 navigator.clipboard 不存在，
 * 裸调用会静默失败，这里降级为隐藏 textarea + execCommand('copy')。
 * 返回是否成功，调用方可据此给反馈。
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限被拒/文档失焦等场景继续走降级
    }
  }
  const ta = document.createElement("textarea")
  ta.value = text
  ta.setAttribute("readonly", "")
  ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none"
  document.body.appendChild(ta)
  ta.select()
  ta.setSelectionRange(0, text.length) // iOS Safari 需要显式选区
  let ok = false
  try {
    ok = document.execCommand("copy")
  } catch {
    ok = false
  }
  document.body.removeChild(ta)
  return ok
}
