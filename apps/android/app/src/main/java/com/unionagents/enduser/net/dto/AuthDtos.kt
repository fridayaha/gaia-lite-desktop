package com.unionagents.enduser.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
)

@Serializable
data class LoginResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("token_type") val tokenType: String = "bearer",
)

@Serializable
data class RefreshRequest(
    @SerialName("refresh_token") val refreshToken: String,
)

@Serializable
data class RefreshResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String? = null,
)

@Serializable
data class UserInfo(
    val id: String? = null,
    val username: String,
    val email: String? = null,
    val phone: String? = null,
    @SerialName("real_name") val realName: String? = null,
    @SerialName("avatar_url") val avatarUrl: String? = null,
    @SerialName("email_verified") val emailVerified: Boolean = false,
    @SerialName("phone_verified") val phoneVerified: Boolean = false,
)

@Serializable
data class UserSelfUpdateRequest(
    @SerialName("real_name") val realName: String? = null,
    val email: String? = null,
    val phone: String? = null,
    @SerialName("avatar_url") val avatarUrl: String? = null,
)

@Serializable
data class AvatarUploadResponse(
    @SerialName("avatar_url") val avatarUrl: String,
)

@Serializable
data class ChangeContactRequest(
    val code: String,
    @SerialName("new_email") val newEmail: String? = null,
    @SerialName("new_phone") val newPhone: String? = null,
)

@Serializable
data class UserVerifyCodeRequest(
    val code: String,
)

@Serializable
data class LoginByContactRequest(
    val contact: String,
    @SerialName("contact_type") val contactType: String, // "email" | "phone"
    val password: String,
    @SerialName("captcha_id") val captchaId: String? = null,
    @SerialName("captcha_answer") val captchaAnswer: String? = null,
)

@Serializable
data class LoginBySmsCodeRequest(
    val phone: String,
    val code: String,
)

@Serializable
data class CaptchaResponse(
    @SerialName("captcha_id") val captchaId: String,
    @SerialName("image_base64") val imageBase64: String,
)

@Serializable
data class VerificationCodeSendRequest(
    val channel: String, // "email" | "sms"
    val target: String,
    val purpose: String, // "reset_password" | "login" | ...
    @SerialName("captcha_id") val captchaId: String,
    @SerialName("captcha_answer") val captchaAnswer: String,
)

@Serializable
data class VerificationCodeSendResponse(
    val sent: Boolean,
    @SerialName("expires_in") val expiresIn: Int? = null,
)

@Serializable
data class VerificationCodeVerifyRequest(
    val channel: String,
    val target: String,
    val purpose: String,
    val code: String,
)

@Serializable
data class VerificationCodeVerifyResponse(
    val verified: Boolean,
    val ticket: String? = null,
)

@Serializable
data class ResetPasswordRequest(
    val ticket: String,
    @SerialName("new_password") val newPassword: String,
)

@Serializable
data class VerificationChannelsResponse(
    val email: Boolean,
    val sms: Boolean,
)

@Serializable
data class PresetAvatarsResponse(
    val code: Int = 0,
    val message: String? = null,
    val data: PresetAvatarsData? = null,
)

@Serializable
data class PresetAvatarsData(
    val items: List<String> = emptyList(),
)
