@Entry
@Component
struct Index {
  build(): void {
    Column() {
      Text('HarmonyOS Phase 0 测试')
        .fontSize(18)
        .margin(20)
    }
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
  }
}
