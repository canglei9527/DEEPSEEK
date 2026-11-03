import {
  DEFAULT_ADJUSTMENT,
  DEFAULT_DECIMALS,
  MAX_ROWS
} from './config.js';

const NUMBER_PATTERN = '[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)';
const NUMBER_ONLY = new RegExp(`^${NUMBER_PATTERN}$`);
const TRAILING_VALUE = new RegExp(`^(.+?)(?:\\s+|[×xX])(${NUMBER_PATTERN})\\s*$`);

/**
 * Convert a user-entered multiplier into a strict finite number.
 * The optional x/× prefix is accepted because it is common in price tables.
 */
export function parseMultiplier(value, fieldName = '倍率') {
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new Error(`${fieldName}必须是有限数字`);
    }
    if (value < 0) {
      throw new Error(`${fieldName}不能小于 0`);
    }
    return value;
  }

  const normalized = String(value ?? '')
    .trim()
    .replace(/^倍率[：:]?\s*/i, '')
    .replace(/^[×x]\s*/i, '');

  if (!NUMBER_ONLY.test(normalized)) {
    throw new Error(`${fieldName}请输入数字，例如 0.22`);
  }

  const parsed = Number(normalized);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${fieldName}必须是大于等于 0 的有限数字`);
  }
  return parsed;
}

export function roundTo(value, decimals = DEFAULT_DECIMALS) {
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 12) {
    throw new Error('小数位数必须是 0 到 12 之间的整数');
  }
  const factor = 10 ** decimals;
  return Number((Math.round((value + Number.EPSILON) * factor) / factor).toFixed(decimals));
}

export function formatMultiplier(value, decimals = DEFAULT_DECIMALS) {
  return roundTo(value, decimals).toFixed(decimals);
}

/** Calculate the customer multiplier from an upstream multiplier. */
export function calculateAdjustedMultiplier(
  upstreamMultiplier,
  adjustment = DEFAULT_ADJUSTMENT,
  decimals = DEFAULT_DECIMALS
) {
  const upstream = parseMultiplier(upstreamMultiplier, '上游倍率');
  const delta = Number(adjustment);
  if (!Number.isFinite(delta)) {
    throw new Error('调整值必须是有限数字');
  }
  const adjusted = upstream + delta;
  if (adjusted < 0) {
    throw new Error('调整后倍率不能小于 0');
  }
  return roundTo(adjusted, decimals);
}

export function calculateRow(row, adjustment = DEFAULT_ADJUSTMENT, decimals = DEFAULT_DECIMALS) {
  const model = String(row?.model ?? '').trim();
  if (!model) {
    throw new Error('模型名称不能为空');
  }
  const upstreamMultiplier = parseMultiplier(row?.upstreamMultiplier, '上游倍率');
  return {
    model,
    upstreamMultiplier: roundTo(upstreamMultiplier, decimals),
    adjustedMultiplier: calculateAdjustedMultiplier(upstreamMultiplier, adjustment, decimals),
    adjustment: roundTo(Number(adjustment), decimals)
  };
}

export function calculateRows(rows, {
  adjustment = DEFAULT_ADJUSTMENT,
  decimals = DEFAULT_DECIMALS,
  maxRows = MAX_ROWS
} = {}) {
  if (!Array.isArray(rows)) {
    throw new Error('模型列表必须是数组');
  }
  if (rows.length > maxRows) {
    throw new Error(`最多处理 ${maxRows} 条模型`);
  }
  return rows
    .filter((row) => String(row?.model ?? '').trim() || String(row?.upstreamMultiplier ?? '').trim())
    .map((row) => calculateRow(row, adjustment, decimals));
}

function parseLine(line) {
  const parts = line
    .split(/[\t,，;；]/)
    .map((part) => part.trim())
    .filter(Boolean);

  if (parts.length >= 2) {
    return { model: parts[0], upstreamMultiplier: parts[1] };
  }

  const match = line.match(TRAILING_VALUE);
  if (match) {
    return { model: match[1].trim(), upstreamMultiplier: match[2] };
  }

  const colonIndex = line.search(/[：:]/);
  if (colonIndex > 0) {
    return {
      model: line.slice(0, colonIndex).trim(),
      upstreamMultiplier: line.slice(colonIndex + 1).trim()
    };
  }

  throw new Error('每行需要“模型名 + 倍率”，可用 Tab、逗号或 × 分隔');
}

export function parseBulkText(text, maxRows = MAX_ROWS) {
  const lines = String(text ?? '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  if (lines.length > maxRows) {
    throw new Error(`最多粘贴 ${maxRows} 行模型`);
  }

  const rows = [];
  lines.forEach((line, index) => {
    try {
      const parsed = parseLine(line);
      const model = String(parsed.model).trim();
      if (!model) throw new Error('模型名称不能为空');
      rows.push({ model, upstreamMultiplier: parseMultiplier(parsed.upstreamMultiplier, '上游倍率') });
    } catch (error) {
      const looksLikeHeader = index === 0 && /模型|model|上游|倍率/i.test(line);
      if (!looksLikeHeader) {
        throw new Error(`第 ${index + 1} 行：${error.message}`);
      }
    }
  });

  return rows;
}

function escapeCsv(value) {
  const text = String(value ?? '');
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function rowsToCsv(rows, {
  adjustment = DEFAULT_ADJUSTMENT,
  decimals = DEFAULT_DECIMALS
} = {}) {
  const calculated = calculateRows(rows, { adjustment, decimals });
  const header = ['模型', '上游倍率', '调整值', '调整后倍率'];
  const lines = calculated.map((row) => [
    row.model,
    formatMultiplier(row.upstreamMultiplier, decimals),
    formatMultiplier(row.adjustment, decimals),
    formatMultiplier(row.adjustedMultiplier, decimals)
  ]);
  return [header, ...lines].map((line) => line.map(escapeCsv).join(',')).join('\r\n') + '\r\n';
}

export function rowsToJson(rows, options = {}) {
  return JSON.stringify(calculateRows(rows, options), null, 2);
}
