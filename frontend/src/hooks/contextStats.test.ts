import { describe, expect, it } from "vitest";
import { contextBarWidth, contextPercent, formatTokenCount, formatTokensPerSecond } from "./contextStats";

describe("contextStats", () => {
  it("formata milhar e taxa em português", () => {
    expect(formatTokenCount(8234)).toBe("8.234");
    expect(formatTokenCount(65536)).toBe("65.536");
    expect(formatTokensPerSecond(56.44)).toBe("56,4 tok/s");
  });

  it("arredonda o percentual e limita a barra a 100", () => {
    expect(contextPercent(8234, 65536)).toBe(13);
    expect(contextBarWidth(8234, 65536)).toBeCloseTo(12.56, 1);
    expect(contextPercent(70000, 65536)).toBe(107);
    expect(contextBarWidth(70000, 65536)).toBe(100);
    expect(contextPercent(4, 0)).toBe(0);
    expect(contextBarWidth(4, 0)).toBe(0);
  });
});
