import path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isToolCallEventType } from "@earendil-works/pi-coding-agent";

let enabled = true;

// Exported so trust-session.ts can flip this gate alongside
// permission-gate.ts's without reaching into module-private state.
export function setGuardRailsEnabled(value: boolean): void {
  enabled = value;
}

export function isDangerousRm(command: string): boolean {
  const rmMatches = command.matchAll(/\brm\s+([^;&|`$()]+)/gi);
  for (const match of rmMatches) {
    const args = match[1] ?? "";
    // Leading space so a `--` in first position splits like any other one:
    // everything after `--` is a file name, so "rm -- -rf" has no flags at all.
    const flagSection = ` ${args}`.split(/\s--\s/)[0] ?? args;
    const hasR =
      /(?:^|\s)-[a-zA-Z]*r/i.test(flagSection) || /(?:^|\s)--recursive\b/i.test(flagSection);
    const hasF = /(?:^|\s)-[a-zA-Z]*f/i.test(flagSection) || /(?:^|\s)--force\b/i.test(flagSection);
    if (hasR && hasF) return true;
  }
  return false;
}

export function isProtectedPath(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, "/");
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length === 0) return false;
  const basename = segments[segments.length - 1]!;
  if (basename === ".env" || basename.startsWith(".env.")) return true;
  if (segments.includes(".git") || segments.includes("node_modules")) return true;
  return false;
}

export function getGitCommitTarget(
  command: string,
  defaultCwd: string,
): { isCommit: boolean; cwd: string } {
  const subcommands = command.split(/[;&|\n]+/);
  // Tracks `cd` between subcommands: `cd <worktree> && git commit` runs the
  // commit in the worktree, not in the session cwd.
  let shellCwd = defaultCwd;
  for (const sub of subcommands) {
    const trimmed = sub.trim();

    const cd = trimmed.match(/^cd\s+(.+)$/i);
    if (cd) {
      const dir = (cd[1] ?? "").trim().replace(/^["']|["']$/g, "");
      // `cd -` (and a bare `cd`, which never matches here) has no target we
      // can resolve statically — leave the tracked cwd as it is.
      if (dir && dir !== "-") {
        shellCwd = path.isAbsolute(dir) ? dir : path.resolve(shellCwd, dir);
      }
      continue;
    }

    const match = trimmed.match(/\bgit(?:\.exe)?\b\s+(.*)/i);
    if (!match) continue;

    const rest = match[1] ?? "";
    const words = rest.split(/\s+/);
    let targetCwd = shellCwd;
    let isCommit = false;

    for (let i = 0; i < words.length; i++) {
      const w = words[i]!;
      if (w === "-C" && i + 1 < words.length) {
        const dir = words[i + 1]!.replace(/^["']|["']$/g, "");
        targetCwd = path.isAbsolute(dir) ? dir : path.resolve(shellCwd, dir);
        i++;
      } else if (w.startsWith("-C")) {
        const dir = w.slice(2).replace(/^["']|["']$/g, "");
        targetCwd = path.isAbsolute(dir) ? dir : path.resolve(shellCwd, dir);
      } else if (w.startsWith("-")) {
        continue;
      } else {
        if (w.toLowerCase() === "commit") {
          isCommit = true;
        }
        break;
      }
    }

    if (isCommit) {
      return { isCommit: true, cwd: targetCwd };
    }
  }
  return { isCommit: false, cwd: defaultCwd };
}

async function currentGitBranch(pi: ExtensionAPI, cwd: string): Promise<string | null> {
  try {
    const result = await pi.exec("git", ["branch", "--show-current"], { cwd, timeout: 2000 });
    if (result.code === 0) {
      const branch = result.stdout.trim();
      return branch || null;
    }
  } catch {
    // Not a git repo or git not available
  }
  return null;
}

// R2/R3 (main-checkout writes, stale worktree base) live in
// claude/scripts/guard_rails.py so all five harnesses share one source of
// truth. The rm -rf / sudo / protected-path / git-commit rules above stay
// here: they need ctx.ui.confirm(), which a subprocess cannot do.
type SharedVerdict = { decision: "allow" | "deny" | "warn"; reason?: string };

async function sharedGuard(
  pi: ExtensionAPI,
  tool: string,
  filePath: string,
): Promise<SharedVerdict | null> {
  try {
    const result = await pi.exec(
      "python3",
      [
        `${process.env.HOME}/.claude/scripts/guard_rails.py`,
        "--tool",
        tool,
        "--cwd",
        path.dirname(filePath),
        "--path",
        filePath,
      ],
      { timeout: 5000 },
    );
    if (result.code !== 0) return null;
    return JSON.parse(result.stdout) as SharedVerdict;
  } catch {
    // Fail open: a guard that cannot answer must not block the loop.
    return null;
  }
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (!enabled) return undefined;

    // Bash guard rails
    if (isToolCallEventType("bash", event)) {
      const command = event.input.command;

      // rm -rf confirmation gate
      if (isDangerousRm(command)) {
        if (!ctx.hasUI) {
          return {
            block: true,
            reason: "rm -rf blocked (no UI for confirmation in headless mode)",
          };
        }
        const ok = await ctx.ui.confirm("⚠️ rm -rf", `Allow destructive command?\n\n  ${command}`);
        if (!ok) {
          return { block: true, reason: "rm -rf blocked by user" };
        }
      }

      // sudo confirmation gate
      if (/\bsudo\b/i.test(command)) {
        if (!ctx.hasUI) {
          return {
            block: true,
            reason: "sudo blocked (no UI for confirmation in headless mode)",
          };
        }
        const ok = await ctx.ui.confirm("⚠️ sudo", `Allow privileged command?\n\n  ${command}`);
        if (!ok) {
          return { block: true, reason: "sudo blocked by user" };
        }
      }

      // git commit on main/master — worktree policy
      const gitCommit = getGitCommitTarget(command, ctx.cwd);
      if (gitCommit.isCommit) {
        const branch = await currentGitBranch(pi, gitCommit.cwd);
        if (branch === "main" || branch === "master") {
          return {
            block: true,
            reason: `git commit blocked on '${branch}' branch. House policy: never commit directly to main. Use a worktree or create a feature branch.`,
          };
        }
      }

      return undefined;
    }

    // Write/edit guard rails
    if (isToolCallEventType("write", event) || isToolCallEventType("edit", event)) {
      const filePath = event.input.path as string;
      if (isProtectedPath(filePath)) {
        if (ctx.hasUI) {
          ctx.ui.notify(`Blocked write to protected path: ${filePath}`, "warning");
        }
        return { block: true, reason: `Path "${filePath}" is protected by guard-rails` };
      }

      const verdict = await sharedGuard(pi, "write", filePath);
      if (verdict?.decision === "deny") {
        return { block: true, reason: verdict.reason ?? "Blocked by guard-rails" };
      }
      if (verdict?.decision === "warn" && ctx.hasUI && verdict.reason) {
        ctx.ui.notify(verdict.reason, "warning");
      }
    }

    return undefined;
  });

  pi.registerCommand("guard-rails-disable", {
    description: "Disable guard-rails for this session",
    handler: async (_args, ctx) => {
      enabled = false;
      ctx.ui.notify("Guard rails disabled", "warning");
    },
  });

  pi.registerCommand("guard-rails-enable", {
    description: "Re-enable guard-rails",
    handler: async (_args, ctx) => {
      enabled = true;
      ctx.ui.notify("Guard rails enabled", "info");
    },
  });
}
