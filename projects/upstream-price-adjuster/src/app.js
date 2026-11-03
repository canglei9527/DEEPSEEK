import {
  DEFAULT_ADJUSTMENT,
  DEFAULT_DECIMALS,
  SAMPLE_ROWS,
  STORAGE_KEY
} from './config.js';
import {
  calculateRow,
  formatMultiplier,
  parseBulkText,
  rowsToCsv,
  rowsToJson
} from './pricing.js';

const hasDocument = typeof document !== 'undefined';
const $ = (selector) => hasDocument ? document.querySelector(selector) : null;
const adjustmentInput = $('#adjustment');
const decimalsInput = $('#decimals');
const rowsBody = $('#rows-body');
const emptyState = $('#empty-state');
const notice = $('#notice');
const bulkText = $('#bulk-text');

let rows = [];

function makeId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function createRow(model = '', upstreamMultiplier = '') {
  return { id: makeId(), model, upstreamMultiplier };
}

function getSettings() {
  const adjustment = Number(adjustmentInput.value);
  const decimals = Number(decimalsInput.value);
  return {
    adjustment: Number.isFinite(adjustment) ? adjustment : DEFAULT_ADJUSTMENT,
    decimals: Number.isInteger(decimals) ? decimals : DEFAULT_DECIMALS
  };
}

function setNotice(message = '', type = '') {
  notice.textContent = message;
  notice.className = `notice ${type}`.trim();
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      rows: rows.map(({ model, upstreamMultiplier }) => ({ model, upstreamMultiplier })),
      adjustment: adjustmentInput.value,
      decimals: decimalsInput.value
    }));
  } catch {
    // Private browsing or a disabled storage area should not block calculations.
  }
}

function restore() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
    if (saved && Array.isArray(saved.rows)) {
      rows = saved.rows.map((row) => createRow(row.model, row.upstreamMultiplier));
      if (saved.adjustment !== undefined) adjustmentInput.value = saved.adjustment;
      if (saved.decimals !== undefined) decimalsInput.value = saved.decimals;
      return;
    }
  } catch {
    // Ignore malformed local state and start with the examples.
  }
  rows = SAMPLE_ROWS.map((row) => createRow(row.model, row.upstreamMultiplier));
}

function render() {
  const settings = getSettings();
  $('#formula-adjustment').textContent = formatMultiplier(settings.adjustment, settings.decimals);
  rowsBody.replaceChildren();
  emptyState.hidden = rows.length > 0;

  rows.forEach((row) => {
    const tr = document.createElement('tr');
    tr.dataset.id = row.id;

    const modelCell = document.createElement('td');
    const modelInput = document.createElement('input');
    modelInput.type = 'text';
    modelInput.className = 'model-input';
    modelInput.placeholder = '例如 claude-sonnet-5';
    modelInput.value = row.model;
    modelInput.setAttribute('aria-label', '模型名称');
    modelCell.append(modelInput);

    const upstreamCell = document.createElement('td');
    const upstreamInput = document.createElement('input');
    upstreamInput.type = 'text';
    upstreamInput.inputMode = 'decimal';
    upstreamInput.className = 'upstream-input';
    upstreamInput.placeholder = '0.200';
    upstreamInput.value = row.upstreamMultiplier;
    upstreamInput.setAttribute('aria-label', '上游倍率');
    upstreamCell.append(upstreamInput);

    const resultCell = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'result-badge';
    try {
      const calculated = calculateRow(row, settings.adjustment, settings.decimals);
      badge.textContent = formatMultiplier(calculated.adjustedMultiplier, settings.decimals);
    } catch (error) {
      badge.textContent = error.message;
      badge.classList.add('invalid');
    }
    resultCell.append(badge);

    const actionCell = document.createElement('td');
    actionCell.className = 'align-right';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'row-remove';
    remove.title = '删除这一行';
    remove.setAttribute('aria-label', `删除 ${row.model || '这一行'}`);
    remove.textContent = '×';
    actionCell.append(remove);

    tr.append(modelCell, upstreamCell, resultCell, actionCell);
    rowsBody.append(tr);
  });

  updateSummary();
  persist();
}

function updateSummary() {
  const calculated = [];
  const errors = [];
  rows.forEach((row, index) => {
    const hasContent = String(row?.model ?? '').trim() || String(row?.upstreamMultiplier ?? '').trim();
    if (!hasContent) return;
    try {
      const settings = getSettings();
      calculated.push(calculateRow(row, settings.adjustment, settings.decimals));
    } catch (error) {
      errors.push(`第 ${index + 1} 行：${error.message}`);
    }
  });
  if (errors.length) {
    setNotice(errors.length === 1 ? errors[0] : `${errors[0]}（另有 ${errors.length - 1} 行待修正）`);
  } else {
    setNotice('');
  }
  $('#row-count').textContent = calculated.length;
  if (!calculated.length) {
    $('#average-upstream').textContent = '—';
    $('#average-adjusted').textContent = '—';
    return;
  }
  const average = (key) => calculated.reduce((sum, row) => sum + row[key], 0) / calculated.length;
  const decimals = getSettings().decimals;
  $('#average-upstream').textContent = formatMultiplier(average('upstreamMultiplier'), decimals);
  $('#average-adjusted').textContent = formatMultiplier(average('adjustedMultiplier'), decimals);
}

function updateRow(id, field, value) {
  const row = rows.find((item) => item.id === id);
  if (!row) return;
  row[field] = value;
  render();
}

function addRow() {
  rows.push(createRow());
  render();
  const input = rowsBody.lastElementChild?.querySelector('.model-input');
  input?.focus();
}

function removeRow(id) {
  rows = rows.filter((row) => row.id !== id);
  render();
}

function replaceWithBulk() {
  try {
    const parsed = parseBulkText(bulkText.value);
    rows = parsed.map((row) => createRow(row.model, row.upstreamMultiplier));
    bulkText.value = '';
    render();
    setNotice(`已解析 ${rows.length} 条模型`, 'success');
  } catch (error) {
    setNotice(error.message);
  }
}

function download(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function copyResults() {
  try {
    const text = rowsToCsv(rows, getSettings());
    await navigator.clipboard.writeText(text);
    setNotice('结果已复制为 CSV，可直接粘贴到表格', 'success');
  } catch {
    setNotice('复制失败，请改用“下载 CSV”');
  }
}

export function init() {
  $('#add-row').addEventListener('click', addRow);
  $('#empty-add').addEventListener('click', addRow);
  $('#load-sample').addEventListener('click', () => {
    rows = SAMPLE_ROWS.map((row) => createRow(row.model, row.upstreamMultiplier));
    render();
    setNotice('已载入示例数据', 'success');
  });
  $('#clear-all').addEventListener('click', () => {
    rows = [];
    render();
    setNotice('列表已清空', 'success');
  });
  $('#apply-bulk').addEventListener('click', replaceWithBulk);
  $('#copy-results').addEventListener('click', copyResults);
  $('#download-csv').addEventListener('click', () => {
    try { download('adjusted-multipliers.csv', rowsToCsv(rows, getSettings()), 'text/csv;charset=utf-8'); } catch (error) { setNotice(error.message); }
  });
  $('#download-json').addEventListener('click', () => {
    try { download('adjusted-multipliers.json', rowsToJson(rows, getSettings()), 'application/json;charset=utf-8'); } catch (error) { setNotice(error.message); }
  });

  adjustmentInput.addEventListener('input', render);
  decimalsInput.addEventListener('change', render);
  rowsBody.addEventListener('input', (event) => {
    const tr = event.target.closest('tr');
    if (!tr) return;
    if (event.target.classList.contains('model-input')) updateRow(tr.dataset.id, 'model', event.target.value);
    if (event.target.classList.contains('upstream-input')) updateRow(tr.dataset.id, 'upstreamMultiplier', event.target.value);
  });
  rowsBody.addEventListener('click', (event) => {
    const button = event.target.closest('.row-remove');
    const tr = event.target.closest('tr');
    if (button && tr) removeRow(tr.dataset.id);
  });

  restore();
  render();
}

if (hasDocument) {
  init();
}
