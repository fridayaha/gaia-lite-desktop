package com.unionagents.enduser.net

import kotlinx.coroutines.flow.Flow

data class TokenData(
    val accessToken: String,
    val refreshToken: String,
)

interface TokenStorage {
    val tokenFlow: Flow<TokenData?>
    suspend fun save(token: TokenData)
    suspend fun get(): TokenData?
    suspend fun getAccessToken(): String?
    suspend fun getRefreshToken(): String?
    suspend fun clear()
    suspend fun refresh(): String?
}
