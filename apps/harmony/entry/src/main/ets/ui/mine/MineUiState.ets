import { UserInfo } from '../../net/dto/AuthDtos';
import { AppReleaseLatest } from '../../net/dto/AppReleaseDtos';
import { FavoriteItem } from '../../net/dto/FeedbackDtos';
import { SavedAccount } from '../../repo/AccountStore';

/**
 * 我的页 UI 状态。复刻 Android ui/mine/MineUiState.kt。
 */
export class MineUiState {
  user: UserInfo | null = null
  appVersion: string = ''
  latestRelease: AppReleaseLatest | null = null
  currentVersionRelease: AppReleaseLatest | null = null
  checkingUpdate: boolean = false
  updateAvailable: boolean = false
  upToDate: boolean = false
  updateError: string | null = null
  downloadProgress: number | null = null
  recentFavorites: FavoriteItem[] = []
  savedAccounts: SavedAccount[] = []
  switchingAccount: boolean = false
  emailChannelEnabled: boolean = false
  smsChannelEnabled: boolean = false
  profileSaving: boolean = false
  profileError: string | null = null
  avatarUploading: boolean = false
  contactVerifying: boolean = false
  presetAvatars: string[] = []
  developerMode: boolean = false
}
