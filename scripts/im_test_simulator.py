#!/usr/bin/env python3
"""
IM 平台回调模拟器 — 本地开发测试工具
独立运行，不参与生产构建。

启动：cd scripts && uvicorn im_test_simulator:app --port 8899 --reload
访问：http://localhost:8899
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urlencode

import httpx
from Crypto.Cipher import AES  # pycryptodome
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("im-simulator")

# ── FastAPI App ──────────────────────────────────────────────

app = FastAPI(title="IM 平台回调模拟器", version="1.0.0")

# ── DB 连接 ──────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "UA_DATABASE_URL",
    "postgresql+asyncpg://unionagents:change-me@localhost:5432/unionagents",
)


async def get_db() -> AsyncSession:
    engine = create_async_engine(DATABASE_URL)
    async with async_sessionmaker(engine, class_=AsyncSession)() as session:
        yield session
    await engine.dispose()


# ── 数据模型 ─────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    channel_type: str = Field(..., description="wecom / feishu / dingtalk")
    agent_id: str = Field(..., description="Agent 的 UUID")
    user_id: str = Field(..., description="IM 平台用户 ID")
    text: str = Field("你好", description="消息内容")
    user_name: str = ""
    gateway_url: str = "http://localhost:8010"
    # WeCom 专用
    wecom_corp_id: str = "ww-test"
    wecom_agent_id: str = "1000001"
    wecom_token: str = "test-token"
    wecom_encoding_aes_key: str = ""
    # Feishu 专用 (encrypt_key 为空时不加密)
    feishu_encrypt_key: str = ""
    feishu_app_id: str = "cli_test"
    # DingTalk 专用
    dingtalk_app_secret: str = "test-app-secret"


# ═══════════════════════════════════════════════════════════════
# Webhook Payload Builders
# ═══════════════════════════════════════════════════════════════

def _aes_pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    pad_len = block_size - len(data) % block_size
    return data + bytes([pad_len] * pad_len)


def _wecom_encrypt_xml(plain_xml: str, encoding_aes_key: str, corp_id: str) -> str:
    """加密 XML 并包装为 WeCom 回调格式"""
    aes_key = base64.b64decode(encoding_aes_key + "=")
    # 构造待加密明文: random_16_bytes + network_bytes_order_len + plain_xml + corp_id
    rand_bytes = os.urandom(16)
    msg_len = len(plain_xml.encode("utf-8"))
    raw = rand_bytes + msg_len.to_bytes(4, "big") + plain_xml.encode("utf-8") + corp_id.encode("utf-8")
    raw = _aes_pkcs7_pad(raw)
    cipher = AES.new(aes_key, AES.MODE_CBC, iv=aes_key[:16])
    encrypted = cipher.encrypt(raw)
    return base64.b64encode(encrypted).decode("utf-8")


def _wecom_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """WeCom SHA1 签名"""
    items = sorted([token, timestamp, nonce, encrypt])
    raw = "".join(items)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def build_wecom_payload(
    agent_id: str,
    user_id: str,
    text: str,
    wecom_token: str,
    wecom_encoding_aes_key: str,
    corp_id: str = "ww-test",
    wecom_agent_id: str = "1000001",
) -> tuple[dict[str, str], str, str, str]:
    """构建 WeCom 回调请求的参数、body、headers"""
    inner_xml = f"""<xml>
    <ToUserName><![CDATA[{corp_id}]]></ToUserName>
    <FromUserName><![CDATA[{user_id}]]></FromUserName>
    <CreateTime>{int(time.time())}</CreateTime>
    <MsgType><![CDATA[text]]></MsgType>
    <Content><![CDATA[{text}]]></Content>
    <MsgId>{int(time.time() * 1000)}</MsgId>
    <AgentID>{wecom_agent_id}</AgentID>
</xml>"""

    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4().int)[:8]

    if wecom_encoding_aes_key:
        encrypt = _wecom_encrypt_xml(inner_xml, wecom_encoding_aes_key, corp_id)
        msg_signature = _wecom_signature(wecom_token, timestamp, nonce, encrypt)
        outer_xml = f"""<xml>
    <Encrypt><![CDATA[{encrypt}]]></Encrypt>
    <MsgSignature><![CDATA[{msg_signature}]]></MsgSignature>
    <TimeStamp>{timestamp}</TimeStamp>
    <Nonce>{nonce}</Nonce>
</xml>"""
        params = {"msg_signature": msg_signature, "timestamp": timestamp, "nonce": nonce}
        body = outer_xml
        content_type = "application/xml"
    else:
        # 不加密 — 直接发明文 XML（需 Gateway 未启用签名验证）
        params = {}
        body = inner_xml
        content_type = "application/xml"

    headers = {"Content-Type": content_type}
    return params, body, headers, "xml"


def build_feishu_payload(
    agent_id: str,
    user_id: str,
    text: str,
    user_name: str = "",
    encrypt_key: str = "",
    app_id: str = "cli_test",
) -> tuple[dict[str, str], str, dict[str, str], str]:
    """构建 Feishu 回调请求的参数、body、headers"""
    event = {
        "schema": "2.0",
        "header": {
            "event_id": f"evt_{uuid.uuid4().hex[:16]}",
            "event_type": "im.message.receive_v1",
            "create_time": str(int(time.time())),
            "app_id": app_id,
        },
        "event": {
            "message": {
                "chat_id": user_id,
                "chat_type": "p2p",
                "message_id": f"om_{uuid.uuid4().hex[:16]}",
                "message_type": "text",
                "content": json.dumps({"text": text}),
            },
            "sender": {
                "sender_id": {"open_id": user_id, "union_id": f"uu_{uuid.uuid4().hex[:8]}"},
                "name": user_name or user_id,
            },
        },
    }

    if encrypt_key:
        # 加密模式
        aes_key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
        cipher = AES.new(aes_key, AES.MODE_CBC, iv=aes_key[:16])
        raw = _aes_pkcs7_pad(json.dumps(event).encode("utf-8"), block_size=16)
        encrypted = base64.b64encode(cipher.encrypt(raw)).decode("utf-8")
        body = json.dumps({"encrypt": encrypted})
    else:
        body = json.dumps(event)

    return {}, body, {"Content-Type": "application/json"}, "json"


def build_dingtalk_payload(
    agent_id: str,
    user_id: str,
    text: str,
    user_name: str = "",
    app_secret: str = "test-app-secret",
) -> tuple[dict[str, str], str, dict[str, str], str]:
    """构建 DingTalk 回调请求的参数、body、headers"""
    timestamp = str(int(time.time()))
    sign = base64.b64encode(
        hmac.new(app_secret.encode("utf-8"), f"{timestamp}\n".encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

    event = {
        "conversationId": f"cid_{uuid.uuid4().hex[:12]}",
        "conversationType": "1",
        "msgId": f"msg_{uuid.uuid4().hex[:16]}",
        "senderId": user_id,
        "senderNick": user_name or user_id,
        "msgtype": "text",
        "text": {"content": f" {text} "},
        "robotCode": agent_id[:8],
        "isInAtList": True,
    }

    headers = {
        "Content-Type": "application/json",
        "timestamp": timestamp,
        "sign": sign,
    }
    return {}, json.dumps(event), headers, "json"


# ═══════════════════════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════════════════════

@app.post("/api/simulate")
async def simulate(req: SimulateRequest):
    """模拟 IM 平台发送回调请求到 Gateway"""
    agent_id = req.agent_id
    # 构造不同平台的 payload
    if req.channel_type == "wecom":
        params, body, headers, fmt = build_wecom_payload(
            agent_id, req.user_id, req.text,
            req.wecom_token, req.wecom_encoding_aes_key,
            req.wecom_corp_id, req.wecom_agent_id,
        )
        url = f"{req.gateway_url}/api/gateway/channel/wecom/{agent_id}/callback"
    elif req.channel_type == "feishu":
        params, body, headers, fmt = build_feishu_payload(
            agent_id, req.user_id, req.text,
            req.user_name, req.feishu_encrypt_key, req.feishu_app_id,
        )
        url = f"{req.gateway_url}/api/gateway/channel/feishu/{agent_id}/callback"
    elif req.channel_type == "dingtalk":
        params, body, headers, fmt = build_dingtalk_payload(
            agent_id, req.user_id, req.text,
            req.user_name, req.dingtalk_app_secret,
        )
        url = f"{req.gateway_url}/api/gateway/channel/dingtalk/{agent_id}/callback"
    else:
        raise HTTPException(status_code=400, detail=f"不支持的 channel_type: {req.channel_type}")

    # 发送请求
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, params=params, content=body, headers=headers)
        return JSONResponse(
            content={
                "status": resp.status_code,
                "body": resp.text[:2000] if resp.text else "(empty)",
                "send_to": url,
            },
            status_code=200 if resp.status_code < 500 else 502,
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"请求 Gateway 失败: {e}")


@app.get("/api/agents")
async def list_agents():
    """获取已发布的 Agent 列表"""
    try:
        return await _query_rows("SELECT id, name, status FROM agents ORDER BY name")
    except Exception as e:
        logger.warning(f"DB query failed: {e}")
        return _fallback_agents()


@app.get("/api/users")
async def list_users():
    """获取用户列表"""
    try:
        return await _query_rows("SELECT id, username FROM users ORDER BY username")
    except Exception as e:
        logger.warning(f"DB query failed: {e}")
        return _fallback_users()


async def _query_rows(sql: str) -> list[dict]:
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        result = await conn.execute(text(sql))
        rows = [dict(row._mapping) for row in result]
        for r in rows:
            for k, v in r.items():
                if isinstance(v, uuid.UUID):
                    r[k] = str(v)
    await engine.dispose()
    return rows


def _fallback_agents():
    return [
        {"id": "d38e436e-a4ae-4706-8b24-93e3f9d7bd15", "name": "门店助手", "status": "PUBLISHED"},
        {"id": "07a72439-0417-4455-a11e-ba4035ec8d4c", "name": "店长助理", "status": "PUBLISHED"},
        {"id": "824c3b84-56eb-40ca-aa7d-0a7772b508d5", "name": "猛士销售智能体-光谷", "status": "PUBLISHED"},
    ]


def _fallback_users():
    return [
        {"id": "39c9e118-9efb-48a1-91d3-f9607307c2e3", "username": "MengLiang"},
        {"id": "21d72d5b-5e0a-40fd-ae5e-1a8f8bca26e2", "username": "LiaoQiWang"},
        {"id": "0f907756-4f58-40f5-8a7a-362b8d37fe3e", "username": "GuoRan"},
        {"id": "72de0272-6f92-4524-9d9c-b72fee1bae05", "username": "LiZhe"},
        {"id": "4c00f5e8-09ec-4d37-a044-d82325ce3d16", "username": "YanHuaYiLeng"},
    ]


@app.get("/api/agent-channels/{agent_id}")
async def get_agent_channels(agent_id: str):
    """获取 Agent 的 IM 渠道配置"""
    try:
        rows = await _query_rows(
            f"SELECT channel_type, config, profile_type FROM agent_channels WHERE agent_id = '{agent_id}' AND enabled = true"
        )
        return rows
    except Exception as e:
        logger.warning(f"DB query failed: {e}")
        return [
            {"channel_type": "wecom", "config": "{}", "profile_type": "INDEPENDENT"},
            {"channel_type": "feishu", "config": "{}", "profile_type": "INDEPENDENT"},
            {"channel_type": "dingtalk", "config": "{}", "profile_type": "INDEPENDENT"},
        ]


# ═══════════════════════════════════════════════════════════════
# 测试页面
# ═══════════════════════════════════════════════════════════════

HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IM 平台回调模拟器</title>
<style>
  :root {
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --text-dim: #94a3b8; --accent: #3b82f6;
    --accent-hover: #2563eb; --success: #22c55e; --error: #ef4444;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--text); min-height: 100vh; }
  .container { max-width: 960px; margin: 0 auto; padding: 24px 16px; }

  .header { margin-bottom: 28px; }
  .header h1 { font-size: 22px; font-weight: 700; }
  .header p { color: var(--text-dim); font-size: 13px; margin-top: 4px; }

  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 10px; padding: 20px; margin-bottom: 16px; }
  .card-title { font-size: 14px; font-weight: 600; margin-bottom: 14px;
                padding-bottom: 10px; border-bottom: 1px solid var(--border); }

  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .form-grid .full { grid-column: 1 / -1; }
  label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 4px; font-weight: 500; }
  select, input, textarea {
    width: 100%; padding: 8px 10px; font-size: 13px;
    background: #0f172a; border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); outline: none; transition: border-color .15s;
  }
  select:focus, input:focus, textarea:focus { border-color: var(--accent); }
  textarea { font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px; resize: vertical; min-height: 50px; }

  .tabs { display: flex; gap: 2px; margin-bottom: 14px; }
  .tab { padding: 6px 16px; font-size: 13px; border-radius: 6px 6px 0 0;
         cursor: pointer; background: var(--bg); color: var(--text-dim);
         border: 1px solid var(--border); border-bottom: none; transition: all .15s; }
  .tab.active { background: var(--surface); color: var(--text); font-weight: 600; }

  .btn { padding: 8px 20px; font-size: 13px; font-weight: 500; border: none;
         border-radius: 6px; cursor: pointer; transition: all .15s; }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-primary:hover { background: var(--accent-hover); }
  .btn-primary:disabled { opacity: .5; cursor: not-allowed; }

  .status-bar { display: flex; align-items: center; gap: 8px;
                padding: 10px 14px; border-radius: 6px; font-size: 13px;
                margin-top: 14px; display: none; }
  .status-bar.success { display: flex; background: #166534; border: 1px solid var(--success); }
  .status-bar.error { display: flex; background: #7f1d1d; border: 1px solid var(--error); }
  .status-bar .spinner { width: 14px; height: 14px; border: 2px solid var(--text-dim);
                         border-top-color: var(--accent); border-radius: 50%;
                         animation: spin .6s linear infinite; display: none; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .response-box { display: none; }
  .response-box.open { display: block; }
  .response-box pre { background: #0f172a; border: 1px solid var(--border);
                      border-radius: 6px; padding: 12px; font-size: 11px;
                      max-height: 300px; overflow: auto; margin-top: 8px; }

  .pill { display: inline-flex; align-items: center; gap: 4px;
          padding: 2px 8px; border-radius: 4px; font-size: 11px;
          background: var(--bg); color: var(--text-dim); cursor: pointer; }
  .pill:hover { color: var(--text); }
  .pill.active { background: var(--accent); color: #fff; }

  .section-label { font-size: 12px; font-weight: 600; color: var(--text-dim);
                   margin-top: 12px; margin-bottom: 4px; text-transform: uppercase;
                   letter-spacing: .5px; }

  .hl { color: var(--accent); font-weight: 500; }
  .error-text { color: var(--error); }
  .success-text { color: var(--success); }
</style>
</head>
<body>
<div class="container" id="app">
  <div class="header">
    <h1>🔄 IM 平台回调模拟器</h1>
    <p>模拟 WeCom / 飞书 / 钉钉 发送消息到 Gateway，用于本地测试 IM 渠道和多 Profile 链路</p>
  </div>

  <!-- 选择平台 -->
  <div class="card">
    <div class="card-title">选择平台</div>
    <div class="tabs">
      <div class="tab active" data-tab="feishu" @click="switchTab('feishu')">💬 飞书</div>
      <div class="tab" data-tab="wecom" @click="switchTab('wecom')">💼 企业微信</div>
      <div class="tab" data-tab="dingtalk" @click="switchTab('dingtalk')">📱 钉钉</div>
    </div>
    <p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">
      提示: 飞书最易模拟（无需加密签名），WeCom 需要 encoding_aes_key，钉钉需要 app_secret。
      Gateway 必须配置了对应的 IM 渠道（agent_channels 表）才能接收回调。
    </p>
  </div>

  <!-- 消息参数 -->
  <div class="card">
    <div class="card-title">消息参数</div>
    <div class="form-grid">
      <div class="full">
        <label>Gateway 地址</label>
        <input v-model="gatewayUrl" placeholder="http://localhost:8010" />
      </div>
      <div>
        <label>选择 Agent <span class="hl">*</span></label>
        <select v-model="agentId" @change="onAgentChange">
          <option value="">-- 加载中 --</option>
          <option v-for="a in agents" :value="a.id">{{ a.name }} ({{ a.status }})</option>
        </select>
        <div style="margin-top:4px">
          <span class="pill" @click="openAgentDialog">自定义 Agent ID</span>
        </div>
      </div>
      <div>
        <label>渠道类型</label>
        <select v-model="channelType">
          <option value="feishu">飞书</option>
          <option value="wecom">企业微信</option>
          <option value="dingtalk">钉钉</option>
        </select>
      </div>
      <div>
        <label>IM 用户 ID <span class="hl">*</span></label>
        <select v-model="imUserId">
          <option value="">-- 选择用户 --</option>
          <option v-for="u in users" :value="u.id">{{ u.username }}</option>
        </select>
        <div style="margin-top:4px">
          <span class="pill" @click="imUserId = 'test_user_' + Date.now()">随机生成</span>
        </div>
      </div>
      <div>
        <label>用户显示名</label>
        <input v-model="userName" placeholder="可选" />
      </div>
      <div>
        <label>IM 绑定映射</label>
        <select v-model="imBindingUserId">
          <option value="">-- 不绑定（使用原始 IM ID）--</option>
          <option v-for="u in users" :value="u.username + ':' + u.id">{{ u.username }}</option>
        </select>
        <div style="font-size:11px;color:var(--text-dim);margin-top:2px">
          选择后将自动调用 Manager API 创建 ''im_user_bindings'' 记录
        </div>
      </div>
      <div class="full">
        <label>消息内容</label>
        <textarea v-model="messageText" rows="2" placeholder="输入消息文本"></textarea>
      </div>
    </div>
  </div>

  <!-- 平台专用配置 -->
  <div class="card" id="platform-config">
    <div class="card-title">平台专用配置</div>

    <!-- Feishu -->
    <div class="form-grid" v-show="channelType === 'feishu'">
      <div>
        <label>Encrypt Key</label>
        <input v-model="feishuEncryptKey" placeholder="留空则不加密" />
      </div>
      <div>
        <label>App ID</label>
        <input v-model="feishuAppId" placeholder="cli_test" />
      </div>
    </div>

    <!-- WeCom -->
    <div class="form-grid" v-show="channelType === 'wecom'">
      <div>
        <label>Token</label>
        <input v-model="wecomToken" placeholder="test-token" />
      </div>
      <div>
        <label>Encoding AES Key</label>
        <input v-model="wecomEncodingAesKey" placeholder="留空则不加密（需 Gateway 关闭验证）" />
      </div>
      <div>
        <label>Corp ID</label>
        <input v-model="wecomCorpId" placeholder="ww-test" />
      </div>
      <div>
        <label>WeCom Agent ID</label>
        <input v-model="wecomAgentId" placeholder="1000001" />
      </div>
    </div>

    <!-- DingTalk -->
    <div class="form-grid" v-show="channelType === 'dingtalk'">
      <div>
        <label>App Secret</label>
        <input v-model="dingtalkAppSecret" placeholder="test-app-secret" />
      </div>
    </div>
  </div>

  <!-- 操作 -->
  <div style="display:flex;gap:8px;margin-bottom:16px">
    <button class="btn btn-primary" @click="sendMessage" :disabled="sending">
      <span v-if="sending">发送中...</span>
      <span v-else>🚀 发送测试消息</span>
    </button>
    <button class="btn" style="background:var(--bg);color:var(--text);border:1px solid var(--border)" @click="clearHistory">
      清空历史
    </button>
  </div>

  <!-- 状态 -->
  <div class="status-bar" :class="{ success: statusOk, error: !statusOk }" v-show="showStatus">
    <div class="spinner" v-show="sending"></div>
    <span>{{ statusText }}</span>
  </div>

  <!-- 响应 -->
  <div class="response-box" :class="{ open: showResponse }">
    <div class="card-title">响应详情</div>
    <pre>{{ responseJson }}</pre>
  </div>
</div>

<script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
<script>
// 🧩 Vue 3 app
const { createApp, ref, reactive, computed, onMounted, watch } = Vue;

createApp({
  setup() {
    const agents = ref([]);
    const users = ref([]);
    const channelType = ref('feishu');
    const agentId = ref('');
    const gatewayUrl = ref('http://localhost:8010');
    const imUserId = ref('');
    const userName = ref('');
    const imBindingUserId = ref('');
    const messageText = ref('你好，我想咨询一下产品信息');
    const sending = ref(false);
    const showStatus = ref(false);
    const statusOk = ref(false);
    const statusText = ref('');
    const showResponse = ref(false);
    const responseJson = ref('');

    // Platform-specific configs
    const feishuEncryptKey = ref('');
    const feishuAppId = ref('cli_test');
    const wecomToken = ref('test-token');
    const wecomEncodingAesKey = ref('');
    const wecomCorpId = ref('ww-test');
    const wecomAgentId = ref('1000001');
    const dingtalkAppSecret = ref('test-app-secret');

    const history = reactive([]);

    async function fetchAgents() {
      try {
        const r = await fetch('/api/agents');
        agents.value = await r.json();
      } catch (e) {
        console.error('Failed to load agents:', e);
      }
    }

    async function fetchUsers() {
      try {
        const r = await fetch('/api/users');
        users.value = await r.json();
      } catch (e) {
        console.error('Failed to load users:', e);
      }
    }

    function switchTab(tab) {
      channelType.value = tab;
      document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    }

    function openAgentDialog() {
      const id = prompt('输入自定义 Agent UUID:');
      if (id) agentId.value = id;
    }

    async function onAgentChange() {
      if (!agentId.value) return;
      try {
        const r = await fetch(`/api/agent-channels/${agentId.value}`);
        const channels = await r.json();
        // Auto-fill platform config from first matching channel
        for (const ch of channels) {
          channelType.value = ch.channel_type;
          const cfg = ch.config || {};
          if (ch.channel_type === 'wecom') {
            wecomToken.value = cfg.token || '';
            wecomEncodingAesKey.value = cfg.encoding_aes_key || '';
            wecomCorpId.value = cfg.corp_id || '';
            wecomAgentId.value = String(cfg.agent_id || '');
          } else if (ch.channel_type === 'feishu') {
            feishuEncryptKey.value = cfg.encrypt_key || '';
            feishuAppId.value = cfg.app_id || '';
          } else if (ch.channel_type === 'dingtalk') {
            dingtalkAppSecret.value = cfg.app_secret || '';
          }
          break; // Use first channel found
        }
        // Switch active tab
        document.querySelectorAll('.tab').forEach(t =>
          t.classList.toggle('active', t.dataset.tab === channelType.value));
      } catch (e) {
        console.warn('Failed to load channel config:', e);
      }
    }

    async function sendMessage() {
      if (!agentId.value) { alert('请选择 Agent'); return; }
      if (!imUserId.value) { alert('请选择或输入 IM 用户 ID'); return; }

      sending.value = true;
      showStatus.value = true;
      statusText.value = '发送中...';

      try {
        // 如果有 IM 绑定配置，先创建绑定
        if (imBindingUserId.value) {
          const [imUser, realUser] = imBindingUserId.value.split(':');
          try {
            const token = await getAdminToken();
            // 先查是否已存在，避免重复创建
            const listResp = await fetch(`http://localhost:8002/api/manager/users/${realUser}/im-bindings`, {
              headers: { 'Authorization': `Bearer ${token}` },
            });
            const list = await listResp.json();
            const exists = (list.items || []).some(
              (b) => b.im_user_id === imUserId.value && b.channel_type === channelType.value
            );
            if (!exists) {
              await fetch(`http://localhost:8002/api/manager/users/${realUser}/im-bindings`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({
                  channel_type: channelType.value,
                  im_user_id: imUserId.value,
                  im_user_name: userName.value || imUser,
                }),
              });
            }
          } catch (e) {
            console.warn('IM binding check/creation failed:', e);
          }
        }

        const payload = {
          channel_type: channelType.value,
          agent_id: agentId.value,
          user_id: imUserId.value,
          text: messageText.value,
          user_name: userName.value,
          gateway_url: gatewayUrl.value,
          feishu_encrypt_key: feishuEncryptKey.value,
          feishu_app_id: feishuAppId.value,
          wecom_token: wecomToken.value,
          wecom_encoding_aes_key: wecomEncodingAesKey.value,
          wecom_corp_id: wecomCorpId.value,
          wecom_agent_id: wecomAgentId.value,
          dingtalk_app_secret: dingtalkAppSecret.value,
        };

        const resp = await fetch('/api/simulate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });

        const data = await resp.json();
        statusOk.value = resp.ok && data.status < 400;
        statusText.value = statusOk.value
          ? `✅ 发送成功！Gateway 响应: ${data.status}`
          : `❌ 发送失败: ${data.detail || JSON.stringify(data)}`;
        responseJson.value = JSON.stringify(data, null, 2);
        showResponse.value = true;
        history.push({ time: new Date().toLocaleTimeString(), channel: channelType.value, status: data.status });
      } catch (e) {
        statusOk.value = false;
        statusText.value = `❌ 请求异常: ${e.message}`;
      } finally {
        sending.value = false;
      }
    }

    async function getAdminToken() {
      const r = await fetch('http://localhost:8002/api/manager/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: 'admin', password: 'admin123' }),
      });
      const d = await r.json();
      return d.access_token;
    }

    function clearHistory() {
      history.splice(0);
      showResponse.value = false;
      showStatus.value = false;
    }

    // ── 持久化到 localStorage ──────────────────────────
    const STORAGE_KEY = 'im_simulator_config';

    function saveConfig() {
      const data = {
        gatewayUrl: gatewayUrl.value,
        agentId: agentId.value,
        channelType: channelType.value,
        imUserId: imUserId.value,
        userName: userName.value,
        imBindingUserId: imBindingUserId.value,
        messageText: messageText.value,
        feishuEncryptKey: feishuEncryptKey.value,
        feishuAppId: feishuAppId.value,
        wecomToken: wecomToken.value,
        wecomEncodingAesKey: wecomEncodingAesKey.value,
        wecomCorpId: wecomCorpId.value,
        wecomAgentId: wecomAgentId.value,
        dingtalkAppSecret: dingtalkAppSecret.value,
      };
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch {}
    }

    function loadConfig() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return;
        const data = JSON.parse(raw);
        if (data.gatewayUrl) gatewayUrl.value = data.gatewayUrl;
        if (data.agentId) agentId.value = data.agentId;
        if (data.channelType) channelType.value = data.channelType;
        if (data.imUserId) imUserId.value = data.imUserId;
        if (data.userName) userName.value = data.userName;
        if (data.imBindingUserId) imBindingUserId.value = data.imBindingUserId;
        if (data.messageText) messageText.value = data.messageText;
        if (data.feishuEncryptKey) feishuEncryptKey.value = data.feishuEncryptKey;
        if (data.feishuAppId) feishuAppId.value = data.feishuAppId;
        if (data.wecomToken) wecomToken.value = data.wecomToken;
        if (data.wecomEncodingAesKey) wecomEncodingAesKey.value = data.wecomEncodingAesKey;
        if (data.wecomCorpId) wecomCorpId.value = data.wecomCorpId;
        if (data.wecomAgentId) wecomAgentId.value = data.wecomAgentId;
        if (data.dingtalkAppSecret) dingtalkAppSecret.value = data.dingtalkAppSecret;
      } catch {}
    }

    // Auto-save on important changes
    watch(gatewayUrl, saveConfig);
    watch(agentId, saveConfig);
    watch(channelType, saveConfig);
    watch(imUserId, saveConfig);
    watch(messageText, saveConfig);
    watch(wecomToken, saveConfig);
    watch(wecomEncodingAesKey, saveConfig);
    watch(wecomCorpId, saveConfig);
    watch(wecomAgentId, saveConfig);
    watch(dingtalkAppSecret, saveConfig);
    watch(feishuEncryptKey, saveConfig);

    onMounted(() => {
      loadConfig();
      fetchAgents();
      fetchUsers();
      // Restore tab visual state
      document.querySelectorAll('.tab').forEach(t =>
        t.classList.toggle('active', t.dataset.tab === channelType.value));
    });

    return {
      agents, users, channelType, agentId, gatewayUrl, imUserId, userName,
      imBindingUserId, messageText, sending, showStatus, statusOk, statusText,
      showResponse, responseJson, history,
      feishuEncryptKey, feishuAppId, wecomToken, wecomEncodingAesKey,
      wecomCorpId, wecomAgentId, dingtalkAppSecret,
      switchTab, openAgentDialog, onAgentChange, sendMessage, clearHistory,
    };
  },
}).mount('#app');
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8899)
