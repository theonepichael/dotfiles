import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import registerSwarmTools, {
  activeWorkerCount,
  buildAgentGetArgv,
  buildAgentListArgv,
  buildAgentPromptArgv,
  buildAgentReadArgv,
  buildAgentSendKeysArgv,
  buildAgentStartArgv,
  buildAgentWaitArgv,
  buildPaneCloseArgv,
  buildPaneRenameArgv,
  buildPaneSplitArgv,
  canOpenNewPane,
  canSpawnNew,
  classifyWaitResult,
  loadState,
  looksTruncated,
  matchOption,
  navigationKeys,
  nextAgentId,
  openPaneCount,
  openPaneSoftCap,
  paneIdentityMismatch,
  parsePicker,
  reconcileState,
  saveState,
  spawnBudget,
  statePath,
  waitResultDetail,
  WORKER_TRUST_COMMAND,
  type SwarmState,
  type WorkerRecord,
} from "../extensions/swarm-tool";

// Captured verbatim from a real herdr agent read against a live blocked
// question-tool.ts picker (see the plan's manual smoke run, section 5 step
// 4) -- not synthesized, so parsePicker/matchOption are tested against the
// real rendering, ANSI stripped by --source recent-unwrapped.
const REAL_PICKER_OUTPUT = `
 question Reply OK to continue?


 ⠧ Working...

─────────────────────────────────────────────────────────────────────────────────────
 Smoke test
 Reply OK to continue?

> 1. OK
     Continue with whatever comes next in the session.
  2. Cancel
     Stop here without doing anything further.
  3. Something else (type it)
     Answer in your own words instead.

 ↑↓ navigate • Enter to select • Esc to cancel
─────────────────────────────────────────────────────────────────────────────────────
~/dotfiles-meta-pi-swarm-orchestration (meta-pi-swarm-orchestration)
↑16k ↓65 $0.001 1.6%/1.0M (auto)                                 glm-5.3-flash • high`;

function makeWorker(overrides: Partial<WorkerRecord> = {}): WorkerRecord {
  return {
    agent: "run1-w1",
    slug: "iron-lb-example",
    paneId: "w1:pA",
    lifecycle: "active",
    ...overrides,
  };
}

function makeState(overrides: Partial<SwarmState> = {}): SwarmState {
  return { runId: "run1", concurrency: 3, nextCounter: 0, workers: [], ...overrides };
}

describe("nextAgentId", () => {
  test("is short, namespaced by run, never exceeds 32 chars", () => {
    expect(nextAgentId("run1", 1)).toBe("run1-w1");
    expect(nextAgentId("run1", 12)).toBe("run1-w12");
    expect(nextAgentId("run1", 1, "fix-bug")).toBe("run1-w1-fix-bug");
    expect(nextAgentId("run1", 1, "a-very-long-backlog-item-slug-name-that-exceeds-limits")).toBe(
      "run1-w1-a-very-long-backlog-item",
    );
    expect(
      nextAgentId("run1", 1, "a-very-long-backlog-item-slug-name-that-exceeds-limits").length,
    ).toBeLessThanOrEqual(32);
  });
});

describe("herdr argv builders", () => {
  test("pane rename: pane id and label", () => {
    expect(buildPaneRenameArgv("w1:pA", "my-slug")).toEqual(["pane", "rename", "w1:pA", "my-slug"]);
  });

  test("pane split: current pane, direction, cwd, no-focus", () => {
    expect(buildPaneSplitArgv("right", "/repo")).toEqual([
      "pane",
      "split",
      "--current",
      "--direction",
      "right",
      "--cwd",
      "/repo",
      "--no-focus",
    ]);
  });

  test("agent start: kind pi, targets the given pane", () => {
    const argv = buildAgentStartArgv("run1-w1", "w1:pB");
    expect(argv).toEqual([
      "agent",
      "start",
      "run1-w1",
      "--kind",
      "pi",
      "--pane",
      "w1:pB",
      "--timeout",
      "30000",
    ]);
  });

  test("agent prompt: prompt is a discrete argv element, never shell-interpolated", () => {
    const nasty = `answer with a "quote" and $(rm -rf /) and 'apostrophes'`;
    const argv = buildAgentPromptArgv("run1-w1", nasty);
    expect(argv).toEqual(["agent", "prompt", "run1-w1", nasty]);
    // one element, not split/concatenated into a shell-executable string
    expect(argv.filter((a) => a.includes("rm -rf"))).toHaveLength(1);
  });

  test("agent wait: --until repeats per state (herdr rejects a comma-joined list), timeout in ms", () => {
    expect(buildAgentWaitArgv("run1-w1", ["idle", "done", "blocked"], 5000)).toEqual([
      "agent",
      "wait",
      "run1-w1",
      "--until",
      "idle",
      "--until",
      "done",
      "--until",
      "blocked",
      "--timeout",
      "5000",
    ]);
  });

  test("agent get / agent read / pane close / agent list", () => {
    expect(buildAgentGetArgv("run1-w1")).toEqual(["agent", "get", "run1-w1"]);
    expect(buildAgentReadArgv("run1-w1", 500)).toEqual([
      "agent",
      "read",
      "run1-w1",
      "--source",
      "recent-unwrapped",
      "--lines",
      "500",
    ]);
    expect(buildPaneCloseArgv("w1:pB")).toEqual(["pane", "close", "w1:pB"]);
    expect(buildAgentListArgv()).toEqual(["agent", "list"]);
  });

  test("agent send-keys: keys are discrete argv elements, in order", () => {
    expect(buildAgentSendKeysArgv("run1-w1", ["down", "down", "enter"])).toEqual([
      "agent",
      "send-keys",
      "run1-w1",
      "down",
      "down",
      "enter",
    ]);
  });
});

describe("parsePicker", () => {
  test("parses the real captured rendering: 3 options, selection on 1", () => {
    const parsed = parsePicker(REAL_PICKER_OUTPUT);
    expect(parsed.selectedIndex).toBe(1);
    expect(parsed.options).toEqual([
      { index: 1, label: "OK" },
      { index: 2, label: "Cancel" },
      { index: 3, label: "Something else (type it)" },
    ]);
  });

  test("no selection marker present -> selectedIndex is null", () => {
    const noMarker = REAL_PICKER_OUTPUT.replace("> 1. OK", "  1. OK");
    expect(parsePicker(noMarker).selectedIndex).toBeNull();
  });

  test("content with no option lines at all parses to an empty list", () => {
    expect(parsePicker("just some ordinary text\nno options here")).toEqual({
      selectedIndex: null,
      options: [],
    });
  });
});

describe("matchOption", () => {
  const options = parsePicker(REAL_PICKER_OUTPUT).options;

  test("exact case-insensitive match", () => {
    expect(matchOption("ok", options)).toEqual({ index: 1, label: "OK" });
    expect(matchOption("Cancel", options)).toEqual({ index: 2, label: "Cancel" });
  });

  test("substring match against a longer label", () => {
    const longer = [{ index: 1, label: "Yes, commit (Recommended)" }, ...options.slice(1)];
    expect(matchOption("yes", longer)).toEqual({ index: 1, label: "Yes, commit (Recommended)" });
  });

  test("never auto-selects the free-text escape option, even if it matches", () => {
    expect(matchOption("something else", options)).toBeNull();
    expect(matchOption("type it", options)).toBeNull();
  });

  test("no match and ambiguous match both return null -- never guess", () => {
    expect(matchOption("nothing like these options", options)).toBeNull();
    const ambiguous = [
      { index: 1, label: "Approve now" },
      { index: 2, label: "Approve later" },
    ];
    expect(matchOption("approve", ambiguous)).toBeNull();
  });

  test("blank answer never matches", () => {
    expect(matchOption("   ", options)).toBeNull();
  });
});

describe("navigationKeys", () => {
  test("moving down N steps then submitting", () => {
    expect(navigationKeys(1, 3)).toEqual(["down", "down", "enter"]);
  });

  test("moving up steps then submitting", () => {
    expect(navigationKeys(3, 1)).toEqual(["up", "up", "enter"]);
  });

  test("already on the target -- just submit, no navigation keys", () => {
    expect(navigationKeys(2, 2)).toEqual(["enter"]);
  });
});

describe("classifyWaitResult", () => {
  // `agent get`/`agent wait` nest the payload one level under "agent" --
  // confirmed live, repeatedly (e.g. `herdr agent get <id>` ->
  // {"result":{"agent":{"agent_status":"blocked",...}}}). An earlier version
  // of both the code and these fixtures used a flat {result:{agent_status}}
  // shape that matched the code's wrong assumption instead of the real
  // captured format, so the tests never caught the bug -- these fixtures
  // are deliberately shaped to match the real wire format, not the
  // implementation, to guard against that happening again.
  function waitStdout(agent_status: string): string {
    return JSON.stringify({ result: { agent: { agent_status } } });
  }

  test("blocked status maps to blocked", () => {
    expect(classifyWaitResult(0, waitStdout("blocked"), "")).toBe("blocked");
  });

  test("idle or done status maps to finished", () => {
    expect(classifyWaitResult(0, waitStdout("idle"), "")).toBe("finished");
    expect(classifyWaitResult(0, waitStdout("done"), "")).toBe("finished");
  });

  test("the old flat (unnested) shape is NOT recognized -- regression guard for the live-discovered field-path bug", () => {
    const flatShape = JSON.stringify({ result: { agent_status: "blocked" } });
    expect(classifyWaitResult(0, flatShape, "")).toBe("error");
  });

  test("a genuine herdr timeout error on stderr maps to timed_out", () => {
    // real captured shape: exit 1, {"error":{"code":"timeout",...}} on stderr
    const stderr = JSON.stringify({
      error: { code: "timeout", message: "timed out waiting for agent status" },
      id: "cli:agent:wait",
    });
    expect(classifyWaitResult(1, "", stderr)).toBe("timed_out");
  });

  test("any other nonzero exit is 'error', not silently folded into timed_out", () => {
    // this is the live-discovered bug under direct test: agent_not_found,
    // a killed process, or any error other than a genuine timeout must be
    // distinguishable from a real 30-minute deadline elapsing.
    const agentNotFound = JSON.stringify({
      error: { code: "agent_not_found", message: "agent target w1 not found" },
    });
    expect(classifyWaitResult(1, "", agentNotFound)).toBe("error");
    expect(classifyWaitResult(1, "", "")).toBe("error"); // unparseable/empty stderr, still not a timeout
    expect(classifyWaitResult(2, "", "some CLI usage error")).toBe("error");
  });

  test("unparseable or unrecognized status on a zero exit is 'error', not a crash and not timed_out", () => {
    // this is exactly the shape a killed-but-resolved exec call produces
    // (code coerced to 0, empty/partial stdout) -- must not look like a
    // real settle or a real timeout.
    expect(classifyWaitResult(0, "not json", "")).toBe("error");
    expect(classifyWaitResult(0, waitStdout("unknown"), "")).toBe("error");
    expect(classifyWaitResult(0, "", "")).toBe("error");
  });
});

describe("paneIdentityMismatch", () => {
  // Same real `agent get` envelope shape as classifyWaitResult's fixtures
  // (result.agent.*, not flat) -- `agent get`/`agent wait` share the
  // nesting, `agent list` does not (see the HerdrEnvelope comment).
  function getStdout(pane_id: string): string {
    return JSON.stringify({ result: { agent: { agent_status: "blocked", pane_id } } });
  }

  test("matching pane_id is not a mismatch", () => {
    expect(paneIdentityMismatch(0, getStdout("w1:pA"), "w1:pA")).toBe(false);
  });

  test("a different pane_id IS a mismatch -- the exact failure mode this guards against", () => {
    expect(paneIdentityMismatch(0, getStdout("w1:pB"), "w1:pA")).toBe(true);
  });

  test("a nonzero exit code (agent_not_found, a crash) is a mismatch -- fail closed, not open", () => {
    expect(paneIdentityMismatch(1, "", "w1:pA")).toBe(true);
  });

  test("unparseable or missing pane_id on a zero exit is a mismatch", () => {
    expect(paneIdentityMismatch(0, "not json", "w1:pA")).toBe(true);
    expect(paneIdentityMismatch(0, JSON.stringify({ result: { agent: {} } }), "w1:pA")).toBe(true);
  });
});

describe("waitResultDetail", () => {
  test("prefers the structured herdr error message when present", () => {
    const stderr = JSON.stringify({
      error: { code: "agent_not_found", message: "agent target w1 not found" },
    });
    expect(waitResultDetail("", stderr)).toBe("agent_not_found: agent target w1 not found");
  });

  test("falls back to raw stdout, then stderr, then a placeholder", () => {
    expect(waitResultDetail("some raw output", "")).toBe("some raw output");
    expect(waitResultDetail("", "raw stderr text")).toBe("raw stderr text");
    expect(waitResultDetail("", "")).toBe("(no output)");
  });
});

describe("looksTruncated", () => {
  test("content filling the exact requested line budget is flagged", () => {
    const content = Array.from({ length: 500 }, (_, i) => `line ${i}`).join("\n");
    expect(looksTruncated(content, 500)).toBe(true);
  });

  test("content well under the budget is not flagged", () => {
    expect(looksTruncated("a\nb\nc", 500)).toBe(false);
  });
});

describe("concurrency and pane accounting", () => {
  test("activeWorkerCount counts only active, not awaiting_relay", () => {
    const state = makeState({
      workers: [
        makeWorker({ agent: "w1", lifecycle: "active" }),
        makeWorker({ agent: "w2", lifecycle: "awaiting_relay" }),
        makeWorker({ agent: "w3", lifecycle: "active" }),
      ],
    });
    expect(activeWorkerCount(state)).toBe(2);
  });

  test("canSpawnNew respects the concurrency cap, ignoring awaiting_relay workers", () => {
    const state = makeState({
      concurrency: 2,
      workers: [
        makeWorker({ agent: "w1", lifecycle: "active" }),
        makeWorker({ agent: "w2", lifecycle: "awaiting_relay" }),
        makeWorker({ agent: "w3", lifecycle: "awaiting_relay" }),
      ],
    });
    // only 1 active against a cap of 2 -- room for one more, even though 3
    // panes total are open (a blocked worker never blocks a new spawn)
    expect(canSpawnNew(state)).toBe(true);
  });

  test("canSpawnNew is false once active count reaches the cap, even mid-overshoot", () => {
    // simulates the round-3 fix: three blocked -> three replacements -> all
    // three resolved back-to-back can transiently push active count above
    // cap. canSpawnNew must say no to a FOURTH spawn in that state, not
    // silently allow unbounded growth.
    const state = makeState({
      concurrency: 3,
      workers: [
        makeWorker({ agent: "w1", lifecycle: "active" }),
        makeWorker({ agent: "w2", lifecycle: "active" }),
        makeWorker({ agent: "w3", lifecycle: "active" }),
        makeWorker({ agent: "w4", lifecycle: "active" }),
      ],
    });
    expect(canSpawnNew(state)).toBe(false);
  });

  test("openPaneSoftCap is 2x concurrency", () => {
    expect(openPaneSoftCap(3)).toBe(6);
    expect(openPaneSoftCap(5)).toBe(10);
  });

  test("openPaneCount counts active + awaiting_relay together", () => {
    const state = makeState({
      workers: [
        makeWorker({ agent: "w1", lifecycle: "active" }),
        makeWorker({ agent: "w2", lifecycle: "awaiting_relay" }),
      ],
    });
    expect(openPaneCount(state)).toBe(2);
  });

  test("canOpenNewPane is false once the soft cap is hit", () => {
    const state = makeState({
      concurrency: 2,
      workers: [
        makeWorker({ agent: "w1", lifecycle: "awaiting_relay" }),
        makeWorker({ agent: "w2", lifecycle: "awaiting_relay" }),
        makeWorker({ agent: "w3", lifecycle: "awaiting_relay" }),
        makeWorker({ agent: "w4", lifecycle: "awaiting_relay" }),
      ],
    });
    expect(canOpenNewPane(state)).toBe(false); // 4 open panes == 2x2 soft cap
  });
});

describe("spawnBudget", () => {
  test("bounded by concurrency, pane soft cap, and the ready queue size, whichever is smallest", () => {
    expect(spawnBudget(makeState({ concurrency: 3, workers: [] }), 10)).toBe(3);
    expect(spawnBudget(makeState({ concurrency: 3, workers: [] }), 1)).toBe(1);
    const nearPaneCap = makeState({
      concurrency: 3,
      workers: Array.from({ length: 5 }, (_, i) =>
        makeWorker({ agent: `w${i}`, lifecycle: "awaiting_relay" }),
      ),
    });
    // pane soft cap is 6, 5 already open -> only 1 more pane fits, even
    // though the concurrency cap alone would allow 3
    expect(spawnBudget(nearPaneCap, 10)).toBe(1);
  });

  test("never negative even when already over cap", () => {
    const over = makeState({
      concurrency: 2,
      workers: Array.from({ length: 5 }, (_, i) =>
        makeWorker({ agent: `w${i}`, lifecycle: "active" }),
      ),
    });
    expect(spawnBudget(over, 10)).toBe(0);
  });
});

describe("reconcileState", () => {
  test("a tracked worker still live in herdr is kept", () => {
    const state = makeState({ workers: [makeWorker({ agent: "w1" })] });
    const { state: reconciled, dropped } = reconcileState(state, ["w1"]);
    expect(reconciled.workers).toHaveLength(1);
    expect(dropped).toHaveLength(0);
  });

  test("a tracked worker no longer in herdr's live list is dropped and reported", () => {
    const state = makeState({
      workers: [makeWorker({ agent: "w1" }), makeWorker({ agent: "w2" })],
    });
    const { state: reconciled, dropped } = reconcileState(state, ["w1"]); // w2 is dead
    expect(reconciled.workers.map((w) => w.agent)).toEqual(["w1"]);
    expect(dropped.map((w) => w.agent)).toEqual(["w2"]);
  });

  test("an empty live list drops every tracked worker", () => {
    const state = makeState({
      workers: [makeWorker({ agent: "w1" }), makeWorker({ agent: "w2" })],
    });
    const { state: reconciled, dropped } = reconcileState(state, []);
    expect(reconciled.workers).toHaveLength(0);
    expect(dropped).toHaveLength(2);
  });
});

describe("state persistence", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "swarm-tool-test-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("loadState on a never-saved run returns null", () => {
    expect(loadState("nope", dir)).toBeNull();
  });

  test("saveState then loadState round-trips", () => {
    const state = makeState({ workers: [makeWorker()] });
    saveState(state, dir);
    expect(loadState("run1", dir)).toEqual(state);
  });

  test("statePath is namespaced by runId under the given dir", () => {
    expect(statePath("run1", dir)).toBe(join(dir, "swarm-run1.json"));
  });

  test("corrupt state file is treated as absent, not thrown", () => {
    const path = statePath("bad", dir);
    mkdirSync(dir, { recursive: true });
    writeFileSync(path, "{not json");
    expect(loadState("bad", dir)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// swarm_poll's execute() wiring.
//
// classifyWaitResult is unit-tested above, but the live failure that
// motivated these tests was never a classification bug in isolation -- it
// was what execute() *does* with the classification. A settle payload that
// classifies as anything other than "blocked" takes the else branch: pane
// closed, worker dropped from state, `herdr agent get` thereafter returning
// agent_not_found. So a worker sitting at a real approval gate got its pane
// destroyed and became unrecoverable, with no relay target left.
//
// These drive the registered tool's real execute() against a stubbed
// ExtensionAPI, asserting the pane/lifecycle consequences rather than the
// returned label -- the part that had no coverage.
// ---------------------------------------------------------------------------

/**
 * A real `herdr agent wait` settle envelope, captured from herdr 0.8.2 on
 * 2026-09-02 (`herdr agent wait swarmctl2 --until idle`): exit 0, payload on
 * stdout, agent fields nested one level under `result.agent`, with
 * `result.type` = "agent_info". Only agent_status/name/pane_id vary here.
 * Shaped to the real wire format on purpose -- an earlier fixture set copied
 * the code's wrong flat assumption instead, so the tests agreed with the bug.
 */
function realWaitEnvelope(agentStatus: string, name: string, paneId: string): string {
  return JSON.stringify({
    id: "cli:agent:wait",
    result: {
      agent: {
        agent: "pi",
        agent_status: agentStatus,
        cwd: "/home/yanil/dotfiles",
        focused: false,
        interactive_ready: true,
        name,
        pane_id: paneId,
        revision: 1,
        tab_id: "w1:t1",
        workspace_id: "w1",
      },
      type: "agent_info",
    },
  });
}

interface ExecCall {
  argv: string[];
}

/** Minimal ExtensionAPI stub -- swarm-tool only ever touches exec + registerTool. */
function makeStubPi(respond: (argv: string[]) => { code: number; stdout: string; stderr: string }) {
  const calls: ExecCall[] = [];
  const tools = new Map<string, { execute: (...a: never[]) => Promise<unknown> }>();
  const pi = {
    exec(_cmd: string, argv: string[]) {
      calls.push({ argv });
      return Promise.resolve(respond(argv));
    },
    registerTool(def: { name: string; execute: (...a: never[]) => Promise<unknown> }) {
      tools.set(def.name, def);
    },
  };
  return { pi, calls, tools };
}

describe("swarm_poll execute() wiring", () => {
  let dir: string;
  let priorStateDir: string | undefined;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "swarm-poll-exec-"));
    // Keeps the tool's own `persist` off the real ~/.pi/agent/state.
    priorStateDir = process.env.PI_SWARM_STATE_DIR;
    process.env.PI_SWARM_STATE_DIR = dir;
  });

  afterEach(() => {
    if (priorStateDir === undefined) delete process.env.PI_SWARM_STATE_DIR;
    else process.env.PI_SWARM_STATE_DIR = priorStateDir;
    rmSync(dir, { recursive: true, force: true });
  });

  /** Seeds one active worker on disk and returns the wired-up swarm_poll. */
  function setup(waitStdout: string, waitCode = 0, waitStderr = "") {
    const runId = "execrun";
    const worker: WorkerRecord = {
      agent: "execrun-w1",
      slug: "some-item",
      paneId: "w1:pZ",
      lifecycle: "active",
    };
    const state: SwarmState = { runId, concurrency: 2, nextCounter: 1, workers: [worker] };
    saveState(state, dir);

    const stub = makeStubPi((argv) => {
      const [a, b] = argv;
      if (a === "agent" && b === "list") {
        return {
          code: 0,
          stdout: JSON.stringify({ result: { agents: [{ name: "execrun-w1" }] } }),
          stderr: "",
        };
      }
      if (a === "agent" && b === "wait") {
        return { code: waitCode, stdout: waitStdout, stderr: waitStderr };
      }
      if (a === "agent" && b === "get") {
        return { code: 0, stdout: realWaitEnvelope("blocked", "execrun-w1", "w1:pZ"), stderr: "" };
      }
      if (a === "agent" && b === "read") {
        return { code: 0, stdout: "Commit these changes?\n> Yes\n  No\n", stderr: "" };
      }
      return { code: 0, stdout: "", stderr: "" };
    });

    registerSwarmTools(stub.pi as unknown as Parameters<typeof registerSwarmTools>[0]);
    const poll = stub.tools.get("swarm_poll");
    if (!poll) throw new Error("swarm_poll was never registered");
    return { runId, poll, stub };
  }

  const paneCloses = (stub: { calls: ExecCall[] }) =>
    stub.calls.filter((c) => c.argv[0] === "pane" && c.argv[1] === "close");

  test("a blocked settle keeps the pane open and parks the worker at awaiting_relay", async () => {
    const { runId, poll, stub } = setup(realWaitEnvelope("blocked", "execrun-w1", "w1:pZ"));

    const res = (await poll.execute(
      ...(["call-1", { runId, timeoutMs: 1000 }, undefined] as unknown as never[]),
    )) as { details: { events: { kind: string }[] } };

    expect(res.details.events.map((e) => e.kind)).toEqual(["blocked"]);
    // The load-bearing assertions: the relay target must survive.
    expect(paneCloses(stub)).toHaveLength(0);
    const persisted = loadState(runId, dir);
    expect(persisted?.workers).toHaveLength(1);
    expect(persisted?.workers[0]?.lifecycle).toBe("awaiting_relay");
  });

  test("the pre-fix flat payload shape closes the pane and drops the worker -- the live bug, pinned", async () => {
    // Exactly what a blocked worker's settle looked like to the old parser:
    // real status present, but at result.agent_status instead of
    // result.agent.agent_status, so it fell through to "error".
    const flat = JSON.stringify({ result: { agent_status: "blocked" } });
    const { runId, poll, stub } = setup(flat);

    const res = (await poll.execute(
      ...(["call-1", { runId, timeoutMs: 1000 }, undefined] as unknown as never[]),
    )) as { details: { events: { kind: string }[] } };

    expect(res.details.events.map((e) => e.kind)).toEqual(["error"]);
    expect(paneCloses(stub)).toHaveLength(1);
    expect(loadState(runId, dir)?.workers).toHaveLength(0);
  });

  test("a genuine herdr timeout closes the pane and drops the worker", async () => {
    const stderr = JSON.stringify({
      error: { code: "timeout", message: "timed out waiting for agent status" },
      id: "cli:agent:wait",
    });
    const { runId, poll, stub } = setup("", 1, stderr);

    const res = (await poll.execute(
      ...(["call-1", { runId, timeoutMs: 1000 }, undefined] as unknown as never[]),
    )) as { details: { events: { kind: string }[] } };

    expect(res.details.events.map((e) => e.kind)).toEqual(["timed_out"]);
    expect(paneCloses(stub)).toHaveLength(1);
    expect(loadState(runId, dir)?.workers).toHaveLength(0);
  });

  test("an idle settle finishes the worker and frees its pane", async () => {
    const { runId, poll, stub } = setup(realWaitEnvelope("idle", "execrun-w1", "w1:pZ"));

    const res = (await poll.execute(
      ...(["call-1", { runId, timeoutMs: 1000 }, undefined] as unknown as never[]),
    )) as { details: { events: { kind: string }[] } };

    expect(res.details.events.map((e) => e.kind)).toEqual(["finished"]);
    expect(paneCloses(stub)).toHaveLength(1);
    expect(loadState(runId, dir)?.workers).toHaveLength(0);
  });

  // The second original bug shape, at the wiring level. pi.exec's underlying
  // execCommand always RESOLVES -- even for a process killed by signal --
  // and coerces the null exit code to 0, so an aborted `agent wait` arrives
  // as exit 0 with empty or partial stdout. That must stay distinguishable
  // from a real settle: it closes the pane and drops the worker, and it is
  // reported as "error" rather than a timeout that never elapsed.
  test.each([
    ["empty stdout, as a signal-killed exec resolves", ""],
    ["partial/unparseable stdout", '{"result":{"agent":{"agent_st'],
    ["valid JSON with no recognized status", JSON.stringify({ result: { agent: {} } })],
  ])("exit-0 with %s closes the pane and drops the worker", async (_label, stdout) => {
    const { runId, poll, stub } = setup(stdout);

    const res = (await poll.execute(
      ...(["call-1", { runId, timeoutMs: 1000 }, undefined] as unknown as never[]),
    )) as { details: { events: { kind: string; detail?: string }[] } };

    expect(res.details.events.map((e) => e.kind)).toEqual(["error"]);
    expect(paneCloses(stub)).toHaveLength(1);
    expect(loadState(runId, dir)?.workers).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// swarm_spawn's worker bootstrap.
//
// Pi ships no permission system of its own; permission-gate.ts supplies one,
// and it defaults to enabled with a "*": "ask" fallback. A worker runs in a
// herdr TUI pane, so ctx.hasUI is true and the gate calls ctx.ui.confirm --
// a human-shaped prompt in a pane nobody is watching. Every bash command
// outside ALLOW_PATTERNS therefore stalls the worker indefinitely, and
// swarm_poll cannot see it: the agent still reports "working", so the
// orchestrator relays nothing and waits out its full timeout. Observed live
// on a 3-worker run -- the only worker doing real tool work had to be
// unblocked by hand, repeatedly, which defeats the point of unattended mode.
//
// So spawn must opt each worker out of the gate before handing it any work.
// ---------------------------------------------------------------------------

describe("swarm_spawn worker bootstrap", () => {
  let dir: string;
  let priorStateDir: string | undefined;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "swarm-spawn-boot-"));
    priorStateDir = process.env.PI_SWARM_STATE_DIR;
    process.env.PI_SWARM_STATE_DIR = dir;
  });

  afterEach(() => {
    if (priorStateDir === undefined) delete process.env.PI_SWARM_STATE_DIR;
    else process.env.PI_SWARM_STATE_DIR = priorStateDir;
    rmSync(dir, { recursive: true, force: true });
  });

  function stubFor(respond: (argv: string[]) => { code: number; stdout: string; stderr: string }) {
    const stub = makeStubPi(respond);
    registerSwarmTools(stub.pi as unknown as Parameters<typeof registerSwarmTools>[0]);
    const spawn = stub.tools.get("swarm_spawn");
    if (!spawn) throw new Error("swarm_spawn was never registered");
    return { spawn, stub };
  }

  const paneSplitOk = (argv: string[]) =>
    argv[0] === "pane" && argv[1] === "split"
      ? {
          code: 0,
          stdout: JSON.stringify({ result: { pane: { pane_id: "w1:pN" } } }),
          stderr: "",
        }
      : { code: 0, stdout: "", stderr: "" };

  const prompts = (stub: { calls: ExecCall[] }) =>
    stub.calls.filter((c) => c.argv[0] === "agent" && c.argv[1] === "prompt");

  const spawnOne = (spawn: { execute: (...a: never[]) => Promise<unknown> }) =>
    spawn.execute(
      ...([
        "call-1",
        { runId: "bootrun", items: ["some-item"], concurrency: 1 },
      ] as unknown as never[]),
    );

  test("turns the bash permission gate off before handing the worker its item", async () => {
    const { spawn, stub } = stubFor(paneSplitOk);
    await spawnOne(spawn);

    const sent = prompts(stub).map((c) => c.argv[3]);
    // Order is load-bearing: the gate must be down before any work starts,
    // or the worker stalls on its first non-allowlisted bash call.
    expect(sent).toEqual([WORKER_TRUST_COMMAND, "/backlog-item --auto some-item"]);
    expect(sent[0]).toBe("/permission-gate-disable");
  });

  test("the trust prompt waits for the worker to settle before work is sent", async () => {
    const { spawn, stub } = stubFor(paneSplitOk);
    await spawnOne(spawn);

    // Without --wait the two prompts race: pi may still be processing the
    // slash command when the backlog prompt lands.
    const trust = prompts(stub).find((c) => c.argv[3] === WORKER_TRUST_COMMAND);
    expect(trust?.argv).toContain("--wait");
  });

  test("a failed trust prompt fails the spawn instead of yielding a worker that will stall", async () => {
    const { spawn, stub } = stubFor((argv) =>
      argv[0] === "agent" && argv[1] === "prompt" && argv[3] === WORKER_TRUST_COMMAND
        ? { code: 1, stdout: "", stderr: "no such command" }
        : paneSplitOk(argv),
    );

    const res = (await spawnOne(spawn)) as {
      details: { spawned: unknown[]; failed: { slug: string; reason: string }[] };
    };

    expect(res.details.spawned).toHaveLength(0);
    expect(res.details.failed).toHaveLength(1);
    expect(res.details.failed[0]?.reason).toContain("permission_gate");
    // and it never went on to hand the worker real work
    expect(prompts(stub).map((c) => c.argv[3])).toEqual([WORKER_TRUST_COMMAND]);
  });
});
