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
  buildTabCloseArgv,
  buildTabCreateArgv,
  buildWorkerCloseArgv,
  canOpenNewPane,
  canSpawnNew,
  classifyWaitResult,
  parseTabCreate,
  loadState,
  looksTruncated,
  matchOption,
  navigationKeys,
  nextAgentId,
  openPaneCount,
  openPaneSoftCap,
  paneIdentityMismatch,
  PANE_CAPTURE_CHARS,
  parseAgentListIds,
  parsePicker,
  reconcileState,
  saveState,
  spawnBudget,
  statePath,
  waitResultDetail,
  WORKER_UNATTENDED_ENV,
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
    tabId: "w1:tA",
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
  test("tab create: cwd, slug label, unattended env, and never steals the human's focus", () => {
    expect(buildTabCreateArgv("/repo", "my-slug")).toEqual([
      "tab",
      "create",
      "--cwd",
      "/repo",
      "--label",
      "my-slug",
      "--env",
      "PI_AGENT_UNATTENDED=1",
      "--no-focus",
    ]);
  });

  test("tab close: the tab id", () => {
    expect(buildTabCloseArgv("w1:tN")).toEqual(["tab", "close", "w1:tN"]);
  });

  test("tearing a worker down closes the tab it owns", () => {
    expect(buildWorkerCloseArgv(makeWorker({ paneId: "w1:pA", tabId: "w1:tA" }))).toEqual([
      "tab",
      "close",
      "w1:tA",
    ]);
  });

  test("a worker restored from a pre-tabs state file is still closed by its pane", () => {
    // Workers used to live in panes split out of the orchestrator's own, so a
    // state file written by that version carries no tabId. Closing its tab is
    // not an option and skipping the close would leak a live pi into a pane
    // nothing polls, so the pane close stays reachable for exactly this case.
    const legacy = makeWorker({ paneId: "w1:pA" });
    delete legacy.tabId;
    expect(buildWorkerCloseArgv(legacy)).toEqual(["pane", "close", "w1:pA"]);
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

// Scrollback above a live picker, holding a numbered list that is not an
// option list. Captured shape from the 2026-09-02 ultrareview: the plan line
// "1. Merge to main and push" parsed as option 1 and navigationKeys computed
// arrow presses from that fabricated index.
const PICKER_BELOW_A_NUMBERED_PLAN = `
 Here is the plan I intend to follow:

 1. Merge to main and push
 2. Remove the worktree
 3. Close the item

─────────────────────────────────────────────────────────────────────────────────────
 Commit
 Commit these changes?

> 1. Commit now (Recommended)
     Commit the current changes right away.
  2. Stop here
     Leave the changes uncommitted and stop.
  3. Something else (type it)
     Answer in your own words instead.

 ↑↓ navigate • Enter to select • Esc to cancel
─────────────────────────────────────────────────────────────────────────────────────
~/dotfiles (main)`;

// Verbatim from a real 42-column worker pane during the 2026-09-02 concurrent
// swarm run. Three workers split a 168-column tab into four equal panes, and
// at that width question-tool's footer wraps -- which the full-width fixtures
// above never showed.
const NARROW_PANE_PICKER = `
 herdr-agent-state.ts exception)?


 \u2387 Working...

───────────────────────────────────────
 Commit
 Commit these changes (docs +
 regression test pinning the
 herdr-agent-state.ts exception)?

> 1. Commit (Recommended)
     Commit the doc + regression test
     in the worktree as docs(pi):
     document herdr-agent-state.ts as
     the managed_dir standing exception
  2. Don't commit yet
     Leave the changes uncommitted in
     the worktree; the item stays in
     progress
  3. Something else (type it)
     Answer in your own words instead.

 \u2191\u2193 navigate \u2022 Enter to select \u2022 Esc to
 cancel
───────────────────────────────────────
~/dotfiles (main)`;

describe("parsePicker anchoring", () => {
  // THE DEFECT. OPTION_LINE matched any numbered line in the 500 lines of
  // scrollback `agent read` returns, with no anchor to the live picker, so a
  // plan list above the picker became the option list.
  test("reads the live picker, not a numbered list in the scrollback above it", () => {
    const parsed = parsePicker(PICKER_BELOW_A_NUMBERED_PLAN);

    expect(parsed.options.map((o) => o.label)).toEqual([
      "Commit now (Recommended)",
      "Stop here",
      "Something else (type it)",
    ]);
    expect(parsed.selectedIndex).toBe(1);
  });

  test("the real single-picker capture still parses unchanged", () => {
    const parsed = parsePicker(REAL_PICKER_OUTPUT);
    expect(parsed.options.map((o) => o.label)).toEqual([
      "OK",
      "Cancel",
      "Something else (type it)",
    ]);
    expect(parsed.selectedIndex).toBe(1);
  });

  // THE REGRESSION. The footer anchor required "navigate ... Esc to cancel" on
  // one line, which only holds in a wide pane. A worker pane in a real 3-way
  // split is 42 columns, the footer wraps after "Esc to", the anchor missed,
  // and parsePicker returned nothing at all -- so every relay to a worker came
  // back needs_manual with "(none parsed)". Caught live on 2026-09-02 the
  // first time the fix from ec5fd5a met a genuinely narrow pane.
  test("finds the picker when a narrow pane wraps the footer line", () => {
    const parsed = parsePicker(NARROW_PANE_PICKER);

    expect(parsed.options.map((o) => o.label)).toEqual([
      "Commit (Recommended)",
      "Don't commit yet",
      "Something else (type it)",
    ]);
    expect(parsed.selectedIndex).toBe(1);
  });

  test("content with no picker at all yields no options", () => {
    const parsed = parsePicker("1. not a picker\n2. still not a picker\n");
    expect(parsed.options).toEqual([]);
    expect(parsed.selectedIndex).toBeNull();
  });

  // question-tool renders `${i + 1}.`, so a real picker's indices are always
  // contiguous from 1. Anything else means the window caught something that
  // is not an option list, and guessing from it is how the wrong option got
  // submitted -- report nothing and let the caller fall back to needs_manual.
  test("non-contiguous indices inside the window are rejected outright", () => {
    const broken = PICKER_BELOW_A_NUMBERED_PLAN.replace("  2. Stop here", "  7. Stop here");
    expect(parsePicker(broken).options).toEqual([]);
  });

  test("a multi-select picker's footer anchors just as well", () => {
    const multi = PICKER_BELOW_A_NUMBERED_PLAN.replace(
      "↑↓ navigate • Enter to select • Esc to cancel",
      "↑↓ navigate • Space to toggle • Enter to confirm • Esc to cancel",
    );
    expect(parsePicker(multi).options).toHaveLength(3);
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

describe("matchOption safety", () => {
  const commitGate = [
    { index: 1, label: "Commit now (Recommended)" },
    { index: 2, label: "Stop here" },
    { index: 3, label: "Something else (type it)" },
  ];

  // THE DEFECT, and the worst one in the relay path. The bare substring
  // fallback made "no" a single unambiguous match for "Commit now
  // (Recommended)" -- the label contains those two letters inside "now", the
  // other label does not -- so the user said no and the worker committed.
  // Reproduced live on 2026-09-02 against a real picker.
  test("a short answer never matches inside a longer word", () => {
    expect(matchOption("no", commitGate)).toBeNull();
    expect(matchOption("No", commitGate)).toBeNull();
  });

  test("an answer that is a whole word in exactly one label still matches", () => {
    expect(matchOption("Stop here", commitGate)?.label).toBe("Stop here");
    expect(matchOption("stop", commitGate)?.label).toBe("Stop here");
    expect(matchOption("commit now", commitGate)?.label).toBe("Commit now (Recommended)");
  });

  // Exact match is checked before any fuzzy rule, so a genuinely short
  // option label stays answerable.
  test("a short answer still matches a label that IS that word", () => {
    const yesNo = [
      { index: 1, label: "Yes" },
      { index: 2, label: "No" },
    ];
    expect(matchOption("no", yesNo)?.label).toBe("No");
    expect(matchOption("YES", yesNo)?.label).toBe("Yes");
  });

  test("an answer matching several labels stays ambiguous", () => {
    const both = [
      { index: 1, label: "Commit and push" },
      { index: 2, label: "Commit only" },
    ];
    expect(matchOption("commit", both)).toBeNull();
  });

  test("the free-text escape is never auto-selected", () => {
    expect(matchOption("Something else (type it)", commitGate)).toBeNull();
    expect(matchOption("type it", commitGate)).toBeNull();
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

  test("a live list mixing a named worker with an unnamed foreign agent keeps only the worker", () => {
    // herdr omits `name` entirely for agents not started with an explicit
    // name (captured live, herdr 0.8.2 -- see realAgentListEnvelope). The
    // parse must extract only the named worker: never dropping it, never
    // adopting the unnamed stranger.
    const state = makeState({ workers: [makeWorker({ agent: "probe-w1" })] });
    const liveIds = parseAgentListIds(
      realAgentListEnvelope(UNNAMED_CLAUDE_ENTRY, {
        agent: "pi",
        agent_status: "idle",
        name: "probe-w1",
        pane_id: "w1:p2N",
      }),
    );
    expect(liveIds).toEqual(["probe-w1"]);
    const { state: reconciled, dropped } = reconcileState(state, liveIds);
    expect(reconciled.workers.map((w) => w.agent)).toEqual(["probe-w1"]);
    expect(dropped).toHaveLength(0);
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

/** One entry of a real `herdr agent list` payload. `name` is omitted entirely (not undefined) exactly when the agent was not started via `herdr agent start <NAME>`. */
interface RealAgentListEntry {
  agent: string;
  agent_status: string;
  name?: string;
  pane_id: string;
}

/**
 * A real `herdr agent list` payload shape, captured from herdr 0.8.2 on
 * 2026-09-02. Entries are verbatim from the capture -- in particular `name`
 * is present only when the agent was started with an explicit name, and a
 * claude session launched without one carries NO name key at all:
 *   {"agent":"claude","agent_status":"working","pane_id":"w1:pX"}               -> no name key
 *   {"agent":"pi","agent_status":"idle","name":"probe-w1","pane_id":"w1:p2N"}   -> name key present
 * Swarm workers are always started named (buildAgentStartArgv passes the
 * synthetic id as NAME), so parseAgentListIds reading `.name` matches our
 * workers and ignores unnamed foreign agents. Shaped to the real wire
 * format on purpose, like realWaitEnvelope above -- an earlier hand-written
 * `{name: ...}` stub pinned the code's own assumption, so the fixture could
 * never have caught a mismatch either way.
 */
function realAgentListEnvelope(...agents: RealAgentListEntry[]): string {
  return JSON.stringify({ result: { agents } });
}

/** The captured unnamed claude-shaped entry, verbatim. */
const UNNAMED_CLAUDE_ENTRY: RealAgentListEntry = {
  agent: "claude",
  agent_status: "working",
  pane_id: "w1:pX",
};

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
      tabId: "w1:tZ",
      lifecycle: "active",
    };
    const state: SwarmState = { runId, concurrency: 2, nextCounter: 1, workers: [worker] };
    saveState(state, dir);

    const stub = makeStubPi((argv) => {
      const [a, b] = argv;
      if (a === "agent" && b === "list") {
        return {
          code: 0,
          // Real wire shape (see realAgentListEnvelope): our named worker
          // alongside an unnamed foreign agent it must ignore.
          stdout: realAgentListEnvelope(UNNAMED_CLAUDE_ENTRY, {
            agent: "pi",
            agent_status: "idle",
            name: "execrun-w1",
            pane_id: "w1:pZ",
          }),
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

  const workerCloses = (stub: { calls: ExecCall[] }) =>
    stub.calls.filter((c) => c.argv[0] === "tab" && c.argv[1] === "close");

  test("a blocked settle keeps the pane open and parks the worker at awaiting_relay", async () => {
    const { runId, poll, stub } = setup(realWaitEnvelope("blocked", "execrun-w1", "w1:pZ"));

    const res = (await poll.execute(
      ...(["call-1", { runId, timeoutMs: 1000 }, undefined] as unknown as never[]),
    )) as { details: { events: { kind: string }[] } };

    expect(res.details.events.map((e) => e.kind)).toEqual(["blocked"]);
    // The load-bearing assertions: the relay target must survive.
    expect(workerCloses(stub)).toHaveLength(0);
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
    expect(workerCloses(stub)).toHaveLength(1);
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
    expect(workerCloses(stub)).toHaveLength(1);
    expect(loadState(runId, dir)?.workers).toHaveLength(0);
  });

  test("an idle settle finishes the worker and frees its pane", async () => {
    const { runId, poll, stub } = setup(realWaitEnvelope("idle", "execrun-w1", "w1:pZ"));

    const res = (await poll.execute(
      ...(["call-1", { runId, timeoutMs: 1000 }, undefined] as unknown as never[]),
    )) as { details: { events: { kind: string }[] } };

    expect(res.details.events.map((e) => e.kind)).toEqual(["finished"]);
    expect(workerCloses(stub)).toHaveLength(1);
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
    expect(workerCloses(stub)).toHaveLength(1);
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

//
// How it confirms the opt-out is the second half of this story, and it was
// wrong. The first version sent the command with `--wait`. herdr 0.8.2
// documents `--wait` from a non-working state as requiring an observed
// lifecycle change within five seconds, and a client-side pi slash command
// applies instantly without ever entering the working state -- so the call
// ALWAYS returned agent_prompt_stalled, and swarm_spawn always discarded a
// worker whose gate had in fact just come down. Observed live twice on
// 2026-09-02 (runId swarm-20260902-wearable3): 0 workers spawned, 3 failed,
// with every discarded worker's pane showing the gate disabled correctly.
// The swarm could not spawn a single worker from ef7c950 until this fix.
//
// The confirmation is now a file the worker itself writes, keyed by a token
// minted per spawn -- not a read of the worker's terminal, which is a
// bounded sliding window of rendered rows (see permission-gate.ts).
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

  const tabCreateOk = (argv: string[]) =>
    argv[0] === "tab" && argv[1] === "create"
      ? {
          code: 0,
          stdout: JSON.stringify({
            result: { root_pane: { pane_id: "w1:pN" }, tab: { tab_id: "w1:tN" } },
          }),
          stderr: "",
        }
      : { code: 0, stdout: "", stderr: "" };

  const prompts = (stub: { calls: ExecCall[] }) =>
    stub.calls.filter((c) => c.argv[0] === "agent" && c.argv[1] === "prompt");

  const workerCloses = (stub: { calls: ExecCall[] }) =>
    stub.calls.filter((c) => c.argv[0] === "tab" && c.argv[1] === "close");

  const PANE_TEXT = "worker log: pi started, gate never came down";

  /** Answers `pane read` with identifiable text, so a test can assert the capture reached the reason. */
  const withPaneText =
    (respond: (argv: string[]) => { code: number; stdout: string; stderr: string }) =>
    (argv: string[]) =>
      argv[0] === "pane" && argv[1] === "read"
        ? { code: 0, stdout: PANE_TEXT, stderr: "" }
        : respond(argv);

  const isItemPrompt = (argv: string[]) =>
    argv[0] === "agent" && argv[1] === "prompt" && (argv[3] ?? "").startsWith("/backlog-item");

  const spawnItems = (spawn: { execute: (...a: never[]) => Promise<unknown> }, items: string[]) =>
    spawn.execute(
      ...(["call-1", { runId: "bootrun", items, concurrency: items.length }] as unknown as never[]),
    ) as Promise<{
      content: { type: string; text: string }[];
      details: { spawned: unknown[]; failed: { slug: string; reason: string }[] };
    }>;

  const spawnOne = (spawn: { execute: (...a: never[]) => Promise<unknown> }) =>
    spawnItems(spawn, ["some-item"]);

  // THE RACE. getOrInitState hands both calls the same cached SwarmState, and
  // state.workers.push only happens after the whole Promise.allSettled block,
  // so two overlapping spawns for one runId each compute spawnBudget against
  // the same empty pool and each spawn up to the full cap. The pane splits
  // interleave for the same reason, which the tool's own promptGuidelines say
  // must never happen ("splits are sequential -- concurrent splits race").
  const spawnRun = (
    spawn: { execute: (...a: never[]) => Promise<unknown> },
    runId: string,
    items: string[],
    concurrency: number,
  ) =>
    spawn.execute(...(["call-1", { runId, items, concurrency }] as unknown as never[])) as Promise<{
      content: { type: string; text: string }[];
      details: { spawned: unknown[]; skipped: string[] };
    }>;

  test("two concurrent spawns for one runId cannot exceed the cap between them", async () => {
    const { spawn } = stubFor(tabCreateOk);

    const [first, second] = await Promise.all([
      spawnRun(spawn, "racerun", ["a1", "a2"], 2),
      spawnRun(spawn, "racerun", ["b1", "b2"], 2),
    ]);

    const spawned = first.details.spawned.length + second.details.spawned.length;
    expect(spawned).toBeLessThanOrEqual(2);
    expect(loadState("racerun", dir)?.workers.length).toBeLessThanOrEqual(2);
  });

  // Serialising must not silently drop the second caller's work: whatever the
  // cap has no room for comes back as skipped, so the orchestrator can see its
  // items were not lost.
  test("the second concurrent spawn reports its items rather than losing them", async () => {
    const { spawn } = stubFor(tabCreateOk);

    const [first, second] = await Promise.all([
      spawnRun(spawn, "accounted", ["a1", "a2"], 2),
      spawnRun(spawn, "accounted", ["b1", "b2"], 2),
    ]);

    // The lock is FIFO, but assert on the pair rather than on which call won:
    // one spawns the cap, the other reports its items as skipped, and nothing
    // silently disappears.
    const shapes = [first, second]
      .map((r) => `${r.details.spawned.length}/${r.details.skipped.length}`)
      .sort();
    expect(shapes).toEqual(["0/2", "2/0"]);
  });

  // The whole run funnels through one chain, so a spawn that throws must
  // still release it -- otherwise one bad call wedges every later spawn for
  // that runId forever.
  test("a spawn that throws does not wedge the run's queue", async () => {
    let explode = true;
    const { spawn } = stubFor((argv) => {
      if (explode && argv[0] === "tab" && argv[1] === "create") {
        throw new Error("herdr fell over mid-create");
      }
      return tabCreateOk(argv);
    });

    await spawnRun(spawn, "wedged", ["a1"], 2).catch(() => undefined);
    explode = false;
    const after = await spawnRun(spawn, "wedged", ["b1"], 2);

    expect(after.details.spawned).toHaveLength(1);
  });

  // The gates settle themselves from the tab's environment, before pi starts.
  // This replaced a prompt-plus-acknowledgement handshake that could only ever
  // cover one of the two gates, and left a window in which a worker held real
  // work while still armed.
  test("the worker's tab carries the unattended environment", async () => {
    const { spawn, stub } = stubFor(tabCreateOk);
    await spawnOne(spawn);

    const create = stub.calls.find((c) => c.argv[0] === "tab" && c.argv[1] === "create");
    expect(create?.argv).toContain("--env");
    expect(create?.argv).toContain(WORKER_UNATTENDED_ENV);
  });

  test("the worker is sent its item and nothing else", async () => {
    const { spawn, stub } = stubFor(tabCreateOk);
    await spawnOne(spawn);

    // Exactly one prompt. A second one would mean something is still being
    // negotiated over the wire that the environment should have settled.
    expect(prompts(stub).map((c) => c.argv[3] ?? "")).toEqual(["/backlog-item --auto some-item"]);
  });

  // THE REGRESSION, kept because the constraint outlives the handshake that
  // exposed it: `--wait` cannot succeed against a prompt that produces no
  // observed lifecycle change, and a spawn must not read that as failure.
  test("a healthy worker is spawned, not discarded on a --wait artefact", async () => {
    const { spawn, stub } = stubFor((argv) =>
      // Real herdr's answer to `agent prompt ... --wait` here: exit 1,
      // agent_prompt_stalled, captured live on 2026-09-02.
      argv.includes("--wait")
        ? {
            code: 1,
            stdout: "",
            stderr: JSON.stringify({
              error: {
                code: "agent_prompt_stalled",
                message: "agent prompt produced no observed state change within 5000 ms",
              },
            }),
          }
        : tabCreateOk(argv),
    );

    const res = await spawnOne(spawn);

    expect(res.details.failed).toEqual([]);
    expect(res.details.spawned).toHaveLength(1);
    // The submit is only meaningful without --wait: with it every submit
    // failed regardless of outcome, so a non-zero exit said nothing.
    expect(prompts(stub).some((c) => c.argv.includes("--wait"))).toBe(false);
  });

  // An orchestrator only ever reads `content`. Confirmed live on 2026-09-02:
  // a real swarm_spawn failure returned its detail in `details`, and the
  // model's very next turn reported "no per-item failure details were
  // included ... no reason text, no failed array". backlog-item.md tells the
  // orchestrator to act differently per reason, so the classification has to
  // travel in the text or that instruction is unfollowable.
  test("a failure's classification reaches the content text, not just details", async () => {
    const { spawn } = stubFor(
      withPaneText((argv) =>
        argv[0] === "agent" && argv[1] === "start"
          ? { code: 1, stdout: "", stderr: '{"error":{"code":"agent_not_ready"}}' }
          : tabCreateOk(argv),
      ),
    );

    const res = await spawnOne(spawn);
    const text = res.content.map((c) => c.text).join("\n");

    expect(text).toContain("some-item");
    expect(text).toContain("agent_not_ready");
    // The pane capture deliberately stays behind in `details`: at
    // PANE_CAPTURE_CHARS per failure, a whole batch of them would flood the
    // orchestrator's context with terminal dumps to no purpose.
    expect(text).not.toContain(PANE_TEXT);
    expect(res.details.failed[0]?.reason).toContain(PANE_TEXT);
  });

  // The item prompt is the only thing sent over the wire now, so an
  // undeliverable one is the only prompt-level spawn failure left.
  test("an undeliverable item prompt fails as agent_prompt_stalled", async () => {
    const { spawn, stub } = stubFor(
      withPaneText((argv) =>
        isItemPrompt(argv)
          ? { code: 1, stdout: "", stderr: '{"error":{"code":"agent_not_found"}}' }
          : tabCreateOk(argv),
      ),
    );

    const res = await spawnOne(spawn);

    expect(res.details.failed[0]?.reason).toContain("agent_prompt_stalled");
    expect(res.details.failed[0]?.reason).toContain("agent_not_found");
    expect(res.details.failed[0]?.reason).toContain(PANE_TEXT);
    expect(workerCloses(stub)).toHaveLength(1);
  });

  // A pane's terminal text is the only record of an early worker crash --
  // the sibling pane-width bug was diagnosed entirely from a leaked pane.
  // Capture it, then close: leaving panes open is what turned two failed
  // runs into six orphan panes subdividing the layout further each retry.
  test("a post-split failure captures the pane's output into the reason, then closes the pane", async () => {
    const { spawn, stub } = stubFor((argv) => {
      if (argv[0] === "agent" && argv[1] === "start") {
        return { code: 1, stdout: "", stderr: '{"error":{"code":"agent_not_ready"}}' };
      }
      if (argv[0] === "pane" && argv[1] === "read") {
        return { code: 0, stdout: "pi: terminal too narrow, exiting", stderr: "" };
      }
      return tabCreateOk(argv);
    });

    const res = await spawnOne(spawn);

    expect(res.details.failed[0]?.reason).toContain("agent_not_ready");
    expect(res.details.failed[0]?.reason).toContain("pi: terminal too narrow");
    expect(workerCloses(stub)).toHaveLength(1);
  });

  test("a capture that itself fails leaves the original failure reason intact", async () => {
    const { spawn, stub } = stubFor((argv) => {
      if (argv[0] === "agent" && argv[1] === "start") {
        return { code: 1, stdout: "", stderr: '{"error":{"code":"agent_not_ready"}}' };
      }
      if (argv[0] === "pane" && argv[1] === "read") {
        return { code: 1, stdout: "", stderr: "pane is gone" };
      }
      return tabCreateOk(argv);
    });

    const res = await spawnOne(spawn);

    // A capture problem never replaces the root cause.
    expect(res.details.failed[0]?.reason).toContain("agent_not_ready");
    expect(workerCloses(stub)).toHaveLength(1);
  });

  test("the pane capture is bounded, so one failure cannot flood the digest", async () => {
    const { spawn } = stubFor((argv) => {
      if (argv[0] === "agent" && argv[1] === "start") {
        return { code: 1, stdout: "", stderr: "agent_not_ready" };
      }
      if (argv[0] === "pane" && argv[1] === "read") {
        return { code: 0, stdout: "x".repeat(PANE_CAPTURE_CHARS * 3), stderr: "" };
      }
      return tabCreateOk(argv);
    });

    const res = await spawnOne(spawn);

    expect(res.details.failed[0]?.reason.length).toBeLessThan(PANE_CAPTURE_CHARS + 500);
  });

  // A herdr call that throws rather than exiting non-zero is still a failure
  // that happened after the pane existed. Left unhandled it lands in
  // allSettled's rejected branch, which files the failure against slug
  // "unknown" and leaks the pane -- the orphan panes that subdivide the
  // layout for every later round.
  test("a herdr call that throws is still captured, attributed, and its pane closed", async () => {
    const { spawn, stub } = stubFor(
      withPaneText((argv) => {
        if (isItemPrompt(argv)) throw new Error("herdr socket closed");
        return tabCreateOk(argv);
      }),
    );

    const res = await spawnOne(spawn);

    expect(res.details.failed[0]?.slug).toBe("some-item");
    expect(res.details.failed[0]?.reason).toContain("herdr socket closed");
    expect(res.details.failed[0]?.reason).toContain(PANE_TEXT);
    expect(workerCloses(stub)).toHaveLength(1);
  });

  // Cleanup that fails is still cleanup: the reason it was cleaning up after
  // has to survive it.
  test("a pane close that throws does not throw away the failure reason", async () => {
    const { spawn } = stubFor((argv) => {
      if (argv[0] === "agent" && argv[1] === "start") {
        return { code: 1, stdout: "", stderr: "agent_not_ready" };
      }
      if (argv[0] === "pane" && argv[1] === "read")
        return { code: 0, stdout: PANE_TEXT, stderr: "" };
      if (argv[0] === "tab" && argv[1] === "close") throw new Error("tab already gone");
      return tabCreateOk(argv);
    });

    const res = await spawnOne(spawn);

    expect(res.details.failed[0]?.slug).toBe("some-item");
    expect(res.details.failed[0]?.reason).toContain("agent_not_ready");
    expect(res.details.failed[0]?.reason).toContain(PANE_TEXT);
  });
});

describe("parseTabCreate", () => {
  // Verbatim shape of a real `herdr tab create --cwd ... --label ... --no-focus`
  // response, herdr 0.8.2, captured 2026-09-02. The two ids the swarm needs sit
  // in different objects: the pane to start an agent in, the tab to close later.
  const REAL_TAB_CREATE = JSON.stringify({
    id: "cli:tab:create",
    result: {
      root_pane: {
        agent_status: "unknown",
        cwd: "/home/yanil/dotfiles",
        focused: false,
        pane_id: "w1:p2W",
        revision: 0,
        scroll: { max_offset_from_bottom: 0, offset_from_bottom: 0, viewport_rows: 38 },
        tab_id: "w1:tN",
        terminal_id: "term_65a871a75f6c053",
        workspace_id: "w1",
      },
      tab: {
        agent_status: "unknown",
        focused: false,
        label: "tabexp",
        number: 21,
        pane_count: 1,
        tab_id: "w1:tN",
        workspace_id: "w1",
      },
      type: "tab_created",
    },
  });

  test("reads the root pane id and the tab id out of a real response", () => {
    expect(parseTabCreate(REAL_TAB_CREATE)).toEqual({ paneId: "w1:p2W", tabId: "w1:tN" });
  });

  test("returns undefined rather than a half-identified worker when either id is missing", () => {
    // A worker recorded with a pane but no tab can be started and never closed,
    // which is exactly the orphan-pane class this change is meant to end.
    const noTab = JSON.stringify({ result: { root_pane: { pane_id: "w1:p2W" } } });
    const noPane = JSON.stringify({ result: { tab: { tab_id: "w1:tN" } } });
    expect(parseTabCreate(noTab)).toBeUndefined();
    expect(parseTabCreate(noPane)).toBeUndefined();
  });

  test("returns undefined on unparseable output instead of throwing", () => {
    expect(parseTabCreate("not json")).toBeUndefined();
    expect(parseTabCreate("{}")).toBeUndefined();
    expect(parseTabCreate("")).toBeUndefined();
  });
});

describe("swarm_resolve_blocked execute() wiring", () => {
  let dir: string;
  let priorStateDir: string | undefined;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "swarm-resolve-exec-"));
    priorStateDir = process.env.PI_SWARM_STATE_DIR;
    process.env.PI_SWARM_STATE_DIR = dir;
  });

  afterEach(() => {
    if (priorStateDir === undefined) delete process.env.PI_SWARM_STATE_DIR;
    else process.env.PI_SWARM_STATE_DIR = priorStateDir;
    rmSync(dir, { recursive: true, force: true });
  });

  const RUN = "resolverun";
  const AGENT = "resolverun-w1";
  const PANE = "w1:pZ";
  const TAB = "w1:tZ";

  /**
   * Stands in for herdr's own `agent wait`: returns the agent once it reaches
   * a state the caller actually asked for, and a real timeout envelope when
   * the requested set never matches. Modelling the deadline this way is the
   * whole point -- the bug is that the requested set omitted the state the
   * worker actually reaches.
   */
  const waitLike =
    (reachedStatus: string) =>
    (argv: string[]): { code: number; stdout: string; stderr: string } => {
      const until = argv.filter((a, i) => argv[i - 1] === "--until");
      if (until.includes(reachedStatus)) {
        return { code: 0, stdout: realWaitEnvelope(reachedStatus, AGENT, PANE), stderr: "" };
      }
      return {
        code: 1,
        stdout: "",
        stderr: JSON.stringify({ error: { code: "timeout", message: "deadline elapsed" } }),
      };
    };

  /** Seeds one worker parked at awaiting_relay -- the state swarm_poll leaves a blocked worker in. */
  function setup(
    respond: (argv: string[]) => { code: number; stdout: string; stderr: string } | undefined,
  ) {
    const worker: WorkerRecord = {
      agent: AGENT,
      slug: "some-item",
      paneId: PANE,
      tabId: TAB,
      lifecycle: "awaiting_relay",
    };
    saveState({ runId: RUN, concurrency: 2, nextCounter: 1, workers: [worker] }, dir);

    const stub = makeStubPi((argv) => {
      const override = respond(argv);
      if (override) return override;
      const [a, b] = argv;
      if (a === "agent" && b === "list") {
        // getOrInitState reconciles against this; an empty list would drop the
        // seeded worker before the code under test ever runs. Real wire shape
        // (see realAgentListEnvelope): the named worker alongside an unnamed
        // foreign agent the parse must ignore.
        return {
          code: 0,
          stdout: realAgentListEnvelope(UNNAMED_CLAUDE_ENTRY, {
            agent: "pi",
            agent_status: "idle",
            name: AGENT,
            pane_id: PANE,
          }),
          stderr: "",
        };
      }
      if (a === "agent" && b === "read") {
        // Numbered lines: parsePicker reads "> 1. OK" shapes, so an
        // unnumbered list parses to zero options and short-circuits to
        // needsManual before any of this reaches the verify.
        return { code: 0, stdout: REAL_PICKER_OUTPUT, stderr: "" };
      }
      if (a === "agent" && b === "get") {
        return { code: 0, stdout: realWaitEnvelope("blocked", AGENT, PANE), stderr: "" };
      }
      return { code: 0, stdout: "", stderr: "" };
    });

    registerSwarmTools(stub.pi as unknown as Parameters<typeof registerSwarmTools>[0]);
    const resolve = stub.tools.get("swarm_resolve_blocked");
    if (!resolve) throw new Error("swarm_resolve_blocked was never registered");
    return { resolve, stub };
  }

  const run = (resolve: { execute: (...a: never[]) => Promise<unknown> }, answer = "OK") =>
    resolve.execute(
      ...(["call-1", { runId: RUN, agent: AGENT, answer }, undefined] as unknown as never[]),
    ) as Promise<{
      content: { type: string; text: string }[];
      details: { relayFailed: boolean; needsManual: boolean };
    }>;

  // Every outcome the promptGuidelines tell an orchestrator to branch on has
  // to be legible in `content`. Verified against pi 0.84's own plumbing: no
  // provider adapter in @earendil-works/pi-ai reads a tool result's `details`
  // at all -- openai-completions builds its wire message from `content` text
  // and image blocks only, and no other adapter references the field. So
  // `details` is a session/UI record, and naming its fields in guidance tells
  // the model to read something it never receives.
  describe("outcome markers reach content, not just details", () => {
    const textOf = (res: { content: { text: string }[] }) =>
      res.content.map((c) => c.text).join("\n");

    test("an unmatched answer is marked needs_manual and names the pane", async () => {
      const { resolve } = setup(() => undefined);

      const res = await run(resolve, "definitely not a listed option");

      expect(res.details.needsManual).toBe(true);
      expect(textOf(res)).toContain("needs_manual:");
      // The guideline says to relay which pane; paneId was only in details.
      expect(textOf(res)).toContain(PANE);
    });

    test("a pane identity mismatch is marked needs_manual and names the pane", async () => {
      const { resolve } = setup((argv) =>
        argv[0] === "agent" && argv[1] === "get"
          ? { code: 0, stdout: realWaitEnvelope("blocked", AGENT, "w1:pWRONG"), stderr: "" }
          : undefined,
      );

      const res = await run(resolve);

      expect(res.details.needsManual).toBe(true);
      expect(textOf(res)).toContain("needs_manual:");
      expect(textOf(res)).toContain(PANE);
    });

    test("an untracked agent is marked relay_failed", async () => {
      const { resolve } = setup(() => undefined);

      const res = (await resolve.execute(
        ...([
          "call-1",
          { runId: RUN, agent: "no-such-agent", answer: "OK" },
          undefined,
        ] as unknown as never[]),
      )) as { content: { text: string }[]; details: { relayFailed: boolean } };

      expect(res.details.relayFailed).toBe(true);
      expect(textOf(res)).toContain("relay_failed:");
    });

    test("a successful relay is marked resolved and names the pane", async () => {
      const { resolve } = setup((argv) =>
        argv[0] === "agent" && argv[1] === "wait" ? waitLike("working")(argv) : undefined,
      );

      const res = await run(resolve);

      expect(res.details.relayFailed).toBe(false);
      expect(textOf(res)).toContain("resolved:");
      expect(textOf(res)).toContain(PANE);
    });
  });

  const workerCloses = (stub: { calls: ExecCall[] }) =>
    stub.calls.filter((c) => c.argv[0] === "tab" && c.argv[1] === "close");

  // THE REGRESSION. A worker that answers correctly resumes its turn and
  // enters `working`. Pre-fix the verify asked only for idle/done/blocked, so
  // herdr waited out the full window and returned a timeout, and the tool
  // reported relay_failed on what is in fact the normal success path.
  test("a worker that answers and resumes into working is resolved, not relay_failed", async () => {
    const { resolve, stub } = setup((argv) =>
      argv[0] === "agent" && argv[1] === "wait" ? waitLike("working")(argv) : undefined,
    );

    const res = await run(resolve);

    expect(res.details.relayFailed).toBe(false);
    expect(res.content[0]?.text).toContain("back in the active pool");
    expect(workerCloses(stub)).toHaveLength(0);
    const persisted = loadState(RUN, dir);
    expect(persisted?.workers[0]?.lifecycle).toBe("active");
  });

  // An answer that ends the worker's turn outright must still pass -- the
  // point is to widen the accepted set, not to swap one narrow set for another.
  test("a worker that goes straight to idle is still resolved", async () => {
    const { resolve } = setup((argv) =>
      argv[0] === "agent" && argv[1] === "wait" ? waitLike("idle")(argv) : undefined,
    );

    const res = await run(resolve);

    expect(res.details.relayFailed).toBe(false);
    expect(loadState(RUN, dir)?.workers[0]?.lifecycle).toBe("active");
  });

  // A worker that never answers reaches none of the requested states, so the
  // wait times out -- that timeout IS the failure signal now.
  test("a worker still blocked after the answer is a genuine relay_failed", async () => {
    const { resolve, stub } = setup((argv) =>
      argv[0] === "agent" && argv[1] === "wait" ? waitLike("blocked")(argv) : undefined,
    );

    const res = await run(resolve);

    expect(res.details.relayFailed).toBe(true);
    // Dropping the worker from state without closing its pane leaves a live
    // pi in an orphan pane that nothing will ever poll or clean up.
    expect(workerCloses(stub)).toHaveLength(1);
    expect(loadState(RUN, dir)?.workers).toHaveLength(0);
  });

  // THE RACE, pinned. Measured against real herdr 0.8.2: after send-keys the
  // status still reads `blocked` for ~90-156ms, which is longer than the one
  // herdr call between send-keys and the verify. Asking for `blocked` made
  // herdr match it in ~2ms and fail a worker that had in fact answered, so
  // the requested set must not contain it at all.
  test("never asks herdr for blocked, so the post-answer race cannot fail a good relay", async () => {
    const { resolve, stub } = setup((argv) =>
      argv[0] === "agent" && argv[1] === "wait" ? waitLike("working")(argv) : undefined,
    );

    await run(resolve);

    const waitCall = stub.calls.find((c) => c.argv[0] === "agent" && c.argv[1] === "wait");
    const until = waitCall?.argv.filter((a, i) => waitCall.argv[i - 1] === "--until") ?? [];
    expect(until).not.toContain("blocked");
    expect(until).toContain("working");
    expect(until).toContain("idle");
    expect(until).toContain("done");
  });

  test("a send-keys failure closes the pane instead of leaking it", async () => {
    const { resolve, stub } = setup((argv) =>
      argv[0] === "agent" && argv[1] === "send-keys"
        ? { code: 1, stdout: "", stderr: "send-keys refused" }
        : undefined,
    );

    const res = await run(resolve);

    expect(res.details.relayFailed).toBe(true);
    expect(workerCloses(stub)).toHaveLength(1);
    expect(loadState(RUN, dir)?.workers).toHaveLength(0);
  });

  test("a pane close that fails does not mask the relay failure", async () => {
    const { resolve } = setup((argv) => {
      if (argv[0] === "agent" && argv[1] === "wait") return waitLike("blocked")(argv);
      if (argv[0] === "tab" && argv[1] === "close") throw new Error("tab already gone");
      return undefined;
    });

    const res = await run(resolve);

    expect(res.details.relayFailed).toBe(true);
    expect(res.content[0]?.text).toContain("did not resume");
  });
});

describe("swarm_poll and workers parked at awaiting_relay", () => {
  let dir: string;
  let priorStateDir: string | undefined;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "swarm-poll-relay-"));
    priorStateDir = process.env.PI_SWARM_STATE_DIR;
    process.env.PI_SWARM_STATE_DIR = dir;
  });

  afterEach(() => {
    if (priorStateDir === undefined) delete process.env.PI_SWARM_STATE_DIR;
    else process.env.PI_SWARM_STATE_DIR = priorStateDir;
    rmSync(dir, { recursive: true, force: true });
  });

  // swarm_poll counted only `active`, so a worker parked at awaiting_relay --
  // a live pi holding an open pane, waiting on the orchestrator -- read as an
  // empty pool. The run ended reporting nothing left to do while that worker
  // and its pane were still there.
  test("says the worker is awaiting a relay rather than reporting an empty pool", async () => {
    const runId = "relayrun";
    saveState(
      {
        runId,
        concurrency: 2,
        nextCounter: 1,
        workers: [
          {
            agent: "relayrun-w1",
            slug: "stuck-item",
            paneId: "w1:pQ",
            tabId: "w1:tQ",
            lifecycle: "awaiting_relay",
          },
        ],
      },
      dir,
    );

    const stub = makeStubPi((argv) =>
      argv[0] === "agent" && argv[1] === "list"
        ? {
            code: 0,
            // reconcileState prunes against this; an empty list would drop the
            // parked worker before the emptiness check ever sees it. Real wire
            // shape (see realAgentListEnvelope): the named worker alongside an
            // unnamed foreign agent the parse must ignore.
            stdout: realAgentListEnvelope(UNNAMED_CLAUDE_ENTRY, {
              agent: "pi",
              agent_status: "idle",
              name: "relayrun-w1",
              pane_id: "w1:pA",
            }),
            stderr: "",
          }
        : { code: 0, stdout: "", stderr: "" },
    );
    registerSwarmTools(stub.pi as unknown as Parameters<typeof registerSwarmTools>[0]);
    const poll = stub.tools.get("swarm_poll");
    if (!poll) throw new Error("swarm_poll was never registered");

    const res = (await poll.execute(
      ...(["call-1", { runId, timeoutMs: 1000 }, undefined] as unknown as never[]),
    )) as { content: { type: string; text: string }[] };

    const text = res.content.map((c) => c.text).join("\n");
    expect(text).not.toBe("No active workers to poll.");
    expect(text).toContain("relayrun-w1");
    expect(text).toContain("swarm_resolve_blocked");
  });
});
