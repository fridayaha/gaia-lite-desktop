package com.unionagents.enduser.repo

import com.unionagents.enduser.net.ManagerApi
import com.unionagents.enduser.net.dto.AppReleaseLatest
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AppReleaseRepository @Inject constructor(
    private val managerApi: ManagerApi,
) {
    suspend fun getLatestRelease(): AppReleaseLatest? = managerApi.getLatestRelease()

    suspend fun getReleaseByVersion(version: String): AppReleaseLatest? =
        managerApi.getReleaseByVersion(version)
}
