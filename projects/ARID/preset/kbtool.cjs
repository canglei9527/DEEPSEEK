/*
 * 本地知识库检索插件（固化版）
 *
 * 将 E:/kb 封装为三个模型工具：kb_search（全文检索）、
 * kb_read（读文档）、kb_list（浏览目录）。
 *
 * 零依赖：不 require 任何 npm 包，构造的是 tools 注册表原生 ToolDefinition。
 * 生命周期全部归入 apply 的 fiber（ctx.effect），随 preset 挂载/卸载。
 */

const KB_ROOT = 'E:/kb'
const MAX_FILE_BYTES = 5 * 1024 * 1024
const MAX_DEPTH = 8
const MAX_FILES = 400

module.exports = {
  name: 'dsh-kb-tools',
  async apply(ctx) {
    const fs = ctx.get('fs')
    if (fs === undefined) return
    const tools = ctx.get('tools')
    if (tools === undefined) return

    const renderJson = (value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }]
    // 原生 tools.register 要求原始 JSON Schema（非 defineTool DSL）
    const output = { schema: { type: 'object' }, render: (_a, value) => renderJson(value) }

    const resolveInside = async (rel) => {
      const root = await fs.resolve(KB_ROOT)
      const target = await fs.resolve(KB_ROOT + '/' + rel)
      if (!fs.contains(root, target)) {
        const err = new Error('path escapes knowledge base root')
        err.kbCode = 'KB_OUTSIDE'
        throw err
      }
      return { root, target }
    }

    const collectFiles = async (signal) => {
      const root = await fs.resolve(KB_ROOT)
      const rootInfo = await fs.stat(root, signal)
      if (rootInfo === undefined) throw new Error('knowledge base root missing: ' + KB_ROOT)
      const files = []
      const walk = async (dirTarget, rel, depth) => {
        if (depth > MAX_DEPTH || files.length >= MAX_FILES) return
        let entries
        try {
          entries = await fs.listDir(dirTarget, signal)
        } catch {
          return
        }
        for (const entry of entries) {
          if (signal && signal.aborted) throw new Error('aborted')
          const childRel = rel ? rel + '/' + entry.name : entry.name
          if (entry.type === 'directory') {
            await walk(entry.target, childRel, depth + 1)
          } else if (entry.type === 'file') {
            if (files.length >= MAX_FILES) return
            files.push({ name: childRel, target: entry.target, size: entry.size })
          }
        }
      }
      await walk(root, '', 0)
      return files
    }

    // —— kb_search ——
    ctx.effect(() => tools.register({
      name: 'kb_search',
      description: '全文关键词搜索本地知识库 E:/kb。按关键词在所有文档中检索（大小写不敏感，空格分隔的多个词任一命中即算），返回命中的文件和匹配行。适合回答"知识库里有没有提到 X""查一下 X 相关的内容"。',
      parameters: {
        type: 'object',
        properties: {
          query: { type: 'string', description: '搜索关键词，多个词用空格分隔，任一命中即算匹配' },
          limit: { type: 'integer', description: '最多返回的文件数，默认 10，最大 50' },
        },
        required: ['query'],
      },
      output,
      async execute(args, exec) {
        const query = String(args.query || '').trim()
        if (!query) return { ok: false, error: 'query 不能为空' }
        const limit = Math.min(Math.max(Number(args.limit) || 10, 1), 50)
        const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
        let files
        try {
          files = await collectFiles(exec.signal)
        } catch (e) {
          return { ok: false, error: (e && e.message) || String(e) }
        }
        const results = []
        let scanned = 0
        for (const file of files) {
          if (exec.signal && exec.signal.aborted) throw new Error('aborted')
          if (file.size !== undefined && file.size > MAX_FILE_BYTES) continue
          let content
          try {
            content = await fs.readText(file.target, exec.signal)
          } catch {
            continue
          }
          scanned += 1
          const lines = content.split(/\r?\n/)
          let score = 0
          const matched = []
          for (let i = 0; i < lines.length && matched.length < 8; i++) {
            const lower = lines[i].toLowerCase()
            let hits = 0
            for (const term of terms) {
              let idx = lower.indexOf(term)
              while (idx !== -1) {
                hits += 1
                idx = lower.indexOf(term, idx + term.length)
              }
            }
            if (hits > 0) {
              score += hits
              let snippet = lines[i].trim()
              if (snippet.length > 150) snippet = snippet.slice(0, 150) + '…'
              matched.push({ line: i + 1, text: snippet })
            }
          }
          if (score > 0) results.push({ path: file.name, matches: score, lines: matched })
        }
        results.sort((a, b) => b.matches - a.matches)
        const top = results.slice(0, limit)
        return {
          ok: true,
          query,
          scanned_files: scanned,
          total_files: files.length,
          result_count: top.length,
          results: top,
        }
      },
    }))

    // —— kb_read ——
    ctx.effect(() => tools.register({
      name: 'kb_read',
      description: '读取知识库 E:/kb 内的一份文档。path 是相对知识库根目录的路径，如 "示例/ollama-安装笔记.md"。长文档用 offset 和 max_chars 翻页读取。',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: '文档相对路径（相对 E:/kb，如 示例/ollama-安装笔记.md）' },
          offset: { type: 'integer', description: '从第几个字符开始读，默认 0' },
          max_chars: { type: 'integer', description: '最多返回的字符数，默认 6000，最大 50000' },
        },
        required: ['path'],
      },
      output,
      async execute(args, exec) {
        const rel = String(args.path || '').trim().replace(/\\/g, '/').replace(/^\/+/, '')
        if (!rel) return { ok: false, error: 'path 不能为空' }
        let target
        try {
          target = (await resolveInside(rel)).target
        } catch (e) {
          return { ok: false, error: '非法路径：' + ((e && e.message) || String(e)) }
        }
        const info = await fs.stat(target, exec.signal)
        if (info === undefined || info.type !== 'file') {
          return { ok: false, error: '文件不存在：' + rel }
        }
        if (info.size !== undefined && info.size > MAX_FILE_BYTES) {
          return { ok: false, error: '文件过大（超过 5MB），无法直接读取：' + rel }
        }
        let content
        try {
          content = await fs.readText(target, exec.signal)
        } catch {
          return { ok: false, error: '无法读取（可能不是文本文件）：' + rel }
        }
        const offset = Math.max(Number(args.offset) || 0, 0)
        const maxChars = Math.min(Math.max(Number(args.max_chars) || 6000, 500), 50000)
        const slice = content.slice(offset, offset + maxChars)
        return {
          ok: true,
          path: rel,
          total_chars: content.length,
          offset,
          returned_chars: slice.length,
          has_more: offset + slice.length < content.length,
          content: slice,
        }
      },
    }))

    // —— kb_list ——
    ctx.effect(() => tools.register({
      name: 'kb_list',
      description: '列出本地知识库 E:/kb 的目录结构。可选 path 只看某个子目录，max_depth 控制深度。适合先了解知识库里有什么资料再决定读哪篇。',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: '相对子目录，如 "示例"；缺省列出整个知识库' },
          max_depth: { type: 'integer', description: '最大深度，默认 3，最大 8' },
        },
      },
      output,
      async execute(args, exec) {
        const rel = String(args.path || '').trim().replace(/\\/g, '/').replace(/^\/+/, '')
        const maxDepth = Math.min(Math.max(Number(args.max_depth) || 3, 1), 8)
        let rootTarget, baseTarget, baseRel
        try {
          if (rel) {
            const r = await resolveInside(rel)
            rootTarget = r.root
            baseTarget = r.target
          } else {
            rootTarget = await fs.resolve(KB_ROOT)
            baseTarget = rootTarget
          }
          baseRel = rel
        } catch (e) {
          return { ok: false, error: '非法路径：' + ((e && e.message) || String(e)) }
        }
        const baseInfo = await fs.stat(baseTarget, exec.signal)
        if (baseInfo === undefined) return { ok: false, error: '目录不存在：' + (rel || KB_ROOT) }
        if (baseInfo.type !== 'directory') return { ok: false, error: '不是目录：' + (rel || KB_ROOT) }
        const entries = []
        const walk = async (dirTarget, curRel, depth) => {
          if (depth > maxDepth) return
          let list
          try {
            list = await fs.listDir(dirTarget, exec.signal)
          } catch {
            return
          }
          for (const entry of list) {
            if (exec.signal && exec.signal.aborted) throw new Error('aborted')
            const childRel = curRel ? curRel + '/' + entry.name : entry.name
            entries.push({ path: childRel, type: entry.type, size: entry.size })
            if (entry.type === 'directory') await walk(entry.target, childRel, depth + 1)
          }
        }
        await walk(baseTarget, baseRel, 0)
        entries.sort((a, b) => {
          if (a.type !== b.type) return a.type === 'directory' ? -1 : 1
          return a.path < b.path ? -1 : 1
        })
        return { ok: true, base: rel || '/', entry_count: entries.length, entries }
      },
    }))
  },
}
