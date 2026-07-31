import { WorkspaceViewModel } from './WorkspaceViewModel';
import { WorkspaceUiState } from './WorkspaceUiState';
import { AppContainer } from '../../di/AppContainer';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { WorkspaceFileEntry } from '../../net/dto/WorkspaceDtos';

/**
 * 工作区主屏。复刻 Android ui/workspace/WorkspaceScreen.kt 的核心结构。
 * 顶栏：返回（子目录=回上级，根目录=退出）+ 标题（云盘/工作区，或 agent 选择器）+ 搜索 + 加号菜单；
 * 顶栏下面包屑（栈路径，当前段高亮）；列表展示文件/文件夹；点击文件夹进入；点击文件进 FilePreview。
 *
 * Phase 1 子集：
 * - 列表 / 进入子目录 / 返回上级 / 面包屑跳转 / 搜索过滤
 * - 新建目录（弹窗）/ 删除文件
 * Phase 2 待补：上传/移动/多选/重命名/分享
 */

@Component
export struct WorkspaceScreen {
  @Consume('container') container: AppContainer
  @Prop agentId: string = ''
  @Prop showBackButton: boolean = true
  /** 标题覆盖：Tab 模式传 agent 名；空串 = 默认「云盘/工作区」。 */
  @Prop titleText: string = ''
  /** 标题点击回调：Tab 模式用于展开 agent 选择器；null = 标题不可点。 */
  onTitleClick: (() => void) | null = null
  @State state: WorkspaceUiState = new WorkspaceUiState()
  @State showSearch: boolean = false
  @State showAddMenu: boolean = false
  @State showCreateFolder: boolean = false
  @State newFolderName: string = ''
  @State createFolderError: string | null = null
  private vm: WorkspaceViewModel | null = null
  private listener: (s: WorkspaceUiState) => void = (s) => { this.state = s }

  aboutToAppear(): void {
    this.vm = new WorkspaceViewModel(this.container.workspaceRepository)
    this.vm.subscribe(this.listener)
    if (this.agentId.length > 0) {
      this.vm.setAgentId(this.agentId)
    }
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  onBack(): void {
    AppRouter.instance().pop()
  }

  async onNavBack(): Promise<void> {
    // 对齐 android：子目录回上级；根目录且允许退出时返回上一屏
    if (this.state.stack.length > 1) {
      await this.vm?.goBack()
    } else if (this.showBackButton) {
      this.onBack()
    }
  }

  onOpenFile(path: string): void {
    const aid = this.state.agentId
    if (aid === null) return
    const navParam: Record<string, string> = { 'agentId': aid, 'path': path }
    AppRouter.instance().navigate(Routes.FILE_PREVIEW, navParam)
  }

  async onEntryClick(entry: WorkspaceFileEntry): Promise<void> {
    if (entry.is_dir) {
      await this.vm?.openEntry(entry)
    } else {
      this.onOpenFile(entry.path)
    }
  }

  async onCreateFolder(): Promise<void> {
    const name = this.newFolderName.trim()
    if (name.length === 0) {
      this.createFolderError = '名称不能为空'
      return
    }
    if (name.indexOf('/') >= 0 || name.indexOf('\\') >= 0) {
      this.createFolderError = '名称包含非法字符'
      return
    }
    if (name.startsWith('.')) {
      this.createFolderError = '名称不能以 . 开头'
      return
    }
    await this.vm?.createFolder(name, (ok, err) => {
      if (ok) {
        this.showCreateFolder = false
        this.newFolderName = ''
        this.createFolderError = null
      } else {
        this.createFolderError = err
      }
    })
  }

  titleLabel(): string {
    if (this.titleText.length > 0) return this.titleText
    return this.state.developerMode ? '工作区' : '云盘'
  }

  @Builder
  entryRow(entry: WorkspaceFileEntry): void {
    Row() {
      Stack() {
        Rect()
          .width(34)
          .height(34)
          .fill(entry.is_dir ? LightTheme.accent : LightTheme.surfaceVariant)
          .radius(10)
        MIcon({
          path: entry.is_dir ? IconPaths.folder : IconPaths.description,
          iconSize: 18,
          color: entry.is_dir ? LightTheme.onAccent : LightTheme.muted,
        })
      }
      .width(34)
      .height(34)
      Column() {
        Text(entry.name)
          .fontSize(Typography.bodyMedium)
          .fontWeight(FontWeight.Medium)
          .fontColor(LightTheme.text)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
        Text(formatEntryMeta(entry))
          .fontSize(Typography.labelSmall)
          .fontColor(LightTheme.muted)
          .margin({ top: 2 })
          .visibility(entry.is_dir ? Visibility.None : Visibility.Visible)
      }
      .layoutWeight(1)
      .margin({ left: 10 })
      .alignItems(HorizontalAlign.Start)
    }
    .width('100%')
    .padding({ left: 12, right: 12, top: 10, bottom: 10 })
    .backgroundColor(LightTheme.surface)
    .borderRadius(0)
    .onClick(() => { this.onEntryClick(entry) })
  }

  @Builder
  breadcrumb(): void {
    Row() {
      ForEach(this.state.stack, (p: string, idx: number) => {
        if (idx > 0) {
          Text('/')
            .fontSize(Typography.labelMedium)
            .fontColor(LightTheme.muted)
            .margin({ right: 4 })
        }
        Text(p === '.' ? '根' : lastSegment(p))
          .fontSize(Typography.labelMedium)
          .fontWeight(idx === this.state.stack.length - 1 ? FontWeight.Bold : FontWeight.Normal)
          .fontColor(idx === this.state.stack.length - 1 ? LightTheme.onAccentContainer : LightTheme.accent)
          .padding({ left: 8, right: 8, top: 4, bottom: 4 })
          .backgroundColor(idx === this.state.stack.length - 1 ? LightTheme.accentContainer : 0x00000000)
          .borderRadius(8)
          .margin({ right: 4 })
          .onClick(() => { this.vm?.loadDir(p) })
      }, (p: string, idx: number) => `${idx}-${p}`)
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 8, bottom: 8 })
    .alignItems(VerticalAlign.Center)
  }

  @Builder
  searchBar(): void {
    Row() {
      MIcon({ path: IconPaths.search, iconSize: 18, color: LightTheme.muted })
        .margin({ left: 12 })
      TextInput({ placeholder: '搜索文件', text: this.state.searchQuery })
        .layoutWeight(1)
        .height(56)
        // 显式浅色底：透明底在部分机型上被渲染成黑色（主题默认填充）
        .backgroundColor(LightTheme.bg)
        .fontSize(Typography.bodySmall)
        .onChange((v) => { this.vm?.setSearchQuery(v) })
      if (this.state.searchQuery.length > 0) {
        Row() {
          MIcon({ path: IconPaths.close, iconSize: 18, color: LightTheme.muted })
        }
        .width(36)
        .height(36)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => { this.vm?.setSearchQuery('') })
      }
    }
    .width('100%')
    .alignItems(VerticalAlign.Center)
    .border({ width: 1, color: LightTheme.muted, radius: 4 })
    .margin({ left: 16, right: 16, top: 8, bottom: 8 })
  }

  @Builder
  addMenu(): void {
    Column() {
      this.addMenuItem(IconPaths.createNewFolder, '新建文件夹', () => {
        this.newFolderName = ''
        this.createFolderError = null
        this.showCreateFolder = true
      })
      this.addMenuItem(IconPaths.image, '上传图片', () => {
        this.getUIContext().getPromptAction().showToast({ message: '上传功能需真机调试，敬请期待', duration: 2000 })
      })
      this.addMenuItem(IconPaths.attachFile, '上传文件', () => {
        this.getUIContext().getPromptAction().showToast({ message: '上传功能需真机调试，敬请期待', duration: 2000 })
      })
    }
    .width(220)
    .padding({ top: 4, bottom: 4 })
    .backgroundColor(LightTheme.surface)
    .borderRadius(20)
    .shadow({ radius: 16, color: 0x24000000, offsetX: 0, offsetY: 4 })
    .alignItems(HorizontalAlign.Start)
  }

  @Builder
  addMenuItem(icon: string, label: string, onTap: () => void): void {
    Row() {
      MIcon({ path: icon, iconSize: 20, color: LightTheme.text })
      Text(label)
        .fontSize(Typography.bodyMedium)
        .fontColor(LightTheme.text)
        .margin({ left: 12 })
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 12, bottom: 12 })
    .alignItems(VerticalAlign.Center)
    .onClick(() => {
      this.showAddMenu = false
      onTap()
    })
  }

  @Builder
  topBar(): void {
    Row() {
      if (this.state.stack.length > 1 || this.showBackButton) {
        Row() {
          MIcon({ path: IconPaths.arrowBack, iconSize: 24, color: LightTheme.text })
        }
        .width(48)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => { this.onNavBack() })
      }
      // 标题：默认「云盘/工作区」；Tab 模式为 agent 选择器（可点）
      if (this.onTitleClick !== null) {
        Row() {
          Text(this.titleLabel())
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.text)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
          MIcon({ path: IconPaths.keyboardArrowDown, iconSize: 20, color: LightTheme.text })
            .margin({ left: 2 })
        }
        .layoutWeight(1)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .margin({ left: this.state.stack.length > 1 || this.showBackButton ? 0 : 16 })
        .onClick(() => { this.onTitleClick?.() })
      } else {
        Text(this.titleLabel())
          .fontSize(Typography.titleMedium)
          .fontWeight(FontWeight.Bold)
          .fontColor(LightTheme.text)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .layoutWeight(1)
          .margin({ left: this.state.stack.length > 1 || this.showBackButton ? 0 : 16 })
      }
      Row() {
        MIcon({ path: IconPaths.search, iconSize: 24, color: LightTheme.text })
      }
      .width(48)
      .height(48)
      .alignItems(VerticalAlign.Center)
      .justifyContent(FlexAlign.Center)
      .onClick(() => {
        this.showSearch = !this.showSearch
        if (!this.showSearch) this.vm?.setSearchQuery('')
      })
      Row() {
        MIcon({ path: IconPaths.add, iconSize: 24, color: LightTheme.text })
      }
      .width(48)
      .height(48)
      .alignItems(VerticalAlign.Center)
      .justifyContent(FlexAlign.Center)
      .onClick(() => { this.showAddMenu = !this.showAddMenu })
      .bindPopup(this.showAddMenu, {
        builder: this.addMenu(),
        placement: Placement.BottomRight,
        popupColor: 0x00000000,
        onStateChange: (e) => { this.showAddMenu = e.isVisible },
      })
    }
    .width('100%')
    .height(64)
    .padding({ left: 4, right: 4 })
    .alignItems(VerticalAlign.Center)
  }

  build(): void {
    Stack() {
      Column() {
        this.topBar()
        this.breadcrumb()

        if (this.showSearch) {
          this.searchBar()
        }

        // 内容区
        if (this.state.loading) {
          Column() {
            LoadingProgress()
              .width(28)
              .height(28)
              .color(LightTheme.accent)
          }
          .width('100%')
          .layoutWeight(1)
          .justifyContent(FlexAlign.Center)
        } else if (this.state.error !== null) {
          Column() {
            Text(this.state.error ?? '加载失败')
              .fontColor(LightTheme.error)
              .margin({ bottom: 12 })
              .textAlign(TextAlign.Center)
              .width('100%')
            Text('重试')
              .fontSize(Typography.bodyMedium)
              .fontWeight(FontWeight.Bold)
              .fontColor(LightTheme.onAccent)
              .padding({ left: 24, right: 24, top: 10, bottom: 10 })
              .backgroundColor(LightTheme.accent)
              .borderRadius(12)
              .onClick(() => { this.vm?.refresh() })
          }
          .width('100%')
          .layoutWeight(1)
          .justifyContent(FlexAlign.Center)
        } else if (this.state.filteredEntries.length === 0) {
          Column() {
            Text('空目录')
              .fontColor(LightTheme.muted)
          }
          .width('100%')
          .layoutWeight(1)
          .justifyContent(FlexAlign.Center)
        } else {
          Scroll() {
            Column() {
              ForEach(this.state.filteredEntries, (entry: WorkspaceFileEntry) => {
                this.entryRow(entry)
              }, (entry: WorkspaceFileEntry) => entry.path)
            }
            .width('100%')
            // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐
            .constraintSize({ minHeight: '100%' })
          }
          .layoutWeight(1)
          .scrollBar(BarState.Off)
        }
      }
      .width('100%')
      .height('100%')
      .backgroundColor(LightTheme.bg)

      // 新建目录弹窗（对齐 android NewFolderDialog：radius 20 + 图标 tile + 输入框 + 右对齐按钮）
      if (this.showCreateFolder) {
        Column() {
          Column() {
            Row() {
              Stack() {
                Rect()
                  .width(32)
                  .height(32)
                  .fill(LightTheme.accentContainer)
                  .radius(10)
                MIcon({ path: IconPaths.folder, iconSize: 18, color: LightTheme.onAccentContainer })
              }
              .width(32)
              .height(32)
              Text('新建文件夹')
                .fontSize(Typography.titleMedium)
                .fontWeight(FontWeight.Bold)
                .fontColor(LightTheme.text)
                .margin({ left: 12 })
            }
            .alignItems(VerticalAlign.Center)
            .margin({ bottom: 16 })

            TextInput({ placeholder: '文件夹名称', text: this.newFolderName })
              .type(InputType.Normal)
              .width('100%')
              .height(56)
              .border({ width: 1, color: this.createFolderError !== null ? LightTheme.error : LightTheme.muted, radius: 12 })
              // 显式浅色底：透明底在部分机型上被渲染成黑色（主题默认填充）
              .backgroundColor(LightTheme.surface)
              .onChange((v) => { this.newFolderName = v; this.createFolderError = null })
            if (this.createFolderError !== null) {
              Text(this.createFolderError ?? '')
                .fontSize(Typography.labelSmall)
                .fontColor(LightTheme.error)
                .margin({ top: 8 })
                .width('100%')
            }
            Row() {
              Text('取消')
                .fontSize(Typography.labelLarge)
                .fontColor(LightTheme.accent)
                .padding({ left: 12, right: 12, top: 8, bottom: 8 })
                .onClick(() => { this.showCreateFolder = false })
              Text('确定')
                .fontSize(Typography.labelLarge)
                .fontWeight(FontWeight.Medium)
                .fontColor(LightTheme.onAccent)
                .padding({ left: 16, right: 16, top: 8, bottom: 8 })
                .backgroundColor(LightTheme.accent)
                .borderRadius(10)
                .margin({ left: 8 })
                .onClick(() => { this.onCreateFolder() })
            }
            .width('100%')
            .justifyContent(FlexAlign.End)
            .margin({ top: 20 })
          }
          .width('80%')
          .padding(20)
          .backgroundColor(LightTheme.surface)
          .borderRadius(20)
          .alignItems(HorizontalAlign.Start)
        }
        .width('100%')
        .height('100%')
        .justifyContent(FlexAlign.Center)
        .backgroundColor(0x80000000)
      }
    }
    .width('100%')
    .height('100%')
  }
}

function lastSegment(path: string): string {
  const idx = path.lastIndexOf('/')
  return idx >= 0 ? path.slice(idx + 1) : path
}

function formatEntryMeta(entry: WorkspaceFileEntry): string {
  if (entry.is_dir) return ''
  const sizeStr = formatSize(entry.size)
  return sizeStr
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}
