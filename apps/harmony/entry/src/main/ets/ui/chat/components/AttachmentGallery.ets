import image from '@ohos.multimedia.image';
import { Attachment } from '../../../net/dto/ChatDtos';
import { LightTheme } from '../../theme/Colors';
import { IconPaths, MIcon } from '../../theme/Icons';

/**
 * 消息附件画廊。复刻 Android ChatScreen.AttachmentGallery：
 * - 本地 Uri（发送后立即渲染）直接 Image 缩略图
 * - 历史消息（无 localUri 有 path）异步下载字节 → PixelMap 渲染，加载中转圈
 * - 非图片附件 → 图标块
 * - 点击图片 → onView（全屏预览）
 */
@Component
export struct AttachmentGallery {
  attachments: Attachment[] = []
  onFetch: (path: string) => Promise<ArrayBuffer | null> = async () => null
  onView: (a: Attachment, pm: image.PixelMap | null) => void = () => {}

  build(): void {
    List({ space: 6 }) {
      ForEach(this.attachments, (a: Attachment, idx: number) => {
        ListItem() {
          AttachmentTile({ attachment: a, onFetch: this.onFetch, onView: this.onView })
        }
      }, (a: Attachment, idx: number) => `${idx}-${a.path}-${a.localUri ?? ''}`)
    }
    .listDirection(Axis.Horizontal)
    .scrollBar(BarState.Off)
    .height(96)
    .width('100%')
  }
}

@Component
struct AttachmentTile {
  attachment: Attachment = new Attachment()
  onFetch: (path: string) => Promise<ArrayBuffer | null> = async () => null
  onView: (a: Attachment, pm: image.PixelMap | null) => void = () => {}
  @State pixelMap: image.PixelMap | null = null
  @State loading: boolean = false

  aboutToAppear(): void {
    const a = this.attachment
    if (a.is_image && a.localUri === null && a.path.length > 0) {
      this.loading = true
      this.onFetch(a.path).then(async (bytes: ArrayBuffer | null) => {
        if (bytes === null) {
          this.loading = false
          return
        }
        try {
          const src = image.createImageSource(bytes)
          this.pixelMap = await src.createPixelMap()
          src.release()
        } catch (e) {}
        this.loading = false
      })
    }
  }

  build(): void {
    Stack() {
      if (this.attachment.is_image) {
        if (this.attachment.localUri !== null) {
          Image(this.attachment.localUri)
            .width(96)
            .height(96)
            .objectFit(ImageFit.Cover)
            .borderRadius(10)
        } else if (this.pixelMap !== null) {
          Image(this.pixelMap)
            .width(96)
            .height(96)
            .objectFit(ImageFit.Cover)
            .borderRadius(10)
        } else if (this.loading) {
          LoadingProgress()
            .width(20)
            .height(20)
            .color(LightTheme.accent)
        } else {
          MIcon({ path: IconPaths.image, iconSize: 28, color: LightTheme.accent })
        }
      } else {
        MIcon({ path: IconPaths.attachFile, iconSize: 28, color: LightTheme.accent })
      }
    }
    .width(96)
    .height(96)
    .backgroundColor(LightTheme.surfaceVariant)
    .borderRadius(10)
    .alignContent(Alignment.Center)
    .onClick(() => {
      if (this.attachment.is_image) this.onView(this.attachment, this.pixelMap)
    })
  }
}
