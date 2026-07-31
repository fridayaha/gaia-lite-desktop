package com.unionagents.enduser.ui.login

import com.unionagents.enduser.net.dto.UserInfo
import com.unionagents.enduser.net.dto.VerificationChannelsResponse

enum class LoginTab { ACCOUNT, EMAIL, PHONE, SMS_CODE }

enum class ForgotStep { LOADING, PICK, CAPTCHA, CODE, RESET, NO_CHANNEL }

data class LoginUiState(
    val tab: LoginTab = LoginTab.ACCOUNT,
    val smsEnabled: Boolean = false,  // 后端 verification-channels.sms，未启用时隐藏 SMS_CODE tab
    val smsPhone: String = "",
    val smsCode: String = "",
    val smsCaptchaId: String = "",
    val smsCaptchaImage: String = "",
    val smsCaptchaAnswer: String = "",
    val smsCountdown: Int = 0,  // 倒计时秒数，0 表示可重发
    val smsCodeSent: Boolean = false,  // 已发送验证码（控制 SMS code 输入框可见性）
    val username: String = "",
    val password: String = "",
    val email: String = "",
    val emailPassword: String = "",
    val phone: String = "",
    val phonePassword: String = "",
    val forgotMode: Boolean = false,
    val forgotStep: ForgotStep = ForgotStep.LOADING,
    val forgotChannels: VerificationChannelsResponse? = null,
    val forgotChannelChoice: String = "",
    val forgotTarget: String = "",
    val forgotCaptchaId: String = "",
    val forgotCaptchaImage: String = "",
    val forgotCaptchaAnswer: String = "",
    val forgotCode: String = "",
    val forgotTicket: String = "",
    val forgotNewPassword: String = "",
    val forgotConfirmPassword: String = "",
    val loading: Boolean = false,
    val error: String? = null,
    val success: String? = null,
    val loggedInUser: UserInfo? = null,
)
