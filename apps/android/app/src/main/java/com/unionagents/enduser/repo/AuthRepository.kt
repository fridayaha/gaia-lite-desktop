package com.unionagents.enduser.repo

import com.unionagents.enduser.net.ManagerApi
import com.unionagents.enduser.net.TokenData
import com.unionagents.enduser.net.TokenStorage
import com.unionagents.enduser.net.dto.CaptchaResponse
import com.unionagents.enduser.net.dto.UserInfo
import com.unionagents.enduser.net.dto.VerificationChannelsResponse
import com.unionagents.enduser.net.dto.VerificationCodeSendRequest
import com.unionagents.enduser.net.dto.VerificationCodeSendResponse
import com.unionagents.enduser.net.dto.VerificationCodeVerifyRequest
import com.unionagents.enduser.net.dto.VerificationCodeVerifyResponse
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody.Companion.asRequestBody
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AuthRepository @Inject constructor(
    private val managerApi: ManagerApi,
    private val tokenStorage: TokenStorage,
    private val accountStore: AccountStore,
) {
    val tokenFlow = tokenStorage.tokenFlow

    suspend fun login(username: String, password: String): UserInfo {
        preserveCurrentAccountIfNeeded()
        val resp = managerApi.login(
            com.unionagents.enduser.net.dto.LoginRequest(username, password),
        )
        val token = TokenData(resp.accessToken, resp.refreshToken)
        tokenStorage.save(token)
        val me = managerApi.getMe()
        accountStore.saveAccount(me, token)
        return me
    }

    suspend fun loginByContact(contact: String, contactType: String, password: String): UserInfo {
        preserveCurrentAccountIfNeeded()
        val resp = managerApi.loginByContact(
            com.unionagents.enduser.net.dto.LoginByContactRequest(
                contact = contact,
                contactType = contactType,
                password = password,
            ),
        )
        val token = TokenData(resp.accessToken, resp.refreshToken)
        tokenStorage.save(token)
        val me = managerApi.getMe()
        accountStore.saveAccount(me, token)
        return me
    }

    suspend fun loginBySmsCode(phone: String, code: String): UserInfo {
        preserveCurrentAccountIfNeeded()
        val resp = managerApi.loginBySmsCode(
            com.unionagents.enduser.net.dto.LoginBySmsCodeRequest(phone, code),
        )
        val token = TokenData(resp.accessToken, resp.refreshToken)
        tokenStorage.save(token)
        val me = managerApi.getMe()
        accountStore.saveAccount(me, token)
        return me
    }

    /**
     * 切账号登录前，若本地有当前 token，先把最新 token 写回 AccountStore，
     * 避免新登录覆盖 tokenStorage 后旧账号 token 丢失、无法再切回。
     */
    private suspend fun preserveCurrentAccountIfNeeded() {
        val currentToken = tokenStorage.get() ?: return
        val currentUser = runCatching { managerApi.getMe() }.getOrNull() ?: return
        accountStore.saveAccount(currentUser, currentToken)
    }

    suspend fun switchToAccount(userId: String): UserInfo? {
        val account = accountStore.getAccount(userId) ?: return null
        val token = TokenData(account.accessToken, account.refreshToken)
        tokenStorage.save(token)
        return runCatching { managerApi.getMe() }.getOrNull()
    }

    suspend fun forgetAccount(userId: String) {
        accountStore.removeAccount(userId)
    }

    suspend fun getCaptcha(): CaptchaResponse = managerApi.getCaptcha()

    suspend fun sendVerificationCode(
        channel: String,
        target: String,
        purpose: String,
        captchaId: String,
        captchaAnswer: String,
    ): VerificationCodeSendResponse = managerApi.sendVerificationCode(
        VerificationCodeSendRequest(
            channel = channel,
            target = target,
            purpose = purpose,
            captchaId = captchaId,
            captchaAnswer = captchaAnswer,
        ),
    )

    suspend fun verifyCode(
        channel: String,
        target: String,
        purpose: String,
        code: String,
    ): VerificationCodeVerifyResponse = managerApi.verifyCode(
        VerificationCodeVerifyRequest(channel, target, purpose, code),
    )

    suspend fun resetPassword(ticket: String, newPassword: String) {
        managerApi.resetPassword(
            com.unionagents.enduser.net.dto.ResetPasswordRequest(ticket, newPassword),
        )
    }

    suspend fun getVerificationChannels(): VerificationChannelsResponse =
        managerApi.getVerificationChannels()

    suspend fun me(): UserInfo = managerApi.getMe()

    /**
     * 自服务改资料（real_name / email / phone / avatar_url）。
     * 改 email/phone 后 verified 回退 false，需再走 verify 流程。
     * 成功后返回最新 user。
     */
    suspend fun updateSelfProfile(
        realName: String? = null,
        email: String? = null,
        phone: String? = null,
        avatarUrl: String? = null,
    ): UserInfo {
        val body = com.unionagents.enduser.net.dto.UserSelfUpdateRequest(
            realName = realName,
            email = email,
            phone = phone,
            avatarUrl = avatarUrl,
        )
        managerApi.updateMe(body)
        return managerApi.getMe()
    }

    /**
     * 预置头像列表（GET /auth/preset-avatars，返回 12 个 MinIO 相对路径）。
     * 选其中一张后走 [updateSelfProfile] 把路径直接 PATCH 到 /auth/me。
     */
    suspend fun getPresetAvatars(): List<String> =
        managerApi.getPresetAvatars().data?.items ?: emptyList()

    /**
     * 上传头像文件（image/png|jpeg|webp|gif ≤ 2MB），后端写 MinIO public bucket 后返回 URL。
     * 成功后返回最新 user。
     */
    suspend fun uploadAvatar(file: java.io.File): UserInfo {
        val mediaType = file.path.substringAfterLast(".", "png").lowercase()
            .let { ext ->
                when (ext) {
                    "jpg", "jpeg" -> "image/jpeg"
                    "png" -> "image/png"
                    "webp" -> "image/webp"
                    "gif" -> "image/gif"
                    else -> "image/png"
                }
            }
        val requestFile = file.asRequestBody(mediaType.toMediaTypeOrNull())
        val multipart = okhttp3.MultipartBody.Part.createFormData(
            "file",
            file.name,
            requestFile,
        )
        managerApi.uploadAvatar(multipart)
        return managerApi.getMe()
    }

    /**
     * 改绑邮箱 — 先用 sendVerificationCode(purpose=change_email) 发码到 newEmail，
     * 用户输入 code 调本方法。成功后返回最新 user（email_verified=false 需重新认证）。
     */
    suspend fun changeEmail(newEmail: String, code: String): UserInfo {
        managerApi.changeEmail(
            com.unionagents.enduser.net.dto.ChangeContactRequest(
                code = code,
                newEmail = newEmail,
            ),
        )
        return managerApi.getMe()
    }

    suspend fun changePhone(newPhone: String, code: String): UserInfo {
        managerApi.changePhone(
            com.unionagents.enduser.net.dto.ChangeContactRequest(
                code = code,
                newPhone = newPhone,
            ),
        )
        return managerApi.getMe()
    }

    /**
     * 认证当前邮箱 — 先用 sendVerificationCode(purpose=verify_email) 发码到 user.email，
     * 用户输入 code 调本方法。成功后 email_verified=true。
     */
    suspend fun verifyEmail(code: String): UserInfo {
        managerApi.verifyEmail(
            com.unionagents.enduser.net.dto.UserVerifyCodeRequest(code = code),
        )
        return managerApi.getMe()
    }

    suspend fun verifyPhone(code: String): UserInfo {
        managerApi.verifyPhone(
            com.unionagents.enduser.net.dto.UserVerifyCodeRequest(code = code),
        )
        return managerApi.getMe()
    }

    suspend fun logout() {
        tokenStorage.clear()
    }
}
