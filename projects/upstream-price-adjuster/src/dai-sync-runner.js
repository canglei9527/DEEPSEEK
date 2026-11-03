import {
  createDaiSyncPlan,
  summarizeDaiSyncPlan
} from './dai-sync-core.js';
import { createDaiLifecyclePlan } from './dai-lifecycle-core.js';

function safeMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

/** Execute one read/plan/(optional) update cycle. Dry-run never calls updateGroup. */
export async function runDaiSyncCycle(adapter, {
  apply = false,
  adjustment,
  decimals,
  tolerance,
  onEvent = () => {}
} = {}) {
  const [upstreams, groups] = await Promise.all([
    adapter.listUpstreams(),
    adapter.listGroups()
  ]);
  const plan = createDaiSyncPlan({ upstreams, groups }, {
    adjustment,
    decimals,
    tolerance,
    upstreamSchema: adapter.upstreamSchema,
    groupSchema: adapter.groupSchema
  });

  for (const issue of plan.issues) onEvent({ type: 'skip', issue });
  for (const update of plan.updates) {
    onEvent({ type: apply ? 'update' : 'dry-run-update', update });
  }

  const result = {
    apply,
    plan,
    summary: summarizeDaiSyncPlan(plan),
    attempted: 0,
    updated: 0,
    failure: null
  };
  if (!apply) return result;

  // Stop after the first remote write error so a bad credential/schema cannot fan out.
  for (const update of plan.updates) {
    result.attempted += 1;
    try {
      await adapter.updateGroup(update);
      result.updated += 1;
      onEvent({ type: 'updated', update });
    } catch (error) {
      result.failure = { update, message: safeMessage(error) };
      onEvent({ type: 'update-failed', ...result.failure });
      break;
    }
  }
  return result;
}

export async function runDaiLifecycleCycle(adapter, {
  apply = false,
  disableStale = false,
  enableRestored = false,
  adjustment = 0.02,
  decimals = 4,
  tolerance = 1e-9,
  rulePriority = 100,
  targetPriority = 1,
  onEvent = () => {}
} = {}) {
  const [upstreams, groups] = await Promise.all([
    adapter.listUpstreams(),
    adapter.listGroups()
  ]);
  const targetsByGroup = {};
  const rulesByGroup = {};
  for (const group of groups) {
    if (group?.id === undefined) continue;
    [targetsByGroup[group.id], rulesByGroup[group.id]] = await Promise.all([
      adapter.listTargets(group.id),
      adapter.listRules(group.id)
    ]);
  }
  const plan = createDaiLifecyclePlan({ upstreams, groups, targetsByGroup, rulesByGroup }, {
    adjustment,
    decimals,
    rulePriority,
    targetPriority,
    tolerance
  });
  for (const issue of plan.issues) onEvent({ type: 'lifecycle-skip', issue });
  for (const update of plan.updates) onEvent({ type: apply ? 'update' : 'dry-run-update', update });
  for (const action of plan.actions) onEvent({ type: apply ? 'lifecycle-action' : 'lifecycle-dry-run', action });
  for (const action of plan.restore) onEvent({ type: enableRestored && apply ? 'lifecycle-restore-action' : 'lifecycle-restore-dry-run', action });
  for (const action of plan.stale) onEvent({ type: disableStale && apply ? 'lifecycle-stale-action' : 'lifecycle-stale-dry-run', action });
  const result = {
    apply,
    disableStale,
    enableRestored,
    plan,
    attempted: 0,
    completed: 0,
    failure: null
  };
  if (!apply) return result;

  const writeUpdates = async () => {
    for (const update of plan.updates) {
      result.attempted += 1;
      try {
        await adapter.updateGroup(update);
        result.completed += 1;
        onEvent({ type: 'updated', update });
      } catch (error) {
        result.failure = { action: { type: 'update-group', ...update }, message: safeMessage(error) };
        onEvent({ type: 'update-failed', update, message: result.failure.message });
        return false;
      }
    }
    return true;
  };
  if (!(await writeUpdates())) return result;

  const temporaryIds = new Map();
  for (const action of plan.actions) {
    result.attempted += 1;
    try {
      let groupId = action.groupId;
      if (action.type === 'create-group') {
        const created = await adapter.createGroup({
          name: action.name,
          description: action.groupDefaults.managedDescription,
          retailPriceBookId: action.groupDefaults.retailPriceBookId,
          desiredMultiplier: action.desiredMultiplier,
          userDefaultVisible: action.groupDefaults.userDefaultVisible,
          allowProtocolConversion: action.groupDefaults.allowProtocolConversion,
          sortOrder: action.groupDefaults.sortOrder
        });
        groupId = created?.id ?? created?.group?.id ?? created?.data?.id;
        if (!groupId) throw new Error('创建分组响应缺少 id');
        temporaryIds.set(action.name, groupId);
      } else if (temporaryIds.has(action.name)) {
        groupId = temporaryIds.get(action.name);
      }
      if (action.type === 'associate-upstream') {
        await adapter.associateUpstream({ groupId, upstreamId: action.upstreamId, priority: action.targetDefaults.priority });
      } else if (action.type === 'create-rule') {
        await adapter.createRule({
          groupId,
          modelCode: action.modelCode,
          clientSurface: action.model.api_format,
          requiredCapability: action.model.capability_type,
          priority: action.ruleDefaults.priority
        });
      }
      result.completed += 1;
      onEvent({ type: 'lifecycle-completed', action: { ...action, groupId } });
    } catch (error) {
      result.failure = { action, message: safeMessage(error) };
      onEvent({ type: 'lifecycle-failed', ...result.failure });
      return result;
    }
  }
  if (enableRestored) {
    for (const action of plan.restore) {
      result.attempted += 1;
      try {
        if (action.type === 'enable-group') await adapter.enableGroup({ groupId: action.groupId });
        if (action.type === 'enable-rule') await adapter.enableRule({ groupId: action.groupId, ruleId: action.ruleId });
        result.completed += 1;
        onEvent({ type: 'lifecycle-completed', action });
      } catch (error) {
        result.failure = { action, message: safeMessage(error) };
        onEvent({ type: 'lifecycle-failed', ...result.failure });
        return result;
      }
    }
  }
  if (disableStale) {
    for (const action of plan.stale) {
      result.attempted += 1;
      try {
        if (action.type === 'disable-group') await adapter.disableGroup({ groupId: action.groupId });
        result.completed += 1;
        onEvent({ type: 'lifecycle-completed', action });
      } catch (error) {
        result.failure = { action, message: safeMessage(error) };
        onEvent({ type: 'lifecycle-failed', ...result.failure });
        break;
      }
    }
  }
  return result;
}
export function formatDaiSyncEvent(event) {
  if (event.type === 'lifecycle-skip') {
    const suffix = event.issue.name === null ? '' : `：${JSON.stringify(event.issue.name)}`;
    return `[生命周期跳过] ${event.issue.code}${suffix}${event.issue.message ? `（${event.issue.message}）` : ''}`;
  }
  if (event.type === 'lifecycle-dry-run' || event.type === 'lifecycle-action') {
    const labels = { 'create-group': '创建分组', 'associate-upstream': '关联上游', 'create-rule': '创建规则', 'enable-group': '恢复分组', 'enable-rule': '恢复规则' };
    const label = labels[event.action.type] ?? event.action.type;
    const detail = event.action.modelCode ? ` 模型=${JSON.stringify(event.action.modelCode)}` : '';
    return `[生命周期${event.type === 'lifecycle-dry-run' ? '预览' : '待执行'}] ${label} ${JSON.stringify(event.action.name)}${detail}`;
  }
  if (event.type === 'lifecycle-restore-dry-run' || event.type === 'lifecycle-restore-action') {
    const label = event.action.type === 'enable-group' ? '恢复分组' : '恢复规则';
    const detail = event.action.modelCode ? ` 模型=${JSON.stringify(event.action.modelCode)}` : '';
    return `[生命周期${event.type === 'lifecycle-restore-dry-run' ? '预览' : '待执行'}] ${label} ${JSON.stringify(event.action.name)}${detail}`;
  }
  if (event.type === 'lifecycle-stale-dry-run' || event.type === 'lifecycle-stale-action') {
    const label = '停用分组';
    const detail = event.action.modelCode ? ` 模型=${JSON.stringify(event.action.modelCode)}` : '';
    return `[生命周期${event.type === 'lifecycle-stale-dry-run' ? '预览' : '待执行'}] ${label} ${JSON.stringify(event.action.name)}${detail}`;
  }
  if (event.type === 'lifecycle-completed') return `[生命周期已完成] ${event.action.type} ${JSON.stringify(event.action.name)}`;
  if (event.type === 'lifecycle-failed') return `[生命周期停止] ${event.action.type} ${JSON.stringify(event.action.name)}：${event.message}`;
  if (event.type === 'skip') {
    const suffix = event.issue.name === null ? '' : `：${JSON.stringify(event.issue.name)}`;
    return `[跳过] ${event.issue.code}${suffix}${event.issue.message ? `（${event.issue.message}）` : ''}`;
  }
  if (event.type === 'dry-run-update') return `[预览] ${JSON.stringify(event.update.name)} ×${event.update.currentMultiplier} -> ×${event.update.desiredMultiplier}`;
  if (event.type === 'update') return `[待更新] ${JSON.stringify(event.update.name)} ×${event.update.currentMultiplier} -> ×${event.update.desiredMultiplier}`;
  if (event.type === 'updated') return `[已更新] ${JSON.stringify(event.update.name)} -> ×${event.update.desiredMultiplier}`;
  if (event.type === 'update-failed') return `[停止更新] ${JSON.stringify(event.update.name)}：${event.message}`;
  return '[同步] 未知事件';
}

export function formatDaiSyncSummary(result) {
  if (result.plan?.summary?.createGroups !== undefined) {
    const summary = result.plan.summary;
    const parts = [
      `[${result.apply ? 'apply' : 'dry-run'}]`,
      `创建分组=${summary.createGroups}`,
      `关联上游=${summary.associateUpstreams}`,
      `创建规则=${summary.createRules}`,
      `更新倍率=${summary.updates}`,
      `待恢复规则=${summary.enableRules}`,
      `待恢复分组=${summary.enableGroups}`,
      `待停用分组=${summary.disableGroups}`,
      `跳过=${summary.skipped}`
    ];
    if (result.apply) parts.push(`已完成=${result.completed}`, `尝试=${result.attempted}`);
    if (result.failure) parts.push('结果=失败');
    return parts.join(' ');
  }
  const { summary } = result;
  const mode = result.apply ? 'apply' : 'dry-run';
  const parts = [
    `[${mode}]`,
    `上游=${summary.upstreamCount}`,
    `分组=${summary.groupCount}`,
    `匹配=${summary.matched}`,
    `一致=${summary.unchanged}`,
    `待更新=${summary.pending}`,
    `跳过=${summary.skipped}`
  ];
  if (result.apply) parts.push(`已更新=${result.updated}`, `尝试=${result.attempted}`);
  if (result.failure) parts.push('结果=失败');
  return parts.join(' ');
}
