package com.unionagents.enduser.ui.login

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.AlternateEmail
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Mail
import androidx.compose.material.icons.filled.Password
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Verified
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.unionagents.enduser.R
import android.graphics.BitmapFactory
import android.util.Base64

@Composable
fun LoginScreen(
    onLoggedIn: () -> Unit,
    viewModel: LoginViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current

    LaunchedEffect(ui.loggedInUser) {
        if (ui.loggedInUser != null) onLoggedIn()
    }
    LaunchedEffect(ui.error) {
        ui.error?.let {
            android.widget.Toast.makeText(context, it, android.widget.Toast.LENGTH_SHORT).show()
            viewModel.clearError()
        }
    }
    LaunchedEffect(ui.success) {
        ui.success?.let {
            android.widget.Toast.makeText(context, it, android.widget.Toast.LENGTH_SHORT).show()
            viewModel.clearSuccess()
        }
    }

    if (ui.forgotMode) {
        ForgotPasswordContent(ui = ui, viewModel = viewModel)
    } else {
        LoginTabContent(ui = ui, viewModel = viewModel)
    }
}

@Composable
private fun LoginTabContent(
    ui: LoginUiState,
    viewModel: LoginViewModel,
) {
    // 根据后端短信渠道是否启用，决定 tab 列表
    // smsEnabled=false：只展示账号/邮箱/手机 3 个密码登录 tab，不渲染 SMS tab
    // smsEnabled=true ：额外加一个「验证码登录」tab
    val tabs = buildList {
        add(LoginTab.ACCOUNT to "账号")
        add(LoginTab.EMAIL to "邮箱")
        add(LoginTab.PHONE to "手机")
        if (ui.smsEnabled) add(LoginTab.SMS_CODE to "验证码")
    }
    Scaffold { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 24.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = stringResource(R.string.app_name),
                style = MaterialTheme.typography.displayLarge,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.height(32.dp))
            // 用 tabs 的下标作为 selectedTabIndex，避免 SMS_CODE enum.ordinal 与可见 tab 列表错位
            val selectedIndex = tabs.indexOfFirst { it.first == ui.tab }.coerceAtLeast(0)
            TabRow(selectedTabIndex = selectedIndex) {
                tabs.forEachIndexed { index, (tab, label) ->
                    Tab(
                        selected = index == selectedIndex,
                        onClick = { viewModel.onTabChange(tab) },
                        text = { Text(label) },
                    )
                }
            }
            Spacer(Modifier.height(24.dp))
            when (ui.tab) {
                LoginTab.ACCOUNT -> AccountTabForm(ui = ui, viewModel = viewModel)
                LoginTab.EMAIL -> EmailTabForm(ui = ui, viewModel = viewModel)
                LoginTab.PHONE -> PhoneTabForm(ui = ui, viewModel = viewModel)
                LoginTab.SMS_CODE -> SmsCodeTabForm(ui = ui, viewModel = viewModel)
            }
            Spacer(Modifier.height(12.dp))
            TextButton(onClick = viewModel::enterForgot) {
                Text("忘记密码？", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun AccountTabForm(ui: LoginUiState, viewModel: LoginViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(
            value = ui.username,
            onValueChange = viewModel::onUsernameChange,
            label = { Text("用户名") },
            leadingIcon = { Icon(Icons.Filled.Person, contentDescription = null) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = ui.password,
            onValueChange = viewModel::onPasswordChange,
            label = { Text(stringResource(R.string.login_password)) },
            leadingIcon = { Icon(Icons.Filled.Lock, contentDescription = null) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            modifier = Modifier.fillMaxWidth(),
        )
        SubmitButton(ui = ui, text = stringResource(R.string.login_button), onClick = viewModel::submit)
    }
}

@Composable
private fun EmailTabForm(ui: LoginUiState, viewModel: LoginViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(
            value = ui.email,
            onValueChange = viewModel::onEmailChange,
            label = { Text("邮箱地址") },
            leadingIcon = { Icon(Icons.Filled.Mail, contentDescription = null) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Email,
                imeAction = ImeAction.Next,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = ui.emailPassword,
            onValueChange = viewModel::onEmailPasswordChange,
            label = { Text(stringResource(R.string.login_password)) },
            leadingIcon = { Icon(Icons.Filled.Lock, contentDescription = null) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            modifier = Modifier.fillMaxWidth(),
        )
        SubmitButton(ui = ui, text = stringResource(R.string.login_button), onClick = viewModel::submit)
    }
}

@Composable
private fun PhoneTabForm(ui: LoginUiState, viewModel: LoginViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(
            value = ui.phone,
            onValueChange = viewModel::onPhoneChange,
            label = { Text("手机号") },
            leadingIcon = { Icon(Icons.Filled.Phone, contentDescription = null) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Phone,
                imeAction = ImeAction.Next,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = ui.phonePassword,
            onValueChange = viewModel::onPhonePasswordChange,
            label = { Text(stringResource(R.string.login_password)) },
            leadingIcon = { Icon(Icons.Filled.Lock, contentDescription = null) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            modifier = Modifier.fillMaxWidth(),
        )
        SubmitButton(ui = ui, text = stringResource(R.string.login_button), onClick = viewModel::submit)
    }
}

/**
 * 短信验证码登录表单：手机号 + 图形验证码 + 短信验证码 + 「获取验证码」按钮（60s 倒计时）+ 登录按钮。
 * 复用 CaptchaCard 以保持与找回密码流程一致的图形验证码 UI。
 */
@Composable
private fun SmsCodeTabForm(ui: LoginUiState, viewModel: LoginViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(
            value = ui.smsPhone,
            onValueChange = viewModel::onSmsPhoneChange,
            label = { Text("手机号") },
            leadingIcon = { Icon(Icons.Filled.Phone, contentDescription = null) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Phone,
                imeAction = ImeAction.Next,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
        CaptchaCard(
            imageBase64 = ui.smsCaptchaImage,
            answer = ui.smsCaptchaAnswer,
            onAnswerChange = viewModel::onSmsCaptchaAnswerChange,
            onRefresh = viewModel::refreshSmsCaptcha,
        )
        OutlinedTextField(
            value = ui.smsCode,
            onValueChange = viewModel::onSmsCodeChange,
            label = { Text("短信验证码") },
            leadingIcon = { Icon(Icons.Filled.Verified, contentDescription = null) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.NumberPassword,
                imeAction = ImeAction.Done,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
        // 获取验证码按钮：倒计时中禁用，文本切换为「Xs 后可重发」
        val countdown = ui.smsCountdown
        val sendLabel = if (countdown > 0) "${countdown}s 后可重发" else "获取验证码"
        OutlinedButton(
            onClick = viewModel::sendSmsCode,
            enabled = countdown == 0 && !ui.loading,
            modifier = Modifier.fillMaxWidth().height(48.dp),
        ) {
            Text(sendLabel)
        }
        SubmitButton(ui = ui, text = stringResource(R.string.login_button), onClick = viewModel::submitSmsLogin)
    }
}

@Composable
private fun SubmitButton(
    ui: LoginUiState,
    text: String,
    onClick: () -> Unit,
) {
    Button(
        onClick = onClick,
        enabled = !ui.loading,
        modifier = Modifier.fillMaxWidth().height(48.dp),
    ) {
        if (ui.loading) {
            CircularProgressIndicator(
                modifier = Modifier.size(20.dp),
                strokeWidth = 2.dp,
                color = MaterialTheme.colorScheme.onPrimary,
            )
        } else {
            Text(text)
        }
    }
}

@Composable
private fun ForgotPasswordContent(
    ui: LoginUiState,
    viewModel: LoginViewModel,
) {
    Scaffold { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 24.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = "找回密码",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.height(8.dp))
            val stepText = when (ui.forgotStep) {
                ForgotStep.LOADING -> "正在加载..."
                ForgotStep.PICK -> "请选择找回方式"
                ForgotStep.NO_CHANNEL -> "暂无可用的找回方式"
                ForgotStep.CAPTCHA -> "第 1 步：验证身份"
                ForgotStep.CODE -> "第 2 步：输入验证码"
                ForgotStep.RESET -> "第 3 步：设置新密码"
            }
            Text(
                text = stepText,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(24.dp))
            when (ui.forgotStep) {
                ForgotStep.LOADING -> ForgotLoadingStep()
                ForgotStep.PICK -> ForgotPickChannelStep(ui = ui, viewModel = viewModel)
                ForgotStep.NO_CHANNEL -> ForgotNoChannelStep()
                ForgotStep.CAPTCHA -> ForgotCaptchaStep(ui = ui, viewModel = viewModel)
                ForgotStep.CODE -> ForgotCodeStep(ui = ui, viewModel = viewModel)
                ForgotStep.RESET -> ForgotResetStep(ui = ui, viewModel = viewModel)
            }
            Spacer(Modifier.height(12.dp))
            TextButton(onClick = viewModel::exitForgot) {
                Text("返回登录", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun ForgotLoadingStep() {
    Box(
        modifier = Modifier.fillMaxWidth().height(120.dp),
        contentAlignment = Alignment.Center,
    ) {
        CircularProgressIndicator(modifier = Modifier.size(28.dp), strokeWidth = 2.dp)
    }
}

@Composable
private fun ForgotNoChannelStep() {
    Column(
        modifier = Modifier.fillMaxWidth().padding(vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Icon(
            Icons.Filled.Info,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(40.dp),
        )
        Text(
            text = "系统未开启邮箱/短信找回渠道，请联系管理员重置密码",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
    }
}

@Composable
private fun ForgotPickChannelStep(ui: LoginUiState, viewModel: LoginViewModel) {
    val channels = ui.forgotChannels
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        channels?.let {
            if (it.email) {
                ChannelPickCard(
                    icon = Icons.Filled.Mail,
                    title = "邮箱找回",
                    desc = "向您的邮箱发送验证码",
                    onClick = { viewModel.onForgotChannelPick("email") },
                )
            }
            if (it.sms) {
                ChannelPickCard(
                    icon = Icons.Filled.Phone,
                    title = "手机号找回",
                    desc = "向您的手机发送短信验证码",
                    onClick = { viewModel.onForgotChannelPick("sms") },
                )
            }
        }
    }
}

@Composable
private fun ChannelPickCard(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    desc: String,
    onClick: () -> Unit,
) {
    Surface(
        onClick = onClick,
        shape = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
        color = MaterialTheme.colorScheme.surface,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Icon(
                icon,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(28.dp),
            )
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = desc,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Icon(
                Icons.AutoMirrored.Filled.KeyboardArrowRight,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(20.dp),
            )
        }
    }
}

@Composable
private fun ForgotCaptchaStep(ui: LoginUiState, viewModel: LoginViewModel) {
    val isEmail = ui.forgotChannelChoice == "email"
    val targetLabel = if (isEmail) "邮箱地址" else "手机号"
    val targetIcon = if (isEmail) Icons.Filled.Mail else Icons.Filled.Phone
    val targetKeyboard = if (isEmail) KeyboardType.Email else KeyboardType.Phone
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(
            value = ui.forgotTarget,
            onValueChange = viewModel::onForgotTargetChange,
            label = { Text(targetLabel) },
            leadingIcon = { Icon(targetIcon, contentDescription = null) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = targetKeyboard,
                imeAction = ImeAction.Next,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
        CaptchaCard(
            imageBase64 = ui.forgotCaptchaImage,
            answer = ui.forgotCaptchaAnswer,
            onAnswerChange = viewModel::onForgotCaptchaAnswerChange,
            onRefresh = viewModel::refreshForgotCaptcha,
        )
        SubmitButton(ui = ui, text = "发送验证码", onClick = viewModel::sendForgotCode)
    }
}

@Composable
private fun ForgotCodeStep(ui: LoginUiState, viewModel: LoginViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            text = "验证码已发送至 ${ui.forgotTarget}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedTextField(
            value = ui.forgotCode,
            onValueChange = viewModel::onForgotCodeChange,
            label = { Text("验证码") },
            leadingIcon = { Icon(Icons.Filled.Verified, contentDescription = null) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.NumberPassword,
                imeAction = ImeAction.Done,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
        SubmitButton(ui = ui, text = "下一步", onClick = viewModel::verifyForgotCode)
    }
}

@Composable
private fun ForgotResetStep(ui: LoginUiState, viewModel: LoginViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(
            value = ui.forgotNewPassword,
            onValueChange = viewModel::onForgotNewPasswordChange,
            label = { Text("新密码（≥8 位）") },
            leadingIcon = { Icon(Icons.Filled.Password, contentDescription = null) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = ui.forgotConfirmPassword,
            onValueChange = viewModel::onForgotConfirmPasswordChange,
            label = { Text("确认新密码") },
            leadingIcon = { Icon(Icons.Filled.Lock, contentDescription = null) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            modifier = Modifier.fillMaxWidth(),
        )
        SubmitButton(ui = ui, text = "重置密码", onClick = viewModel::resetPassword)
    }
}

@Composable
private fun CaptchaCard(
    imageBase64: String,
    answer: String,
    onAnswerChange: (String) -> Unit,
    onRefresh: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        val bitmap = remember(imageBase64) {
            runCatching {
                val raw = imageBase64.substringAfter("base64,")
                val data = Base64.decode(raw, Base64.DEFAULT)
                BitmapFactory.decodeByteArray(data, 0, data.size)
            }.getOrNull()
        }
        if (bitmap != null) {
            Image(
                bitmap = bitmap.asImageBitmap(),
                contentDescription = "图形验证码",
                modifier = Modifier.size(width = 120.dp, height = 48.dp),
            )
        } else {
            Surface(
                modifier = Modifier.size(width = 120.dp, height = 48.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                Box(
                    contentAlignment = Alignment.Center,
                    modifier = Modifier.fillMaxSize(),
                ) {
                    Text("加载中…", style = MaterialTheme.typography.labelSmall)
                }
            }
        }
        IconButton(onClick = onRefresh, modifier = Modifier.size(40.dp)) {
            Icon(Icons.Filled.Refresh, contentDescription = "刷新验证码")
        }
        OutlinedTextField(
            value = answer,
            onValueChange = onAnswerChange,
            label = { Text("图形验证码") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Ascii,
                imeAction = ImeAction.Done,
            ),
            modifier = Modifier.weight(1f),
        )
    }
}


