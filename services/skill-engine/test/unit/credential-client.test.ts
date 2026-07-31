import { describe, it, expect, afterEach, vi } from "vitest";
import { LlmCredentialClient } from "../../src/llm/credential-client.js";

describe("LlmCredentialClient", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns credentials on success and sends the internal token", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ api_key: "sk-test-key", base_url: "http://litellm:4000/v1" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const client = new LlmCredentialClient("http://manager:8002", "tok-123");
    const creds = await client.fetchCredentials();

    expect(creds).toEqual({ apiKey: "sk-test-key", baseUrl: "http://litellm:4000/v1" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-Internal-Token"]).toBe("tok-123");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://manager:8002/api/manager/internal/skill-engine/litellm-key",
    );
  });

  it("returns null on 401 (bad token)", async () => {
    vi.stubGlobal("fetch", async () => ({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Invalid internal token" }),
    }));
    const client = new LlmCredentialClient("http://manager:8002", "wrong");
    expect(await client.fetchCredentials()).toBeNull();
  });

  it("returns null on network error", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new Error("ECONNREFUSED");
    });
    const client = new LlmCredentialClient("http://manager:8002", "tok");
    expect(await client.fetchCredentials()).toBeNull();
  });

  it("returns null when api_key is empty", async () => {
    vi.stubGlobal("fetch", async () => ({
      ok: true,
      status: 200,
      json: async () => ({ api_key: "", base_url: "x" }),
    }));
    const client = new LlmCredentialClient("http://manager:8002", "tok");
    expect(await client.fetchCredentials()).toBeNull();
  });
});
