import { describe, expect, test } from "bun:test";
import { classify, patternToRegExp } from "../extensions/permission-gate";

describe("patternToRegExp", () => {
  test("anchors the whole command", () => {
    const re = patternToRegExp("pwd");
    expect(re.test("pwd")).toBe(true);
    expect(re.test("pwdx")).toBe(false);
    expect(re.test("  pwd  ")).toBe(false);
  });

  test("* becomes a wildcard, other regex metacharacters stay literal", () => {
    const re = patternToRegExp("git log*");
    expect(re.test("git log")).toBe(true);
    expect(re.test("git log --oneline")).toBe(true);
    expect(re.test("gitx log")).toBe(false);
    const literal = patternToRegExp("python3 ~/.claude/scripts/grill.py *");
    expect(literal.test("python3 ~/.claude/scripts/grill.py --help")).toBe(true);
    expect(literal.test("python3 ~/.claudeXscripts/grill.py --help")).toBe(false);
  });
});

describe("classify", () => {
  test("allowlisted commands are allowed", () => {
    expect(classify("git status")).toBe("allow");
    expect(classify("git diff HEAD~1")).toBe("allow");
    expect(classify("uv run pytest -q")).toBe("allow");
    expect(classify("ls -la")).toBe("allow");
    expect(classify("pwd")).toBe("allow");
    expect(classify("git -C /repo status")).toBe("allow");
  });

  test("unlisted commands fall through to ask", () => {
    expect(classify("rm -rf /")).toBe("ask");
    expect(classify("git push")).toBe("ask");
    expect(classify("curl http://example.com")).toBe("ask");
    expect(classify("python3 ~/.claude/scripts/dev_status.py render")).toBe("ask");
  });

  test("leading and trailing whitespace does not bypass the gate", () => {
    expect(classify("  git status  ")).toBe("allow");
    expect(classify("  rm -rf /  ")).toBe("ask");
  });

  test("the deny tier exists but is empty today, so nothing denies", () => {
    expect(classify("anything")).not.toBe("deny");
  });
});
