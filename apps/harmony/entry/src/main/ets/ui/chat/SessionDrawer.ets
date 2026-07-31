import { Session } from '../../net/dto/ChatDtos';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { SessionGroup, groupSessionsByDate, formatSessionTime } from './SessionGrouping';

// M3 baseline errorContainer / onErrorContainer（主题未覆盖该槽位，对齐 android DeleteDialog 用色）
const ERROR_CONTAINER = 0xFFF9DEDC
const ON_ERROR_CONTAINER = 0xFF410E0B

// 抽屉列表扁平项：分组头 | 会话行
interface DrawerEntry {
  kind: string
  label: string
  session: Session | null
  key: string
}

/**
 * 会话抽屉。复刻 Android ui/chat/SessionDrawer.kt（含长按批量多选）。
 * 头部：返回 + 「对话」+ 新建会话胶囊；搜索框；按 今天/昨天/本周/上周/更早 分组的会话列表。
 * 长按进入批量模式：顶部显示已选条数 + 全选/删除/取消，行首变选中圆圈，隐藏单条编辑/删除按钮。
 */
@Component
export struct SessionDrawer {
  @Prop sessions: Session[] = []
  @Prop currentSessionId: string = ''
  @Prop loading: boolean = false
  @Prop multiSelectMode: boolean = false
  @Prop selectedSessionIds: string[] = []
  onSelect: (sessionId: string) => void = () => {}
  onNew: () => void = () => {}
  onRename: (sessionId: string, title: string) => void = () => {}
  onDelete: (sessionId: string) => void = () => {}
  onClose: () => void = () => {}
  onEnterMultiSelect: (sessionId: string) => void = () => {}
  onToggleSelected: (sessionId: string) => void = () => {}
  onSelectAll: () => void = () => {}
  onCancelMultiSelect: () => void = () => {}
  onDeleteSelected: () => void = () => {}
  @State searchQuery: string = ''
  @State renameTarget: Session | null = null
  @State renameText: string = ''
  @State deleteTarget: Session | null = null
  @State confirmBatchDelete: boolean = false

  private filteredSessions(): Session[] {
    const q = this.searchQuery.trim().toLowerCase()
    if (q.length === 0) return this.sessions
    return this.sessions.filter((s) => s.stableTitle.toLowerCase().includes(q))
  }

  private groupedSessions(): SessionGroup[] {
    return groupSessionsByDate(this.filteredSessions())
  }

  // 扁平化分组 → 单层列表。ArkUI ForEach 对 key 不变的项不会重跑 builder（外层分组复用会导致
  // 内层闭包仍持有旧 items，删除后不刷新）；行 key 带标题/时间，保证重命名/新消息也触发重建。
  private flatEntries(): DrawerEntry[] {
    const out: DrawerEntry[] = []
    for (const g of this.groupedSessions()) {
      out.push({ kind: 'header', label: g.label, session: null, key: `h|${g.label}` })
      for (const s of g.items) {
        out.push({
          kind: 'row',
          label: '',
          session: s,
          key: `r|${s.stableId}|${s.stableTitle}|${s.stableLastAt ?? s.stableCreatedAt}`,
        })
      }
    }
    return out
  }

  private isCurrent(s: Session): boolean {
    return s.stableId === this.currentSessionId
  }

  private isSelected(s: Session): boolean {
    return this.selectedSessionIds.indexOf(s.stableId) >= 0
  }

  private rowTitleColor(s: Session): number {
    return this.isCurrent(s) || this.isSelected(s) ? LightTheme.onAccentContainer : LightTheme.text
  }

  private rowSubtitleColor(s: Session): number {
    return this.isCurrent(s) || this.isSelected(s) ? LightTheme.onAccentContainer : LightTheme.muted
  }

  private rowBg(s: Session): number {
    return this.isCurrent(s) || this.isSelected(s) ? LightTheme.accentContainer : LightTheme.surface
  }

  private timeText(s: Session): string {
    return formatSessionTime(s.stableLastAt ?? s.stableCreatedAt)
  }

  @Builder
  sessionRow(s: Session): void {
    Row() {
      // 批量模式：左侧选中圆圈；非批量模式：会话图标 tile（对齐 android SessionRow）
      if (this.multiSelectMode) {
        MIcon({
          path: this.isSelected(s) ? IconPaths.checkCircle : IconPaths.radioButtonUnchecked,
          iconSize: 22,
          color: this.isSelected(s) ? LightTheme.accent : LightTheme.muted,
        })
      } else {
        Stack() {
          Rect()
            .width(34)
            .height(34)
            .fill(this.isCurrent(s) ? LightTheme.accent : LightTheme.surfaceVariant)
            .radius(10)
          MIcon({
            path: IconPaths.chat,
            iconSize: 18,
            color: this.isCurrent(s) ? LightTheme.onAccent : LightTheme.muted,
          })
        }
        .width(34)
        .height(34)
      }

      Column() {
        Text(s.stableTitle)
          .fontSize(Typography.bodyMedium)
          .fontWeight(FontWeight.Medium)
          .fontColor(this.rowTitleColor(s))
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
        Row() {
          if (s.model !== null && s.model.length > 0) {
            Text(s.model)
              .fontSize(Typography.labelSmall)
              .fontColor(this.rowSubtitleColor(s))
              .maxLines(1)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .constraintSize({ maxWidth: 140 })
          }
          if (this.timeText(s).length > 0) {
            Text(this.timeText(s))
              .fontSize(Typography.labelSmall)
              .fontColor(this.rowSubtitleColor(s))
              .maxLines(1)
              .margin({ left: s.model !== null && s.model.length > 0 ? 6 : 0 })
          }
        }
        .margin({ top: 2 })
        .alignItems(VerticalAlign.Center)
      }
      .layoutWeight(1)
      .margin({ left: 10 })
      .alignItems(HorizontalAlign.Start)

      // 批量模式下隐藏单条 rename/delete 按钮，避免误操作
      if (!this.multiSelectMode) {
        Row() {
          MIcon({ path: IconPaths.edit, iconSize: 16, color: this.rowSubtitleColor(s) })
        }
        .width(32)
        .height(32)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => {
          this.renameTarget = s
          this.renameText = s.stableTitle
        })

        Row() {
          MIcon({ path: IconPaths.delete, iconSize: 16, color: LightTheme.error })
        }
        .width(32)
        .height(32)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => { this.deleteTarget = s })
      }
    }
    .width('100%')
    .padding({ left: 12, right: 12, top: 10, bottom: 10 })
    .backgroundColor(this.rowBg(s))
    .borderRadius(14)
    .margin({ bottom: 6 })
    .onClick(() => {
      if (this.multiSelectMode) this.onToggleSelected(s.stableId)
      else this.onSelect(s.stableId)
    })
    .gesture(
      LongPressGesture().onAction(() => {
        if (!this.multiSelectMode) this.onEnterMultiSelect(s.stableId)
        else this.onToggleSelected(s.stableId)
      })
    )
  }

  @Builder
  renameDialog(): void {
    // 遮罩
    Column() {}
    .width('100%')
    .height('100%')
    .backgroundColor(0x52000000)
    .onClick(() => { this.renameTarget = null })

    Column() {
      Column() {
        Row() {
          Stack() {
            Rect()
              .width(32)
              .height(32)
              .fill(LightTheme.accentContainer)
              .radius(10)
            MIcon({ path: IconPaths.edit, iconSize: 18, color: LightTheme.onAccentContainer })
          }
          .width(32)
          .height(32)
          Text('重命名会话')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.text)
            .margin({ left: 12 })
        }
        .width('100%')
        .alignItems(VerticalAlign.Center)

        TextInput({ text: this.renameText })
          .width('100%')
          .margin({ top: 16 })
          .borderRadius(12)
          .border({ width: 1, color: LightTheme.border })
          .backgroundColor(LightTheme.surface)
          .onChange((v) => { this.renameText = v })

        Row() {
          Blank()
            .layoutWeight(1)
          Text('取消')
            .fontSize(Typography.labelLarge)
            .fontColor(LightTheme.accent)
            .padding({ left: 16, right: 16, top: 8, bottom: 8 })
            .onClick(() => { this.renameTarget = null })
          Row() {
            Text('保存')
              .fontSize(Typography.labelLarge)
              .fontWeight(FontWeight.Medium)
              .fontColor(LightTheme.onAccent)
          }
          .padding({ left: 16, right: 16, top: 8, bottom: 8 })
          .backgroundColor(LightTheme.accent)
          .borderRadius(10)
          .margin({ left: 8 })
          .onClick(() => {
            const t = this.renameTarget
            if (t !== null) this.onRename(t.stableId, this.renameText)
            this.renameTarget = null
          })
        }
        .width('100%')
        .margin({ top: 20 })
        .alignItems(VerticalAlign.Center)
      }
      .width('85%')
      .backgroundColor(LightTheme.surface)
      .borderRadius(20)
      .padding(20)
      .shadow({ radius: 8, color: 0x26000000, offsetX: 0, offsetY: 2 })
    }
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
  }

  @Builder
  batchActionBar(): void {
    Row() {
      Text(`已选 ${this.selectedSessionIds.length}`)
        .fontSize(Typography.labelLarge)
        .fontWeight(FontWeight.Medium)
        .fontColor(LightTheme.onAccentContainer)
        .layoutWeight(1)
        .margin({ left: 4 })
      this.batchAction(IconPaths.selectAll, '全选', LightTheme.accent, () => { this.onSelectAll() })
      this.batchAction(IconPaths.delete, '删除', LightTheme.error, () => { this.confirmBatchDelete = true })
      this.batchAction(IconPaths.close, '取消', LightTheme.accent, () => { this.onCancelMultiSelect() })
    }
    .width('100%')
    .padding({ left: 8, right: 8, top: 6, bottom: 6 })
    .margin({ left: 12, right: 12, top: 4, bottom: 4 })
    .backgroundColor(LightTheme.accentContainer)
    .borderRadius(12)
    .alignItems(VerticalAlign.Center)
  }

  @Builder
  batchAction(icon: string, label: string, color: number, onTap: () => void): void {
    Row() {
      MIcon({ path: icon, iconSize: 16, color: color })
      Text(label)
        .fontSize(Typography.labelLarge)
        .fontColor(color)
        .margin({ left: 4 })
    }
    .padding({ left: 10, right: 10, top: 6, bottom: 6 })
    .alignItems(VerticalAlign.Center)
    .onClick(onTap)
  }

  @Builder
  deleteDialog(title: string, onConfirm: () => void, onDismiss: () => void): void {
    // 遮罩
    Column() {}
    .width('100%')
    .height('100%')
    .backgroundColor(0x52000000)
    .onClick(onDismiss)

    Column() {
      Column() {
        Row() {
          Stack() {
            Rect()
              .width(32)
              .height(32)
              .fill(ERROR_CONTAINER)
              .radius(10)
            MIcon({ path: IconPaths.delete, iconSize: 18, color: ON_ERROR_CONTAINER })
          }
          .width(32)
          .height(32)
          Text('删除会话')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.text)
            .margin({ left: 12 })
        }
        .width('100%')
        .alignItems(VerticalAlign.Center)

        Row() {
          Text(`「${title}」删除后不可恢复，会话内的所有消息将一并清除。`)
            .fontSize(Typography.bodySmall)
            .fontColor(ON_ERROR_CONTAINER)
        }
        .width('100%')
        .margin({ top: 16 })
        .padding({ left: 12, right: 12, top: 10, bottom: 10 })
        .backgroundColor(ERROR_CONTAINER)
        .borderRadius(10)

        Row() {
          Blank()
            .layoutWeight(1)
          Text('取消')
            .fontSize(Typography.labelLarge)
            .fontColor(LightTheme.accent)
            .padding({ left: 16, right: 16, top: 8, bottom: 8 })
            .onClick(onDismiss)
          Row() {
            Text('删除')
              .fontSize(Typography.labelLarge)
              .fontWeight(FontWeight.Medium)
              .fontColor(LightTheme.onAccent)
          }
          .padding({ left: 16, right: 16, top: 8, bottom: 8 })
          .backgroundColor(LightTheme.error)
          .borderRadius(10)
          .margin({ left: 8 })
          .onClick(onConfirm)
        }
        .width('100%')
        .margin({ top: 20 })
        .alignItems(VerticalAlign.Center)
      }
      .width('85%')
      .backgroundColor(LightTheme.surface)
      .borderRadius(20)
      .padding(20)
      .shadow({ radius: 8, color: 0x26000000, offsetX: 0, offsetY: 2 })
    }
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
  }

  build(): void {
    Stack() {
      Column() {
        // 头部：返回 + 对话 + 新建会话胶囊
        Row() {
          Row() {
            MIcon({ path: IconPaths.arrowBack, iconSize: 24, color: LightTheme.text })
          }
          .width(48)
          .height(48)
          .alignItems(VerticalAlign.Center)
          .justifyContent(FlexAlign.Center)
          .onClick(() => { this.onClose() })

          Text('对话')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.text)
            .layoutWeight(1)

          Row() {
            MIcon({ path: IconPaths.add, iconSize: 14, color: LightTheme.onAccent })
            Text('新建会话')
              .fontSize(Typography.labelMedium)
              .fontWeight(FontWeight.Medium)
              .fontColor(LightTheme.onAccent)
              .margin({ left: 4 })
          }
          .padding({ left: 12, right: 12, top: 6, bottom: 6 })
          .backgroundColor(LightTheme.accent)
          .borderRadius(50)
          .alignItems(VerticalAlign.Center)
          .onClick(() => { this.onNew() })
        }
        .width('100%')
        .padding({ left: 8, right: 8, top: 8, bottom: 8 })
        .alignItems(VerticalAlign.Center)

        // 批量模式：顶部 bar 显示已选条数 + 全选/删除/取消；否则显示搜索框（对齐 android）
        if (this.multiSelectMode) {
          this.batchActionBar()
        } else {
          Row() {
            MIcon({ path: IconPaths.search, iconSize: 16, color: LightTheme.muted })
            TextInput({ placeholder: '搜索对话', text: this.searchQuery })
              .layoutWeight(1)
              // 显式浅色底：透明底在部分机型上被渲染成黑色（主题默认填充）
              .backgroundColor(LightTheme.surface)
              .fontSize(Typography.bodySmall)
              .placeholderFont({ size: Typography.bodySmall })
              .onChange((v) => { this.searchQuery = v })
          }
          .width('100%')
          .padding({ left: 12, right: 12 })
          .margin({ left: 12, right: 12, top: 4, bottom: 4 })
          .borderRadius(12)
          .border({ width: 1, color: LightTheme.border })
          .alignItems(VerticalAlign.Center)
        }

        if (this.loading && this.sessions.length === 0) {
          Column() {
            LoadingProgress()
              .width(28)
              .height(28)
              .color(LightTheme.accent)
          }
          .width('100%')
          .padding(32)
          .justifyContent(FlexAlign.Center)
        } else if (this.filteredSessions().length === 0) {
          Text(this.searchQuery.trim().length > 0 ? '未找到匹配的对话' : '暂无会话')
            .fontSize(Typography.bodySmall)
            .fontColor(LightTheme.muted)
            .padding({ left: 16, right: 16, top: 24, bottom: 24 })
            .width('100%')
        } else {
          Scroll() {
            Column() {
              ForEach(this.flatEntries(), (e: DrawerEntry) => {
                if (e.kind === 'header') {
                  Text(e.label)
                    .fontSize(Typography.labelSmall)
                    .fontWeight(FontWeight.Bold)
                    .fontColor(LightTheme.muted)
                    .padding({ left: 4, right: 4, top: 4, bottom: 4 })
                    .width('100%')
                } else if (e.session !== null) {
                  this.sessionRow(e.session)
                }
              }, (e: DrawerEntry) => e.key)
            }
            .width('100%')
            .padding({ left: 12, right: 12, top: 4, bottom: 4 })
            // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐
            .constraintSize({ minHeight: '100%' })
          }
          .layoutWeight(1)
          .scrollBar(BarState.Off)
        }
      }
      .width('100%')
      .height('100%')

      if (this.renameTarget !== null) {
        this.renameDialog()
      }
      if (this.deleteTarget !== null) {
        this.deleteDialog(this.deleteTarget?.stableTitle ?? '', () => {
          const t = this.deleteTarget
          if (t !== null) this.onDelete(t.stableId)
          this.deleteTarget = null
        }, () => { this.deleteTarget = null })
      }
      if (this.confirmBatchDelete) {
        this.deleteDialog(`${this.selectedSessionIds.length} 条会话`, () => {
          this.onDeleteSelected()
          this.confirmBatchDelete = false
        }, () => { this.confirmBatchDelete = false })
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.surface)
  }
}
