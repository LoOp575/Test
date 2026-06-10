/* ============================================================
   Utility & Helper Functions
   ============================================================ */

export function normalRandom() {
  let u1 = Math.random();
  let u2 = Math.random();

  while (u1 === 0) u1 = Math.random();

  return Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
}

export function clamp(value, min, max) {
  const safeValue = Number.isFinite(Number(value)) ? Number(value) : 0;
  return Math.min(Math.max(safeValue, min), max);
}

export function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function formatNumber(num, decimals = 2) {
  const safeNum = Number.isFinite(Number(num)) ? Number(num) : 0;

  return safeNum.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });
}

export function formatCurrency(num) {
  const safeNum = Number.isFinite(Number(num)) ? Number(num) : 0;

  if (Math.abs(safeNum) > 0 && Math.abs(safeNum) < 0.01) {
    return '$' + safeNum.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 8
    });
  }

  return '$' + formatNumber(safeNum);
}

export function formatPercent(num) {
  const safeNum = Number.isFinite(Number(num)) ? Number(num) : 0;
  return (safeNum * 100).toFixed(2) + '%';
}

export function getStatusColor(status) {
  const map = {
    STRONG_SHORT_SETUP: '#00e676',
    SHORT_VALID: '#00e676',
    SHORT_WATCH: '#ffb800',
    WEAK_WATCH: '#ffb800',
    NO_SHORT: '#4e4e6e',
    DANGER_STOP_RISK: '#ff2d55'
  };

  return map[status] || '#4e4e6e';
}

export function getStatusClass(status) {
  const map = {
    STRONG_SHORT_SETUP: 'short-valid',
    SHORT_VALID: 'short-valid',
    SHORT_WATCH: 'short-watch',
    WEAK_WATCH: 'short-watch',
    NO_SHORT: 'no-short',
    DANGER_STOP_RISK: 'danger'
  };

  return map[status] || 'no-short';
}

export function getStatusLabel(status) {
  const map = {
    STRONG_SHORT_SETUP: 'STRONG SHORT SETUP',
    SHORT_VALID: 'SHORT VALID',
    SHORT_WATCH: 'SHORT WATCH',
    WEAK_WATCH: 'WEAK WATCH',
    NO_SHORT: 'NO SHORT',
    DANGER_STOP_RISK: 'DANGER — STOP RISK'
  };

  return map[status] || status || 'NO SHORT';
}
