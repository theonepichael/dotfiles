/**
 * Philosophy Header
 *
 * Replaces pi's built-in header (logo + keybinding hints) with a π wordmark
 * and a tagline drawn from ~/dotfiles — STYLE.md and claude/global-instructions.md.
 *
 * The tagline is picked once per session, so it stays stable while you work
 * but varies across sessions.
 *
 * Restore the built-in header with /builtin-header.
 */

import type { ExtensionAPI, Theme } from "@earendil-works/pi-coding-agent";

/** π wordmark, 17 columns wide. */
export const PI_LOGO: readonly string[] = [
  "█████████████████",
  "█████████████████",
  "  ███       ███  ",
  "  ███       ███  ",
  "  ███       ███  ",
  "  ███       ███  ",
  "  ███       ███  ",
  "  ███       ███  ",
];

/** Verbatim or near-verbatim from ~/dotfiles. */
export const TAGLINES: readonly string[] = [
  "prevent the whole class, not just this instance",
  "verification means running it",
  "uniformity is paramount — small, explicit, testable",
  "a test that can't be made to fail first isn't verifying anything",
  "normal verbosity by default",
  "never commit directly to main",
];

/** Colorizer with the same shape as `Theme.fg`, narrowed to what the header uses. */
export type Colorize = (role: "accent" | "muted" | "dim", text: string) => string;

/**
 * Choose a tagline from a 0..1 sample.
 *
 * Split out from the header factory so the selection is deterministic under
 * test: `Math.random()` stays at the single call site in the default export.
 * Values at or past 1 (and any NaN) clamp to the last entry rather than
 * indexing off the end.
 */
export function pickTagline(sample: number): string {
  const count = TAGLINES.length;
  const index = Number.isFinite(sample) ? Math.floor(sample * count) : 0;
  const clamped = Math.min(Math.max(index, 0), count - 1);
  return TAGLINES[clamped]!;
}

/** Build the header's rendered lines. Pure, so the layout is testable. */
export function renderHeaderLines(tagline: string, fg: Colorize): string[] {
  return [
    "",
    ...PI_LOGO.map((line) => fg("accent", line)),
    "",
    fg("muted", `  ${tagline}`),
    fg("dim", "  /hotkeys · ctrl+c clear · escape abort"),
    "",
  ];
}

export default function (pi: ExtensionAPI) {
  let tagline = TAGLINES[0]!;

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui") return;

    // One tagline per session — stable while you work, varied across sessions.
    tagline = pickTagline(Math.random());

    ctx.ui.setHeader((_tui, theme: Theme) => ({
      render(_width: number): string[] {
        return renderHeaderLines(tagline, (role, text) => theme.fg(role, text));
      },
      invalidate() {},
    }));
  });

  pi.registerCommand("builtin-header", {
    description: "Restore pi's built-in header",
    handler: async (_args, ctx) => {
      ctx.ui.setHeader(undefined);
      ctx.ui.notify("Built-in header restored", "info");
    },
  });
}
