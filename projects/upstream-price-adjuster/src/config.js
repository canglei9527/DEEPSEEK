export const DEFAULT_ADJUSTMENT = 0.02;
export const DEFAULT_DECIMALS = 3;
export const DEFAULT_PORT = 4173;
export const MAX_ROWS = 500;
export const STORAGE_KEY = 'upstream-price-adjuster:v1';

export const SAMPLE_ROWS = Object.freeze([
  { model: 'cc-kiro-特价', upstreamMultiplier: 0.2 },
  { model: 'Claude-max盲号', upstreamMultiplier: 1.5 },
  { model: 'codex-20x-Pro号池', upstreamMultiplier: 0.2 },
  { model: 'gpt-image-2', upstreamMultiplier: 0.135 },
  { model: 'gpt-luna', upstreamMultiplier: 0.25 },
  { model: 'pro-特价', upstreamMultiplier: 0.16 }
]);
