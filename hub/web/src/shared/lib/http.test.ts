import { describe, expect, it } from "vitest";
import { apiUrl } from "./http";

describe("apiUrl", () => {
  it("builds a bare api path", () => {
    expect(apiUrl("/runs")).toBe("/api/runs");
  });

  it("appends query params in order", () => {
    expect(apiUrl("/reports/preview", { file: "a.xlsx", sheet: 0 })).toBe(
      "/api/reports/preview?file=a.xlsx&sheet=0",
    );
  });

  it("skips undefined params", () => {
    expect(apiUrl("/runs", { a: undefined, b: "x" })).toBe("/api/runs?b=x");
  });
});
