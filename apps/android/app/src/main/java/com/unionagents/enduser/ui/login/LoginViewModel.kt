package com.unionagents.enduser.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.unionagents.enduser.repo.AuthRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import retrofit2.HttpException
import javax.inject.Inject

@HiltViewModel
class LoginViewModel @Inject constructor(
    private val authRepo: AuthRepository,
    private val json: Json,
) : ViewModel() {

    private val _ui = MutableStateFlow(LoginUiState())
    val ui: StateFlow<LoginUiState> = _ui.asStateFlow()

    private var countdownJob: Job? = null

    init {
        // 拉取 verification-channels 以决定是否显示「验证码」登录 tab
        // 失败时默认 smsEnabled=false（隐藏 SMS tab），不影响主登录流程
        viewModelScope.launch {
            runCatching { authRepo.getVerificationChannels() }
                .onSuccess { ch -> _ui.update { it.copy(smsEnabled = ch.sms) } }
        }
    }

    fun onTabChange(tab: LoginTab) {
        // 防御：SMS_CODE tab 在 smsEnabled=false 时不让切
        if (tab == LoginTab.SMS_CODE && !_ui.value.smsEnabled) return
        _ui.update { it.copy(tab = tab, error = null, success = null) }
        // 进入 SMS_CODE tab 时若没 captcha，自动拉一张
        if (tab == LoginTab.SMS_CODE && _ui.value.smsCaptchaId.isBlank()) {
            refreshSmsCaptcha()
        }
    }
    fun onUsernameChange(v: String) = _ui.update { it.copy(username = v, error = null) }
    fun onPasswordChange(v: String) = _ui.update { it.copy(password = v, error = null) }
    fun onEmailChange(v: String) = _ui.update { it.copy(email = v, error = null) }
    fun onEmailPasswordChange(v: String) = _ui.update { it.copy(emailPassword = v, error = null) }
    fun onPhoneChange(v: String) = _ui.update { it.copy(phone = v, error = null) }
    fun onPhonePasswordChange(v: String) = _ui.update { it.copy(phonePassword = v, error = null) }
    fun onSmsPhoneChange(v: String) = _ui.update { it.copy(smsPhone = v, error = null) }
    fun onSmsCodeChange(v: String) = _ui.update { it.copy(smsCode = v, error = null) }
    fun onSmsCaptchaAnswerChange(v: String) = _ui.update { it.copy(smsCaptchaAnswer = v, error = null) }
    fun onForgotTargetChange(v: String) = _ui.update { it.copy(forgotTarget = v, error = null) }

    /** 拉一张图形验证码，刷新后清空用户已填的验证码答案。 */
    fun refreshSmsCaptcha() {
        viewModelScope.launch {
            runCatching { authRepo.getCaptcha() }
                .onSuccess { cap ->
                    _ui.update {
                        it.copy(
                            smsCaptchaId = cap.captchaId,
                            smsCaptchaImage = cap.imageBase64,
                            smsCaptchaAnswer = "",
                        )
                    }
                }
                .onFailure { e -> _ui.update { it.copy(error = friendlyError(e)) } }
        }
    }

    /**
     * 发送短信验证码。需要先填手机号 + 图形验证码答案。
     * 成功后启动 60s 倒计时，倒计时归零前不可重发。
     */
    fun sendSmsCode() {
        val s = _ui.value
        if (s.loading) return
        val phone = s.smsPhone.trim()
        if (phone.isBlank()) { _ui.update { it.copy(error = "请输入手机号") }; return }
        if (s.smsCaptchaId.isBlank() || s.smsCaptchaAnswer.isBlank()) {
            _ui.update { it.copy(error = "请输入图形验证码") }; return
        }
        if (s.smsCountdown > 0) return  // 倒计时中，忽略重复点击
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            runCatching {
                authRepo.sendVerificationCode(
                    channel = "sms",
                    target = phone,
                    purpose = "login",
                    captchaId = s.smsCaptchaId,
                    captchaAnswer = s.smsCaptchaAnswer.trim(),
                )
            }
                .onSuccess {
                    _ui.update {
                        it.copy(loading = false, smsCodeSent = true, success = "验证码已发送")
                    }
                    startSmsCountdown(60)
                }
                .onFailure { e ->
                    _ui.update { it.copy(loading = false, error = friendlyError(e)) }
                    refreshSmsCaptcha()
                }
        }
    }

    private fun startSmsCountdown(seconds: Int) {
        countdownJob?.cancel()
        countdownJob = viewModelScope.launch {
            for (i in seconds downTo 1) {
                _ui.update { it.copy(smsCountdown = i) }
                delay(1000)
            }
            _ui.update { it.copy(smsCountdown = 0) }
        }
    }

    /**
     * 短信验证码登录：phone + code 直接换 token，不需要密码。
     */
    fun submitSmsLogin() {
        val s = _ui.value
        if (s.loading) return
        val phone = s.smsPhone.trim()
        val code = s.smsCode.trim()
        if (phone.isBlank() || code.isBlank()) {
            _ui.update { it.copy(error = "请输入手机号和验证码") }; return
        }
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            runCatching { authRepo.loginBySmsCode(phone, code) }
                .onSuccess { user ->
                    countdownJob?.cancel()
                    _ui.update { it.copy(loading = false, smsCountdown = 0, loggedInUser = user) }
                }
                .onFailure { e -> _ui.update { it.copy(loading = false, error = friendlyError(e)) } }
        }
    }
    fun onForgotCaptchaAnswerChange(v: String) =
        _ui.update { it.copy(forgotCaptchaAnswer = v, error = null) }
    fun onForgotCodeChange(v: String) = _ui.update { it.copy(forgotCode = v, error = null) }
    fun onForgotNewPasswordChange(v: String) =
        _ui.update { it.copy(forgotNewPassword = v, error = null) }
    fun onForgotConfirmPasswordChange(v: String) =
        _ui.update { it.copy(forgotConfirmPassword = v, error = null) }

    fun onForgotChannelPick(channel: String) {
        _ui.update {
            it.copy(
                forgotChannelChoice = channel,
                forgotStep = ForgotStep.CAPTCHA,
                forgotTarget = "",
                error = null,
            )
        }
        fetchForgotCaptcha()
    }

    fun submit() {
        val s = _ui.value
        if (s.loading) return
        when (s.tab) {
            LoginTab.ACCOUNT -> {
                val (u, p) = s.username.trim() to s.password
                if (u.isEmpty() || p.isEmpty()) { _ui.update { it.copy(error = "请输入用户名和密码") }; return }
                _ui.update { it.copy(loading = true, error = null) }
                viewModelScope.launch {
                    runCatching { authRepo.login(u, p) }
                        .onSuccess { user ->
                            _ui.update { it.copy(loading = false, loggedInUser = user) }
                        }
                        .onFailure { e -> _ui.update { it.copy(loading = false, error = friendlyError(e)) } }
                }
            }
            LoginTab.EMAIL -> {
                val (e, p) = s.email.trim() to s.emailPassword
                if (e.isEmpty() || p.isEmpty()) { _ui.update { it.copy(error = "请输入邮箱和密码") }; return }
                _ui.update { it.copy(loading = true, error = null) }
                viewModelScope.launch {
                    runCatching { authRepo.loginByContact(e, "email", p) }
                        .onSuccess { user ->
                            _ui.update { it.copy(loading = false, loggedInUser = user) }
                        }
                        .onFailure { e2 -> _ui.update { it.copy(loading = false, error = friendlyError(e2)) } }
                }
            }
            LoginTab.PHONE -> {
                val (ph, p) = s.phone.trim() to s.phonePassword
                if (ph.isEmpty() || p.isEmpty()) { _ui.update { it.copy(error = "请输入手机号和密码") }; return }
                _ui.update { it.copy(loading = true, error = null) }
                viewModelScope.launch {
                    runCatching { authRepo.loginByContact(ph, "phone", p) }
                        .onSuccess { user ->
                            _ui.update { it.copy(loading = false, loggedInUser = user) }
                        }
                        .onFailure { e3 -> _ui.update { it.copy(loading = false, error = friendlyError(e3)) } }
                }
            }
            LoginTab.SMS_CODE -> submitSmsLogin()
        }
    }

    fun enterForgot() {
        _ui.update {
            it.copy(
                forgotMode = true,
                forgotStep = ForgotStep.LOADING,
                forgotChannels = null,
                forgotChannelChoice = "",
                forgotTarget = "",
                forgotCaptchaId = "",
                forgotCaptchaImage = "",
                forgotCaptchaAnswer = "",
                forgotCode = "",
                forgotTicket = "",
                forgotNewPassword = "",
                forgotConfirmPassword = "",
                error = null,
                success = null,
            )
        }
        viewModelScope.launch {
            runCatching { authRepo.getVerificationChannels() }
                .onSuccess { ch ->
                    val pick = when {
                        ch.email && ch.sms -> "" // 用户自选
                        ch.email -> "email"
                        ch.sms -> "sms"
                        else -> ""
                    }
                    _ui.update {
                        it.copy(
                            forgotChannels = ch,
                            forgotChannelChoice = pick,
                            forgotStep = if (ch.email || ch.sms) {
                                if (pick.isNotEmpty()) ForgotStep.CAPTCHA else ForgotStep.PICK
                            } else ForgotStep.NO_CHANNEL,
                        )
                    }
                    if (pick.isNotEmpty()) fetchForgotCaptcha()
                }
                .onFailure { e ->
                    _ui.update {
                        it.copy(
                            forgotStep = ForgotStep.NO_CHANNEL,
                            error = friendlyError(e),
                        )
                    }
                }
        }
    }

    fun exitForgot() {
        _ui.update {
            it.copy(
                forgotMode = false,
                forgotStep = ForgotStep.LOADING,
                forgotChannels = null,
                forgotChannelChoice = "",
                forgotTarget = "",
                forgotCaptchaId = "",
                forgotCaptchaImage = "",
                forgotCaptchaAnswer = "",
                forgotCode = "",
                forgotTicket = "",
                forgotNewPassword = "",
                forgotConfirmPassword = "",
                error = null,
                success = null,
            )
        }
    }

    private fun fetchForgotCaptcha() {
        viewModelScope.launch {
            runCatching { authRepo.getCaptcha() }
                .onSuccess { cap ->
                    _ui.update {
                        it.copy(
                            forgotCaptchaId = cap.captchaId,
                            forgotCaptchaImage = cap.imageBase64,
                            forgotCaptchaAnswer = "",
                        )
                    }
                }
                .onFailure { e -> _ui.update { it.copy(error = friendlyError(e)) } }
        }
    }

    fun refreshForgotCaptcha() = fetchForgotCaptcha()

    fun sendForgotCode() {
        val s = _ui.value
        val channel = s.forgotChannelChoice
        if (channel.isEmpty()) { _ui.update { it.copy(error = "请选择找回方式") }; return }
        val target = s.forgotTarget.trim()
        if (target.isBlank()) {
            val label = if (channel == "email") "邮箱" else "手机号"
            _ui.update { it.copy(error = "请输入$label") }; return
        }
        if (s.forgotCaptchaAnswer.isBlank()) { _ui.update { it.copy(error = "请输入图形验证码") }; return }
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            runCatching {
                authRepo.sendVerificationCode(
                    channel = channel,
                    target = target,
                    purpose = "reset_password",
                    captchaId = s.forgotCaptchaId,
                    captchaAnswer = s.forgotCaptchaAnswer.trim(),
                )
            }
                .onSuccess {
                    _ui.update {
                        it.copy(
                            loading = false,
                            forgotStep = ForgotStep.CODE,
                            success = "验证码已发送",
                        )
                    }
                }
                .onFailure { e ->
                    _ui.update {
                        it.copy(loading = false, error = friendlyError(e))
                    }
                    fetchForgotCaptcha()
                }
        }
    }

    fun verifyForgotCode() {
        val s = _ui.value
        if (s.forgotCode.isBlank()) { _ui.update { it.copy(error = "请输入验证码") }; return }
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            runCatching {
                authRepo.verifyCode(
                    channel = s.forgotChannelChoice,
                    target = s.forgotTarget.trim(),
                    purpose = "reset_password",
                    code = s.forgotCode.trim(),
                )
            }
                .onSuccess { resp ->
                    val ticket = resp.ticket
                    if (ticket.isNullOrBlank()) {
                        _ui.update { it.copy(loading = false, error = "验证失败") }
                    } else {
                        _ui.update {
                            it.copy(
                                loading = false,
                                forgotStep = ForgotStep.RESET,
                                forgotTicket = ticket,
                                success = "验证成功，请设置新密码",
                            )
                        }
                    }
                }
                .onFailure { e -> _ui.update { it.copy(loading = false, error = friendlyError(e)) } }
        }
    }

    fun resetPassword() {
        val s = _ui.value
        val p1 = s.forgotNewPassword
        val p2 = s.forgotConfirmPassword
        if (p1.length < 8) { _ui.update { it.copy(error = "密码至少 8 位") }; return }
        if (p1 != p2) { _ui.update { it.copy(error = "两次输入的密码不一致") }; return }
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            runCatching {
                authRepo.resetPassword(s.forgotTicket, p1)
            }
                .onSuccess {
                    _ui.update {
                        it.copy(
                            loading = false,
                            forgotMode = false,
                            forgotStep = ForgotStep.LOADING,
                            forgotTarget = "",
                            forgotCaptchaId = "",
                            forgotCaptchaImage = "",
                            forgotCaptchaAnswer = "",
                            forgotCode = "",
                            forgotTicket = "",
                            forgotNewPassword = "",
                            forgotConfirmPassword = "",
                            success = "密码已重置，请用新密码登录",
                        )
                    }
                }
                .onFailure { e -> _ui.update { it.copy(loading = false, error = friendlyError(e)) } }
        }
    }

    fun clearError() = _ui.update { it.copy(error = null) }
    fun clearSuccess() = _ui.update { it.copy(success = null) }

    private fun friendlyError(e: Throwable): String = when (e) {
        is HttpException -> {
            val raw = runCatching { e.response()?.errorBody()?.string() }.getOrNull()
            val detail = runCatching {
                raw?.let { json.parseToJsonElement(it).jsonObject["detail"]?.jsonPrimitive?.contentOrNull }
            }.getOrNull()
            if (detail != null) errorMessageFromDetail(detail) else e.message() ?: "请求失败，请重试"
        }
        else -> e.message ?: "网络异常，请稍后重试"
    }
}

internal fun errorMessageFromDetail(detail: String?): String = when (detail) {
    "invalid_credentials" -> "用户名或密码错误"
    "captcha_required" -> "登录失败次数过多，请稍后再试或联系管理员重置"
    "captcha_invalid" -> "图形验证码错误或已过期"
    "account_locked" -> "账号已被锁定，请稍后再试"
    "ticket_invalid" -> "验证码已失效，请重新获取"
    "code_invalid" -> "验证码错误或已过期"
    "password_too_weak" -> "密码强度不足，建议 8 位以上含字母与数字"
    else -> "请求失败，请重试"
}

