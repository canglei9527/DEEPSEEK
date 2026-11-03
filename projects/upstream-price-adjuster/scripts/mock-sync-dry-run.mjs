import { createServer } from 'node:http';
import { once } from 'node:events';
import { main } from '../bin/dai-group-sync.mjs';

const payloads = {
  '/api/v1/tenants/me/upstream-resources': {
    items: [{ id: 'upstream-1', name: 'local-mock-model', tenant_multiplier: 0.2 }]
  },
  '/api/v1/tenants/me/groups': {
    items: [{
      id: 'group-1',
      name: 'local-mock-model',
      default_user_multiplier: 0.1,
      description: 'local verification only',
      retail_price_book_id: 'local-book',
      user_default_visible: true,
      allow_protocol_conversion: true,
      sort_order: 100,
      status: 'active'
    }]
  }
};

let patchCount = 0;
const server = createServer((request, response) => {
  if (request.method === 'PATCH') {
    patchCount += 1;
    response.writeHead(500, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ error: 'dry-run must never patch' }));
    return;
  }
  const payload = payloads[request.url];
  if (!payload || request.method !== 'GET') {
    response.writeHead(404, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ error: 'not found' }));
    return;
  }
  response.writeHead(200, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(payload));
});

server.listen(0, '127.0.0.1');
await once(server, 'listening');
const address = server.address();
const baseUrl = `http://127.0.0.1:${address.port}`;

try {
  const code = await main(['--once', '--dry-run'], {
    config: { baseUrl, allowApply: false, headers: {} }
  });
  if (code !== 0) throw new Error(`mock dry-run returned exit code ${code}`);
  if (patchCount !== 0) throw new Error(`dry-run unexpectedly received ${patchCount} PATCH request(s)`);
  console.log('本地 mock dry-run 通过：仅收到两个 GET，没有 PATCH。');
} finally {
  server.close();
  await once(server, 'close');
}
