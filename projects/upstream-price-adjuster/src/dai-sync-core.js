import {
  calculateAdjustedMultiplier,
  parseMultiplier
} from './pricing.js';

export const DAI_SYNC_ADJUSTMENT = 0.02;
export const DEFAULT_SYNC_DECIMALS = 4;
export const DEFAULT_SYNC_TOLERANCE = 1e-9;
export const DEFAULT_SYNC_INTERVAL_MS = 60_000;

export const DEFAULT_UPSTREAM_SCHEMA = Object.freeze({
  idField: 'id',
  nameField: 'name',
  multiplierField: 'tenant_multiplier'
});

export const DEFAULT_GROUP_SCHEMA = Object.freeze({
  idField: 'id',
  nameField: 'name',
  multiplierField: 'default_user_multiplier'
});

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isUsableName(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function isUsableId(value) {
  return (typeof value === 'string' && value.trim().length > 0)
    || (typeof value === 'number' && Number.isFinite(value));
}

function normalizeSchema(schema, defaults, label) {
  const value = { ...defaults, ...(schema ?? {}) };
  for (const key of ['idField', 'nameField', 'multiplierField']) {
    if (typeof value[key] !== 'string' || !value[key].trim()) {
      throw new Error(`${label}字段映射中的 ${key} 必须是非空字符串`);
    }
  }
  return value;
}

function addIssue(issues, code, name, details = {}) {
  issues.push({ code, name, ...details });
}

function collectNamedRecords(records, schema, role, issues) {
  if (!Array.isArray(records)) {
    throw new Error(`${role}列表必须是数组`);
  }

  const byName = new Map();
  records.forEach((record, index) => {
    if (!isRecord(record)) {
      addIssue(issues, `invalid-${role}-record`, null, { index, message: '记录必须是对象' });
      return;
    }

    const name = record[schema.nameField];
    if (!isUsableName(name)) {
      addIssue(issues, `invalid-${role}-name`, null, {
        index,
        message: `${schema.nameField} 必须是非空字符串`
      });
      return;
    }

    const entries = byName.get(name) ?? [];
    entries.push({ record, index, name });
    byName.set(name, entries);
  });

  return byName;
}

function prepareSingleRecord(entry, schema, role, issues) {
  const value = entry.record[schema.multiplierField];
  let multiplier;
  try {
    multiplier = parseMultiplier(value, `${role}倍率`);
  } catch (error) {
    addIssue(issues, `invalid-${role}-multiplier`, entry.name, {
      index: entry.index,
      message: error.message
    });
    return null;
  }

  if (role === 'group' && !isUsableId(entry.record[schema.idField])) {
    addIssue(issues, 'invalid-group-id', entry.name, {
      index: entry.index,
      message: `${schema.idField} 必须是非空 ID`
    });
    return null;
  }

  return {
    ...entry,
    multiplier
  };
}

function assertOptions(options) {
  const adjustment = options.adjustment ?? DAI_SYNC_ADJUSTMENT;
  const decimals = options.decimals ?? DEFAULT_SYNC_DECIMALS;
  const tolerance = options.tolerance ?? DEFAULT_SYNC_TOLERANCE;

  if (!Number.isFinite(adjustment) || adjustment < 0 || adjustment > 1) {
    throw new Error('同步调整值必须是 0 到 1 之间的有限数字');
  }
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 12) {
    throw new Error('同步精度必须是 0 到 12 之间的整数');
  }
  if (!Number.isFinite(tolerance) || tolerance < 0) {
    throw new Error('同步容差必须是大于等于 0 的有限数字');
  }

  return { adjustment, decimals, tolerance };
}

/**
 * Build an exact-name, one-to-one synchronization plan without changing remote state.
 * Names are intentionally not trimmed or case-normalized: "a" and "a " are different.
 */
export function createDaiSyncPlan({ upstreams, groups }, options = {}) {
  const { adjustment, decimals, tolerance } = assertOptions(options);
  const upstreamSchema = normalizeSchema(options.upstreamSchema, DEFAULT_UPSTREAM_SCHEMA, '上游');
  const groupSchema = normalizeSchema(options.groupSchema, DEFAULT_GROUP_SCHEMA, '分组');
  const issues = [];
  const upstreamByName = collectNamedRecords(upstreams, upstreamSchema, 'upstream', issues);
  const groupByName = collectNamedRecords(groups, groupSchema, 'group', issues);
  const updates = [];
  const unchanged = [];
  const matched = [];
  const names = new Set([...upstreamByName.keys(), ...groupByName.keys()]);

  for (const name of names) {
    const upstreamEntries = upstreamByName.get(name) ?? [];
    const groupEntries = groupByName.get(name) ?? [];

    if (upstreamEntries.length > 1) {
      addIssue(issues, 'duplicate-upstream-name', name, { count: upstreamEntries.length });
    }
    if (groupEntries.length > 1) {
      addIssue(issues, 'duplicate-group-name', name, { count: groupEntries.length });
    }
    if (upstreamEntries.length > 1 || groupEntries.length > 1) {
      continue;
    }
    if (upstreamEntries.length === 0) {
      addIssue(issues, 'missing-upstream', name);
      continue;
    }
    if (groupEntries.length === 0) {
      addIssue(issues, 'missing-group', name);
      continue;
    }

    const upstream = prepareSingleRecord(upstreamEntries[0], upstreamSchema, 'upstream', issues);
    const group = prepareSingleRecord(groupEntries[0], groupSchema, 'group', issues);
    if (!upstream || !group) {
      continue;
    }

    let desiredMultiplier;
    try {
      desiredMultiplier = calculateAdjustedMultiplier(
        upstream.multiplier,
        adjustment,
        decimals
      );
    } catch (error) {
      addIssue(issues, 'invalid-desired-multiplier', name, { message: error.message });
      continue;
    }

    const difference = Math.abs(group.multiplier - desiredMultiplier);
    const match = {
      name,
      group: group.record,
      groupId: group.record[groupSchema.idField],
      upstream: upstream.record,
      currentMultiplier: group.multiplier,
      upstreamMultiplier: upstream.multiplier,
      desiredMultiplier,
      difference
    };
    matched.push(match);
    if (difference <= tolerance) {
      unchanged.push(match);
    } else {
      updates.push(match);
    }
  }

  return {
    adjustment,
    decimals,
    tolerance,
    upstreamCount: upstreams.length,
    groupCount: groups.length,
    matched,
    unchanged,
    updates,
    issues
  };
}

export function summarizeDaiSyncPlan(plan) {
  return {
    upstreamCount: plan.upstreamCount,
    groupCount: plan.groupCount,
    matched: plan.matched.length,
    unchanged: plan.unchanged.length,
    pending: plan.updates.length,
    skipped: plan.issues.length
  };
}

export function validateSyncInterval(intervalMs) {
  if (!Number.isInteger(intervalMs) || intervalMs < 1_000) {
    throw new Error('轮询间隔必须是不小于 1000 毫秒的整数');
  }
  return intervalMs;
}
