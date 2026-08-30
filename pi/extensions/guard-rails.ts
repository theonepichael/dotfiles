import path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isToolCallEventType } from "@earendil-works/pi-coding-agent";

let enabled = true;

export function isDangerousRm(command: string): boolean {
  const rmMatches = command.matchAll(/\brm\s+([^;&|`$()]+)/gi);
  for (const match of rmMatches) {
    const args = match[1] ?? "";
    const flagSection = args.split(/\s--\s/)[0] ?? args;
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
  for (const sub of subcommands) {
    const trimmed = sub.trim();
    const match = trimmed.match(/\bgit(?:\.exe)?\b\s+(.*)/i);
    if (!match) continue;

    const rest = match[1] ?? "";
    const words = rest.split(/\s+/);
    let targetCwd = defaultCwd;
    let isCommit = false;

    for (let i = 0; i < words.length; i++) {
      const w = words[i]!;
      if (w === "-C" && i + 1 < words.length) {
        const dir = words[i + 1]!.replace(/^["']|["']$/g, "");
        targetCwd = path.isAbsolute(dir) ? dir : path.resolve(defaultCwd, dir);
        i++;
      } else if (w.startsWith("-C")) {
        const dir = w.slice(2).replace(/^["']|["']$/g, "");
        targetCwd = path.isAbsolute(dir) ? dir : path.resolve(defaultCwd, dir);
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
