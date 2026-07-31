package com.unionagents.enduser.ui.chat.components

/**
 * 活动事件流条目（对齐 web 端 `activityEvents`）：
 * 紧凑的审计日志，记录 run 启动 / 模型 / 工具 / 审批 / 失败等过程事件，
 * 供活动 feed 渲染。流式期间维护在 UI state；回复落定时随消息快照一份
 * （`Message.liveActivityEvents`），渲染在回复气泡上方的中间过程收起栏内。
 *
 * - kind：model / tool / run / waiting / warning
 * - status：waiting（进行中）/ done（已完成）/ error（失败）
 * - ts：epoch 秒
 */
data class ActivityEvent(
    val kind: String,
    val label: String,
    val detail: String? = null,
    val status: String,
    val ts: Long,
)

/** 追加一条事件，时间戳自动取当前 epoch 秒。 */
fun pushActivity(
    events: List<ActivityEvent>,
    kind: String,
    label: String,
    detail: String? = null,
    status: String = "waiting",
): List<ActivityEvent> = events + ActivityEvent(
    kind = kind,
    label = label,
    detail = detail,
    status = status,
    ts = System.currentTimeMillis() / 1000,
)

/** 移除指定 kind 的全部事件（如 tool.started 到来时清掉 'waiting' 占位）。 */
fun filterKind(events: List<ActivityEvent>, kind: String): List<ActivityEvent> =
    events.filter { it.kind != kind }

/** 把指定 kind 且 status='waiting' 的事件标记为 done；可选更新 label / 清 detail；ts 刷新为完成时刻。 */
fun markKindDone(
    events: List<ActivityEvent>,
    kind: String,
    newLabel: String? = null,
): List<ActivityEvent> = events.map {
    if (it.kind == kind && it.status == "waiting") {
        it.copy(status = "done", label = newLabel ?: it.label, detail = null, ts = System.currentTimeMillis() / 1000)
    } else it
}
