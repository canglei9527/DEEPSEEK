// lib/http-utils.mjs
// HTTP 请求/响应工具

/**
 * 发送 JSON 响应
 */
export function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

/**
 * 读取请求体
 */
export function readBody(req, limit = 1024 * 1024) {
  return new Promise((resolveP, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) {
        reject(new Error('请求体过大（上限 1MB）。'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => resolveP(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

/**
 * 解析 JSON 请求体
 */
export async function parseJsonBody(req) {
  const raw = await readBody(req);
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error('请求体不是合法的 JSON。');
  }
}
