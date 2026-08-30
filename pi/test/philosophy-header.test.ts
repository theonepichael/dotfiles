import { describe, expect, test } from "bun:test";
import { PI_LOGO, TAGLINES, pickTagline, renderHeaderLines } from "../extensions/philosophy-header";

describe("pickTagline", () => {
  test("0 selects the first tagline", () => {
    expect(pickTagline(0)).toBe(TAGLINES[0]);
  });

  test("a sample just under 1 selects the last tagline", () => {
    expect(pickTagline(0.999)).toBe(TAGLINES[TAGLINES.length - 1]);
  });

  test("every bucket of the 0..1 range is reachable", () => {
    const seen = new Set<string>();
    for (let i = 0; i < TAGLINES.length; i++) {
      seen.add(pickTagline((i + 0.5) / TAGLINES.length));
    }
    expect(seen.size).toBe(TAGLINES.length);
  });

  test("clamps rather than indexing off the end", () => {
    // Math.random() is documented as [0, 1), but the header must not be able
    // to render `undefined` if that ever changes or a caller passes 1.
    expect(pickTagline(1)).toBe(TAGLINES[TAGLINES.length - 1]);
    expect(pickTagline(5)).toBe(TAGLINES[TAGLINES.length - 1]);
    expect(pickTagline(-1)).toBe(TAGLINES[0]);
    expect(pickTagline(Number.NaN)).toBe(TAGLINES[0]);
  });
});

describe("renderHeaderLines", () => {
  const plain = (_role: string, text: string): string => text;

  test("wraps the logo in blank lines and appends the tagline and hint", () => {
    const lines = renderHeaderLines("stay honest", plain);
    expect(lines[0]).toBe("");
    expect(lines.slice(1, 1 + PI_LOGO.length)).toEqual([...PI_LOGO]);
    expect(lines[1 + PI_LOGO.length]).toBe("");
    expect(lines[2 + PI_LOGO.length]).toBe("  stay honest");
    expect(lines[3 + PI_LOGO.length]).toContain("ctrl+c clear");
    expect(lines[lines.length - 1]).toBe("");
  });

  test("colorizes the logo as accent and the tagline as muted", () => {
    const roles: string[] = [];
    renderHeaderLines("x", (role, text) => {
      roles.push(role);
      return text;
    });
    expect(roles.slice(0, PI_LOGO.length).every((r) => r === "accent")).toBe(true);
    expect(roles[PI_LOGO.length]).toBe("muted");
    expect(roles[PI_LOGO.length + 1]).toBe("dim");
  });

  test("the wordmark is a uniform width so the header never ragged-edges", () => {
    const widths = new Set(PI_LOGO.map((line) => [...line].length));
    expect(widths.size).toBe(1);
  });
});
