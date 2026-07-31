/**
 * Tests for the auth-client + jwt-store JWT helpers (ADR-016/017 Phase 5).
 *
 * `decodeJwtPayload`, `getJwt`, `setJwt`, `clearJwt` (in jwt-store) are pure
 * utility functions tested directly. `fetchJwt` (in auth-client) calls the
 * Better Auth client SDK — mocked here.
 */
import { describe, it, expect, vi, beforeEach, beforeAll, afterAll } from 'vitest';

// Mock the Better Auth client SDK before importing auth-client. Use
// vi.hoisted so the mock fn is initialized before the factory runs (vitest
// hoists vi.mock calls above imports, but the factory closure captures
// variables at call time — hoisted refs avoid TDZ errors).
const { tokenMock } = vi.hoisted(() => ({ tokenMock: vi.fn() }));
vi.mock('better-auth/react', () => ({
  createAuthClient: () => ({
    token: tokenMock,
    useSession: () => ({ data: null, isPending: false }),
    signIn: { email: vi.fn() },
    signOut: vi.fn(),
    signUp: { email: vi.fn() },
  }),
}));
vi.mock('better-auth/client/plugins', () => ({ jwtClient: () => ({ id: 'jwt' }) }));

import { decodeJwtPayload, getJwt, setJwt, clearJwt } from '../jwt-store';
import { fetchJwt, AUTH_ENABLED } from '../auth-client';

function makeJwt(payload: object): string {
  // header.payload.signature (signature omitted — not validated by decode)
  const header = btoa(JSON.stringify({ alg: 'EdDSA', typ: 'JWT' }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.sig`;
}

describe('decodeJwtPayload', () => {
  it('decodes a standard JWT payload', () => {
    const token = makeJwt({ sub: 'user-1', email: 'a@b.com', roles: ['admin'] });
    const payload = decodeJwtPayload(token);
    expect(payload).toMatchObject({ sub: 'user-1', email: 'a@b.com', roles: ['admin'] });
  });

  it('handles base64url encoding (dashes/underscores)', () => {
    // Construct a base64url payload manually.
    const json = JSON.stringify({ sub: 'x-y_z' });
    const b64url = btoa(json).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const token = `header.${b64url}.sig`;
    expect(decodeJwtPayload(token)).toMatchObject({ sub: 'x-y_z' });
  });

  it('returns null on a malformed token', () => {
    expect(decodeJwtPayload('not-a-jwt')).toBeNull();
    expect(decodeJwtPayload('')).toBeNull();
  });
});

describe('jwt-store lifecycle', () => {
  beforeEach(() => {
    localStorage.clear();
    clearJwt();
  });

  it('setJwt stores a JWT in cache and localStorage', () => {
    setJwt('stored-token');
    expect(getJwt()).toBe('stored-token');
    expect(localStorage.getItem('gaia.jwt')).toBe('stored-token');
  });

  it('getJwt returns null when nothing is stored', () => {
    expect(getJwt()).toBeNull();
  });

  it('clearJwt removes the token from cache and storage', () => {
    setJwt('token');
    expect(getJwt()).toBe('token');
    clearJwt();
    expect(getJwt()).toBeNull();
    expect(localStorage.getItem('gaia.jwt')).toBeNull();
  });

  it('jwt-store reads localStorage on module init (page reload simulation)', async () => {
    // Set localStorage, then re-import the module to simulate a reload.
    localStorage.setItem('gaia.jwt', 'reload-token');
    // Dynamic re-import — vitest caches modules, so use vi.resetModules.
    vi.resetModules();
    const { getJwt: freshGetJwt } = await import('../jwt-store');
    expect(freshGetJwt()).toBe('reload-token');
  });
});

// fetchJwt 的行为受 AUTH_ENABLED 模块常量控制（import 时定死）。
// 为避免测试依赖 .env.local 的 VITE_AUTH_ENABLED，这里显式 stubEnv=true
// 并动态 re-import auth-client，保证 fetchJwt 真正调用被 mock 的 token 接口。
describe('fetchJwt', () => {
  let fetchJwtEnabled: typeof fetchJwt;
  let dynGetJwt: typeof getJwt;
  let dynSetJwt: typeof setJwt;
  let dynClearJwt: typeof clearJwt;

  beforeAll(async () => {
    vi.stubEnv('VITE_AUTH_ENABLED', 'true');
    vi.resetModules();
    // 动态 re-import：auth-client 与 jwt-store 必须是同一个重新加载的模块实例，
    // 否则 setJwt 写入的缓存与 getJwt 读取的缓存不在同一作用域。
    const authMod = await import('../auth-client');
    const jwtMod = await import('../jwt-store');
    fetchJwtEnabled = authMod.fetchJwt;
    dynGetJwt = jwtMod.getJwt;
    dynSetJwt = jwtMod.setJwt;
    dynClearJwt = jwtMod.clearJwt;
  });
  afterAll(() => vi.unstubAllEnvs());

  beforeEach(() => {
    dynClearJwt();
    tokenMock.mockReset();
  });

  it('fetches, caches, and persists a JWT from the Better Auth client', async () => {
    tokenMock.mockResolvedValue({ data: { token: 'fresh-jwt' }, error: null });
    const token = await fetchJwtEnabled();
    expect(token).toBe('fresh-jwt');
    expect(dynGetJwt()).toBe('fresh-jwt');
    expect(localStorage.getItem('gaia.jwt')).toBe('fresh-jwt');
  });

  it('returns null and clears cache when the client returns an error', async () => {
    dynSetJwt('stale-token');
    tokenMock.mockResolvedValue({ data: null, error: { message: 'no session' } });
    const token = await fetchJwtEnabled();
    expect(token).toBeNull();
    expect(dynGetJwt()).toBeNull();
  });

  it('returns null when the client throws', async () => {
    tokenMock.mockRejectedValue(new Error('network'));
    const token = await fetchJwtEnabled();
    expect(token).toBeNull();
    expect(dynGetJwt()).toBeNull();
  });
});

describe('AUTH_ENABLED', () => {
  it('is a boolean reflecting the VITE_AUTH_ENABLED env flag', () => {
    expect(typeof AUTH_ENABLED).toBe('boolean');
  });
});
