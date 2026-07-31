/**
 * Tests for the API client auth-token injection (ADR-016/017 Phase 5).
 *
 * Verifies that `request()` and `authFetch()` attach the JWT (read from the
 * shared `jwt-store`), omit it when none is stored (dev fallback), and clear
 * the token on a 401 response.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { request, authFetch, ApiError } from '../client';
import { setJwt, clearJwt } from '../../lib/jwt-store';

// Mock global fetch.
const fetchMock = vi.fn();
vi.stubGlobal('fetch', fetchMock);

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    headers: new Headers(),
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as Response;
}

describe('request() auth header injection', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    clearJwt();
  });

  it('omits Authorization when no JWT is stored (dev fallback)', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    await request('/auth/me');
    const callOpts = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = callOpts.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it('attaches Authorization: Bearer <jwt> when a token is available', async () => {
    setJwt('fake-jwt-123');
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    await request('/ontologies');
    const callOpts = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = callOpts.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer fake-jwt-123');
  });

  it('preserves a caller-supplied Authorization header', async () => {
    setJwt('auto-jwt');
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    await request('/x', {
      headers: { Authorization: 'Bearer override' },
    });
    const callOpts = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = callOpts.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer override');
  });

  it('clears the stored JWT on a 401 response', async () => {
    setJwt('expired-jwt');
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'unauthorized' }, 401));
    await expect(request('/authz/check')).rejects.toThrow();
    // JWT should be cleared after 401.
    // Re-import to check — jwt-store is a singleton, getJwt() reflects the
    // cleared state.
    const { getJwt } = await import('../../lib/jwt-store');
    expect(getJwt()).toBeNull();
  });

  it('throws ApiError with status on non-ok responses', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'nope' }, 403));
    await expect(request('/x')).rejects.toMatchObject({
      name: 'ApiError',
      status: 403,
    });
  });
});

describe('authFetch()', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    clearJwt();
  });

  it('attaches the JWT and returns the raw Response', async () => {
    setJwt('jwt-for-stream');
    const raw = { ok: true, status: 200, body: { getReader: () => {} } } as unknown as Response;
    fetchMock.mockResolvedValue(raw);
    const res = await authFetch('/ai/stream', { method: 'POST' });
    expect(res).toBe(raw);
    const callOpts = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = callOpts.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer jwt-for-stream');
  });
});

describe('ApiError', () => {
  it('parses backend error fields from JSON body', () => {
    const err = new ApiError('msg', 502, {
      code: 'TRINO_UNAVAILABLE',
      detail: 'Trino down',
      errorType: 'TrinoUnavailableError',
    });
    expect(err.status).toBe(502);
    expect(err.code).toBe('TRINO_UNAVAILABLE');
    expect(err.detail).toBe('Trino down');
    expect(err.errorType).toBe('TrinoUnavailableError');
  });
});
