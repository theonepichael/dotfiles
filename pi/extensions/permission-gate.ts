import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isToolCallEventType } from "@earendil-works/pi-coding-agent";

// Ported from opencode.jsonc's `permission.bash` block (the same allowlist
// opencode reads today) — one house source of truth for "which bash
// commands are safe to run without asking," re-expressed for Pi's
// tool_call event instead of opencode's declarative permission config
// (which has no Pi equivalent: Pi ships no built-in permission-prompt
// system at all — docs/usage.md's Design Principles: "it intentionally
// does not include built-in MCP, sub-agents, permission popups, plan mode,
// to-dos, or background bash").
//
// Only bash is gated here, matching opencode.jsonc's actual current scope
// (no deny entries exist there today, only allow + a "*": "ask" default) —
// opencode.jsonc's separate `external_directory` permission type has no
// direct Pi analog (Pi's file tools have no comparable directory-scoped
// gate) and is out of scope for this port.
const ALLOW_PATTERNS: string[] = [
  "python3 ~/.claude/scripts/dev_status.py *",
  "python3 ~/.claude/scripts/grill.py *",
  "python3 ~/.claude/scripts/second_opinion.py *",
  "python3 ~/.claude/scripts/settings_seed_drift_check.py *",
  "python3 ~/.claude/scripts/dotfiles_sync_check.py *",
  "git log*",
  "git status*",
  "git diff*",
  "git show*",
  "git ls-files*",
  "git check-ignore*",
  "git -C * log*",
  "git -C * status*",
  "git -C * diff*",
  "git -C * show*",
  "git -C * ls-files*",
  "git -C * check-ignore*",
  "uv sync*",
  "uv run pytest*",
  "uv run ruff check*",
  "uv run ruff format*",
  "lsof *",
  "ps *",
  "ls*",
  "pwd",
  "which *",
  "head *",
  "tail *",
  "wc *",
  "sort *",
  "uniq *",
  "grep *",
  "rg *",
  "find *",
  "file *",
  "stat *",
  "du *",
  "df *",
  "date*",
  "whoami*",
  "env",
  "printenv*",
  "cat *",
  "sed -n *",
  "strings *",
  "readlink *",
  "jq *",
  "diff *",
  "diff",
  "echo *",
  "echo",
  "head",
  "tail",
  "wc",
  "sort",
  "uniq",
  "cat",
  "pgrep *",
  "ss *",
  "systemctl status*",
  "systemctl is-active*",
  "systemctl is-enabled*",
];

// No deny entries in opencode.jsonc today, but kept as a real tier (not
// folded into "everything not allowed is ask") so a future deny rule is a
// one-line addition here, not a structural change.
const DENY_PATTERNS: string[] = [];

function patternToRegExp(pattern: string): RegExp {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`);
}

const ALLOW_REGEXPS = ALLOW_PATTERNS.map(patternToRegExp);
const DENY_REGEXPS = DENY_PATTERNS.map(patternToRegExp);

type Verdict = "allow" | "deny" | "ask";

function classify(command: string): Verdict {
  const trimmed = command.trim();
  if (DENY_REGEXPS.some((re) => re.test(trimmed))) return "deny";
  if (ALLOW_REGEXPS.some((re) => re.test(trimmed))) return "allow";
  return "ask";
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (!isToolCallEventType("bash", event)) return;

    const command = event.input.command;
    const verdict = classify(command);

    if (verdict === "allow") return;

    if (verdict === "deny") {
      return { block: true, reason: `Denied by permission-gate: ${command}` };
    }

    // "ask" tier. ctx.hasUI is true in TUI and RPC modes, false in print
    // mode (-p) and JSON mode (docs/extensions.md's ExtensionContext) —
    // there is nothing to confirm through in the false case, so block
    // rather than silently letting an unreviewed command run headless.
    if (!ctx.hasUI) {
      return {
        block: true,
        reason: `Blocked by permission-gate (no UI to confirm through in this mode): ${command}`,
      };
    }

    const ok = await ctx.ui.confirm("Run bash command?", command);
    if (!ok) return { block: true, reason: "Blocked by user" };
  });
}
