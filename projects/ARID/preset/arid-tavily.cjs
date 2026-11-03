/*
 * ARID Tavily 网络搜索插件（agent preset 挂载）。
 *
 * 零依赖：不 require 任何 npm 包，直接 fetch Tavily REST API。
 * 走 DSH web seam：ctx.web.registerSearchProvider({ id, available, search })。
 * 响应归一化成 seam 格式 { sources: [{url,title?,snippet?,publishedAt?}], truncated: false }。
 *
 * 密钥纪律：process.env.TAVILY_API_KEY 只在请求体里使用，任何错误 message 不包含 key。
 * 错误码沿用 DSH web 约定：WEB_PROVIDER_ERROR / WEB_PROVIDER_CREDENTIAL_MISSING / WEB_ABORTED。
 */

const API_BASE = "https://api.tavily.com/search"

/** 解析进程级 TAVILY_API_KEY；trim 后非空才有效。 */
function apiKey() {
  const raw = process.env.TAVILY_API_KEY
  return typeof raw === "string" && raw.trim() ? raw : undefined
}

/** 构造带 code 的 WebError（不包含 key）。 */
function webError(code, message, cause) {
  const err = new Error(message, cause === undefined ? undefined : { cause })
  err.code = code
  return err
}

/** 求值 max_results：TAVILY_MAX_RESULTS 是正整数则用之，否则默认 5。 */
function maxResultsFromEnv() {
  const n = Number(process.env.TAVILY_MAX_RESULTS)
  return Number.isInteger(n) && n > 0 ? n : 5
}

/** 求值 search_depth：环境变量合法才用，默认 advanced。 */
function searchDepthFromEnv() {
  const v = process.env.TAVILY_SEARCH_DEPTH
  return v === "advanced" || v === "basic" ? v : "advanced"
}

/** 求值 include_answer：TAVILY_INCLUDE_ANSWER !== 'false' 才为 true。 */
function includeAnswerFromEnv() {
  return process.env.TAVILY_INCLUDE_ANSWER !== "false"
}

/**
 * 把 Tavily 响应归一化成 seam 格式。
 * SDK 字段 content → snippet、published_date/publishedDate → publishedAt；
 * 相同 url 只保留首条（去重）。
 */
function mapSources(data) {
  if (data === null || typeof data !== "object" || !Array.isArray(data.results)) {
    throw webError("WEB_PROVIDER_ERROR", "Tavily returned an invalid response body (no results array)")
  }
  const seen = new Set()
  const sources = []
  for (const item of data.results) {
    if (item === null || typeof item !== "object") continue
    const url = typeof item.url === "string" ? item.url : ""
    if (!url || seen.has(url)) continue
    seen.add(url)
    const source = { url }
    if (typeof item.title === "string" && item.title) source.title = item.title
    // content → snippet（SDK 主字段）；容错 raw_content
    let snippet = typeof item.content === "string" ? item.content : ""
    if (!snippet) snippet = typeof item.raw_content === "string" ? item.raw_content : ""
    if (snippet) source.snippet = snippet
    const published =
      typeof item.published_date === "string"
        ? item.published_date
        : typeof item.publishedDate === "string"
          ? item.publishedDate
          : undefined
    if (published) source.publishedAt = published
    sources.push(source)
  }
  return { sources, truncated: false }
}

module.exports = {
  name: "web-search-tavily",
  inject: ["web"],
  async apply(ctx) {
    ctx.effect(() =>
      ctx.web.registerSearchProvider({
        id: "tavily",
        available() {
          // 只有配好 key 才可用
          return apiKey() !== undefined
        },
        async search(request, signal) {
          // key 缺失 → CREDENTIAL_MISSING
          const key = apiKey()
          if (!key) {
            throw webError("WEB_PROVIDER_CREDENTIAL_MISSING", "Tavily search has no TAVILY_API_KEY; set it in .env")
          }
          // 调用方已中止 → WEB_ABORTED
          if (signal && signal.aborted) {
            throw webError("WEB_ABORTED", "Tavily search aborted")
          }

          const body = {
            api_key: key,
            query: request.query,
            search_depth: searchDepthFromEnv(),
            max_results: maxResultsFromEnv(),
            include_answer: includeAnswerFromEnv(),
            include_raw_content: false,
          }

          let response
          try {
            response = await fetch(API_BASE, {
              method: "POST",
              headers: { "content-type": "application/json", accept: "application/json" },
              body: JSON.stringify(body),
              ...(signal !== undefined ? { signal } : {}),
            })
          } catch (error) {
            if (signal && signal.aborted) {
              throw webError("WEB_ABORTED", "Tavily search aborted", error)
            }
            // 网络/协议异常；message 不含 key
            throw webError("WEB_PROVIDER_ERROR", `Tavily search request failed: ${String(error)}`, error)
          }

          if (!response.ok) {
            // 非 2xx → WEB_PROVIDER_ERROR；尽量取服务端 error 描述（不含 key）
            let detail = ""
            try {
              const parsed = await response.json()
              const e = parsed && typeof parsed.error === "object" ? parsed.error : parsed
              if (e && typeof e.message === "string") detail = e.message
              else if (typeof parsed.message === "string") detail = parsed.message
            } catch (error) {
              // 响应体解析失败不掩盖原状态
            }
            throw webError(
              "WEB_PROVIDER_ERROR",
              detail && detail.length > 0
                ? `Tavily API error: ${detail}`
                : `Tavily API error (HTTP ${response.status})`,
            )
          }

          try {
            const data = await response.json()
            return mapSources(data)
          } catch (error) {
            // 结构非法 → WEB_PROVIDER_ERROR
            if (error && error.code) throw error
            throw webError("WEB_PROVIDER_ERROR", `Tavily returned an unprocessable response: ${String(error)}`, error)
          }
        },
      }),
    )
  },
}
