package com.unionagents.enduser.ui.mine

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/**
 * 通用文本字段编辑页。field 取值：
 * - "real_name" → 真实姓名
 * - "email" → 邮箱
 * - "phone" → 手机
 *
 * 保存调 viewModel.updateProfile。改 email/phone 后 verified 回退 false，UI 自动出现「认证」按钮。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EditTextFieldScreen(
    field: String,
    onBack: () -> Unit,
    viewModel: MineViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()

    val (title, initialValue, keyboardType) = when (field) {
        "real_name" -> Triple("真实姓名", ui.user?.realName ?: "", KeyboardType.Text)
        "email" -> Triple("邮箱", ui.user?.email ?: "", KeyboardType.Email)
        "phone" -> Triple("手机", ui.user?.phone ?: "", KeyboardType.Phone)
        else -> Triple("编辑", "", KeyboardType.Text)
    }

    var value by remember(initialValue) { mutableStateOf(initialValue) }
    var error by remember { mutableStateOf<String?>(null) }
    var pendingSave by remember { mutableStateOf(false) }

    LaunchedEffect(ui.profileError) {
        ui.profileError?.let { msg ->
            error = msg
            pendingSave = false
            viewModel.clearProfileError()
        }
    }
    // profileSaving 由 true→false 且无新 error：保存成功，回退
    LaunchedEffect(ui.profileSaving) {
        if (pendingSave && !ui.profileSaving && ui.profileError == null) {
            pendingSave = false
            onBack()
        }
    }

    val saving = ui.profileSaving

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
            OutlinedTextField(
                value = value,
                onValueChange = {
                    value = it
                    error = null
                },
                label = { Text(title) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
                isError = error != null,
                supportingText = error?.let { { Text(it) } },
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                onClick = {
                    val trimmed = value.trim()
                    if (trimmed.isEmpty()) {
                        error = "不能为空"
                        return@Button
                    }
                    pendingSave = true
                    when (field) {
                        "real_name" -> viewModel.updateProfile(realName = trimmed)
                        "email" -> viewModel.updateProfile(email = trimmed)
                        "phone" -> viewModel.updateProfile(phone = trimmed)
                    }
                },
                enabled = !saving && value.trim() != initialValue.trim(),
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (saving) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(16.dp),
                        strokeWidth = 1.5.dp,
                        color = MaterialTheme.colorScheme.onPrimary,
                    )
                } else {
                    Text("保存")
                }
            }
            if (field == "email" || field == "phone") {
                Text(
                    text = "修改后认证状态会重置，需重新认证。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
