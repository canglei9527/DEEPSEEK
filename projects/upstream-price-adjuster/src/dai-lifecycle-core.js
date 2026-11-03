import { calculateAdjustedMultiplier, parseMultiplier } from './pricing.js';

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function addIssue(issues, code, name, details = {}) {
  issues.push({ code, name, ...details });
}

function usableString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function usableId(value) {
  return usableString(value) || (typeof value === 'number' && Number.isFinite(value));
}

function usableNonNegativeNumber(value) {
  return Number.isFinite(Number(value)) && Number(value) >= 0;
}

function normalizeModel(model, upstream, index, issues) {
  if (!isRecord(model) || !usableString(model.model_code)) {
    addIssue(issues, 'invalid-upstream-model', upstream.name, { index, message: '模型缺少非空 model_code' });
    return null;
  }
  if (!usableString(model.api_format) || !usableString(model.capability_type)) {
    addIssue(issues, 'invalid-upstream-model', upstream.name, {
      index,
      modelCode: model.model_code,
      message: '模型缺少 api_format 或 capability_type'
    });
    return null;
  }
  return model;
}

function normalizeUpstream(upstream, index, issues) {
  if (!isRecord(upstream) || !usableId(upstream.id) || !usableString(upstream.name)) {
    addIssue(issues, 'invalid-upstream-resource', upstream?.name ?? null, {
      index,
      message: '上游资源缺少有效 id 或 name'
    });
    return null;
  }
  if (!Array.isArray(upstream.models)) {
    addIssue(issues, 'missing-upstream-models', upstream.name, {
      index,
      message: '上游资源没有可用的 models 列表，本轮不创建或停用分组'
    });
    return { upstream, models: null };
  }
  const models = upstream.models
    .map((model, modelIndex) => normalizeModel(model, upstream, modelIndex, issues))
    .filter(Boolean);
  return { upstream, models };
}

function normalizeGroup(group, index, issues) {
  if (!isRecord(group) || !usableId(group.id) || !usableString(group.name)) {
    addIssue(issues, 'invalid-group', group?.name ?? null, { index, message: '分组缺少有效 id 或 name' });
    return null;
  }
  return group;
}

function normalizeCollection(value) {
  return Array.isArray(value) ? value : [];
}

function targetAccountId(target) {
  return target?.account_id ?? target?.upstream_resource_id ?? target?.resource_id ?? null;
}

function targetKey(target) {
  const accountId = targetAccountId(target);
  return accountId === null ? null : String(accountId);
}

function ruleKey(rule) {
  if (!isRecord(rule) || rule.match_type !== 'exact') return null;
  const matchValue = rule.match_value;
  const targetModel = rule.target_model_code ?? rule.logical_model_code ?? rule.target_model;
  const surface = rule.client_surface ?? '';
  if (!usableString(matchValue) || !usableString(targetModel)) return null;
  return `${surface}\u0000${matchValue}\u0000${targetModel}`;
}

function defaultGroupDefaults(groups) {
  const active = groups.find((group) => group?.status === 'active' && usableString(group?.retail_price_book_id))
    ?? groups.find((group) => usableString(group?.retail_price_book_id))
    ?? {};
  const maxSort = groups.reduce((max, group) => {
    const value = Number(group?.sort_order);
    return Number.isFinite(value) ? Math.max(max, value) : max;
  }, 0);
  return {
    retailPriceBookId: active.retail_price_book_id,
    userDefaultVisible: active.user_default_visible ?? true,
    allowProtocolConversion: active.allow_protocol_conversion ?? true,
    nextSortOrder: maxSort + 1
  };
}

function createRuleKey(model) {
  return `${model.api_format}\u0000${model.model_code}\u0000${model.model_code}`;
}

function planMultiplierUpdate(updates, issues, upstream, group, {
  adjustment,
  decimals,
  tolerance
}) {
  let upstreamMultiplier;
  let currentMultiplier;
  let desiredMultiplier;
  try {
    upstreamMultiplier = parseMultiplier(upstream.tenant_multiplier, '上游倍率');
    currentMultiplier = parseMultiplier(group.default_user_multiplier, '分组倍率');
    desiredMultiplier = calculateAdjustedMultiplier(upstreamMultiplier, adjustment, decimals);
  } catch (error) {
    addIssue(issues, 'invalid-lifecycle-multiplier', upstream.name, { message: error.message });
    return;
  }
  const difference = Math.abs(currentMultiplier - desiredMultiplier);
  if (difference <= tolerance) return;
  updates.push({
    name: upstream.name,
    group,
    groupId: group.id,
    upstream,
    currentMultiplier,
    upstreamMultiplier,
    desiredMultiplier,
    difference
  });
}

function addUniqueIssue(issues, code, name, details = {}) {
  if (issues.some((issue) => issue.code === code && issue.name === name && issue.modelCode === details.modelCode)) return;
  addIssue(issues, code, name, details);
}

function summarizeAction(action) {
  return {
    type: action.type,
    name: action.name,
    modelCode: action.modelCode,
    groupId: action.groupId,
    upstreamId: action.upstreamId,
    ruleId: action.ruleId,
    reason: action.reason
  };
}

/**
 * Build a lifecycle plan without remote writes. Each model is represented by
 * ordered create/associate/rule actions. Existing matched resources may also
 * receive precise restore or stale actions; creation remains limited to new groups.
 */
export function createDaiLifecyclePlan({ upstreams, groups, targetsByGroup = {}, rulesByGroup = {} }, {
  adjustment = 0.02,
  decimals = 4,
  tolerance = 1e-9,
  rulePriority = 100,
  targetPriority = 1,
  managedDescriptionPrefix = '[dai-lifecycle] ',
  groupDefaults
} = {}) {
  const issues = [];
  const normalizedUpstreams = normalizeCollection(upstreams)
    .map((upstream, index) => normalizeUpstream(upstream, index, issues))
    .filter(Boolean);
  const normalizedGroups = normalizeCollection(groups)
    .map((group, index) => normalizeGroup(group, index, issues))
    .filter(Boolean);
  const defaults = { ...defaultGroupDefaults(normalizedGroups), ...(groupDefaults ?? {}) };
  const upstreamByName = new Map();
  const duplicateUpstreamNames = new Set();
  for (const item of normalizedUpstreams) {
    if (upstreamByName.has(item.upstream.name)) {
      addIssue(issues, 'duplicate-upstream-resource-name', item.upstream.name);
      duplicateUpstreamNames.add(item.upstream.name);
    } else {
      upstreamByName.set(item.upstream.name, item);
    }
  }
  const groupByName = new Map();
  for (const group of normalizedGroups) {
    const named = groupByName.get(group.name) ?? [];
    named.push(group);
    groupByName.set(group.name, named);
    if (named.length === 2) addIssue(issues, 'duplicate-group-name', group.name);
  }
  const groupsByUpstreamId = new Map();
  for (const group of normalizedGroups) {
    for (const target of normalizeCollection(targetsByGroup[group.id])) {
      const accountId = targetKey(target);
      if (!accountId) continue;
      const matched = groupsByUpstreamId.get(accountId) ?? [];
      matched.push(group);
      groupsByUpstreamId.set(accountId, matched);
    }
  }

  const actions = [];
  const updates = [];
  const createdGroups = [];
  const stale = [];
  const restore = [];
  const matchedGroupIds = new Set();
  let nextSortOrder = Number.isFinite(Number(defaults.nextSortOrder)) ? Number(defaults.nextSortOrder) : 1;

  for (const item of normalizedUpstreams) {
    const upstream = item.upstream;
    if (duplicateUpstreamNames.has(upstream.name)) continue;
    const targetMatches = groupsByUpstreamId.get(String(upstream.id)) ?? [];
    const namedMatches = groupByName.get(upstream.name) ?? [];
    if (targetMatches.length > 1) {
      addIssue(issues, 'duplicate-upstream-target', upstream.name, { message: '多个分组关联了同一个上游资源，本轮不写入' });
      continue;
    }
    if (targetMatches.length === 0 && namedMatches.length > 1) {
      addIssue(issues, 'ambiguous-group-name', upstream.name, { message: '多个同名分组无法安全选择，本轮不写入' });
      continue;
    }
    const group = targetMatches[0] ?? namedMatches[0] ?? null;
    const isNewGroup = !group;
    if (group) matchedGroupIds.add(String(group.id));
    if (item.models === null) continue;
    if (group) planMultiplierUpdate(updates, issues, upstream, group, { adjustment, decimals, tolerance });
    const groupId = group?.id ?? `new:${String(upstream.id)}`;
    if (isNewGroup) {
      if (!usableString(defaults.retailPriceBookId)) {
        addIssue(issues, 'missing-retail-price-book', upstream.name, { message: '没有可复制的 retail_price_book_id，未创建分组' });
        continue;
      }
      if (!usableNonNegativeNumber(upstream.tenant_multiplier)) {
        addIssue(issues, 'invalid-upstream-multiplier', upstream.name, { message: '上游倍率无效，未创建分组' });
        continue;
      }
      createdGroups.push({ upstream, temporaryGroupId: groupId });
      actions.push({
        type: 'create-group',
        name: upstream.name,
        upstream,
        upstreamId: upstream.id,
        groupId,
        desiredMultiplier: Number((Number(upstream.tenant_multiplier) + Number(adjustment)).toFixed(decimals)),
        groupDefaults: { ...defaults, sortOrder: nextSortOrder++, managedDescription: `${managedDescriptionPrefix}${upstream.name}` }
      });
    }

    if (isNewGroup) {
      actions.push({
        type: 'associate-upstream',
        name: upstream.name,
        upstream,
        upstreamId: upstream.id,
        group,
        groupId,
        targetDefaults: { priority: targetPriority }
      });
      const observedRuleKeys = new Set();
      for (const model of item.models) {
        const key = createRuleKey(model);
        if (observedRuleKeys.has(key)) {
          addUniqueIssue(issues, 'duplicate-upstream-model', upstream.name, { modelCode: model.model_code });
          continue;
        }
        observedRuleKeys.add(key);
        if (model.availability && model.availability !== 'available') {
          addIssue(issues, 'unavailable-upstream-model', upstream.name, {
            modelCode: model.model_code,
            availability: model.availability
          });
          continue;
        }
        actions.push({
          type: 'create-rule',
          name: upstream.name,
          upstream,
          upstreamId: upstream.id,
          group,
          groupId,
          model,
          modelCode: model.model_code,
          ruleDefaults: { priority: rulePriority }
        });
      }
    } else {
      const rules = normalizeCollection(rulesByGroup[group.id]);
      const availableModelKeys = new Set();
      for (const model of item.models) {
        const key = createRuleKey(model);
        if (model.availability && model.availability !== 'available') continue;
        availableModelKeys.add(key);
      }
      if (group.status === 'disabled') {
        restore.push({
          type: 'enable-group',
          name: upstream.name,
          group,
          groupId,
          reason: '上游供应商已恢复'
        });
      }
      for (const rule of rules) {
        const key = ruleKey(rule);
        if (!key || rule.id === undefined) continue;
        if (rule.status === 'inactive' && availableModelKeys.has(key)) {
          restore.push({
            type: 'enable-rule',
            name: upstream.name,
            group,
            groupId,
            rule,
            ruleId: rule.id,
            modelCode: rule.match_value,
            reason: '上游模型已恢复'
          });
        }
      }
    }
  }

  for (const group of normalizedGroups) {
    if (matchedGroupIds.has(String(group.id))) continue;
    if (group.status !== 'active') continue;
    stale.push({
      type: 'disable-group',
      name: group.name,
      group,
      groupId: group.id,
      reason: '上游资源中已不存在该供应商'
    });
  }

  return {
    adjustment,
    decimals,
    tolerance,
    rulePriority,
    targetPriority,
    upstreamCount: normalizedUpstreams.length,
    groupCount: normalizedGroups.length,
    actions,
    updates,
    restore,
    stale,
    issues,
    createdGroups,
    summary: {
      createGroups: actions.filter((action) => action.type === 'create-group').length,
      associateUpstreams: actions.filter((action) => action.type === 'associate-upstream').length,
      createRules: actions.filter((action) => action.type === 'create-rule').length,
      updates: updates.length,
      enableGroups: restore.filter((action) => action.type === 'enable-group').length,
      enableRules: restore.filter((action) => action.type === 'enable-rule').length,
      disableGroups: stale.filter((action) => action.type === 'disable-group').length,
      skipped: issues.length
    },
    previewActions: [...updates, ...actions, ...restore, ...stale].map(summarizeAction)
  };
}
