const tokenCount = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const tokenRate = new Intl.NumberFormat("pt-BR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

export function formatTokenCount(value: number): string {
  return tokenCount.format(Math.max(0, Math.round(value)));
}

export function formatTokensPerSecond(value: number): string {
  return `${tokenRate.format(value)} tok/s`;
}

export function contextPercent(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.round((used / limit) * 100);
}

export function contextBarWidth(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.min(100, Math.max(0, (used / limit) * 100));
}
