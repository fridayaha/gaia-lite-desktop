import { MessageFeedbackRepository } from '../../repo/MessageFeedbackRepository';
import { FavoriteItem } from '../../net/dto/FeedbackDtos';
import { logE } from '../../util/Logger';

/**
 * 收藏列表 ViewModel。复刻 Android ui/mine/FavoritesViewModel.kt。
 */
export class FavoritesUiState {
  loading: boolean = true
  items: FavoriteItem[] = []
  error: string | null = null
}

export class FavoritesViewModel {
  private _state: FavoritesUiState = new FavoritesUiState()
  private listeners: Array<(s: FavoritesUiState) => void> = []
  private repo: MessageFeedbackRepository

  constructor(repo: MessageFeedbackRepository) {
    this.repo = repo
  }

  get state(): FavoritesUiState {
    return this._state
  }

  subscribe(listener: (s: FavoritesUiState) => void): void {
    this.listeners.push(listener)
    listener(this._state)
  }

  unsubscribe(listener: (s: FavoritesUiState) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener)
  }

  private setState(patch: (s: FavoritesUiState) => void): void {
    const next = new FavoritesUiState()
    next.loading = this._state.loading
    next.items = this._state.items.slice()
    next.error = this._state.error
    patch(next)
    this._state = next
    for (const l of this.listeners) {
      try { l(this._state) } catch (e) { logE('FavoritesViewModel', `notify failed: ${e}`) }
    }
  }

  async load(): Promise<void> {
    this.setState((s) => { s.loading = true; s.error = null })
    try {
      const items = await this.repo.listMyFavorites()
      this.setState((s) => { s.loading = false; s.items = items })
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '加载失败') : '加载失败'
      this.setState((s) => { s.loading = false; s.error = msg })
    }
  }
}
