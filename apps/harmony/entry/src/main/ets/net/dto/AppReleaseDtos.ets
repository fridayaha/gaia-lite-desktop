/**
 * App 版本发布 DTO。复刻 Android net/dto/AppReleaseDtos.kt。
 * 镜像 services/manager/app/schemas/__init__.py AppReleaseLatestResponse。
 */

export class AppReleaseLatest {
  id: string | null = null
  display_name: string | null = null
  description: string | null = null
  icon_url: string | null = null
  version: string | null = null
  size: number | null = null

  static fromJson(o: Record<string, Object>): AppReleaseLatest {
    const r = new AppReleaseLatest()
    if (typeof o['id'] === 'string') r.id = o['id']
    if (typeof o['display_name'] === 'string') r.display_name = o['display_name']
    if (typeof o['description'] === 'string') r.description = o['description']
    if (typeof o['icon_url'] === 'string') r.icon_url = o['icon_url']
    if (typeof o['version'] === 'string') r.version = o['version']
    if (typeof o['size'] === 'number') r.size = o['size']
    return r
  }
}
