import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

// Registers swarm_spawn / swarm_poll / swarm_resolve_blocked -- lets a pi
// session process several READY dev_status.py backlog items concurrently by
// spawning one recursive pi worker per item in its own herdr pane, pooling
// completion via herdr's socket API. Design, decisions, and three rounds of
// /second-opinion critique: ~/.claude/data/grill/2026-09-01-pi-side-agent-swarm-orchestratio-plan.md
// and its -critique-notes.md companion.
//
// The herdr mechanics below (agent_status JSON shape, --until repetition,
// send-keys navigation) were verified against a real live blocked agent
// during implementation, not just inferred from `herdr --skill`'s docs --
// four real gaps surfaced that way and are fixed in this file:
//   1. `--until` must be repeated per state; a comma-joined list is a CLI
//      usage error (exit 2), not silently accepted.
//   2. herdr's pi integration (herdr-agent-state.ts) reports "blocked" only
//      when told to via a `herdr:blocked` event -- it does no screen
//      detection once its lifecycle hook is authoritative. Nothing in this
//      repo emitted that event before this change; question-tool.ts now
//      does (see that file), and backlog-item.md's commit/merge-push gates
//      were updated to go through it instead of "ask in plain text" (which
//      just ends the turn -- indistinguishable from finishing).
//   3. `agent prompt` refuses an agent already `blocked` (`agent_blocked`
//      error) -- it cannot be used to answer a picker.
//   4. There is no `agent send-text`; answering a blocked picker means
//      driving it via `agent send-keys` arrow-key navigation (confirmed:
//      "down"/"up" move the rendered `>` marker, "enter" submits).
//
// A fifth gap surfaced later, live, on a real multi-item swarm run: an
// earlier version of swarm_poll raced every active worker's `herdr agent
// wait` call and ABORTED every non-winner the instant any one settled --
// including workers that were still genuinely active or had just reached a
// real blocked state. `pi.exec`'s underlying execCommand (dist/core/exec.js)
// always RESOLVES, even on abort, coercing a signal-killed process's null
// exit code to 0 -- so a killed wait call could resolve with exitCode 0 and
// empty stdout, which fell into classifyWaitResult's old fallback bucket
// and got reported as a false timed_out. Confirmed live: a worker that had
// actually finished successfully (dev_status.py showed status: done) and
// another that had reached a genuine blocked question were both misreported
// this way. Fix: no aborting at all. Each active worker gets exactly one
// long-running `agent wait` call, armed once and never killed -- losers of
// a race just keep running toward their own --timeout, feeding a shared
// per-run event queue as they naturally resolve. classifyWaitResult also no
// longer folds every non-timeout nonzero exit into "timed_out" -- only
// herdr's own `{"error":{"code":"timeout"}}` (on stderr) counts as a real
// timeout; anything else (agent_not_found, a crash, an unparseable
// response) is reported as "error" instead, with the raw detail attached,
// rather than silently mislabeled as a timeout that never happened.

/**
 * Where a run's state file lives. Resolved per call, not captured at module
 * load, so a test can point it somewhere disposable after importing this
 * module -- swarm_poll/swarm_spawn persist through the closure's `persist`,
 * which has no stateDir parameter to thread one through, so an env override
 * is the only seam that keeps an execute()-level test off the real ~/.pi.
 */
function herdrStateDir(): string {
  return process.env.PI_SWARM_STATE_DIR ?? join(homedir(), ".pi", "agent", "state");
}

const DEFAULT_CONCURRENCY = 3;
const OPEN_PANE_SOFT_CAP_MULTIPLIER = 2;
const AGENT_START_TIMEOUT_MS = 30_000;
const DEFAULT_WAIT_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes, matching --auto's generous per-step conventions
const RESOLVE_VERIFY_TIMEOUT_MS = 5_000;
const BLOCKED_READ_LINES = 500;
const BLOCKED_READ_LINES_RETRY = 2000;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type WorkerLifecycle = "active" | "awaiting_relay";

export interface WorkerRecord {
  agent: string; // synthetic id, e.g. "w1" -- never the raw slug (herdr names cap at 32 chars)
  slug: string;
  paneId: string;
  lifecycle: WorkerLifecycle;
}

export interface SwarmState {
  runId: string;
  concurrency: number;
  nextCounter: number;
  workers: WorkerRecord[];
}

export type PollEventKind = "blocked" | "finished" | "timed_out" | "error";

export interface PollEvent {
  kind: PollEventKind;
  agent: string;
  slug: string;
  paneId: string;
  rawPrompt?: string; // blocked only
  truncated?: boolean; // blocked only
  detail?: string; // timed_out/error only -- the raw herdr error detail, for an honest digest
}

// ---------------------------------------------------------------------------
// State file persistence and crash recovery
// ---------------------------------------------------------------------------

export function statePath(runId: string, stateDir: string = herdrStateDir()): string {
  return join(stateDir, `swarm-${runId}.json`);
}

export function loadState(runId: string, stateDir: string = herdrStateDir()): SwarmState | null {
  const path = statePath(runId, stateDir);
  if (!existsSync(path)) return null;
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, "utf8"));
    if (parsed && typeof parsed === "object" && "workers" in parsed) {
      return parsed as SwarmState;
    }
    return null;
  } catch {
    return null;
  }
}

export function saveState(state: SwarmState, stateDir: string = herdrStateDir()): void {
  mkdirSync(stateDir, { recursive: true });
  writeFileSync(statePath(state.runId, stateDir), JSON.stringify(state, null, 2));
}

/**
 * Reconcile persisted bookkeeping against herdr's live agent list. A
 * tracked-but-dead entry (in state, absent from herdr's live list) is
 * dropped and reported for the digest -- the pane/agent is gone, nothing
 * left to reconcile.
 *
 * This does not attempt to *adopt* an untracked-but-live herdr agent: doing
 * so would need a slug to resume tracking it under, and no slug is
 * recoverable from a bare agent id (the id↔slug↔pane mapping lives only in
 * the state file itself, per nextAgentId's design). The actual crash-
 * recovery win is `loadState` reading that file straight off disk after a
 * process restart -- the mapping survives as long as `saveState` ran before
 * the crash. The narrow gap this leaves (a worker spawned but never
 * persisted before a crash) has no general fix and isn't attempted here.
 */
export function reconcileState(
  state: SwarmState,
  liveAgentIds: readonly string[],
): { state: SwarmState; dropped: WorkerRecord[] } {
  const live = new Set(liveAgentIds);
  const dropped: WorkerRecord[] = [];
  const kept: WorkerRecord[] = [];

  for (const worker of state.workers) {
    if (live.has(worker.agent)) {
      kept.push(worker);
    } else {
      dropped.push(worker);
    }
  }

  return { state: { ...state, workers: kept }, dropped };
}

// ---------------------------------------------------------------------------
// Naming
// ---------------------------------------------------------------------------

/** Synthetic herdr agent name incorporating the slug, capped at herdr's 32-char limit. */
export function nextAgentId(runId: string, counter: number, slug?: string): string {
  const cleanSlug = slug ? slug.replace(/[^a-zA-Z0-9_-]/g, "") : "";
  const base = cleanSlug ? `${runId}-w${counter}-${cleanSlug}` : `${runId}-w${counter}`;
  return base.slice(0, 32);
}

// ---------------------------------------------------------------------------
// herdr argv builders -- every element is a discrete argv item, never a
// concatenated shell string (delegate-tool.ts's buildArgv pattern). This is
// load-bearing: swarm_resolve_blocked passes the human's free-text answer
// straight through as one of these elements.
// ---------------------------------------------------------------------------

export function buildPaneRenameArgv(paneId: string, label: string): string[] {
  return ["pane", "rename", paneId, label];
}

export function buildPaneSplitArgv(direction: "right" | "down", cwd: string): string[] {
  return ["pane", "split", "--current", "--direction", direction, "--cwd", cwd, "--no-focus"];
}

export function buildAgentStartArgv(agentId: string, paneId: string): string[] {
  return [
    "agent",
    "start",
    agentId,
    "--kind",
    "pi",
    "--pane",
    paneId,
    "--timeout",
    String(AGENT_START_TIMEOUT_MS),
  ];
}

export function buildAgentPromptArgv(agentId: string, prompt: string): string[] {
  return ["agent", "prompt", agentId, prompt];
}

/** `agent prompt` refuses a blocked agent outright (agent_blocked, confirmed live) -- send-keys is the only way to answer its picker. */
export function buildAgentSendKeysArgv(agentId: string, keys: readonly string[]): string[] {
  return ["agent", "send-keys", agentId, ...keys];
}

/** `--until` must be repeated once per state -- herdr rejects a comma-joined list (confirmed live: `--until idle,done,blocked` exits 2, "invalid agent status"). */
export function buildAgentWaitArgv(
  agentId: string,
  until: readonly string[],
  timeoutMs: number,
): string[] {
  return [
    "agent",
    "wait",
    agentId,
    ...until.flatMap((state) => ["--until", state]),
    "--timeout",
    String(timeoutMs),
  ];
}

export function buildAgentGetArgv(agentId: string): string[] {
  return ["agent", "get", agentId];
}

export function buildAgentReadArgv(agentId: string, lines: number): string[] {
  return ["agent", "read", agentId, "--source", "recent-unwrapped", "--lines", String(lines)];
}

export function buildPaneCloseArgv(paneId: string): string[] {
  return ["pane", "close", paneId];
}

export function buildAgentListArgv(): string[] {
  return ["agent", "list"];
}

// ---------------------------------------------------------------------------
// herdr response parsing
// ---------------------------------------------------------------------------

interface HerdrEnvelope {
  // `agent get`/`agent wait` nest the single-agent payload one level under
  // "agent" -- confirmed live, repeatedly (e.g. `herdr agent get <id>` ->
  // {"result":{"agent":{"agent_status":"blocked",...}}}). `agent list`'s
  // `result.agents[]` array elements do NOT have this extra nesting (each
  // element already has agent_status/agent_session directly on it) -- these
  // are two different response shapes for two different commands, not one
  // shared shape; do not conflate them into a single flat interface again.
  result?: {
    agent?: { agent_status?: string; pane_id?: string };
    agents?: { agent_status?: string; agent_session?: unknown; pane_id?: string }[];
  };
  error?: { code?: string; message?: string };
}

function parseHerdrJson(text: string): HerdrEnvelope | null {
  try {
    return JSON.parse(text) as HerdrEnvelope;
  } catch {
    return null;
  }
}

/**
 * Classify a settled `herdr agent wait` result into blocked/finished/
 * timed_out/error. herdr reports server errors as JSON on stderr with exit
 * status 1 (confirmed live) -- only `{"error":{"code":"timeout"}}` counts as
 * a genuine timeout. Any other nonzero exit (agent_not_found, a crash) or an
 * exit-0 response with no recognized agent_status is "error", not silently
 * folded into "timed_out" -- see this file's header comment for why that
 * distinction matters (a killed-but-resolved exec call looks exactly like
 * the exit-0/unrecognized-status case).
 */
export function classifyWaitResult(
  exitCode: number,
  stdout: string,
  stderr: string,
): PollEventKind {
  if (exitCode === 0) {
    const status = parseHerdrJson(stdout)?.result?.agent?.agent_status;
    if (status === "blocked") return "blocked";
    if (status === "idle" || status === "done") return "finished";
    return "error";
  }
  const code = parseHerdrJson(stderr)?.error?.code;
  return code === "timeout" ? "timed_out" : "error";
}

/**
 * Cross-check that `agent get`'s reported pane_id still matches the pane
 * this worker was spawned into, right before `swarm_resolve_blocked` sends
 * any keystrokes. herdr's `agent <name>` commands (read/get/send-keys) all
 * resolve the same name -> pane mapping internally; if that mapping ever
 * goes stale or collides under concurrency, every one of those calls would
 * consistently hit the same wrong pane, so re-reading by agent name before
 * sending keys can't catch it -- only a cross-check against the pane_id
 * swarm-tool tracked independently at spawn time can. A missing/unparseable
 * pane_id is treated as a mismatch (fail closed, not open).
 */
export function paneIdentityMismatch(
  getExitCode: number,
  getStdout: string,
  expectedPaneId: string,
): boolean {
  if (getExitCode !== 0) return true;
  const reportedPaneId = parseHerdrJson(getStdout)?.result?.agent?.pane_id;
  return reportedPaneId !== expectedPaneId;
}

/** The raw herdr error detail for a timed_out/error event -- for an honest digest, not just a bare label. */
export function waitResultDetail(stdout: string, stderr: string): string {
  const err = parseHerdrJson(stderr)?.error;
  if (err) return `${err.code ?? "unknown"}: ${err.message ?? stderr.trim()}`;
  return stdout.trim() || stderr.trim() || "(no output)";
}

/** Content that fills the requested line budget exactly is a truncation signal, not necessarily proof -- see plan section 4. */
export function looksTruncated(content: string, requestedLines: number): boolean {
  return content.split("\n").length >= requestedLines;
}

// ---------------------------------------------------------------------------
// Answering a blocked worker's picker -- `herdr agent prompt` refuses a
// blocked agent outright (agent_blocked error, confirmed live) and there is
// no `agent send-text` for literal input. The only real path is driving
// question-tool.ts's rendered picker via `agent send-keys` (arrow-key
// navigation, confirmed live: "down" moves the `>` marker, "enter" submits).
// Free-text answers matching no listed option aren't auto-answerable this
// way -- see swarm_resolve_blocked's needsManual outcome below.
// ---------------------------------------------------------------------------

export interface RenderedOption {
  index: number;
  label: string;
}

export interface ParsedPicker {
  selectedIndex: number | null;
  options: RenderedOption[];
}

const OPTION_LINE = /^\s*(>)?\s*(\d+)\.\s+(.+?)\s*$/;
const OTHER_OPTION_LABEL = "Something else (type it)";

/** Parse question-tool.ts's rendered picker (plain `recent-unwrapped` text, no ANSI) into its option list and current selection. */
export function parsePicker(content: string): ParsedPicker {
  const options: RenderedOption[] = [];
  let selectedIndex: number | null = null;
  for (const line of content.split("\n")) {
    const m = OPTION_LINE.exec(line);
    if (!m) continue;
    const index = Number(m[2]);
    const label = m[3]!;
    options.push({ index, label });
    if (m[1] === ">") selectedIndex = index;
  }
  return { selectedIndex, options };
}

/**
 * Match a free-text answer to a listed option -- case-insensitive exact
 * match first, then a substring match, both excluding the always-present
 * free-text escape option (never auto-select "Something else" via fuzzy
 * matching). Returns null on no match or an ambiguous (multiple) match --
 * the caller falls back to reporting needsManual rather than guessing.
 */
export function matchOption(
  answer: string,
  options: readonly RenderedOption[],
): RenderedOption | null {
  const candidates = options.filter(
    (o) => o.label.toLowerCase() !== OTHER_OPTION_LABEL.toLowerCase(),
  );
  const needle = answer.trim().toLowerCase();
  if (!needle) return null;

  const exact = candidates.filter((o) => o.label.toLowerCase() === needle);
  if (exact.length === 1) return exact[0]!;

  const partial = candidates.filter((o) => o.label.toLowerCase().includes(needle));
  if (partial.length === 1) return partial[0]!;

  return null;
}

/** Arrow-key presses to move from the currently selected option to the target, then submit. */
export function navigationKeys(fromIndex: number, toIndex: number): string[] {
  const steps = toIndex - fromIndex;
  const key = steps > 0 ? "down" : "up";
  return [...Array(Math.abs(steps)).fill(key), "enter"];
}

// ---------------------------------------------------------------------------
// Concurrency and pane accounting -- pure, so the cap/backpressure rules are
// independently testable from the async pool machinery that calls them.
// ---------------------------------------------------------------------------

export function activeWorkerCount(state: SwarmState): number {
  return state.workers.filter((w) => w.lifecycle === "active").length;
}

export function canSpawnNew(state: SwarmState): boolean {
  return activeWorkerCount(state) < state.concurrency;
}

export function openPaneCount(state: SwarmState): number {
  return state.workers.length; // active + awaiting_relay; finished workers' entries are removed on close
}

export function openPaneSoftCap(concurrency: number): number {
  return concurrency * OPEN_PANE_SOFT_CAP_MULTIPLIER;
}

export function canOpenNewPane(state: SwarmState): boolean {
  return openPaneCount(state) < openPaneSoftCap(state.concurrency);
}

// ---------------------------------------------------------------------------
// Queue selection
// ---------------------------------------------------------------------------

/** How many new items can be spawned right now, bounded by both the concurrency cap and the open-pane soft cap. */
export function spawnBudget(state: SwarmState, readyCount: number): number {
  const byConcurrency = Math.max(0, state.concurrency - activeWorkerCount(state));
  const byPaneCap = Math.max(0, openPaneSoftCap(state.concurrency) - openPaneCount(state));
  return Math.min(byConcurrency, byPaneCap, readyCount);
}

export default function (pi: ExtensionAPI) {
  const activeRuns = new Map<string, SwarmState>();

  async function herdr(pi_: ExtensionAPI, argv: string[], signal?: AbortSignal) {
    return pi_.exec("herdr", argv, { signal });
  }

  function parseAgentListIds(stdout: string): string[] {
    try {
      const parsed = JSON.parse(stdout) as {
        result?: { agents?: { name?: string }[] };
      };
      // Each `agent list` entry carries the caller-chosen herdr name
      // directly as `.name` (confirmed live) -- this is what `reconcileState`
      // must match against `worker.agent` (the synthetic id assigned at
      // spawn time). An earlier version of this read `agent_session.value`
      // (a pi session file path) instead, which could never match a
      // synthetic id like "run1-w1" -- every reconciliation would have
      // wrongly dropped every genuinely-live worker as dead.
      return (parsed.result?.agents ?? [])
        .map((a) => a.name)
        .filter((v): v is string => typeof v === "string");
    } catch {
      return [];
    }
  }

  /** Loads state, reconciling against herdr's live truth only on a cold load (state wasn't already in this process's memory) -- avoids a herdr round-trip on every call once a run is warm. */
  async function getOrInitState(runId: string, concurrency: number): Promise<SwarmState> {
    const cached = activeRuns.get(runId);
    if (cached) return cached;

    const loaded = loadState(runId);
    if (!loaded) {
      const fresh: SwarmState = { runId, concurrency, nextCounter: 0, workers: [] };
      activeRuns.set(runId, fresh);
      return fresh;
    }

    const listResult = await herdr(pi, buildAgentListArgv());
    const liveIds = listResult.code === 0 ? parseAgentListIds(listResult.stdout) : [];
    // Dropped entries (tracked in state, dead in herdr's live list) are
    // simply excluded from `reconciled.workers` -- a dead worker's item just
    // won't produce further events; there's nothing left to reconcile it
    // against once its pane and process are gone.
    const { state: reconciled } = reconcileState(loaded, liveIds);
    activeRuns.set(runId, reconciled);
    saveState(reconciled);
    return reconciled;
  }

  function persist(state: SwarmState): void {
    activeRuns.set(state.runId, state);
    saveState(state);
  }

  // ---------------------------------------------------------------------------
  // Per-run wait tracking: exactly one long-running `herdr agent wait` call
  // per active worker, armed once, never killed (the round-2 fix -- see this
  // file's header comment). Each arm's own settlement pushes a classified
  // event onto `pendingEvents`; `swarm_poll` drains whatever's queued, or
  // waits for the next arrival. Purely in-process, not persisted -- a
  // restart just re-arms fresh waits for whatever the reconciled state
  // (which IS persisted) says is still active.
  // ---------------------------------------------------------------------------

  interface RunRuntime {
    inFlight: Set<string>; // agent ids with a wait currently running
    pendingEvents: PollEvent[];
    waiters: (() => void)[]; // resolvers for swarm_poll calls currently waiting on the next event
  }

  const runtimes = new Map<string, RunRuntime>();

  function getRuntime(runId: string): RunRuntime {
    let rt = runtimes.get(runId);
    if (!rt) {
      rt = { inFlight: new Set(), pendingEvents: [], waiters: [] };
      runtimes.set(runId, rt);
    }
    return rt;
  }

  /** Arms a worker's wait call if one isn't already running for it. Idempotent -- safe to call every time swarm_poll checks in on the active pool. */
  function armWait(rt: RunRuntime, worker: WorkerRecord, timeoutMs: number): void {
    if (rt.inFlight.has(worker.agent)) return;
    rt.inFlight.add(worker.agent);
    void herdr(pi, buildAgentWaitArgv(worker.agent, ["idle", "done", "blocked"], timeoutMs)).then(
      (result) => {
        rt.inFlight.delete(worker.agent);
        const kind = classifyWaitResult(result.code, result.stdout, result.stderr);
        const event: PollEvent = {
          kind,
          agent: worker.agent,
          slug: worker.slug,
          paneId: worker.paneId,
        };
        if (kind === "timed_out" || kind === "error") {
          event.detail = waitResultDetail(result.stdout, result.stderr);
        }
        rt.pendingEvents.push(event);
        const waiting = rt.waiters.splice(0);
        for (const wake of waiting) wake();
      },
    );
  }

  pi.registerTool({
    name: "swarm_spawn",
    label: "Swarm spawn",
    description:
      "Spawn recursive pi workers for a batch of READY backlog items, one herdr pane each, up to the concurrency cap.",
    promptSnippet: "Spawn concurrent pi workers for a batch of backlog items via herdr",
    promptGuidelines: [
      "Panes are split sequentially (herdr pane split mutates shared layout state -- concurrent splits race), then agent start+prompt run concurrently across the resulting panes.",
      "A per-item spawn failure (agent_not_ready, agent_prompt_stalled, or an unparseable pane-split response) is reported in `failed`, not thrown -- other items in the batch are unaffected.",
      "Call swarm_poll next to begin the completion loop.",
    ],
    parameters: Type.Object({
      runId: Type.String({
        description:
          "Identifier for this swarm run -- reused across spawn/poll/resolve calls, and to recover state after a restart.",
      }),
      items: Type.Array(Type.String(), {
        description: "Backlog item slugs to spawn, in queue order.",
      }),
      concurrency: Type.Optional(
        Type.Number({ description: "Max concurrent active workers. Default 3." }),
      ),
    }),
    async execute(_toolCallId, params) {
      const typed = params as { runId: string; items: string[]; concurrency?: number };
      const state = await getOrInitState(typed.runId, typed.concurrency ?? DEFAULT_CONCURRENCY);

      const budget = spawnBudget(state, typed.items.length);
      const toSpawn = typed.items.slice(0, budget);

      const splitPanes: {
        slug: string;
        paneId?: string;
        failed?: { slug: string; reason: string };
      }[] = [];
      for (const [i, slug] of toSpawn.entries()) {
        const direction = i % 2 === 0 ? "right" : "down";
        const result = await herdr(pi, buildPaneSplitArgv(direction, process.cwd()));
        if (result.code !== 0) {
          splitPanes.push({
            slug,
            failed: { slug, reason: `pane split failed: ${result.stderr || result.stdout}` },
          });
          continue;
        }
        try {
          const parsed = JSON.parse(result.stdout) as { result?: { pane?: { pane_id?: string } } };
          const paneId = parsed.result?.pane?.pane_id;
          if (!paneId) throw new Error("no pane_id in response");
          await herdr(pi, buildPaneRenameArgv(paneId, slug));
          splitPanes.push({ slug, paneId });
        } catch (e) {
          splitPanes.push({
            slug,
            failed: { slug, reason: `could not parse pane split response: ${String(e)}` },
          });
        }
      }

      const startResults = await Promise.allSettled(
        splitPanes.map(async (p) => {
          if (p.failed || !p.paneId) return { slug: p.slug, failed: p.failed };
          state.nextCounter += 1;
          const agentId = nextAgentId(typed.runId, state.nextCounter, p.slug);
          const startResult = await herdr(pi, buildAgentStartArgv(agentId, p.paneId));
          if (startResult.code !== 0) {
            return {
              slug: p.slug,
              failed: {
                slug: p.slug,
                reason: `agent_not_ready: ${startResult.stderr || startResult.stdout}`,
              },
            };
          }
          const promptResult = await herdr(
            pi,
            buildAgentPromptArgv(agentId, `/backlog-item --auto ${p.slug}`),
          );
          if (promptResult.code !== 0) {
            return {
              slug: p.slug,
              failed: {
                slug: p.slug,
                reason: `agent_prompt_stalled: ${promptResult.stderr || promptResult.stdout}`,
              },
            };
          }
          return {
            worker: {
              agent: agentId,
              slug: p.slug,
              paneId: p.paneId,
              lifecycle: "active" as const,
            },
          };
        }),
      );

      const spawned: WorkerRecord[] = [];
      const failed: { slug: string; reason: string }[] = [];
      for (const r of startResults) {
        if (r.status === "fulfilled") {
          if (r.value.worker) spawned.push(r.value.worker);
          else if (r.value.failed) failed.push(r.value.failed);
        } else {
          failed.push({ slug: "unknown", reason: String(r.reason) });
        }
      }

      state.workers.push(...spawned);
      persist(state);

      const skipped = typed.items.slice(budget);

      return {
        content: [
          {
            type: "text",
            text: `Spawned ${spawned.length} worker(s), ${failed.length} failed to spawn, ${skipped.length} skipped (cap).`,
          },
        ],
        details: { spawned, failed, skipped },
      };
    },
  });

  pi.registerTool({
    name: "swarm_poll",
    label: "Swarm poll",
    description:
      "Wait for at least one active swarm worker to settle (blocked/finished/timed_out/error), returning every event currently queued.",
    promptSnippet: "Wait for swarm workers to settle and report events",
    promptGuidelines: [
      "Blocks until >=1 active worker settles. Returns an array -- process every event in it, relaying each blocked event to the user one at a time, before calling swarm_poll again.",
      "A blocked event's raw_prompt is verbatim herdr output -- never assume it's a diff or a yes/no. Never send a blocked worker another agent prompt except the actual answer via swarm_resolve_blocked -- any prompt is interpreted as the gate's answer.",
      "timed_out means herdr's own wait deadline genuinely elapsed. error means something else went wrong (the agent disappeared, a crash, an unrecognized response) -- both close the pane, free the slot, and get flagged in the digest, never silently retried, but they are not the same failure and event.detail carries the raw reason.",
      "finished/timed_out/error events already closed their pane and freed their slot; if the READY queue still has items and the cap has headroom, call swarm_spawn again for the next batch.",
    ],
    parameters: Type.Object({
      runId: Type.String(),
      timeoutMs: Type.Optional(
        Type.Number({
          description: `Per-worker wait timeout, applied the first time a given worker's wait is armed (a worker already being waited on keeps its original timeout). Default ${DEFAULT_WAIT_TIMEOUT_MS}.`,
        }),
      ),
    }),
    async execute(_toolCallId, params, signal) {
      const typed = params as { runId: string; timeoutMs?: number };
      const state = await getOrInitState(typed.runId, DEFAULT_CONCURRENCY);
      const timeoutMs = typed.timeoutMs ?? DEFAULT_WAIT_TIMEOUT_MS;
      const rt = getRuntime(typed.runId);

      const active = state.workers.filter((w) => w.lifecycle === "active");
      for (const w of active) armWait(rt, w, timeoutMs);

      if (active.length === 0 && rt.pendingEvents.length === 0) {
        return {
          content: [{ type: "text", text: "No active workers to poll." }],
          details: { events: [] },
        };
      }

      if (rt.pendingEvents.length === 0) {
        await new Promise<void>((resolve) => rt.waiters.push(resolve));
      }

      const events = rt.pendingEvents.splice(0);

      for (const event of events) {
        const worker = state.workers.find((w) => w.agent === event.agent);
        if (!worker) continue;
        if (event.kind === "blocked") {
          const getResult = await herdr(pi, buildAgentGetArgv(event.agent), signal);
          let readResult = await herdr(
            pi,
            buildAgentReadArgv(event.agent, BLOCKED_READ_LINES),
            signal,
          );
          let truncated = looksTruncated(readResult.stdout, BLOCKED_READ_LINES);
          if (truncated) {
            readResult = await herdr(
              pi,
              buildAgentReadArgv(event.agent, BLOCKED_READ_LINES_RETRY),
              signal,
            );
            truncated = looksTruncated(readResult.stdout, BLOCKED_READ_LINES_RETRY);
          }
          event.rawPrompt = readResult.stdout || getResult.stdout;
          event.truncated = truncated;
          worker.lifecycle = "awaiting_relay";
        } else {
          await herdr(pi, buildPaneCloseArgv(worker.paneId), signal);
          state.workers = state.workers.filter((w) => w.agent !== worker.agent);
        }
      }
      persist(state);

      return {
        content: [
          {
            type: "text",
            text: events
              .map((e) =>
                e.kind === "blocked"
                  ? `${e.slug} (${e.agent}) is blocked${e.truncated ? " -- content may be truncated, inspect pane " + e.paneId + " directly" : ""}:\n${e.rawPrompt}`
                  : `${e.slug} (${e.agent}) ${e.kind}${e.detail ? `: ${e.detail}` : ""}`,
              )
              .join("\n\n"),
          },
        ],
        details: { events },
      };
    },
  });

  pi.registerTool({
    name: "swarm_resolve_blocked",
    label: "Swarm resolve",
    description:
      "Answer a blocked worker's picker by navigating to the matching option and submitting it.",
    promptSnippet: "Answer a blocked swarm worker's picker",
    promptGuidelines: [
      "answer is matched against the blocked worker's currently rendered option labels (re-read fresh, not from a stale raw_prompt) -- pass the user's own words, not a paraphrase, so the match is against what they actually said.",
      "herdr agent prompt refuses a blocked agent outright -- this tool drives the picker via arrow-key navigation instead, the only way to answer it.",
      "If answer matches no listed option (or matches more than one ambiguously), this returns needsManual: true instead of guessing -- relay that back to the user verbatim (which pane, and the exact listed option labels) rather than retrying blindly.",
      "Verifies the worker actually left `blocked` within a short window after submitting; if it didn't (pane closed, still stuck), the item is marked relay_failed rather than silently treated as resolved.",
      "Re-checks the target's pane_id against what it was spawned into immediately before sending any keys -- if herdr's agent-name-to-pane mapping ever drifted, this is what catches it (a stale mapping would make read/match agree with the wrong pane too, so this is a second, independent identity check, not a repeat of the read). A mismatch returns needsManual: true and sends nothing.",
    ],
    parameters: Type.Object({
      runId: Type.String(),
      agent: Type.String({
        description: "The synthetic agent id from a blocked swarm_poll event.",
      }),
      answer: Type.String({
        description:
          "The human's exact answer -- matched against the picker's listed option labels.",
      }),
    }),
    async execute(_toolCallId, params, signal) {
      const typed = params as { runId: string; agent: string; answer: string };
      const state = await getOrInitState(typed.runId, DEFAULT_CONCURRENCY);
      const worker = state.workers.find((w) => w.agent === typed.agent);
      if (!worker) {
        return {
          content: [
            { type: "text", text: `No tracked worker "${typed.agent}" in run ${typed.runId}.` },
          ],
          details: { relayFailed: true, needsManual: false, slug: "", paneId: "" },
        };
      }

      const readResult = await herdr(
        pi,
        buildAgentReadArgv(typed.agent, BLOCKED_READ_LINES),
        signal,
      );
      const picker = parsePicker(readResult.stdout);
      const target = matchOption(typed.answer, picker.options);

      if (!target || picker.selectedIndex === null) {
        const optionList = picker.options.map((o) => `"${o.label}"`).join(", ");
        return {
          content: [
            {
              type: "text",
              text:
                `Could not match "${typed.answer}" to exactly one listed option for ${typed.agent} ` +
                `(${worker.slug}). Listed options: ${optionList || "(none parsed)"}. ` +
                `Attach directly (herdr agent attach ${typed.agent}) or retry with text matching one ` +
                `option's label exactly.`,
            },
          ],
          details: {
            relayFailed: false,
            needsManual: true,
            slug: worker.slug,
            paneId: worker.paneId,
          },
        };
      }

      const getResult = await herdr(pi, buildAgentGetArgv(typed.agent), signal);
      if (paneIdentityMismatch(getResult.code, getResult.stdout, worker.paneId)) {
        return {
          content: [
            {
              type: "text",
              text:
                `needs_manual: ${typed.agent} (${worker.slug})'s pane identity no longer matches ` +
                `what it was spawned into -- refusing to send keys rather than risk hitting the wrong ` +
                `pane. Attach directly (herdr agent attach ${typed.agent}) to answer it by hand.`,
            },
          ],
          details: {
            relayFailed: false,
            needsManual: true,
            slug: worker.slug,
            paneId: worker.paneId,
          },
        };
      }

      const keysResult = await herdr(
        pi,
        buildAgentSendKeysArgv(typed.agent, navigationKeys(picker.selectedIndex, target.index)),
        signal,
      );
      if (keysResult.code !== 0) {
        state.workers = state.workers.filter((w) => w.agent !== typed.agent);
        persist(state);
        return {
          content: [
            {
              type: "text",
              text: `relay_failed: could not send navigation keys to ${typed.agent}: ${keysResult.stderr || keysResult.stdout}`,
            },
          ],
          details: {
            relayFailed: true,
            needsManual: false,
            slug: worker.slug,
            paneId: worker.paneId,
          },
        };
      }

      const verify = await herdr(
        pi,
        buildAgentWaitArgv(typed.agent, ["idle", "done", "blocked"], RESOLVE_VERIFY_TIMEOUT_MS),
        signal,
      );
      const stillBlocked =
        classifyWaitResult(verify.code, verify.stdout, verify.stderr) === "blocked";

      if (verify.code !== 0 || stillBlocked) {
        state.workers = state.workers.filter((w) => w.agent !== typed.agent);
        persist(state);
        return {
          content: [
            {
              type: "text",
              text: `relay_failed: ${typed.agent} did not leave blocked after "${target.label}" was submitted.`,
            },
          ],
          details: {
            relayFailed: true,
            needsManual: false,
            slug: worker.slug,
            paneId: worker.paneId,
          },
        };
      }

      worker.lifecycle = "active";
      persist(state);
      return {
        content: [
          {
            type: "text",
            text: `${typed.agent} (${worker.slug}) answered "${target.label}", back in the active pool.`,
          },
        ],
        details: {
          relayFailed: false,
          needsManual: false,
          slug: worker.slug,
          paneId: worker.paneId,
        },
      };
    },
  });
}
