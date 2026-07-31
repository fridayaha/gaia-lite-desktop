import UIAbility from '@ohos.app.ability.UIAbility';
import type { AbilityConstant, Want } from '@ohos.app.ability.AbilityConstant';
import type window from '@ohos.window';
import hypium from '@ohos/hypium';
import testsuite from '../test/List.test';

/**
 * 测试入口 Ability。DevEco Studio 运行测试时由系统启动。
 */
export default class TestAbility extends UIAbility {
  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    // no-op
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    windowStage.loadContent('testability/Index', (err) => {
      // no-op
    })
    hypium.hypiumTest(this.context, testsuite)
  }

  onDestroy(): void {
    // no-op
  }
}
