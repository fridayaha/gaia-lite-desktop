import { ManagerApi } from '../net/ManagerApi';
import { AppReleaseLatest } from '../net/dto/AppReleaseDtos';

/**
 * App 发布信息仓库。复刻 Android repo/AppReleaseRepository.kt。
 */
export class AppReleaseRepository {
  private managerApi: ManagerApi

  constructor(managerApi: ManagerApi) {
    this.managerApi = managerApi
  }

  async getLatestRelease(): Promise<AppReleaseLatest | null> {
    return await this.managerApi.getLatestRelease()
  }

  async getReleaseByVersion(version: string): Promise<AppReleaseLatest | null> {
    return await this.managerApi.getReleaseByVersion(version)
  }
}
