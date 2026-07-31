import { describe, it, expect } from '@ohos/hypium';
import { isVersionNewer } from '../../../main/ets/util/VersionUtil';

/**
 * isVersionNewer 单元测试。复刻 Android VersionUtilTest.kt。
 */
export function registerVersionUtilTests(): void {
  describe('VersionUtil', () => {
    it('newer by patch version', 0, () => {
      expect(isVersionNewer('1.0.1', '1.0.0')).assertTrue()
    })

    it('newer by minor version', 0, () => {
      expect(isVersionNewer('1.10.0', '1.9.0')).assertTrue()
    })

    it('newer by major version', 0, () => {
      expect(isVersionNewer('2.0.0', '1.99.99')).assertTrue()
    })

    it('same version returns false', 0, () => {
      expect(isVersionNewer('1.0.0', '1.0.0')).assertFalse()
    })

    it('older version returns false', 0, () => {
      expect(isVersionNewer('1.0.0', '1.0.1')).assertFalse()
    })

    it('prerelease newer than prerelease', 0, () => {
      expect(isVersionNewer('1.0.0-beta.2', '1.0.0-beta.1')).assertTrue()
    })

    it('prerelease older than release', 0, () => {
      expect(isVersionNewer('1.0.0-beta.1', '1.0.0')).assertFalse()
    })

    it('release newer than prerelease', 0, () => {
      expect(isVersionNewer('1.0.0', '1.0.0-beta.1')).assertTrue()
    })

    it('empty newer returns false', 0, () => {
      expect(isVersionNewer('', '1.0.0')).assertFalse()
    })

    it('empty current returns true', 0, () => {
      expect(isVersionNewer('1.0.0', '')).assertTrue()
    })

    it('non-numeric segment treated as zero', 0, () => {
      expect(isVersionNewer('1.x.0', '1.0.0')).assertFalse()
    })
  })
}
