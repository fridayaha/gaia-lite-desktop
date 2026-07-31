import media from '@ohos.multimedia.media';
import fs from '@ohos.file.fs';

/**
 * AVRecorder m4a 录音封装。对齐 Android Composer.createAudioRecorder/finalizeRecording：
 * MIC → AAC → MPEG_4A 容器（m4a），输出到 cacheDir/recordings/record_<ts>.m4a。
 * 文件描述符由调用方持有，stop 时随 release 一并关闭。
 */
export class AudioRecorder {
  private recorder: media.AVRecorder | null = null
  private file: fs.File | null = null
  private filePath: string = ''

  /** 打开输出文件 + prepare + start。失败抛异常（调用方 toast）。 */
  async start(cacheDir: string): Promise<void> {
    const dir = `${cacheDir}/recordings`
    try {
      fs.mkdirSync(dir, true)
    } catch (e) {}
    this.filePath = `${dir}/record_${Date.now()}.m4a`
    const f = fs.openSync(this.filePath, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE)
    this.file = f
    const r = await media.createAVRecorder()
    const profile: media.AVRecorderProfile = {
      audioBitrate: 64000,
      audioChannels: 1,
      audioCodec: media.CodecMimeType.AUDIO_AAC,
      audioSampleRate: 44100,
      fileFormat: media.ContainerFormatType.CFT_MPEG_4A,
    }
    const config: media.AVRecorderConfig = {
      audioSourceType: media.AudioSourceType.AUDIO_SOURCE_TYPE_MIC,
      profile: profile,
      url: `fd://${f.fd}`,
    }
    try {
      await r.prepare(config)
      await r.start()
    } catch (e) {
      try { fs.closeSync(f) } catch (e2) {}
      this.file = null
      throw e instanceof Error ? e : new Error(String(e))
    }
    this.recorder = r
  }

  /** 停止并释放，返回录音文件路径；失败（录音太短/未启动）返回 null。 */
  async stop(): Promise<string | null> {
    const r = this.recorder
    const f = this.file
    this.recorder = null
    this.file = null
    if (r === null) return null
    try {
      await r.stop()
      await r.release()
      if (f !== null) {
        try { fs.closeSync(f) } catch (e) {}
      }
      return this.filePath
    } catch (e) {
      try { await r.release() } catch (e2) {}
      if (f !== null) {
        try { fs.closeSync(f) } catch (e3) {}
      }
      this.discard()
      return null
    }
  }

  /** 丢弃：尽力 release + 删文件。 */
  async discard(): Promise<void> {
    const r = this.recorder
    const f = this.file
    this.recorder = null
    this.file = null
    if (r !== null) {
      try { await r.stop() } catch (e) {}
      try { await r.release() } catch (e) {}
    }
    if (f !== null) {
      try { fs.closeSync(f) } catch (e) {}
    }
    try { fs.unlink(this.filePath) } catch (e) {}
  }

  get currentPath(): string {
    return this.filePath
  }
}
