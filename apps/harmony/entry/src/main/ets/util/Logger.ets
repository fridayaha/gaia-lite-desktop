import hilog from '@ohos.hilog';

const DOMAIN = 0x0001
const TAG = 'UnionAgents'

export type LogLevel = 'D' | 'I' | 'W' | 'E'

export function logD(msg: string, ...args: Object[]): void {
  hilog.info(DOMAIN, TAG, `D/${msg} ${args.map(a => typeof a === 'object' ? JSON.stringify(a) : a).join(' ')}`)
}

export function logI(msg: string, ...args: Object[]): void {
  hilog.info(DOMAIN, TAG, `I/${msg} ${args.map(a => typeof a === 'object' ? JSON.stringify(a) : a).join(' ')}`)
}

export function logW(msg: string, ...args: Object[]): void {
  hilog.warn(DOMAIN, TAG, `W/${msg} ${args.map(a => typeof a === 'object' ? JSON.stringify(a) : a).join(' ')}`)
}

export function logE(msg: string, ...args: Object[]): void {
  hilog.error(DOMAIN, TAG, `E/${msg} ${args.map(a => typeof a === 'object' ? JSON.stringify(a) : a).join(' ')}`)
}
