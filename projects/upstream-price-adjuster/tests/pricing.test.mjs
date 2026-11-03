import test from 'node:test';
import assert from 'node:assert/strict';
import {
  calculateAdjustedMultiplier,
  calculateRows,
  parseBulkText,
  parseMultiplier,
  rowsToCsv
} from '../src/pricing.js';

test('adds the adjustment instead of multiplying by a percentage', () => {
  assert.equal(calculateAdjustedMultiplier('×0.2'), 0.22);
  assert.equal(calculateAdjustedMultiplier(1.5), 1.52);
});

test('rounds results consistently at the requested precision', () => {
  assert.equal(calculateAdjustedMultiplier(0.135, 0.02, 3), 0.155);
  assert.equal(calculateAdjustedMultiplier(0.135, 0.02, 2), 0.16);
});

test('rejects malformed and negative multipliers', () => {
  assert.throws(() => parseMultiplier('0.2xfoo'), /请输入数字/);
  assert.throws(() => parseMultiplier(-0.1), /不能小于/);
  assert.throws(() => calculateAdjustedMultiplier(0.01, -0.02), /不能小于 0/);
});

test('parses supported bulk formats and skips a header', () => {
  const rows = parseBulkText([
    '模型\t上游倍率',
    'cc-kiro-特价\t×0.2',
    'Claude-max盲号,1.5',
    'gpt-image-2 ×0.135',
    'pro-特价：0.16'
  ].join('\n'));
  assert.deepEqual(rows, [
    { model: 'cc-kiro-特价', upstreamMultiplier: 0.2 },
    { model: 'Claude-max盲号', upstreamMultiplier: 1.5 },
    { model: 'gpt-image-2', upstreamMultiplier: 0.135 },
    { model: 'pro-特价', upstreamMultiplier: 0.16 }
  ]);
});

test('calculates rows and exports a stable CSV schema', () => {
  const rows = calculateRows([
    { model: 'a', upstreamMultiplier: 0.2 },
    { model: 'b', upstreamMultiplier: 1.5 }
  ], { adjustment: 0.02, decimals: 3 });
  assert.equal(rows[0].adjustedMultiplier, 0.22);
  const csv = rowsToCsv(rows, { adjustment: 0.02, decimals: 3 });
  assert.match(csv, /模型,上游倍率,调整值,调整后倍率/);
  assert.match(csv, /a,0\.200,0\.020,0\.220/);
});
