import { describe, it, expect } from '@ohos/hypium';
import { ServerConfigResolver } from '../../../main/ets/net/ServerConfigResolver';

/**
 * ServerConfigResolver 单元测试。复刻 Android ServerConfigResolverTest.kt。
 */
export function registerServerConfigResolverTests(): void {
  describe('ServerConfigResolver', () => {
    it('uses real values when set', 0, () => {
      const raw = '{"manager_url":"http://example.com/","gateway_url":"http://gw.example.com/"}'
      const r = ServerConfigResolver.resolve(raw, 'default-m', 'default-g')
      expect(r.managerUrl).assertEqual('http://example.com/')
      expect(r.gatewayUrl).assertEqual('http://gw.example.com/')
    })

    it('falls back to defaults when raw is null', 0, () => {
      const r = ServerConfigResolver.resolve(null, 'default-m', 'default-g')
      expect(r.managerUrl).assertEqual('default-m')
      expect(r.gatewayUrl).assertEqual('default-g')
    })

    it('falls back to defaults when raw is empty', 0, () => {
      const r = ServerConfigResolver.resolve('', 'default-m', 'default-g')
      expect(r.managerUrl).assertEqual('default-m')
      expect(r.gatewayUrl).assertEqual('default-g')
    })

    it('falls back when placeholder __UA_ present', 0, () => {
      const raw = '{"manager_url":"__UA_MANAGER_URL__","gateway_url":"__UA_GATEWAY_URL__"}'
      const r = ServerConfigResolver.resolve(raw, 'default-m', 'default-g')
      expect(r.managerUrl).assertEqual('default-m')
      expect(r.gatewayUrl).assertEqual('default-g')
    })

    it('falls back when invalid json', 0, () => {
      const r = ServerConfigResolver.resolve('not json', 'default-m', 'default-g')
      expect(r.managerUrl).assertEqual('default-m')
      expect(r.gatewayUrl).assertEqual('default-g')
    })

    it('manager real, gateway placeholder mixes correctly', 0, () => {
      const raw = '{"manager_url":"http://real.example.com/","gateway_url":"__UA_GATEWAY_URL__"}'
      const r = ServerConfigResolver.resolve(raw, 'default-m', 'default-g')
      expect(r.managerUrl).assertEqual('http://real.example.com/')
      expect(r.gatewayUrl).assertEqual('default-g')
    })

    it('blank value treated as placeholder', 0, () => {
      const raw = '{"manager_url":"  ","gateway_url":"real"}'
      const r = ServerConfigResolver.resolve(raw, 'default-m', 'default-g')
      expect(r.managerUrl).assertEqual('default-m')
      expect(r.gatewayUrl).assertEqual('real')
    })
  })
}
