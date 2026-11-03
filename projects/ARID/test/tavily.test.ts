import { test, describe, after } from "node:test";
import assert from "node:assert/strict";
import {
  tavilySearch,
  tavilyKeyFromEnv,
  redactKey,
  TavilyError,
  type TavilyClient,
} from "../src/tavily/search.js";

// 本文件全程不联网：注入 fake client，只断言传给 SDK 的参数与归一化结果。
// tavilySearch 内部用 tavilyKeyFromEnv() 读进程级 TAVILY_API_KEY，
// 这里在文件级设一个测试用的假 key（非真实密钥），跑完还原。
const FAKE_KEY = "tvly-test-fake-key-0000";
const KEY_BACKUP = process.env.TAVILY_API_KEY;
process.env.TAVILY_API_KEY = FAKE_KEY;

after(() => {
  if (KEY_BACKUP === undefined) delete process.env.TAVILY_API_KEY;
  else process.env.TAVILY_API_KEY = KEY_BACKUP;
});

/** fake client：记录收到的 (query, options)，并返回脚本化的响应。 */
function makeFake(response: unknown): TavilyClient & { lastQuery?: string; lastOptions?: Record<string, unknown> } {
  const fake: TavilyClient & {
    lastQuery?: string;
    lastOptions?: Record<string, unknown>;
  } = {
    lastQuery: undefined,
    lastOptions: undefined,
    async search(query, options) {
      fake.lastQuery = query;
      fake.lastOptions = options;
      return response;
    },
  };
  return fake;
}

/** 构造标准 SDK 响应样例：两条 results，含 title/content/score/publishedDate。 */
function sdkResponseSample(): Record<string, unknown> {
  return {
    query: "DeepSeek",
    answer: "DeepSeek 是一家 AI 公司",
    results: [
      {
        title: "DeepSeek 官方",
        url: "https://www.deepseek.com/",
        content: "深度求索官方首页",
        score: 0.98,
        publishedDate: "2024-01-01",
      },
      {
        title: "DeepSeek 论文",
        url: "https://arxiv.org/abs/xxxx",
        content: "论文摘要内容",
        score: 0.9,
        publishedDate: "2024-02-02",
      },
    ],
  };
}

describe("tavilySearch", () => {
  test("归一化：results → sources（content→snippet、publishedDate→publishedAt），answer 透传", async () => {
    const client = makeFake(sdkResponseSample());
    const result = await tavilySearch("DeepSeek", undefined, {
      createClient: () => client,
    });
    assert.equal(result.query, "DeepSeek");
    assert.equal(result.answer, "DeepSeek 是一家 AI 公司");
    assert.equal(result.sources.length, 2);
    assert.equal(result.sources[0]!.url, "https://www.deepseek.com/");
    assert.equal(result.sources[0]!.title, "DeepSeek 官方");
    assert.equal(result.sources[0]!.snippet, "深度求索官方首页");
    assert.equal(result.sources[0]!.score, 0.98);
    assert.equal(result.sources[0]!.publishedAt, "2024-01-01");
    assert.equal(result.sources[1]!.url, "https://arxiv.org/abs/xxxx");
    assert.equal(result.sources[1]!.publishedAt, "2024-02-02");
  });

  test("默认参数：advanced / 5 / includeAnswer=true / includeRawContent=false", async () => {
    const client = makeFake(sdkResponseSample());
    await tavilySearch("DeepSeek", undefined, { createClient: () => client });
    assert.deepEqual(client.lastOptions, {
      searchDepth: "advanced",
      maxResults: 5,
      includeAnswer: true,
      includeRawContent: false,
    });
  });

  test("显式传参覆盖默认", async () => {
    const client = makeFake(sdkResponseSample());
    await tavilySearch("DeepSeek", { searchDepth: "basic", maxResults: 3, includeAnswer: false, includeRawContent: true }, {
      createClient: () => client,
    });
    assert.deepEqual(client.lastOptions, {
      searchDepth: "basic",
      maxResults: 3,
      includeAnswer: false,
      includeRawContent: true,
    });
  });

  test("apiKey 缺失 → CREDENTIAL_MISSING", async () => {
    const saved = process.env.TAVILY_API_KEY;
    delete process.env.TAVILY_API_KEY;
    try {
      await assert.rejects(
        tavilySearch("DeepSeek", undefined, { createClient: () => makeFake(sdkResponseSample()) }),
        (err: unknown) =>
          err instanceof TavilyError &&
          err.code === "CREDENTIAL_MISSING" &&
          !String((err as Error).message).includes("tvly"),
      );
    } finally {
      if (saved === undefined) delete process.env.TAVILY_API_KEY;
      else process.env.TAVILY_API_KEY = saved;
    }
  });

  test("响应结构非法（results 非数组）→ INVALID_RESPONSE", async () => {
    const client = makeFake({ query: "x", results: "not-an-array" });
    await assert.rejects(
      tavilySearch("DeepSeek", undefined, { createClient: () => client }),
      (err: unknown) => err instanceof TavilyError && err.code === "INVALID_RESPONSE",
    );
  });

  test("响应非对象 → INVALID_RESPONSE", async () => {
    const client = makeFake("plain-text");
    await assert.rejects(
      tavilySearch("DeepSeek", undefined, { createClient: () => client }),
      (err: unknown) => err instanceof TavilyError && err.code === "INVALID_RESPONSE",
    );
  });
});

describe("tavilyKeyFromEnv", () => {
  test("有值 → trim 后返回", () => {
    assert.equal(tavilyKeyFromEnv({ TAVILY_API_KEY: "  tvly-abc  " }), "tvly-abc");
  });

  test("空白 → undefined", () => {
    assert.equal(tavilyKeyFromEnv({ TAVILY_API_KEY: "   " }), undefined);
  });

  test("缺失 → undefined", () => {
    assert.equal(tavilyKeyFromEnv({}), undefined);
  });
});

describe("redactKey", () => {
  test("长 key 只露头尾 4 字符 ", () => {
    assert.equal(redactKey("tvly-abcdefghijklmnop"), "tvly****mnop");
  });

  test("短 key（长度 ≤ 8）整体遮住", () => {
    assert.equal(redactKey("short"), "****");
    assert.equal(redactKey("12345678"), "****");
  });

  test("结果不包含完整 key", () => {
    const key = "tvly-0123456789abcdef";
    const redacted = redactKey(key);
    assert.ok(!redacted.includes(key));
    assert.ok(!redacted.includes(key.slice(2)));
  });
});
