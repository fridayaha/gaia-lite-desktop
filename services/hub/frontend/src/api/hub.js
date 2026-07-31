const BASE = '/api'

async function request(url, options = {}) {
  const res = await fetch(BASE + url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = data?.detail || res.statusText
    throw new Error(detail)
  }
  return data
}

function post(url, body) {
  return request(url, { method: 'POST', body: JSON.stringify(body) })
}

function put(url, body) {
  return request(url, { method: 'PUT', body: JSON.stringify(body) })
}

export function fetchItems(params = {}) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v) qs.set(k, v)
  })
  return request('/hub/items?' + qs.toString())
}

export function fetchItem(id) {
  return request(`/hub/items/${id}`)
}

export function createItem(data) {
  return post('/hub/items', data)
}

export function updateItem(id, data) {
  return put(`/hub/items/${id}`, data)
}

export function createVersion(itemId, data) {
  return post(`/hub/items/${itemId}/versions`, { hub_item_id: itemId, ...data })
}

export function fetchVersions(itemId) {
  return request(`/hub/items/${itemId}/versions`)
}

export function scanVersion(versionId) {
  return post(`/hub/versions/${versionId}/scan`, { operator: 'demo_admin' })
}

export function getScanReport(versionId) {
  return request(`/hub/versions/${versionId}/scan-report`)
}

export function submitReview(versionId) {
  return post(`/hub/versions/${versionId}/submit-review`, { operator: 'demo_admin' })
}

export function approveVersion(versionId, comment) {
  return post(`/hub/versions/${versionId}/approve`, { operator: 'demo_admin', comment: comment || '' })
}

export function rejectVersion(versionId, comment) {
  return post(`/hub/versions/${versionId}/reject`, { operator: 'demo_admin', comment: comment || '' })
}

export function requestChange(versionId, comment) {
  return post(`/hub/versions/${versionId}/request-change`, { operator: 'demo_admin', comment: comment || '' })
}

export function publishVersion(versionId) {
  return post(`/hub/versions/${versionId}/publish`, { operator: 'demo_admin' })
}

export function submitItem(itemId) {
  return post(`/hub/items/${itemId}/submit`, { operator: 'demo_admin' })
}

export function disableItem(itemId) {
  return post(`/hub/items/${itemId}/disable`, { operator: 'demo_admin' })
}

export function archiveItem(itemId) {
  return post(`/hub/items/${itemId}/archive`, { operator: 'demo_admin' })
}

export function rollbackItem(itemId, targetVersionId, reason) {
  return post(`/hub/items/${itemId}/rollback`, {
    target_version_id: targetVersionId,
    operator: 'demo_admin',
    reason: reason || '',
  })
}

export function initPresets() {
  return post('/hub/presets/init')
}

export async function importPackage(file) {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch('/api/hub/imports/package', {
    method: 'POST',
    body: formData,
  })
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = data?.detail
    const msg = typeof detail === 'string' ? detail : JSON.stringify(detail)
    throw new Error(msg || res.statusText)
  }
  return data
}

export function fetchItemRelations(itemId) {
  return request(`/hub/items/${itemId}/relations`)
}

export function discoverCapabilities(params = {}) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, v)
  })
  return request('/runtime/capabilities/discover?' + qs.toString())
}

export function resolveCapability(itemId) {
  return request(`/runtime/capabilities/${itemId}/resolve`)
}
