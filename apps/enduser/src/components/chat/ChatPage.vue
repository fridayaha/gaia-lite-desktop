<template>
  <div class="enduser-chat" :class="{ 'chat-content-rtl': isRtl }">

    <!-- Mobile titlebar: hamburger + agent name + workspace btn -->
    <header class="app-titlebar" v-if="isMobile">
      <button class="app-titlebar-hamburger" @click="toggleSidebar" aria-label="打开会话列表">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>
      <span class="app-titlebar-title">{{ agentName || currentAgentInfo?.name || '智能体' }}</span>
      <button class="app-titlebar-action" @click="openRightpanelMobile" aria-label="打开工作区">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z"/></svg>
      </button>
    </header>

    <!-- Layout: rail + sidebar + main -->
    <div class="layout">
    <!-- 左侧导航 Rail -->
    <nav class="rail" :class="{ expanded: railExpanded }" aria-label="Primary navigation">
      <!-- 顶部：返回智能体列表 -->
      <button class="rail-btn nav-tab" @click="router.push('/agents')" aria-label="返回智能体列表">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><polyline points="12 19 5 12 12 5"/></svg>
        <span class="rail-btn-label">返回</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'chat' }"
        @click="switchPanel('chat')" aria-label="对话">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        <span class="rail-btn-label">对话</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'tasks' }"
        @click="switchPanel('tasks')" aria-label="定时任务">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
        <span class="rail-btn-label">定时</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'kanban' }"
        @click="switchPanel('kanban')" aria-label="看板">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v16"/><path d="M16 4v16"/><path d="M3 10h18"/></svg>
        <span class="rail-btn-label">看板</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'skills' }"
        @click="switchPanel('skills')" aria-label="技能">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        <span class="rail-btn-label">技能</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'memory' }"
        @click="switchPanel('memory')" aria-label="记忆">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96-.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96-.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2z"/></svg>
        <span class="rail-btn-label">记忆</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'workspaces' }"
        @click="switchPanel('workspaces')" aria-label="工作区">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        <span class="rail-btn-label">工作区</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'profiles' }"
        @click="switchPanel('profiles')" aria-label="智能体配置">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        <span class="rail-btn-label">配置</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'todos' }"
        @click="switchPanel('todos')" aria-label="任务列表">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="5" width="6" height="6" rx="1"/><path d="m3 17 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/></svg>
        <span class="rail-btn-label">待办</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'insights' }"
        @click="switchPanel('insights')" aria-label="洞察">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/></svg>
        <span class="rail-btn-label">洞察</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'logs' }"
        @click="switchPanel('logs')" aria-label="日志">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h8"/><path d="M8 9h2"/></svg>
        <span class="rail-btn-label">日志</span>
      </button>
      <div class="rail-spacer"></div>
      <button class="rail-btn nav-tab" @click="handleLogout" aria-label="退出">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        <span class="rail-btn-label">退出</span>
      </button>
      <button class="rail-btn nav-tab"
        :class="{ active: currentPanel === 'settings' }"
        @click="switchPanel('settings')" aria-label="设置">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        <span class="rail-btn-label">设置</span>
      </button>
      <!-- 底部折叠条（参考 admin SidebarLeftCollapse）-->
      <button class="rail-btn rail-collapse-toggle"
        @click="toggleRail"
        :aria-label="railExpanded ? '收起菜单' : '展开菜单'">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
          :style="{ transform: railExpanded ? 'none' : 'rotateY(180deg)' }">
          <polyline points="11 17 6 12 11 7"/>
          <polyline points="18 17 13 12 18 7"/>
        </svg>
        <span class="rail-btn-label">{{ railExpanded ? '收起' : '展开' }}</span>
      </button>
      <!-- 右边缘悬浮展开按钮（参考 admin SidebarCenterCollapse，仅收起状态 + hover 显示）-->
      <button v-if="!railExpanded" class="rail-edge-toggle"
        @click="toggleRail"
        aria-label="展开菜单">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      </button>
    </nav>

    <!-- 侧边栏 -->
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <!-- 智能体切换器（chat 内切换，无需返回列表页） -->
      <div class="agent-switcher" ref="agentMenuRef">
        <button class="agent-switcher-trigger" @click="toggleAgentMenu" :aria-expanded="agentMenuOpen" aria-label="切换智能体">
          <span class="agent-switcher-name">{{ agentName || currentAgentInfo?.name || '智能体' }}</span>
          <span v-if="currentAgentInfo?.engine_type" class="agent-engine-badge" :class="currentAgentInfo.engine_type.toLowerCase()">
            {{ engineLabel(currentAgentInfo.engine_type) }}
          </span>
          <svg class="agent-switcher-chevron" :class="{ open: agentMenuOpen }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <div v-if="agentMenuOpen" class="agent-switcher-dropdown" role="menu">
          <div class="agent-switcher-dropdown-label">切换智能体</div>
          <button
            v-for="a in agentStore.accessibleAgents"
            :key="a.id"
            class="agent-switcher-item"
            :class="{ active: a.id === agentId }"
            role="menuitem"
            @click="switchAgent(a.id)"
          >
            <span class="agent-switcher-item-name">{{ a.name }}</span>
            <span class="agent-engine-badge" :class="a.engine_type.toLowerCase()">{{ engineLabel(a.engine_type) }}</span>
            <svg v-if="a.id === agentId" class="agent-switcher-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          </button>
          <div v-if="agentStore.accessibleAgents.length === 0" class="agent-switcher-empty">暂无其他可用智能体</div>
        </div>
      </div>
      <!-- 移动端侧边栏导航 (与 rail 同步，仅 mobile 可见) -->
      <div class="sidebar-nav">
        <button class="nav-tab" :class="{ active: currentPanel === 'chat' }" @click="switchPanel('chat')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'tasks' }" @click="switchPanel('tasks')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'kanban' }" @click="switchPanel('kanban')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 4v16"/><path d="M16 4v16"/><path d="M3 10h18"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'skills' }" @click="switchPanel('skills')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'memory' }" @click="switchPanel('memory')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96-.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96-.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2z"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'workspaces' }" @click="switchPanel('workspaces')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'profiles' }" @click="switchPanel('profiles')">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'todos' }" @click="switchPanel('todos')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="6" height="6" rx="1"/><path d="m3 17 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'insights' }" @click="switchPanel('insights')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'logs' }" @click="switchPanel('logs')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h8"/><path d="M8 9h2"/></svg>
        </button>
        <button class="nav-tab" :class="{ active: currentPanel === 'settings' }" @click="switchPanel('settings')">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        </button>
      </div>
      <!-- Chat 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'chat' }">
        <div class="panel-head">
          <span>对话</span>
          <div class="panel-head-actions">
            <button class="panel-head-btn has-tooltip" data-tooltip="新建对话" @click="newSession">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            </button>
          </div>
        </div>
        <div class="session-search sidebar-search">
          <div class="session-search-field">
            <svg class="sidebar-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
            <input v-model="searchQuery" placeholder="搜索对话..." autocomplete="off">
          </div>
        </div>
        <ChatSessionList
          :sessions="filteredSessions"
          :currentSessionId="currentSession?.session_id"
          @select="selectSession"
          @rename="(id, title) => updateSessionTitle(id, title)"
          @delete="deleteSession"
        />
      </div>

      <!-- Tasks (Cron) 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'tasks' }">
        <div class="panel-head">
          <span>定时任务</span>
          <div class="panel-head-actions">
            <button class="panel-head-btn has-tooltip" data-tooltip="刷新" @click="wsRefreshKey++"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button>
          </div>
        </div>
        <div class="cron-list" id="cronList">
          <div style="padding:12px;color:var(--muted);font-size:12px">定时任务（发布后可用）</div>
        </div>
      </div>

      <!-- Kanban 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'kanban' }">
        <div class="panel-head">
          <span>看板</span>
        </div>
        <div style="padding:12px;color:var(--muted);font-size:12px">看板（发布后可用）</div>
      </div>

      <!-- Skills 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'skills' }">
        <div class="panel-head">
          <span>技能</span>
        </div>
        <div class="skills-search sidebar-search">
          <div class="session-search-field">
            <svg class="sidebar-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
            <input placeholder="搜索技能..." autocomplete="off">
          </div>
        </div>
        <div class="skills-list">
          <div style="padding:12px;color:var(--muted);font-size:12px">暂无技能</div>
        </div>
      </div>

      <!-- Memory 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'memory' }">
        <div class="panel-head">
          <span>个人记忆</span>
        </div>
        <div class="side-menu">
          <div style="padding:12px;color:var(--muted);font-size:12px">暂无记忆</div>
        </div>
      </div>

      <!-- Workspaces 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'workspaces' }">
        <div class="panel-head">
          <span>工作区</span>
        </div>
        <div class="panel-head-sub">智能体工作区（只读）</div>
        <div style="flex:1;overflow-y:auto;padding:8px">
          <div v-if="workspaces.length === 0" style="color:var(--muted);font-size:12px">暂无工作区</div>
          <button v-for="ws in workspaces" :key="ws.name"
            class="side-menu-item"
            :class="{ active: ws.name === currentWs || (ws.name === '.' && currentWs === '.') }"
            @click="switchWorkspace(ws.name)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
            <span>{{ ws.label || ws.name }}</span>
          </button>
        </div>
      </div>

      <!-- Profiles 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'profiles' }">
        <div class="panel-head">
          <span>智能体配置</span>
          <div class="panel-head-actions">
            <button class="panel-head-btn has-tooltip" data-tooltip="新建配置" @click="createProfile"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>
          </div>
        </div>
        <div style="flex:1;overflow-y:auto;padding:8px">
          <div v-if="profiles.length === 0" style="color:var(--muted);font-size:12px">暂无配置</div>
          <button v-for="p in profiles" :key="p.id || p.name"
            class="side-menu-item"
            :class="{ active: p.name === currentProfile }"
            @click="switchProfile(p.name)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            <span>{{ p.name }}</span>
          </button>
        </div>
      </div>

      <!-- Todos 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'todos' }">
        <div class="panel-head">
          <span>任务列表</span>
        </div>
        <div id="todoPanel" style="flex:1;overflow-y:auto;padding:8px 12px">
          <div style="color:var(--muted);font-size:12px">暂无任务</div>
        </div>
      </div>

      <!-- Insights 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'insights' }">
        <div class="panel-head">
          <span>洞察</span>
          <div class="panel-head-actions">
            <button class="panel-head-btn has-tooltip" data-tooltip="刷新"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button>
          </div>
        </div>
        <div class="panel-head-sub" style="padding:0 12px 8px">
          <select v-model="insightsPeriod" style="width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:4px 8px;font-size:12px">
            <option value="7">7 天</option>
            <option value="30" selected>30 天</option>
            <option value="90">90 天</option>
            <option value="365">365 天</option>
          </select>
        </div>
      </div>

      <!-- Logs 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'logs' }">
        <div class="panel-head">
          <span>日志</span>
        </div>
        <div class="logs-control-panel">
          <label class="logs-control-label">级别</label>
          <select v-model="logSeverity" style="padding:3px 6px;background:var(--input-bg);color:var(--text);border:1px solid var(--border);border-radius:4px;font-size:12px">
            <option value="all">全部</option>
            <option value="errors">错误</option>
            <option value="warnings">警告+</option>
          </select>
        </div>
      </div>

      <!-- Settings 面板 -->
      <div class="panel-view" :class="{ active: currentPanel === 'settings' }">
        <div class="panel-head">
          <span>设置</span>
        </div>
        <div class="side-menu">
          <button type="button" class="side-menu-item" :class="{ active: settingsSection === 'conversation' }" @click="settingsSection='conversation'">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <span>对话</span>
          </button>
          <button type="button" class="side-menu-item" :class="{ active: settingsSection === 'appearance' }" @click="settingsSection='appearance'">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
            <span>外观</span>
          </button>
          <button type="button" class="side-menu-item" :class="{ active: settingsSection === 'preferences' }" @click="settingsSection='preferences'">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
            <span>偏好</span>
          </button>
          <button type="button" class="side-menu-item" :class="{ active: settingsSection === 'system' }" @click="settingsSection='system'">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><line x1="6" y1="7" x2="6.01" y2="7"/><line x1="6" y1="17" x2="6.01" y2="17"/></svg>
            <span>系统</span>
          </button>
        </div>
      </div>
      <div class="resize-handle" id="sidebarResize"></div>
    </aside>

    <!-- 主内容区 -->
    <main class="main" @touchstart="onTouchStart" @touchend="onTouchEnd">
      <!-- Chat 主视图 -->
      <div v-show="currentPanel === 'chat'" id="mainChat" class="main-view">
        <div class="messages-shell">
          <ChatMessages
            :messages="currentSession?.messages || []"
            :isStreaming="isStreaming"
            :streamingContent="streamingContent"
            :isEmpty="!currentSession || (!!currentSession._loaded && currentSession.messages.length === 0 && !isStreaming)"
            :isLoadingMessages="!!currentSession && !currentSession._loaded && !isStreaming"
            :toolCalls="toolCalls"
            :thinkingText="thinkingText"
            :thinkingStatus="thinkingStatus"
            :activityEvents="activityEvents"
            :agentName="agentName"
            :streamFadeEffect="streamFadeEffect"
            :currentSessionId="currentSessionId"
            @send-suggestion="suggestText => sendMessage(suggestText)"
            @retry="msg => retryMessage(msg)"
            @edit-message="(msg, text) => editMessage(msg, text)"
            @regenerate="msg => regenerateResponse(msg)"
            @feedback="(msg, r) => setFeedback(msg, r)"
            @favorite="(msg, favored) => setFavorite(msg, favored)"
          >
            <template #logo>
              <HermesLogo v-if="engineType === 'HERMES'" style="font-size: 64px" />
              <OpenClawLogo v-else-if="engineType === 'OPENCLAW'" style="font-size: 64px" />
            </template>
          </ChatMessages>
        </div>
        <!-- 离线横幅 -->
        <div class="offline-banner" id="offlineBanner" v-if="isOffline">
          <div class="offline-copy">
            <strong>连接断开</strong>
            <span>你的设备已离线</span>
          </div>
        </div>
        <div class="composer-wrap">
          <!-- 审批卡片：网关 SSE 下发 approval.request 时出现，浮层吸附 composer 上方 -->
          <ApprovalCard
            v-if="approvalPending"
            :command="approvalPending.command"
            :description="approvalPending.description"
            :choices="approvalPending.choices"
            :status="approvalPending.status"
            :choice="approvalPending.choice"
            :submitting="approvalPending.submitting"
            @respond="submitApproval"
          />
          <DownFeedbackDialog
            v-if="downFeedbackTarget"
            :message="downFeedbackTarget"
            @submit="(reason, comment) => submitDownFeedback(downFeedbackTarget, reason, comment)"
            @cancel="closeDownFeedbackDialog"
          />
          <ChatComposer
            :disabled="isStreaming || browserTakeoverActive"
            :models="models"
            :currentModel="currentModel"
            :sendKey="sendKey"
            @send="sendMessage"
            @stop="chat.stopStreaming"
            @select-model="currentModel = $event"
          />
        </div>
      </div>

      <!-- Settings 主视图 -->
      <div v-show="currentPanel === 'settings'" id="mainSettings" class="main-view">
        <div class="settings-main">
          <!-- Conversation 设置 -->
          <div class="settings-pane" :class="{ active: settingsSection === 'conversation' }">
            <div class="settings-section-head">
              <div>
                <div class="settings-section-title">对话</div>
                <div class="settings-section-meta">导出、导入和管理对话</div>
              </div>
            </div>
            <div class="action-grid">
              <button class="settings-action-btn" @click="downloadTranscript">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                <span>文本</span>
              </button>
              <button class="settings-action-btn" @click="exportSessionJSON">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1"/><path d="M16 3h1a2 2 0 0 1 2 2v5a2 2 0 0 0 2 2 2 2 0 0 0-2 2v5a2 2 0 0 1-2 2h-1"/></svg>
                <span>JSON</span>
              </button>
              <button class="settings-action-btn" @click="importSessionJSON">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                <span>导入</span>
              </button>
              <button class="settings-action-btn danger" @click="clearConversation">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 1 2 2 2v2"/></svg>
                <span>清空</span>
              </button>
            </div>
          </div>

          <!-- Appearance 设置 -->
          <div class="settings-pane" :class="{ active: settingsSection === 'appearance' }">
            <div class="settings-section-head">
              <div>
                <div class="settings-section-title">外观</div>
                <div class="settings-section-meta">主题和视觉样式</div>
              </div>
            </div>
            <div class="settings-field">
              <label>主题</label>
              <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:4px">
                <button type="button" :class="['theme-pick-btn', { active: theme === 'light' }]" @click="setTheme('light')" style="border:1px solid var(--border2);border-radius:10px;padding:10px 8px;text-align:center;cursor:pointer;background:none">
                  <div style="width:100%;height:40px;border-radius:6px;background:#fff;border:1px solid rgba(0,0,0,.12);margin-bottom:6px;display:flex;align-items:center;justify-content:center">
                    <svg width="16" height="16" fill="none" stroke="#999" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
                  </div>
                  <span style="font-size:12px;font-weight:500;color:var(--text)">浅色</span>
                </button>
                <button type="button" :class="['theme-pick-btn', { active: theme === 'dark' }]" @click="setTheme('dark')" style="border:1px solid var(--border2);border-radius:10px;padding:10px 8px;text-align:center;cursor:pointer;background:none">
                  <div style="width:100%;height:40px;border-radius:6px;background:#1a1a2e;border:1px solid rgba(255,255,255,.1);margin-bottom:6px;display:flex;align-items:center;justify-content:center">
                    <svg width="16" height="16" fill="none" stroke="#666" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1111.21 3a7 7 0 009.79 9.79z"/></svg>
                  </div>
                  <span style="font-size:12px;font-weight:500;color:var(--text)">深色</span>
                </button>
                <button type="button" :class="['theme-pick-btn', { active: theme === 'system' }]" @click="setTheme('system')" style="border:1px solid var(--border2);border-radius:10px;padding:10px 8px;text-align:center;cursor:pointer;background:none">
                  <div style="width:100%;height:40px;border-radius:6px;background:linear-gradient(to right,#fff,#1a1a2e);border:1px solid rgba(0,0,0,.12);margin-bottom:6px;display:flex;align-items:center;justify-content:center">
                    <svg width="16" height="16" fill="none" stroke="#888" stroke-width="2" viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
                  </div>
                  <span style="font-size:12px;font-weight:500;color:var(--text)">系统</span>
                </button>
              </div>
            </div>
            <div class="settings-field">
              <label>字体大小</label>
              <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px;margin-top:4px">
                <button v-for="fs in fontSizes" :key="fs.value" type="button"
                  :class="['font-size-pick-btn', { active: fontSize === fs.value }]"
                  @click="setFontSize(fs.value)"
                  style="border:1px solid var(--border2);border-radius:10px;padding:10px 8px;text-align:center;cursor:pointer;background:none">
                  <div style="width:100%;height:40px;border-radius:6px;background:var(--surface);border:1px solid var(--border);margin-bottom:6px;display:flex;align-items:center;justify-content:center">
                    <span :style="{ fontSize: fs.previewSize, fontWeight: 600, color: 'var(--muted)' }">Aa</span>
                  </div>
                  <span style="font-size:12px;font-weight:500;color:var(--text)">{{ fs.label }}</span>
                </button>
              </div>
            </div>
            <div class="settings-field">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
                <input type="checkbox" v-model="keepWorkspacePanelOpen" style="width:15px;height:15px;accent-color:var(--accent)">
                <span>默认打开工作区面板</span>
              </label>
            </div>
          </div>

          <!-- Preferences 设置 -->
          <div class="settings-pane" :class="{ active: settingsSection === 'preferences' }">
            <div class="settings-section-head">
              <div>
                <div class="settings-section-title">偏好</div>
                <div class="settings-section-meta">默认行为和界面设置</div>
              </div>
            </div>
            <div class="settings-field">
              <label for="settingsModel">默认模型</label>
              <select v-model="currentModel" style="width:100%;padding:8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px">
                <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
            <div class="settings-field">
              <label for="settingsSendKey">发送键</label>
              <select v-model="sendKey" style="width:100%;padding:8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px">
                <option value="enter">Enter（Shift+Enter 换行）</option>
                <option value="ctrl+enter">Ctrl+Enter（Enter 换行）</option>
              </select>
            </div>
            <div class="settings-field">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
                <input type="checkbox" v-model="streamFadeEffect" style="width:15px;height:15px;accent-color:var(--accent)">
                <span>流式淡入动画</span>
              </label>
            </div>
          </div>

          <!-- System 设置 -->
          <div class="settings-pane" :class="{ active: settingsSection === 'system' }">
            <div class="settings-section-head">
              <div>
                <div class="settings-section-title">系统</div>
                <div class="settings-section-meta">服务器信息与诊断</div>
              </div>
            </div>
            <div class="settings-field">
              <label>智能体 ID</label>
              <div style="font-size:13px;color:var(--muted)">{{ agentId }}</div>
            </div>
          </div>
        </div>
      </div>
    </main>
    <!-- Workspace right panel -->
    <aside class="rightpanel" :class="{ open: rightpanelOpen }" id="workspacePanel" ref="workspacePanelRef">
      <div class="resize-handle" id="rightpanelResize"></div>
      <div class="panel-header">
        <div class="workspace-panel-title-group">
          <span class="workspace-panel-heading" title="工作区">工作区</span>
        </div>
        <div class="panel-actions">
          <button class="panel-icon-btn has-tooltip" data-tooltip="关闭" @click="toggleWorkspacePanel"><LucideIcon name="x" :size="14" /></button>
        </div>
      </div>
      <div class="file-tree" id="fileTree">
        <!-- 共享 FileBrowser：树 + CodeMirror 只读高亮（editable=false） -->
        <FileBrowser :file-api="fileApi" :editable="false" title="工作区文件" />
      </div>
    </aside>

    <!-- 浏览器沙箱面板（云桌面 VNC）：接管/全屏按钮在 header，与 rightpanel 互斥 -->
    <aside v-if="browserSandboxEnabled" class="browserpanel" id="browserPanel">
      <div class="panel-header">
        <div class="workspace-panel-title-group">
          <span class="workspace-panel-heading" title="云桌面">云桌面</span>
          <span class="workspace-panel-sub">{{ browserTakeoverActive ? '已接管' : '只读' }}</span>
        </div>
        <div class="panel-actions">
          <!-- 接管/取消接管（全屏按钮前）：run 活跃时禁用 -->
          <button
            class="panel-icon-btn has-tooltip"
            :class="{ 'takeover-active': browserTakeoverActive }"
            :data-tooltip="browserTakeoverActive ? '取消接管' : '接管云桌面'"
            :disabled="isStreaming"
            @click="toggleBrowserTakeover"
          >
            <LucideIcon :name="browserTakeoverActive ? 'mouse-pointer' : 'hand'" :size="14" />
          </button>
          <!-- 全屏/取消全屏 -->
          <button
            class="panel-icon-btn has-tooltip"
            :data-tooltip="browserFullscreen ? '取消全屏' : '全屏'"
            @click="toggleBrowserFullscreen"
          >
            <LucideIcon :name="browserFullscreen ? 'minimize-2' : 'maximize-2'" :size="14" />
          </button>
          <button class="panel-icon-btn has-tooltip" data-tooltip="关闭" @click="toggleBrowserPanel">
            <LucideIcon name="x" :size="14" />
          </button>
        </div>
      </div>
      <div class="browser-stage">
        <BrowserView
          :agentId="agentId"
          :viewOnly="!browserTakeoverActive"
          :active="browserPanelOpen"
        />
      </div>
    </aside>
    </div><!-- /.layout -->

    <button class="workspace-panel-edge-toggle has-tooltip" id="btnWorkspacePanelEdgeToggle"
      type="button" @click="toggleWorkspacePanel"
      data-tooltip="显示工作区"
      aria-label="Show workspace panel"
      aria-expanded="false">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="9 6 15 12 9 18"/>
      </svg>
    </button>
    <BottomTabbar v-if="isMobile" :current-panel="currentPanel" @switch="switchPanel" @back="router.push('/agents')" />
    <div
      v-if="isMobile && (sidebarOpen || rightpanelOpen)"
      class="sidebar-overlay"
      @click="() => { closeSidebar(); rightpanelOpen = false }"
    ></div>

    <!-- 浏览器沙箱边缘展开按钮（屏幕右边缘；仅启用沙箱的实例展示） -->
    <button
      v-if="!browserPanelOpen && browserSandboxEnabled"
      class="browser-panel-edge-toggle has-tooltip"
      type="button"
      @click="toggleBrowserPanel"
      data-tooltip="云桌面"
      aria-label="Show browser panel"
    >
      <LucideIcon name="monitor" :size="14" />
    </button>
  </div><!-- /.enduser-chat -->
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, provide } from "vue"
import {
  chatContextKey,
  FileBrowser,
  ChatMessages,
  ChatComposer,
  ChatSessionList,
  ApprovalCard,
  BottomTabbar,
  type FileApi,
} from "@ua/chat"
import BrowserView from "./BrowserView.vue"
import DownFeedbackDialog from "./DownFeedbackDialog.vue"
import LucideIcon from "@/components/icons/LucideIcon.vue"
import HermesLogo from "@/components/icons/HermesLogo.vue"
import OpenClawLogo from "@/components/icons/OpenClawLogo.vue"
import { useChat } from "@/composables/useChat"
import { useMobile } from "@/composables/useMobile"
import { readAgentFileContent, downloadAgentFile, listAgentFiles } from "@/api/endpoints"

const props = defineProps<{ agentId: string; engineType?: string; agentName?: string }>()
const emit = defineEmits<{ 'engine-unavailable': [] }>()

// 注入共享包聊天上下文：把宿主 API 封装成闭包（捕获 agentId），
// 包内 ChatMessages / AuthenticatedImage / ChatFileBrowser 通过 inject 消费，不直接 import @/api。
provide(chatContextKey, {
  imageResolver: (path: string) => readAgentFileContent(props.agentId, path) as Promise<{ is_image: boolean; content_b64: string }>,
  fileDownloader: (path: string) => downloadAgentFile(props.agentId, path),
  fileLister: (path: string) => listAgentFiles(props.agentId, path),
})

// FileApi 适配器：归一 enduser 文件 API（per-dir list + 富 read 含图片）供共享 FileBrowser 消费。
const fileApi = computed<FileApi>(() => ({
  async list(path: string) {
    const res = await listAgentFiles(props.agentId, path || ".")
    return (res.entries || []).map((e) => ({ name: e.name, path: e.path, isDir: e.is_dir, size: e.size }))
  },
  async read(path: string) {
    const c = await readAgentFileContent(props.agentId, path)
    return {
      path: c.path,
      content: c.content || "",
      isBinary: !c.is_text && !c.is_image,
      isImage: c.is_image,
      contentB64: c.content_b64 || undefined,
      size: c.size,
      truncated: c.truncated,
    }
  },
  async download(path: string) {
    await downloadAgentFile(props.agentId, path)
  },
}))

interface SessionData {
  session_id: string
  title: string
  messages: any[]
  created_at: number
  last_message_at: number | null
  model: string
}

const chat = useChat(props.agentId, props.engineType)
const sessions = chat.sessions as any
const currentSessionId = chat.currentSessionId as any
const currentSession = chat.currentSession as any
const filteredSessions = chat.filteredSessions as any
const searchQuery = chat.searchQuery as any
const isStreaming = chat.isStreaming as any
const streamingContent = chat.streamingContent as any
const currentModel = chat.currentModel as any
const models = chat.models as any
const workspaceNames = chat.workspaceNames as any
const currentWs = chat.currentWs as any
const newSession = chat.newSession as () => Promise<void>
const selectSession = chat.selectSession as (id: string) => void
const sendMessage = chat.sendMessage as (text: string, files?: File[]) => Promise<void>
const retryMessage = chat.retryMessage as (msg: any) => Promise<void>
const editMessage = chat.editMessage as (msg: any, newText: string) => Promise<void>
const regenerateResponse = chat.regenerateResponse as (msg: any) => Promise<void>
const setFeedback = chat.setFeedback as (msg: any, rating: "up" | "down") => void
const setFavorite = chat.setFavorite as (msg: any, favored: boolean) => Promise<void>
const submitDownFeedback = chat.submitDownFeedback as (msg: any, reason: string, comment: string | null) => Promise<void>
const closeDownFeedbackDialog = chat.closeDownFeedbackDialog as () => void
const downFeedbackTarget = chat.downFeedbackTarget as any
const loadSessions = chat.loadSessions as () => Promise<void>
const loadModels = chat.loadModels as () => Promise<void>
const updateSessionTitle = chat.updateSessionTitle as (id: string, title: string) => Promise<void>
const deleteSession = chat.deleteSession as (id: string) => Promise<void>
const switchWorkspace = chat.switchWorkspace as (ws: string) => Promise<void>
const profiles = chat.profiles as any
const currentProfile = chat.currentProfile as any
const switchProfile = chat.switchProfile as (p: string) => void
const thinkingText = chat.thinkingText as any
const thinkingStatus = chat.thinkingStatus as any
const toolCalls = chat.toolCalls as any
const activityEvents = chat.activityEvents as any
const engineAvailable = chat.engineAvailable
const approvalPending = chat.approvalPending as any
const browserTakeoverActive = chat.browserTakeoverActive as any
const submitApproval = chat.submitApproval as (choice: any) => Promise<void>
const resumePendingRuns = chat.resumePendingRuns as () => Promise<void>
import { useAuthStore } from "@/stores/auth"
import { useAgentStore } from "@/stores/agent"
import { useRouter } from "vue-router"
const authStore = useAuthStore()
const router = useRouter()
const agentStore = useAgentStore()

// ── Chat 内切换智能体 ──
const agentMenuOpen = ref(false)
const agentMenuRef = ref<HTMLElement | null>(null)
// 当前 agent 信息：优先 store.currentAgent（由 AgentChatPage.checkStatus 设置），回退按 props.agentId 查
const currentAgentInfo = computed(() => {
  return agentStore.accessibleAgents.find((a) => a.id === props.agentId) || agentStore.currentAgent || null
})
// 浏览器沙箱是否启用：仅启用的实例才展示「云桌面」入口（未启用则不渲染按钮/面板）
const browserSandboxEnabled = computed(() => !!currentAgentInfo.value?.browser_sandbox_enabled)
// 切换 agent 时丢弃未发送内容前确认（composer 文本/附件）
function hasUnsentInput(): boolean {
  const composerTextarea = document.querySelector('.composer-box textarea') as HTMLTextAreaElement | null
  return !!(composerTextarea && composerTextarea.value.trim())
}
function switchAgent(id: string) {
  agentMenuOpen.value = false
  if (id === props.agentId) return
  if (hasUnsentInput() && !confirm("切换智能体将丢弃当前未发送的输入，是否继续？")) return
  router.push(`/agents/${id}`)
}
function toggleAgentMenu() {
  agentMenuOpen.value = !agentMenuOpen.value
  // 懒加载：若 store 列表为空则补拉
  if (agentMenuOpen.value && agentStore.accessibleAgents.length === 0) {
    agentStore.loadAccessibleAgents().catch(() => {})
  }
}
function handleAgentMenuDocClick(e: MouseEvent) {
  if (agentMenuRef.value && !agentMenuRef.value.contains(e.target as Node)) {
    agentMenuOpen.value = false
  }
}
onMounted(() => {
  document.addEventListener("click", handleAgentMenuDocClick)
})
onBeforeUnmount(() => {
  document.removeEventListener("click", handleAgentMenuDocClick)
})
function engineLabel(type?: string) {
  if (type === "HERMES") return "Hermes"
  if (type === "DIFY") return "Dify"
  if (type === "CLAUDE_CODE") return "Claude"
  return "OpenClaw"
}

function handleLogout() {
  if (!confirm("确定要退出登录吗？")) return
  authStore.logout()
  router.push("/login")
}

const currentPanel = ref("chat")
const settingsSection = ref("conversation")
const workspacePanelOpen = ref(false)
const browserPanelOpen = ref(false)
const browserFullscreen = ref(false)
const railExpanded = ref(localStorage.getItem("ua-rail-expanded") === "true")

// ── Mobile responsive state ──
const { isMobile } = useMobile()
const sidebarOpen = ref(false)
const rightpanelOpen = ref(false)
function toggleSidebar() {
  sidebarOpen.value = !sidebarOpen.value
  if (sidebarOpen.value) rightpanelOpen.value = false
}
function closeSidebar() {
  sidebarOpen.value = false
}
function openRightpanelMobile() {
  rightpanelOpen.value = !rightpanelOpen.value
  if (rightpanelOpen.value) sidebarOpen.value = false
}

// ── Mobile 左滑手势：边缘横滑唤出 sidebar/rightpanel ──
const touchStart = { x: 0, y: 0 }
function onTouchStart(e: TouchEvent) {
  if (!isMobile.value) return
  const t = e.touches[0]
  touchStart.x = t.clientX
  touchStart.y = t.clientY
}
function onTouchEnd(e: TouchEvent) {
  if (!isMobile.value) return
  const t = e.changedTouches[0]
  const dx = t.clientX - touchStart.x
  const dy = t.clientY - touchStart.y
  const absDx = Math.abs(dx)
  const absDy = Math.abs(dy)
  // 横向位移<80 或 横向<纵向 → 不算横滑（让竖向滚动正常）
  if (absDx < 80 || absDx < absDy) return
  const edge = 32
  if (dx > 0 && touchStart.x < edge) {
    sidebarOpen.value = true
    rightpanelOpen.value = false
  } else if (dx < 0 && touchStart.x > window.innerWidth - edge) {
    rightpanelOpen.value = true
    sidebarOpen.value = false
  } else if (dx < 0 && sidebarOpen.value) {
    sidebarOpen.value = false
  } else if (dx > 0 && rightpanelOpen.value) {
    rightpanelOpen.value = false
  }
}

// ── 移动端软键盘：visualViewport 监听，写 --kb-h CSS 变量 ──
function updateKeyboardOffset() {
  const vv = window.visualViewport
  if (!vv) return
  const kbH = Math.max(0, window.innerHeight - vv.height)
  document.documentElement.style.setProperty("--kb-h", `${kbH}px`)
}
onMounted(() => {
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", updateKeyboardOffset)
    window.visualViewport.addEventListener("scroll", updateKeyboardOffset)
    updateKeyboardOffset()
  }
})
onBeforeUnmount(() => {
  if (window.visualViewport) {
    window.visualViewport.removeEventListener("resize", updateKeyboardOffset)
    window.visualViewport.removeEventListener("scroll", updateKeyboardOffset)
  }
})

// ── 引擎可用性监控 ──
watch(engineAvailable, (val) => {
  if (!val) emit("engine-unavailable")
})

// ── Theme management ──
const theme = ref(localStorage.getItem("ua-theme") || "dark")
const fontSize = ref(localStorage.getItem("ua-font-size") || "default")
const isRtl = ref(localStorage.getItem("ua-rtl") === "true")
const keepWorkspacePanelOpen = ref(localStorage.getItem("ua-workspace-panel") === "open")
const sendKey = ref<"enter" | "ctrl+enter">(localStorage.getItem("ua-send-key") === "ctrl+enter" ? "ctrl+enter" : "enter")
const streamFadeEffect = ref(localStorage.getItem("ua-stream-fade") === "true")
const logSeverity = ref("all")
const insightsPeriod = ref("30")
const isOffline = ref(typeof navigator !== 'undefined' && !navigator.onLine)
const fontSizes = [
  { value: "small", label: "小", previewSize: "10px" },
  { value: "default", label: "默认", previewSize: "13px" },
  { value: "large", label: "大", previewSize: "17px" },
  { value: "xlarge", label: "最大", previewSize: "20px" },
]

// ── Workspaces ──
const workspaces = computed(() => {
  const names = workspaceNames?.value ?? []
  if (!Array.isArray(names)) return []
  return names.map((name: string) => ({
    name,
    label: name === "." || name === "/" ? "工作区" : name.replace(/^\/+/, ""),
  }))
})

// ── Workspace panel ──
function toggleWorkspacePanel() {
  workspacePanelOpen.value = !workspacePanelOpen.value
  const val = workspacePanelOpen.value ? 'open' : 'closed'
  document.documentElement.dataset.workspacePanel = val
  localStorage.setItem('ua-workspace-panel', val)
  // Reset inline width style when closing so CSS width:0 takes effect
  if (!workspacePanelOpen.value) {
    const panel = document.querySelector('.rightpanel') as HTMLElement
    if (panel) panel.style.width = ''
  }
  // 互斥：开工作区面板 → 收浏览器面板
  if (workspacePanelOpen.value && browserPanelOpen.value) {
    browserPanelOpen.value = false
    document.documentElement.dataset.browserPanel = 'closed'
  }
}
// ── 浏览器沙箱面板（云桌面 VNC）──
function toggleBrowserPanel() {
  browserPanelOpen.value = !browserPanelOpen.value
  document.documentElement.dataset.browserPanel = browserPanelOpen.value ? 'open' : 'closed'
  // 互斥：开浏览器面板 → 收工作区面板
  if (browserPanelOpen.value && workspacePanelOpen.value) {
    workspacePanelOpen.value = false
    document.documentElement.dataset.workspacePanel = 'closed'
    localStorage.setItem('ua-workspace-panel', 'closed')
    const panel = document.querySelector('.rightpanel') as HTMLElement
    if (panel) panel.style.width = ''
  }
  // 关闭面板时退出接管 + 取消全屏
  if (!browserPanelOpen.value) {
    browserFullscreen.value = false
    document.documentElement.dataset.browserFullscreen = 'off'
    browserTakeoverActive.value = false
  }
}
function toggleBrowserFullscreen() {
  browserFullscreen.value = !browserFullscreen.value
  document.documentElement.dataset.browserFullscreen = browserFullscreen.value ? 'on' : 'off'
}
// 接管云桌面 / 取消接管（与 isStreaming 互斥：run 活跃时禁接管，接管时禁发消息）
function toggleBrowserTakeover() {
  if (isStreaming.value) return  // 智能体执行中不允许接管
  browserTakeoverActive.value = !browserTakeoverActive.value
}
function toggleRail() {
  railExpanded.value = !railExpanded.value
  localStorage.setItem("ua-rail-expanded", String(railExpanded.value))
}
// ── 工作区文件浏览：由共享 <FileBrowser> 自管（树 + 只读 CodeMirror 高亮），ChatPage 仅保留 wsRefreshKey 供定时任务面板误引用兼容 ──
const wsRefreshKey = ref(0)

// ── Theme helpers ──
function applyTheme(t: string) {
  const root = document.documentElement
  if (t === "dark") root.classList.add("dark")
  else root.classList.remove("dark")
  if (t === "system") {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches
    if (prefersDark) root.classList.add("dark")
    else root.classList.remove("dark")
  }
}

function setTheme(t: string) {
  theme.value = t
  localStorage.setItem("ua-theme", t)
  applyTheme(t)
}

function setFontSize(fs: string) {
  fontSize.value = fs
  localStorage.setItem("ua-font-size", fs)
  if (fs === "default") document.documentElement.removeAttribute("data-font-size")
  else document.documentElement.dataset.fontSize = fs
}

// ── Panel switching ──
function switchPanel(panel: string) {
  currentPanel.value = panel
  if (panel === "chat" && !currentSession.value) {
    newSession()
  }
  // Mobile: 切到非 chat 面板时打开 sidebar 抽屉让用户看到对应列表；切回 chat 关闭
  if (isMobile.value) {
    if (panel === "chat") {
      sidebarOpen.value = false
    } else {
      sidebarOpen.value = true
      rightpanelOpen.value = false
    }
  }
}

// Track online/offline status
if (typeof window !== 'undefined') {
  window.addEventListener('online', () => { isOffline.value = false })
  window.addEventListener('offline', () => { isOffline.value = true })
}

// ── Settings actions ──
function downloadTranscript() {
  if (!currentSession.value) return
  const text = currentSession.value.messages
    .map((m: any) => `## ${m.role}\n\n${m.content}`)
    .join("\n\n---\n\n")
  const blob = new Blob([text], { type: "text/markdown" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url; a.download = `transcript-${currentSession.value.session_id}.md`
  a.click(); URL.revokeObjectURL(url)
}

function exportSessionJSON() {
  if (!currentSession.value) return
  const blob = new Blob([JSON.stringify(currentSession.value, null, 2)], { type: "application/json" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url; a.download = `session-${currentSession.value.session_id}.json`
  a.click(); URL.revokeObjectURL(url)
}

function importSessionJSON() {
  const input = document.createElement("input")
  input.type = "file"; input.accept = ".json"
  input.onchange = async (e: any) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      const data = JSON.parse(text)
      if (!data.messages || !Array.isArray(data.messages)) {
        alert("无效的会话文件")
        return
      }
      await newSession()
      if (currentSession.value) {
        currentSession.value.messages = data.messages.map((m: any) => ({
          role: m.role || "user",
          content: m.content || "",
          _ts: m._ts,
        }))
        if (data.title) {
          updateSessionTitle(currentSession.value.session_id, data.title)
        }
      }
    } catch {
      alert("无法解析 JSON 文件")
    }
  }
  input.click()
}

async function clearConversation() {
  if (!currentSession.value || !confirm("清空所有消息？")) return
  await chat.clearConversation()
}

// ── Profile ──
function createProfile() {
  const name = prompt("Profile name:", "default")
  if (name) switchProfile(name)
}

// ── Init ──
onMounted(() => {
  applyTheme(theme.value)
  if (fontSize.value !== "default") {
    document.documentElement.dataset.fontSize = fontSize.value
  }
  const wsInitVal = keepWorkspacePanelOpen.value ? 'open' : 'closed'
  workspacePanelOpen.value = keepWorkspacePanelOpen.value
  document.documentElement.dataset.workspacePanel = wsInitVal
  Promise.all([loadModels(), loadSessions()]).then(() => {
    if (sessions?.value?.length > 0) {
      selectSession(sessions.value[0].session_id)
    } else {
      newSession()
    }
  })
  const wss = workspaceNames?.value
  if (wss?.length) {
    switchWorkspace(wss[0])
  }
  // 中断恢复：续接上次未完成的 pending run（嫁接自 Repo1 resumePendingHermesRuns）
  resumePendingRuns()
})

// Resize handles for sidebar and rightpanel
function initResizeHandle(handleId: string, targetSelector: string, side: 'left' | 'right', minWidth = 180, maxWidth = 500) {
  const handle = document.getElementById(handleId)
  if (!handle) return
  const target = handle.closest(targetSelector) as HTMLElement
  if (!target) return

  let startX = 0, startW = 0

  function onMouseDown(e: MouseEvent) {
    e.preventDefault()
    startX = e.clientX
    startW = target.offsetWidth
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
    document.body.classList.add('resizing')
  }

  function onMouseMove(e: MouseEvent) {
    const dx = e.clientX - startX
    let w = side === 'right' ? startW + dx : startW - dx
    w = Math.max(minWidth, Math.min(maxWidth, w))
    target.style.width = w + 'px'
  }

  function onMouseUp() {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    document.body.classList.remove('resizing')
  }

  handle.addEventListener('mousedown', onMouseDown)
}

// Watch workspace panel preferences
watch(keepWorkspacePanelOpen, (val) => {
  localStorage.setItem("ua-workspace-panel", val ? "open" : "closed")
})

// Persist send key preference
watch(sendKey, (val) => {
  localStorage.setItem("ua-send-key", val)
})

// Persist stream fade preference
watch(streamFadeEffect, (val) => {
  localStorage.setItem("ua-stream-fade", String(val))
})

// Init resize handles after mount
setTimeout(() => {
  initResizeHandle('sidebarResize', '.sidebar', 'right')
  initResizeHandle('rightpanelResize', '.rightpanel', 'left')
}, 0)
</script>
