import { describe, it, expect } from '@ohos/hypium';
import { registerThrottleTests } from './ThrottleTest';
import { registerCloseFencesTests } from './CloseFencesTest';
import { registerJwtUtilTests } from './JwtUtilTest';
import { registerStreamEventParserTests } from './StreamEventParserTest';
import { registerReasoningDedupTests } from './ReasoningDedupTest';
import { registerServerConfigResolverTests } from './ServerConfigResolverTest';
import { registerTokenRefresherTests } from './TokenRefresherTest';
import { registerVersionUtilTests } from './VersionUtilTest';
import { registerTimeFormatTests } from './TimeFormatTest';
import { registerMessageFeedbackRefTests } from './MessageFeedbackRefTest';
import { registerSpeechPlayerCleanTests } from './SpeechPlayerCleanTest';
import { registerSavedAccountTests } from './SavedAccountTest';
import { registerWorkspaceUiStateTests } from './WorkspaceUiStateTest';

/**
 * 测试入口。所有 test/*Test.ets 的 register* 函数集中在这里调用。
 * 由 TestAbility 启动后调用 hypium 的 run() 传入。
 */
export default function testsuite(): void {
  registerThrottleTests()
  registerCloseFencesTests()
  registerJwtUtilTests()
  registerStreamEventParserTests()
  registerReasoningDedupTests()
  registerServerConfigResolverTests()
  registerTokenRefresherTests()
  registerVersionUtilTests()
  registerTimeFormatTests()
  registerMessageFeedbackRefTests()
  registerSpeechPlayerCleanTests()
  registerSavedAccountTests()
  registerWorkspaceUiStateTests()
}
