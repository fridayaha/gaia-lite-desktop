/**
 * 流式 markdown 渲染前把未闭合的代码围栏临时补上 ``` 闭合，避免渲染器把后续文本全部吞进代码块。
 * 反引号数 /3 计围栏数，奇数说明有未闭合围栏。
 */
export function closeUnclosedFences(markdown: string): string {
  const fenceCount = Math.floor(markdown.split('').filter(c => c === '`').length / 3)
  return fenceCount % 2 === 1 ? `${markdown}\n\`\`\`` : markdown
}
