import { afterEach, describe, expect, test } from "bun:test";
import guardRails from "../extensions/guard-rails";
import permissionGate from "../extensions/permission-gate";
import trustSession from "../extensions/trust-session";

type Handler = (event: any, ctx: any) => Promise<any> | any;

function makeFakePi() {
  const handlers: Handler[] = [];
  const commands: Record<string, { handler: (args: string, ctx: any) => Promise<void> }> = {};
  return {
    pi: {
      on(_event: string, handler: Handler) {
        handlers.push(handler);
      },
      registerCommand(name: string, opts: { handler: (args: string, ctx: any) => Promise<void> }) {
        commands[name] = opts;
      },
      exec: async () => ({ code: 0, stdout: "", stderr: "" }),
    },
    handlers,
    commands,
  };
}

function makeCtx(confirmResult: boolean) {
  const confirmTitles: string[] = [];
  return {
    ctx: {
      hasUI: true,
      cwd: "/repo",
      ui: {
        confirm: async (title: string) => {
          confirmTitles.push(title);
          return confirmResult;
        },
        notify: () => {},
      },
    },
    getConfirmTitles: () => confirmTitles,
  };
}

async function fireCommand(handlers: Handler[], command: string, confirmResult: boolean) {
  const { ctx, getConfirmTitles } = makeCtx(confirmResult);
  const event = { toolName: "bash", input: { command } };
  let finalResult: any;
  for (const h of handlers) {
    const r = await h(event, ctx);
    if (r !== undefined) {
      finalResult = r;
      break;
    }
  }
  return { finalResult, titles: getConfirmTitles() };
}

// Simulates the real ~/.pi/agent/extensions/ dir: guard-rails.ts,
// permission-gate.ts, and trust-session.ts all loaded together, subscribed
// to the same tool_call event in registration order, the way Pi's event bus
// actually runs them.
describe("real-world layout: guard-rails, permission-gate, and trust-session all loaded", () => {
  test("disabling guard-rails alone does not stop sudo from being asked about -- permission-gate still gates it", async () => {
    const { pi, handlers, commands } = makeFakePi();
    guardRails(pi as any);
    permissionGate(pi as any);

    const before = await fireCommand(handlers, "sudo apt update", true);
    expect(before.titles).toEqual(["⚠️ sudo", "Run bash command?"]);

    await commands["guard-rails-disable"]!.handler("", { ui: { notify: () => {} } });

    const after = await fireCommand(handlers, "sudo apt update", true);
    expect(after.titles).toEqual(["Run bash command?"]);
  });

  test("/trust-session disables both gates at once", async () => {
    const { pi, handlers, commands } = makeFakePi();
    guardRails(pi as any);
    permissionGate(pi as any);
    trustSession(pi as any);

    expect(commands["trust-session"]).toBeDefined();
    await commands["trust-session"]!.handler("", { ui: { notify: () => {} } });

    const sudoResult = await fireCommand(handlers, "sudo apt update", true);
    expect(sudoResult.titles).toEqual([]);
    expect(sudoResult.finalResult).toBeUndefined();

    const unlisted = await fireCommand(handlers, "git merge some-branch", true);
    expect(unlisted.titles).toEqual([]);
    expect(unlisted.finalResult).toBeUndefined();
  });

  test("/trust-session-off restores both gates", async () => {
    const { pi, handlers, commands } = makeFakePi();
    guardRails(pi as any);
    permissionGate(pi as any);
    trustSession(pi as any);

    await commands["trust-session"]!.handler("", { ui: { notify: () => {} } });
    await commands["trust-session-off"]!.handler("", { ui: { notify: () => {} } });

    const result = await fireCommand(handlers, "sudo apt update", true);
    expect(result.titles).toEqual(["⚠️ sudo", "Run bash command?"]);
  });
});

// ---------------------------------------------------------------------------
// Unattended sessions.
//
// A swarm worker lives in a herdr tab nobody is watching, so ctx.hasUI is true
// -- it really is a TUI -- while no human will ever answer a dialog in it. A
// worker that reached guard-rails' rm -rf or sudo confirmation waited forever
// while herdr still reported it as working, and the run stopped making
// progress with no signal (observed live, 2026-09-02).
//
// The resolution is to BLOCK, not to allow. Disabling guard-rails wholesale
// would also drop protected-path writes and the git-commit-on-main worktree
// policy, which is far more autonomy than the stranding problem calls for.
// These tests pin both halves: the two dialogs become refusals, and nothing
// else changes.
// ---------------------------------------------------------------------------

describe("guard-rails in an unattended session", () => {
  const prior = process.env.PI_AGENT_UNATTENDED;

  function unattended(): void {
    process.env.PI_AGENT_UNATTENDED = "1";
  }

  afterEach(() => {
    if (prior === undefined) delete process.env.PI_AGENT_UNATTENDED;
    else process.env.PI_AGENT_UNATTENDED = prior;
  });

  test("rm -rf is refused outright, with no dialog raised", async () => {
    unattended();
    const { pi, handlers } = makeFakePi();
    guardRails(pi as any);

    // confirmResult true: had a dialog been raised at all, the command would
    // have been allowed through. It must never get that far.
    const res = await fireCommand(handlers, "rm -rf /tmp/scratch", true);

    expect(res.titles).toEqual([]);
    expect(res.finalResult?.block).toBe(true);
    expect(res.finalResult?.reason).toContain("no human is watching");
  });

  test("sudo is refused outright, with no dialog raised", async () => {
    unattended();
    const { pi, handlers } = makeFakePi();
    guardRails(pi as any);

    const res = await fireCommand(handlers, "sudo apt update", true);

    expect(res.titles).toEqual([]);
    expect(res.finalResult?.block).toBe(true);
    expect(res.finalResult?.reason).toContain("no human is watching");
  });

  test("an attended session still asks, rather than refusing", async () => {
    // The same handler, same command, only the environment differs -- this is
    // what makes the two tests above about unattendedness and not about
    // guard-rails having simply started blocking rm -rf for everyone.
    const { pi, handlers } = makeFakePi();
    guardRails(pi as any);

    const res = await fireCommand(handlers, "rm -rf /tmp/scratch", true);

    expect(res.titles).toEqual(["⚠️ rm -rf"]);
    expect(res.finalResult).toBeUndefined();
  });

  test("everything else guard-rails protects stays armed", async () => {
    unattended();
    const { pi, handlers } = makeFakePi();
    guardRails(pi as any);

    // A protected-path write is blocked without ever consulting a human, so
    // it is unaffected by whether one is present -- and must stay that way.
    const { ctx } = makeCtx(true);
    const event = { toolName: "write", input: { path: "repo/.env.production" } };
    let result: any;
    for (const h of handlers) {
      const r = await h(event, ctx);
      if (r !== undefined) {
        result = r;
        break;
      }
    }

    expect(result?.block).toBe(true);
    expect(result?.reason).toContain("protected");
  });
});
