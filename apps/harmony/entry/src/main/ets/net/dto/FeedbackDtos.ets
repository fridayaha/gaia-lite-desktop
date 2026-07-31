/**
 * 消息级反馈 / 收藏 DTO。复刻 Android net/dto/FeedbackDtos.kt。
 * 字段名对齐 manager /api/manager/message-feedback(s)。
 */

export class FeedbackUpsertRequest {
  agent_id: string = ''
  session_id: string = ''
  message_ref: string = ''
  run_id: string | null = null
  /** "up" / "down" / null=取消 */
  value: string | null = null
  /** down 时必选：inaccurate/harmful/off_topic/other */
  reason: string | null = null
  comment: string | null = null
  content_snapshot: string = ''
}

export class FeedbackItem {
  session_id: string | null = null
  message_ref: string | null = null
  run_id: string | null = null
  value: string | null = null
  reason: string | null = null
  comment: string | null = null

  static fromJson(o: Record<string, Object>): FeedbackItem {
    const r = new FeedbackItem()
    if (typeof o['session_id'] === 'string') r.session_id = o['session_id']
    if (typeof o['message_ref'] === 'string') r.message_ref = o['message_ref']
    if (typeof o['run_id'] === 'string') r.run_id = o['run_id']
    if (typeof o['value'] === 'string') r.value = o['value']
    if (typeof o['reason'] === 'string') r.reason = o['reason']
    if (typeof o['comment'] === 'string') r.comment = o['comment']
    return r
  }
}

export class FavoriteUpsertRequest {
  agent_id: string = ''
  session_id: string = ''
  message_ref: string = ''
  run_id: string | null = null
  content_snapshot: string = ''
}

export class FavoriteDeleteRequest {
  session_id: string = ''
  message_ref: string = ''
}

export class FavoriteItem {
  id: string | null = null
  agent_id: string | null = null
  agent_name: string | null = null
  session_id: string | null = null
  message_ref: string | null = null
  content_snapshot: string | null = null
  created_at: string | null = null

  static fromJson(o: Record<string, Object>): FavoriteItem {
    const r = new FavoriteItem()
    if (typeof o['id'] === 'string') r.id = o['id']
    if (typeof o['agent_id'] === 'string') r.agent_id = o['agent_id']
    if (typeof o['agent_name'] === 'string') r.agent_name = o['agent_name']
    if (typeof o['session_id'] === 'string') r.session_id = o['session_id']
    if (typeof o['message_ref'] === 'string') r.message_ref = o['message_ref']
    if (typeof o['content_snapshot'] === 'string') r.content_snapshot = o['content_snapshot']
    if (typeof o['created_at'] === 'string') r.created_at = o['created_at']
    return r
  }
}
