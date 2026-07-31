/**
 * 智能体相关 DTO。复刻 Android net/dto/AgentDtos.kt。
 */

export class AccessibleAgent {
  id: string = ''
  name: string = ''
  description: string | null = null
  engine_type: string | null = null
  last_accessed_at: string | null = null

  static fromJson(o: Record<string, Object>): AccessibleAgent {
    const r = new AccessibleAgent()
    if (typeof o['id'] === 'string') r.id = o['id']
    if (typeof o['name'] === 'string') r.name = o['name']
    if (typeof o['description'] === 'string') r.description = o['description']
    if (typeof o['engine_type'] === 'string') r.engine_type = o['engine_type']
    if (typeof o['last_accessed_at'] === 'string') r.last_accessed_at = o['last_accessed_at']
    return r
  }
}

export class AgentDeploymentStatus {
  agent_id: string = ''
  status: string = ''
  engine_url: string | null = null
  last_active_at: string | null = null
  error_message: string | null = null
  pod_name: string | null = null
  pod_start_time: string | null = null
  pod_phase: string | null = null

  static fromJson(o: Record<string, Object>): AgentDeploymentStatus {
    const r = new AgentDeploymentStatus()
    if (typeof o['agent_id'] === 'string') r.agent_id = o['agent_id']
    if (typeof o['status'] === 'string') r.status = o['status']
    if (typeof o['engine_url'] === 'string') r.engine_url = o['engine_url']
    if (typeof o['last_active_at'] === 'string') r.last_active_at = o['last_active_at']
    if (typeof o['error_message'] === 'string') r.error_message = o['error_message']
    if (typeof o['pod_name'] === 'string') r.pod_name = o['pod_name']
    if (typeof o['pod_start_time'] === 'string') r.pod_start_time = o['pod_start_time']
    if (typeof o['pod_phase'] === 'string') r.pod_phase = o['pod_phase']
    return r
  }
}

export class AgentModelItem {
  id: string = ''
  object: string | null = null
  provider: string | null = null

  static fromJson(o: Record<string, Object>): AgentModelItem {
    const r = new AgentModelItem()
    if (typeof o['id'] === 'string') r.id = o['id']
    if (typeof o['object'] === 'string') r.object = o['object']
    if (typeof o['provider'] === 'string') r.provider = o['provider']
    return r
  }
}

export class AgentModelsResponse {
  object: string | null = null
  data: AgentModelItem[] = []

  static fromJson(o: Record<string, Object>): AgentModelsResponse {
    const r = new AgentModelsResponse()
    if (typeof o['object'] === 'string') r.object = o['object']
    const data = o['data']
    if (data instanceof Array) {
      r.data = data.filter((v: Object) => v instanceof Object)
        .map((v: Object) => AgentModelItem.fromJson(v as Record<string, Object>))
    }
    return r
  }
}
