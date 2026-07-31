package com.unionagents.enduser.ui.mine

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.unionagents.enduser.net.dto.CaptchaResponse
import com.unionagents.enduser.repo.AuthRepository
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.launch

/**
 * 邮箱/手机认证 — channel = "email" | "phone"。
 * 流程：
 *   1. 拉图形验证码 → 用户输入答案
 *   2. 调 sendVerificationCode(purpose=verify_email/verify_phone) 发码到 user.email/phone
 *   3. 用户输入收到的 6 位 code
 *   4. 调 viewModel.verifyEmail/verifyPhone(code) 认证
 * 成功后回退到 AccountSettings。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun VerifyContactScreen(
    channel: String,
    onBack: () -> Unit,
    viewModel: MineViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current

    val (title, target) = when (channel) {
        "email" -> "邮箱认证" to (ui.user?.email?.ifBlank { null } ?: "")
        "phone" -> "手机认证" to (ui.user?.phone?.ifBlank { null } ?: "")
        else -> "认证" to ""
    }
    if (target.isBlank()) {
        // 没有目标联系方式：直接回退（理论上 AccountSettings 不会让进来）
        LaunchedEffect(Unit) { onBack() }
        return
    }

    val purpose = if (channel == "email") "verify_email" else "verify_phone"

    val authRepository = remember {
        EntryPointAccessors.fromApplication(context, AuthRepoEntryPoint::class.java).authRepository()
    }
    val scope = rememberCoroutineScope()

    var captcha by remember { mutableStateOf<CaptchaResponse?>(null) }
    var captchaAnswer by remember { mutableStateOf("") }
    var sendingCode by remember { mutableStateOf(false) }
    var codeSent by remember { mutableStateOf(false) }
    var code by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    // 拉图形验证码
    LaunchedEffect(Unit) {
        try {
            captcha = authRepository.getCaptcha()
        } catch (_: Throwable) {
            error = "图形验证码加载失败"
        }
    }

    LaunchedEffect(ui.profileError) {
        ui.profileError?.let { msg ->
            error = msg
            viewModel.clearProfileError()
        }
    }
    var pendingVerify by remember { mutableStateOf(false) }
    LaunchedEffect(ui.contactVerifying) {
        if (pendingVerify && !ui.contactVerifying && ui.profileError == null) {
            pendingVerify = false
            onBack()
        } else if (pendingVerify && !ui.contactVerifying && ui.profileError != null) {
            pendingVerify = false
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(title) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                    titleContentColor = MaterialTheme.colorScheme.onBackground,
                    navigationIconContentColor = MaterialTheme.colorScheme.onBackground,
                ),
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(padding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = "向 $target 发送验证码",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurface,
            )

            if (!codeSent) {
                // 图形验证码
                val cap = captcha
                if (cap != null) {
                    val bitmap = remember(cap.captchaId) {
                        Base64.decode(cap.imageBase64.substringAfter(","), Base64.DEFAULT)
                            .let { BitmapFactory.decodeByteArray(it, 0, it.size) }
                            ?.asImageBitmap()
                    }
                    if (bitmap != null) {
                        Surface(
                            shape = MaterialTheme.shapes.small,
                            color = MaterialTheme.colorScheme.surface,
                            onClick = {
                                scope.launch {
                                    try { captcha = authRepository.getCaptcha() } catch (_: Throwable) {}
                                }
                            },
                            modifier = Modifier.size(width = 160.dp, height = 60.dp),
                        ) {
                            Image(
                                bitmap = bitmap,
                                contentDescription = "图形验证码",
                                modifier = Modifier.fillMaxWidth().height(60.dp),
                            )
                        }
                        Text(
                            text = "点击图片刷新",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                } else {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                }

                OutlinedTextField(
                    value = captchaAnswer,
                    onValueChange = { captchaAnswer = it; error = null },
                    label = { Text("图形验证码") },
                    singleLine = true,
                    isError = error != null,
                    supportingText = error?.let { { Text(it) } },
                    modifier = Modifier.fillMaxWidth(),
                )

                Button(
                    onClick = {
                        if (captcha == null) { error = "验证码未加载"; return@Button }
                        if (captchaAnswer.isBlank()) { error = "请输入图形验证码"; return@Button }
                        sendingCode = true
                        scope.launch {
                            try {
                                authRepository.sendVerificationCode(
                                    channel = channel,
                                    target = target,
                                    purpose = purpose,
                                    captchaId = captcha!!.captchaId,
                                    captchaAnswer = captchaAnswer.trim(),
                                )
                                codeSent = true
                                error = null
                            } catch (e: Throwable) {
                                error = e.message ?: "发送验证码失败"
                                // 刷新图形验证码
                                try { captcha = authRepository.getCaptcha() } catch (_: Throwable) {}
                            } finally {
                                sendingCode = false
                            }
                        }
                    },
                    enabled = !sendingCode && captchaAnswer.isNotBlank() && captcha != null,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (sendingCode) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(16.dp),
                            strokeWidth = 1.5.dp,
                            color = MaterialTheme.colorScheme.onPrimary,
                        )
                    } else {
                        Text("发送验证码")
                    }
                }
            } else {
                // 已发码 → 输入 6 位 code
                Text(
                    text = "验证码已发送，请输入收到的 6 位数字",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                OutlinedTextField(
                    value = code,
                    onValueChange = { code = it.filter { c -> c.isDigit() }.take(6); error = null },
                    label = { Text("验证码") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    isError = error != null,
                    supportingText = error?.let { { Text(it) } },
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = {
                        if (code.length != 6) { error = "请输入 6 位验证码"; return@Button }
                        pendingVerify = true
                        if (channel == "email") {
                            viewModel.verifyEmail(code)
                        } else {
                            viewModel.verifyPhone(code)
                        }
                    },
                    enabled = !ui.contactVerifying && code.length == 6,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (ui.contactVerifying) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(16.dp),
                            strokeWidth = 1.5.dp,
                            color = MaterialTheme.colorScheme.onPrimary,
                        )
                    } else {
                        Text("提交认证")
                    }
                }
                // 重新发送
                Surface(
                    onClick = {
                        scope.launch {
                            try { captcha = authRepository.getCaptcha() } catch (_: Throwable) {}
                            codeSent = false
                            code = ""
                        }
                    },
                    color = androidx.compose.ui.graphics.Color.Transparent,
                ) {
                    Row(
                        modifier = Modifier.padding(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Filled.Refresh, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.size(4.dp))
                        Text("重新发送", style = MaterialTheme.typography.labelMedium)
                    }
                }
            }
        }
    }
}

@EntryPoint
@InstallIn(SingletonComponent::class)
interface AuthRepoEntryPoint {
    fun authRepository(): AuthRepository
}
