package com.unionagents.enduser.repo

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 端侧 TTS 播放（Android TextToSpeech，免费离线）。
 * speakingRef：正在朗读的消息锚点（message_ref），UI 据此高亮喇叭图标；null=空闲。
 * 设备无 TTS 引擎（部分国产 ROM 精简）时 onUnavailable 回调给 UI toast。
 */
@Singleton
class SpeechPlayer @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private var tts: TextToSpeech? = null
    private var pendingSpeak: Pair<String, String>? = null

    private val _speakingRef = MutableStateFlow<String?>(null)
    val speakingRef: StateFlow<String?> = _speakingRef.asStateFlow()

    var onUnavailable: (() -> Unit)? = null

    fun toggle(ref: String, text: String) {
        if (_speakingRef.value == ref) {
            stop()
            return
        }
        speak(ref, text)
    }

    fun stop() {
        tts?.stop()
        pendingSpeak = null
        _speakingRef.value = null
    }

    /** 开始朗读新内容（正在播放的会被打断）。自动朗读场景用：新回复总是替换当前播放。 */
    fun speak(ref: String, text: String) {
        val cleaned = cleanForSpeech(text)
        if (cleaned.isBlank()) return
        val engine = tts
        if (engine == null) {
            pendingSpeak = ref to cleaned
            initEngine()
            return
        }
        doSpeak(engine, ref, cleaned)
    }

    private fun initEngine() {
        // init 回调必定异步触发（构造函数返回后），lateinit 在此安全
        lateinit var engine: TextToSpeech
        engine = TextToSpeech(context) { status ->
            if (status != TextToSpeech.SUCCESS) {
                onUnavailable?.invoke()
                pendingSpeak = null
                runCatching { engine.shutdown() }
                return@TextToSpeech
            }
            val lang = engine.setLanguage(Locale.CHINA)
            if (lang == TextToSpeech.LANG_MISSING_DATA || lang == TextToSpeech.LANG_NOT_SUPPORTED) {
                onUnavailable?.invoke()
                pendingSpeak = null
                runCatching { engine.shutdown() }
                return@TextToSpeech
            }
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}
                override fun onDone(utteranceId: String?) {
                    if (_speakingRef.value == utteranceId) _speakingRef.value = null
                }
                @Deprecated("deprecated in API 21")
                override fun onError(utteranceId: String?) {
                    if (_speakingRef.value == utteranceId) _speakingRef.value = null
                }
            })
            tts = engine
            pendingSpeak?.let { (ref, text) ->
                pendingSpeak = null
                doSpeak(engine, ref, text)
            }
        }
    }

    private fun doSpeak(engine: TextToSpeech, ref: String, text: String) {
        _speakingRef.value = ref
        // utteranceId 用 ref：onDone 回调据此归位播放状态
        engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, ref)
    }

    /** 朗读前清洗 markdown/链接/代码块，避免读出 "星号星号""井号" 这类噪音。 */
    companion object {
        internal fun cleanForSpeech(raw: String): String {
            var s = raw
            // 代码块整体移除（朗读代码无意义）
            s = s.replace(Regex("```[\\s\\S]*?```"), "，代码片段，")
            s = s.replace(Regex("`[^`]*`"), " ")
            // 链接/图片：保留可见文本
            s = s.replace(Regex("!?\\[([^]]*)]\\([^)]*\\)"), "$1")
            // 标题/加粗/斜体/删除线/引用符号
            s = s.replace(Regex("^\\s{0,3}#{1,6}\\s*", RegexOption.MULTILINE), "")
            s = s.replace(Regex("\\*\\*|__|\\*|~~"), "")
            s = s.replace(Regex("^\\s{0,3}>\\s?", RegexOption.MULTILINE), "")
            // 无序列表符号
            s = s.replace(Regex("^\\s*[-*+]\\s+", RegexOption.MULTILINE), "")
            // 连续空白
            s = s.replace(Regex("\\s{2,}"), " ")
            return s.trim()
        }
    }
}
