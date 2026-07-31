package com.unionagents.enduser.net

import org.junit.Assert.assertEquals
import org.junit.Test

class ServerConfigResolverTest {

    private val defaultManager = "http://default.example.com/api/manager/"
    private val defaultGateway = "http://default.example.com/api/gateway/"

    @Test
    fun `resolves real URLs from patched asset`() {
        val raw = """{"manager_url":"http://ecs.example.com/api/manager/","gateway_url":"http://ecs.example.com/api/gateway/"}"""
        val r = ServerConfigResolver.resolve(raw, defaultManager, defaultGateway)
        assertEquals("http://ecs.example.com/api/manager/", r.managerUrl)
        assertEquals("http://ecs.example.com/api/gateway/", r.gatewayUrl)
    }

    @Test
    fun `falls back when asset still contains placeholders`() {
        val raw = """{"manager_url":"__UA_MANAGER_URL__","gateway_url":"__UA_GATEWAY_URL__"}"""
        val r = ServerConfigResolver.resolve(raw, defaultManager, defaultGateway)
        assertEquals(defaultManager, r.managerUrl)
        assertEquals(defaultGateway, r.gatewayUrl)
    }

    @Test
    fun `falls back when asset is empty json`() {
        val r = ServerConfigResolver.resolve("{}", defaultManager, defaultGateway)
        assertEquals(defaultManager, r.managerUrl)
        assertEquals(defaultGateway, r.gatewayUrl)
    }

    @Test
    fun `falls back per field when one is missing`() {
        val raw = """{"manager_url":"http://ecs.example.com/api/manager/"}"""
        val r = ServerConfigResolver.resolve(raw, defaultManager, defaultGateway)
        assertEquals("http://ecs.example.com/api/manager/", r.managerUrl)
        assertEquals(defaultGateway, r.gatewayUrl)
    }

    @Test
    fun `falls back when raw is null`() {
        val r = ServerConfigResolver.resolve(null, defaultManager, defaultGateway)
        assertEquals(defaultManager, r.managerUrl)
        assertEquals(defaultGateway, r.gatewayUrl)
    }

    @Test
    fun `falls back when json is malformed`() {
        val r = ServerConfigResolver.resolve("not json", defaultManager, defaultGateway)
        assertEquals(defaultManager, r.managerUrl)
        assertEquals(defaultGateway, r.gatewayUrl)
    }

    @Test
    fun `falls back when field is blank`() {
        val raw = """{"manager_url":"","gateway_url":"   "}"""
        val r = ServerConfigResolver.resolve(raw, defaultManager, defaultGateway)
        assertEquals(defaultManager, r.managerUrl)
        assertEquals(defaultGateway, r.gatewayUrl)
    }
}
