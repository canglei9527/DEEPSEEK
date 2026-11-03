const $ = (selector) => document.querySelector(selector);
const state = {
  status: null,
  pollTimer: null,
  busy: false
};

function setFeedback(message, type = '') {
  const element = $('#config-feedback');
  element.textContent = message;
  element.className = `feedback ${type}`.trim();
}

function requestBody() {
  const intervalValue = Number($('#interval-ms').value);
  const adjustmentValue = Number($('#adjustment-value').value);
  const body = {
    configPath: $('#config-path').value.trim(),
    intervalMs: Number.isInteger(intervalValue) && intervalValue > 0 ? intervalValue : undefined,
    adjustment: Number.isFinite(adjustmentValue) && adjustmentValue >= 0 ? adjustmentValue : undefined,
    lifecycle: $('#sync-type')?.value === 'lifecycle',
    disableStale: $('#disable-stale')?.checked === true,
    enableRestored: $('#enable-restored')?.checked === true
  };
  if (body.intervalMs === undefined) delete body.intervalMs;
  if (body.adjustment === undefined) delete body.adjustment;
  return body;
}

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin'
  });
  let payload;
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  return payload;
}

function updateAdjustmentLabel() {
  const value = $('#adjustment-value')?.value ?? '0.02';
  const label = $('#rule-adjustment');
  if (label) label.textContent = value;
}
function formatDate(value) {
  if (!value) return '—';
  try { return new Date(value).toLocaleString('zh-CN', { hour12: false }); } catch { return value; }
}

function formatInterval(value) {
  if (!Number.isFinite(value)) return '—';
  return value >= 60000 ? `${Math.round(value / 60000)} 分钟` : `${value} ms`;
}

function modeText(mode) {
  return mode === 'dry-run' ? 'dry-run' : mode === 'apply' ? 'apply' : '—';
}

function stateText(value) {
  return value === 'running' ? '运行中' : value === 'stopping' ? '停止中' : '已停止';
}

function renderConfig(config, { applyAdjustment = false } = {}) {
  const summary = $('#config-summary');
  if (!config) {
    summary.hidden = true;
    summary.replaceChildren();
    return;
  }
  if (config.adjustment !== undefined && applyAdjustment) {
    const adjustment = String(config.adjustment);
    const select = $('#adjustment-value');
    if (select && [...select.options].some((option) => option.value === adjustment)) {
      select.value = adjustment;
    }
    updateAdjustmentLabel();
  }
  summary.hidden = false;
  const requiredEnvironment = config.requiredEnvironment ?? [];
  const envText = requiredEnvironment.length
    ? requiredEnvironment.map((item) => `<span class="${item.available ? 'available' : 'missing'}">${escapeHtml(item.name)}：${item.available ? '已设置' : '未设置'}</span>`).join('<br>')
    : config.browser
      ? '浏览器会话模式：请打开登录窗口并保存会话'
      : '未配置认证请求头（仅适合本地 mock）';
  summary.innerHTML = `<dl>
    <dt>配置文件</dt><dd>${escapeHtml(config.configPath)}</dd>
    <dt>服务地址</dt><dd>${escapeHtml(config.baseUrl)}</dd>
    <dt>allowApply</dt><dd>${config.allowApply ? '<span class="available">true</span>' : 'false（安全默认）'}</dd>
    <dt>生命周期创建</dt><dd>${config.allowLifecycleApply ? '<span class="available">允许</span>' : '关闭（安全默认）'}</dd>
    <dt>失效停用</dt><dd>${config.allowDisableStale ? '<span class="available">允许</span>' : '关闭（安全默认）'}</dd>
    <dt>恢复启用</dt><dd>${config.allowEnableRestored ? '<span class="available">允许</span>' : '关闭（安全默认）'}</dd>
    <dt>调整值</dt><dd>${escapeHtml(config.adjustment ?? '0.02')}</dd>
    <dt>环境变量</dt><dd>${envText}</dd>
  </dl>`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
}

function renderList(element, values, type) {
  element.replaceChildren();
  if (!values?.length) {
    const empty = document.createElement('li');
    empty.className = 'empty-list';
    empty.textContent = type === 'mismatch'
      ? '执行预览后会在这里显示待调整的同名分组。'
      : type === 'lifecycle'
        ? '生命周期预览会在这里显示倍率更新、创建、关联、规则、恢复和停用计划。'
        : '解析到的重复名、缺失名和无效值会显示在这里。';
    element.append(empty);
    return;
  }
  for (const value of values) {
    const item = document.createElement('li');
    const title = document.createElement('span');
    title.className = `event-title ${type === 'skipped' ? 'event-skip' : ''}`;
    if (type === 'mismatch') {
      title.textContent = value.name;
      const detail = document.createElement('span');
      detail.className = 'event-detail';
      detail.textContent = `×${value.currentMultiplier} → ×${value.desiredMultiplier}`;
      item.append(title, detail);
    } else if (type === 'lifecycle') {
      title.textContent = value.name;
      const detail = document.createElement('span');
      detail.className = 'event-detail';
      detail.textContent = [value.action, value.modelCode && `模型=${value.modelCode}`].filter(Boolean).join(' · ');
      item.append(title, detail);
    } else {
      title.textContent = value.code;
      const detail = document.createElement('span');
      detail.className = 'event-detail';
      detail.textContent = [value.detail, value.message].filter(Boolean).join(' · ') || '该记录未参与同步';
      item.append(title, detail);
    }
    element.append(item);
  }
}

function renderStatus(status) {
  if (!status) return;
  state.status = status;
  const running = status.state === 'running';
  const badge = $('#connection-badge');
  badge.className = `connection-badge ${status.state}`;
  $('#connection-text').textContent = stateText(status.state);
  $('#status-state').textContent = stateText(status.state);
  $('#status-activity').textContent = status.activity === 'preview' ? '单轮预览' : status.activity === 'watch' ? '持续监视器' : '等待本地操作';
  $('#status-mode').textContent = modeText(status.mode);
  $('#status-interval').textContent = formatInterval(status.intervalMs);
  if (status.lastCycle) {
    const lifecycleSummary = status.lastCycle.type === 'lifecycle';
    $('#status-cycle').textContent = `第 ${status.lastCycle.cycle} 轮`;
    $('#status-cycle-detail').textContent = lifecycleSummary
      ? `${status.lastCycle.createGroups} 分组 · ${status.lastCycle.createRules} 规则 · ${status.lastCycle.updates} 倍率更新 · ${status.lastCycle.enableRules + status.lastCycle.enableGroups} 待恢复 · ${status.lastCycle.disableRules + status.lastCycle.disableGroups} 待停用 · ${formatDate(status.lastCycle.at)}`
      : `${status.lastCycle.pending} 待更新 · ${formatDate(status.lastCycle.at)}`;
  } else {
    $('#status-cycle').textContent = '尚未执行';
    $('#status-cycle-detail').textContent = '—';
  }
  $('#stop-watch').disabled = !running && status.state !== 'stopping';
  $('#preview-once').disabled = state.busy || running || status.state === 'stopping';
  $('#start-watch').disabled = state.busy || running || status.state === 'stopping';
  const logs = status.logs ?? [];
  const log = $('#sync-log');
  log.textContent = logs.length
    ? logs.map((entry) => `[${formatDate(entry.at)}] ${entry.stream === 'stderr' ? 'ERR' : entry.stream === 'system' ? 'SYS' : 'OUT'} ${entry.line}`).join('\n')
    : '等待本地同步进程输出…';
  log.scrollTop = log.scrollHeight;
  const mismatches = status.mismatches ?? [];
  const lifecycleActions = status.lifecycleActions ?? [];
  const skipped = status.skipped ?? [];
  $('#mismatch-count').textContent = String(mismatches.length);
  $('#lifecycle-count').textContent = String(lifecycleActions.length);
  $('#skipped-count').textContent = String(skipped.length);
  renderList($('#mismatch-list'), mismatches, 'mismatch');
  renderList($('#lifecycle-list'), lifecycleActions, 'lifecycle');
  renderList($('#skipped-list'), skipped, 'skipped');
  if (status.config) renderConfig(status.config, { applyAdjustment: false });
  const browser = status.browser;
  if (browser) {
    const saved = browser.session?.saved;
    $('#browser-feedback').textContent = browser.message || (saved ? '登录会话已保存，可以启动 apply。' : '请打开登录窗口并完成登录。');
    $('#browser-feedback').className = `feedback ${saved ? 'success' : ''}`.trim();
    $('#browser-save').disabled = state.busy || !browser.manualLoginOpen;
    $('#browser-close').disabled = state.busy || !browser.manualLoginOpen;
    $('#browser-open').disabled = state.busy || browser.manualLoginOpen;
  }
}

async function refreshStatus() {
  try { renderStatus(await api('/api/sync/status')); } catch (error) { setFeedback(error.message, 'error'); }
}

function setBusy(value) {
  state.busy = value;
  renderStatus(state.status ?? { state: 'stopped', logs: [] });
}

async function validateConfig() {
  setBusy(true);
  try {
    const result = await api('/api/sync/validate', requestBody());
    renderStatus({ ...result.status, browser: result.browser });
    renderConfig(result.config, { applyAdjustment: true });
    setFeedback('配置校验通过。认证值只报告环境变量是否存在，不会显示其内容。', 'success');
  } catch (error) {
    setFeedback(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

async function previewOnce() {
  setBusy(true);
  try {
    const result = await api('/api/sync/preview', requestBody());
    renderStatus(result);
    setFeedback($('#sync-type')?.value === 'lifecycle'
      ? '已启动生命周期 dry-run；会展示创建、关联、规则和待停用计划，不会写入远程系统。'
      : '已启动一次 dry-run 预览；等待进程输出摘要。', 'success');
  } catch (error) {
    setFeedback(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

function validateRunSelection({ lifecycle, apply, disableStale, enableRestored }) {
  if ((disableStale || enableRestored) && !lifecycle) return '请先把同步类型切换为“供应商模型生命周期同步”。';
  if ((disableStale || enableRestored) && !apply) return '停用或恢复已识别资源必须把监视器模式切换为 apply。';
  return '';
}

async function startWatch() {
  const lifecycle = $('#sync-type')?.value === 'lifecycle';
    const disableStale = $('#disable-stale')?.checked === true;
    const enableRestored = $('#enable-restored')?.checked === true;
    const apply = $('#run-mode').value === 'apply';
    const selectionError = validateRunSelection({ lifecycle, apply, disableStale, enableRestored });
  if (selectionError) {
    setFeedback(selectionError, 'error');
    return;
  }
  const body = { ...requestBody(), apply };
  setBusy(true);
  try {
    renderStatus(await api('/api/sync/start', body));
    setFeedback(apply ? 'apply 监视器已启动；请持续观察日志和最近一轮。' : 'dry-run 监视器已启动，不会发送 PATCH。', 'success');
  } catch (error) {
    setFeedback(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

async function stopWatch() {
  setBusy(true);
  try {
    renderStatus(await api('/api/sync/stop', {}));
    setFeedback('已请求停止本地同步进程。', 'success');
  } catch (error) {
    setFeedback(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

async function browserAction(path, message) {
  setBusy(true);
  try {
    const result = await api(path, {});
    if (result.browser) renderStatus({ ...(state.status ?? { state: 'stopped', logs: [] }), browser: result.browser });
    setFeedback(message, 'success');
  } catch (error) {
    setFeedback(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

$('#validate-config').addEventListener('click', validateConfig);
$('#adjustment-value').addEventListener('change', updateAdjustmentLabel);
$('#disable-stale').addEventListener('change', () => {
  if ($('#disable-stale').checked) $('#sync-type').value = 'lifecycle';
});
$('#enable-restored').addEventListener('change', () => {
  if ($('#enable-restored').checked) $('#sync-type').value = 'lifecycle';
});
$('#sync-type').addEventListener('change', () => {
  if ($('#sync-type').value !== 'lifecycle') {
    $('#disable-stale').checked = false;
    $('#enable-restored').checked = false;
  }
});
updateAdjustmentLabel();
$('#preview-once').addEventListener('click', previewOnce);
$('#start-watch').addEventListener('click', startWatch);
$('#stop-watch').addEventListener('click', stopWatch);
$('#browser-open').addEventListener('click', () => browserAction('/api/sync/browser/open', '已打开 D-AI 登录窗口；请完成登录后保存会话。'));
$('#browser-save').addEventListener('click', () => browserAction('/api/sync/browser/save', '登录会话已保存，可以选择 apply 启动自动修改。'));
$('#browser-close').addEventListener('click', () => browserAction('/api/sync/browser/close', '登录窗口已关闭。'));
$('#clear-log-view').addEventListener('click', () => { $('#sync-log').scrollTop = $('#sync-log').scrollHeight; });
refreshStatus();
state.pollTimer = setInterval(refreshStatus, 1000);
