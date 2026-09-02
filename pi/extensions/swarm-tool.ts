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

const HERDR_STATE_DIR = join(homedir(), ".pi", "agent", "state");

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

export type PollEventKind = "blocked" | "finished" | "timed_out";

export interface PollEvent {
  kind: PollEventKind;
  agent: string;
  slug: string;
  paneId: string;
  rawPrompt?: string; // blocked only
  truncated?: boolean; // blocked only
}

// ---------------------------------------------------------------------------
// State file persistence and crash recovery
// ---------------------------------------------------------------------------

export function statePath(runId: string, stateDir: string = HERDR_STATE_DIR): string {
  return join(stateDir, `swarm-${runId}.json`);
}

export function loadState(runId: string, stateDir: string = HERDR_STATE_DIR): SwarmState | null {
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

export function saveState(state: SwarmState, stateDir: string = HERDR_STATE_DIR): void {
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

/** Short synthetic herdr agent name -- never the raw slug (herdr caps names at 32 chars; real backlog slugs exceed that). */
export function nextAgentId(runId: string, counter: number): string {
  return `${runId}-w${counter}`;
}

// ---------------------------------------------------------------------------
// herdr argv builders -- every element is a discrete argv item, never a
// concatenated shell string (delegate-tool.ts's buildArgv pattern). This is
// load-bearing: swarm_resolve_blocked passes the human's free-text answer
// straight through as one of these elements.
// ---------------------------------------------------------------------------

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
  result?: { agent_status?: string; agents?: { agent_session?: unknown; pane_id?: string }[] };
}

function parseHerdrJson(stdout: string): HerdrEnvelope | null {
  try {
    return JSON.parse(stdout) as HerdrEnvelope;
  } catch {
    return null;
  }
}

/**
 * Classify a settled `herdr agent wait` result into blocked/finished/timed_out.
 * A nonzero exit that isn't a recognized settle is treated as timed_out --
 * concurrency-limits decision: never silently retried, always flagged.
 */
export function classifyWaitResult(exitCode: number, stdout: string): PollEventKind {
  if (exitCode !== 0) return "timed_out";
  const parsed = parseHerdrJson(stdout);
  const status = parsed?.result?.agent_status;
  if (status === "blocked") return "blocked";
  if (status === "idle" || status === "done") return "finished";
  return "timed_out";
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
// Multi-event collection -- the selection logic is pure and testable
// independent of real async timing. swarm_poll's execute() attaches this to
// a live Promise.allSettled snapshot; tests exercise it directly against a
// fabricated snapshot.
// ---------------------------------------------------------------------------

export interface SettledWait {
  agent: string;
  slug: string;
  paneId: string;
  exitCode: number;
  stdout: string;
}

/** Every already-settled result at the moment the race's winner resolved -- never just the winner alone (a same-tick second settlement must not be discarded). */
export function collectSettledEvents(settled: readonly SettledWait[]): PollEvent[] {
  return settled.map((s) => ({
    kind: classifyWaitResult(s.exitCode, s.stdout),
    agent: s.agent,
    slug: s.slug,
    paneId: s.paneId,
  }));
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
        result?: { agents?: { agent_session?: { source?: string; value?: string } }[] };
      };
      // `herdr agent list` doesn't expose the caller-chosen name directly in
      // the shape captured during design -- reconciliation below matches on
      // whatever identifying value is present; adjust if the live schema
      // (verified once, not for every agent kind) differs.
      return (parsed.result?.agents ?? [])
        .map((a) => a.agent_session?.value)
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
          const agentId = nextAgentId(typed.runId, state.nextCounter);
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
      "Wait for at least one active swarm worker to settle (blocked/finished/timed_out), returning every event that settled in the same window.",
    promptSnippet: "Wait for swarm workers to settle and report events",
    promptGuidelines: [
      "Blocks until >=1 active worker settles. Returns an array -- process every event in it, relaying each blocked event to the user one at a time, before calling swarm_poll again.",
      "A blocked event's raw_prompt is verbatim herdr output -- never assume it's a diff or a yes/no. Never send a blocked worker another agent prompt except the actual answer via swarm_resolve_blocked -- any prompt is interpreted as the gate's answer.",
      "finished/timed_out events already closed their pane and freed their slot; if the READY queue still has items and the cap has headroom, call swarm_spawn again for the next batch.",
    ],
    parameters: Type.Object({
      runId: Type.String(),
      timeoutMs: Type.Optional(
        Type.Number({
          description: `Per-worker wait timeout. Default ${DEFAULT_WAIT_TIMEOUT_MS}.`,
        }),
      ),
    }),
    async execute(_toolCallId, params, signal) {
      const typed = params as { runId: string; timeoutMs?: number };
      const state = await getOrInitState(typed.runId, DEFAULT_CONCURRENCY);
      const timeoutMs = typed.timeoutMs ?? DEFAULT_WAIT_TIMEOUT_MS;

      const active = state.workers.filter((w) => w.lifecycle === "active");
      if (active.length === 0) {
        return {
          content: [{ type: "text", text: "No active workers to poll." }],
          details: { events: [] },
        };
      }

      const controllers = active.map(() => new AbortController());
      const settled: SettledWait[] = [];
      let winnerIndex = -1;

      const waits = active.map((w, i) =>
        herdr(
          pi,
          buildAgentWaitArgv(w.agent, ["idle", "done", "blocked"], timeoutMs),
          controllers[i]!.signal,
        ).then((result) => {
          settled.push({
            agent: w.agent,
            slug: w.slug,
            paneId: w.paneId,
            exitCode: result.code,
            stdout: result.stdout,
          });
          if (winnerIndex === -1) winnerIndex = i;
          return result;
        }),
      );

      await Promise.race(waits);
      // Give same-tick settlements a chance to land before snapshotting --
      // Promise.race resolves as soon as the first `.then` above runs, but
      // other already-fulfilled promises' `.then` callbacks may not have
      // flushed yet within the same microtask turn.
      await Promise.resolve();

      const winners = settled.slice(); // snapshot of everything settled so far
      const settledAgents = new Set(winners.map((s) => s.agent));
      for (const [i, w] of active.entries()) {
        if (!settledAgents.has(w.agent)) controllers[i]!.abort();
      }

      const events = collectSettledEvents(winners);

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
                  : `${e.slug} (${e.agent}) ${e.kind}`,
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
        verify.code === 0 &&
        classifyWaitResult(verify.code, verify.stdout) === "blocked" &&
        parseHerdrJson(verify.stdout)?.result?.agent_status === "blocked";

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
