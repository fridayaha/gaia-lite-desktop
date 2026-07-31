/**
 * 共享 zxcvbn 配置：
 * 1. 装载 @zxcvbn-ts/language-en + language-common 字典，让前端评分与后端 zxcvbn-python 对齐
 *    （不装字典时，前端会把 password1A! 这种字典词误判为 score=4，与后端 score=1 不一致）
 * 2. 把 zxcvbn-ts 的 warning key（如 "similarToCommon"）和 suggestions key 翻译成中文，
 *    让用户看到具体原因（哪个部分弱），而不是只看到"强度不足"。
 *
 * 用法：`import { zxcvbn } from "@/utils/zxcvbn"` 替代 `import { zxcvbn } from "@zxcvbn-ts/core"`。
 */
import { zxcvbn as _zxcvbn, zxcvbnOptions } from "@zxcvbn-ts/core";
import { dictionary as enDictionary, translations as enTranslations } from "@zxcvbn-ts/language-en";
import { dictionary as commonDictionary, adjacencyGraphs as commonAdjacencyGraphs } from "@zxcvbn-ts/language-common";

// zxcvbn-ts 的 warning key → 中文（与后端 _ZXCVBN_WARNING_CN 对应句子语义保持一致）
const zhTranslations = {
  warnings: {
    straightRow: "键盘直行序列（如 qwerty、12345678）极易被猜中",
    keyPattern: "键盘短模式（如 asdf）易被猜中",
    simpleRepeat: "重复字符（如 aaa）易被猜中",
    extendedRepeat: "重复字符模式（如 abcabcabc）易被猜中",
    sequences: "字符序列（如 abc、6543）易被猜中",
    recentYears: "近期年份易被猜中",
    dates: "日期易被猜中",
    topTen: "该密码是 top-10 最常见密码，攻击者会优先试它",
    topHundred: "该密码是 top-100 常见密码",
    common: "该密码过于常见",
    similarToCommon:
      "密码包含常见弱序列或字典词（如 12345678、qwerty、password），建议打散数字部分或换词",
    wordByItself: "单词本身易被猜中",
    namesByThemselves: "单个人名或姓氏易被猜中",
    commonNames: "常见人名姓氏易被猜中",
    userInputs: "密码不应包含个人或页面相关信息",
    pwned: "该密码已在数据泄露事件中曝光，请立即更换"
  },
  suggestions: {
    l33t: "避免可预测的字符替换（如 @ 替 a）",
    reverseWords: "避免常见词的反转拼写",
    allUppercase: "仅首字母大写帮助不大",
    capitalization: "应多处大写，不只首字母",
    dates: "避免与你相关的日期和年份",
    recentYears: "避免近期年份",
    associatedYears: "避免与你相关的年份",
    sequences: "避免常见字符序列",
    repeated: "避免重复单词和字符",
    longerKeyboardPattern: "用更长的键盘模式并多次改变输入方向",
    anotherWord: "增加更多不常见的词",
    useWords: "用多个词，但避免常见短语",
    noNeed: "无符号/数字/大写也能构造强密码",
    pwned: "如果在别处也用了此密码，应立即更换"
  },
  timeEstimation: enTranslations.timeEstimation
};

zxcvbnOptions.setOptions({
  dictionary: { ...commonDictionary, ...enDictionary },
  graphs: commonAdjacencyGraphs,
  translations: zhTranslations
});

/** 配置好字典 + 中文 translations 的 zxcvbn 函数 */
export const zxcvbn = _zxcvbn;

export type ZxcvbnResult = ReturnType<typeof _zxcvbn>;

/**
 * 取密码强度的中文提示文案（score < 3 时返回非空字符串，score ≥ 3 返回空）。
 * 优先用 zxcvbn 给的具体 warning（已翻译成中文），让用户知道为什么弱。
 */
export function getPasswordStrengthHint(password: string): string {
  if (!password) return "";
  const result = zxcvbn(password);
  if (result.score >= 3) return "";
  const warning = result.feedback.warning || "";
  const suggestion = (result.feedback.suggestions || [])[0] || "";
  const base = result.score <= 1 ? "密码强度过低" : "密码强度一般";
  if (warning) return `${base}：${warning}`;
  if (suggestion) return `${base}：${suggestion}`;
  if (result.score <= 1) {
    return `${base}，请使用大小写字母 + 数字 + 符号的组合，长度 ≥ 8 位`;
  }
  return `${base}，建议增加长度或添加更多字符类型（大小写字母/数字/符号）`;
}
