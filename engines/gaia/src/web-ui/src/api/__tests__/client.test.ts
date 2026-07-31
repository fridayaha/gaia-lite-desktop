import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ApiError } from '../client';

// request() 内部用全局 fetch；这里直接测试 ApiError 的结构化字段解析。
// 用 vi.stubGlobal 模拟 fetch 返回不同错误响应。

async function fetchThrowing(status: number, body: string): Promise<void> {
  const fetchMock = vi.fn().mockResolvedValue({
    status,
    ok: false,
    statusText: 'Err',
    text: async () => body,
    json: async () => JSON.parse(body),
  });
  vi.stubGlobal('fetch', fetchMock);
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('ApiError structured parsing (DATASOURCE_UNREACHABLE regression)', () => {
  // 后端统一错误格式：{ detail, error_type, code }
  // request() 应解析出 code/detail/errorType，而非把整个 JSON 字符串当 message。

  it('解析数据源不可达错误（502 + DATASOURCE_UNREACHABLE）', async () => {
    await fetchThrowing(
      502,
      JSON.stringify({
        detail: '无法连接到数据源 mysql@localhost:3306',
        error_type: 'DataSourceUnreachableError',
        code: 'DATASOURCE_UNREACHABLE',
      }),
    );
    // 动态 import 确保拿到 stub 后的 fetch
    const { exploreDataSource } = await import('../client');
    await expect(exploreDataSource('x')).rejects.toMatchObject({
      status: 502,
      code: 'DATASOURCE_UNREACHABLE',
      detail: '无法连接到数据源 mysql@localhost:3306',
      errorType: 'DataSourceUnreachableError',
    });
  });

  it('解析 Trino 不可用错误（503 + TRINO_UNAVAILABLE）', async () => {
    await fetchThrowing(
      503,
      JSON.stringify({
        detail: 'Trino server unreachable',
        error_type: 'TrinoUnavailableError',
        code: 'TRINO_UNAVAILABLE',
      }),
    );
    const { exploreDataSource } = await import('../client');
    await expect(exploreDataSource('x')).rejects.toMatchObject({
      status: 503,
      code: 'TRINO_UNAVAILABLE',
    });
  });

  it('非 JSON 错误响应退化为原始 body', async () => {
    // 如 nginx 502 返回 HTML
    await fetchThrowing(502, '<html>Bad Gateway</html>');
    const { exploreDataSource } = await import('../client');
    const err = await exploreDataSource('x').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(502);
    expect(err.code).toBeUndefined();
    expect(err.message).toContain('Bad Gateway');
  });

  it('成功响应不抛错', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => '',
      json: async () => ({ database: 'public', tables: [] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const { exploreDataSource } = await import('../client');
    const r = await exploreDataSource('x');
    expect(r).toMatchObject({ database: 'public', tables: [] });
  });
});
