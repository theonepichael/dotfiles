/**
 * Model picker — /models overlay showing context window, cost, and reasoning
 * effort per model.
 *
 * Supplements, not replaces, the built-in /model picker (built-in interactive
 * commands cannot be overridden by extensions, and /model / Ctrl+L stay
 * untouched). Per model, the rows show what the built-in picker doesn't:
 * context window size, per-1M-token input/output cost, reasoning/vision
 * badges, and a `no key` tag when the provider has no configured auth — so
 * unconfigured catalogue entries are visible before Enter rather than
 * discovered by failure.
 *
 * Keys:
 *   ↑/↓        move highlight (wraps)
 *   type/bksp  filter rows (fuzzy match on provider/id)
 *   ←/→        move the reasoning-effort segment for the highlighted model
 *   Enter      apply panel effort + switch to the highlighted model
 *   ctrl+s     persist highlighted model as the default model
 *   ctrl+a     add all shown models to enabledModels (additive union)
 *   Esc        close
 *
 * The dialog renders as a true floating overlay ({ overlay: true }) drawn as
 * a solid panel — bg-filled rows and side borders, so it contrasts with the
 * terminal behind it instead of scrolling inline into the chat.
 *
 * Model application happens AFTER the overlay closes: calling pi.setModel
 * from inside the open overlay froze the TUI (observed live, twice — the
 * pane stopped rendering and stopped answering input). Auth is surfaced
 * pre-Enter via the `no key` row tag instead of by keeping the picker open
 * on a failed switch.
 *
 * Ctrl+A mirrors the built-in scoped-models selector's enableAll() semantics
 * exactly: additive union into the existing list, null (all-enabled) stays
 * null, full coverage collapses back to null. Persistence goes through pi's
 * own SettingsManager, whose write path already handles what a hand-rolled
 * read-modify-write would get wrong: locked atomic write, unknown keys
 * preserved, and a corrupt settings file aborting the write instead of
 * clobbering it (errors surfaced via drainErrors).
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { SettingsManager, type Theme } from "@earendil-works/pi-coding-agent";
import type { Model, ModelCost, ModelThinkingLevel } from "@earendil-works/pi-ai";
import type { Api } from "@earendil-works/pi-ai";
import {
  Container,
  fuzzyFilter,
  Key,
  matchesKey,
  type SelectItem,
  SelectList,
  Text,
  truncateToWidth,
  visibleWidth,
  type TUI,
} from "@earendil-works/pi-tui";

/** Ordered non-"off" thinking levels, matching pi-ai's ThinkingLevel. */
const EFFORT_LEVELS = ["minimal", "low", "medium", "high", "xhigh", "max"] as const;

type AnyModel = Model<Api>;

/** Canonical "provider/id" reference for a model. */
export function modelRef(model: Pick<AnyModel, "provider" | "id">): string {
  return `${model.provider}/${model.id}`;
}

/**
 * "200K" / "1.0M" style context size, or "—" when the metadata is missing
 * or zero (Model.contextWindow is typed non-optional but defaults to 0).
 */
export function formatContext(tokens: number | undefined): string {
  if (tokens === undefined || tokens <= 0) return "—";
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${trimNum(tokens / 1_000)}K`;
  return String(tokens);
}

function trimNum(n: number): string {
  const rounded = Math.round(n * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

/**
 * "$3.00 / $15.00" input/output per 1M tokens; "free" when both are zero;
 * "—" when the rates are missing.
 */
export function formatCost(cost: ModelCost | undefined): string {
  if (!cost || typeof cost.input !== "number" || typeof cost.output !== "number") return "—";
  if (cost.input === 0 && cost.output === 0) return "free";
  return `$${cost.input.toFixed(2)} / $${cost.output.toFixed(2)}`;
}

/** Dedupe by provider/id and sort by provider, then id. */
export function dedupeAndSort(models: AnyModel[]): AnyModel[] {
  const seen = new Set<string>();
  const unique: AnyModel[] = [];
  for (const model of models) {
    const ref = modelRef(model);
    if (!seen.has(ref)) {
      seen.add(ref);
      unique.push(model);
    }
  }
  return unique.sort((a, b) => a.provider.localeCompare(b.provider) || a.id.localeCompare(b.id));
}

/**
 * Effort levels the picker offers for a model: always "off"; for reasoning
 * models the pi thinking levels whose thinkingLevelMap entry is not null
 * (a missing key uses the provider default, so it stays offered).
 */
export function effortLevelsFor(
  model: Pick<AnyModel, "reasoning" | "thinkingLevelMap">,
): ModelThinkingLevel[] {
  if (!model.reasoning) return ["off"];
  const levels: ModelThinkingLevel[] = ["off"];
  for (const level of EFFORT_LEVELS) {
    if (model.thinkingLevelMap?.[level] !== null) levels.push(level);
  }
  return levels;
}

/**
 * Panel pre-fill for a highlighted model: its current level when it is the
 * active model (and the level is still supported there), otherwise "off" —
 * never a guessed mid-level, so Enter on an untouched panel is a no-op.
 */
export function prefillEffort(
  model: Pick<AnyModel, "provider" | "id" | "reasoning" | "thinkingLevelMap">,
  activeRef: string | undefined,
  currentLevel: ModelThinkingLevel,
): ModelThinkingLevel {
  if (modelRef(model) !== activeRef) return "off";
  return effortLevelsFor(model).includes(currentLevel) ? currentLevel : "off";
}

/**
 * Additive union mirroring the built-in scoped-models selector's enableAll():
 * null (all-enabled) stays null; otherwise target ids are appended to the
 * existing list (pattern entries are never replaced with a narrower literal
 * list); full coverage of allIds collapses back to null.
 */
export function unionEnabled(
  current: string[] | null,
  targetIds: string[],
  allIds: string[],
): string[] | null {
  if (current === null) return null;
  const result = [...current];
  for (const id of targetIds) {
    if (!result.includes(id)) result.push(id);
  }
  return result.length === allIds.length && result.every((id) => allIds.includes(id))
    ? null
    : result;
}

export interface PersistOutcome {
  ok: boolean;
  error?: string;
}

/**
 * Persist the default model (provider + id, same fields the built-in
 * selector writes). SettingsManager.create without an explicit agentDir
 * resolves via getAgentDir(), honoring PI_CODING_AGENT_DIR.
 */
export async function saveDefaultModel(
  cwd: string,
  provider: string,
  modelId: string,
  agentDir?: string,
): Promise<PersistOutcome> {
  try {
    const settings = SettingsManager.create(cwd, agentDir);
    settings.setDefaultModelAndProvider(provider, modelId);
    await settings.flush();
    const errors = settings.drainErrors();
    if (errors.length > 0) {
      return { ok: false, error: errors.map((e) => `${e.scope}: ${e.error.message}`).join("; ") };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Persist enabledModels patterns; null means unrestricted/all-enabled and
 * clears the field (what the built-in selector writes when everything is
 * enabled).
 */
export async function saveEnabledModels(
  cwd: string,
  patterns: string[] | null,
  agentDir?: string,
): Promise<PersistOutcome> {
  try {
    const settings = SettingsManager.create(cwd, agentDir);
    settings.setEnabledModels(patterns ?? undefined);
    await settings.flush();
    const errors = settings.drainErrors();
    if (errors.length > 0) {
      return { ok: false, error: errors.map((e) => `${e.scope}: ${e.error.message}`).join("; ") };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/** Badge suffix for a row ("  [reasoning, vision]"), or "" when none apply. */
export function badgesFor(
  model: AnyModel,
  opts: { isActive?: boolean; hasAuth?: boolean } = {},
): string {
  const badges: string[] = [];
  if (model.reasoning) badges.push("reasoning");
  if (model.input?.includes("image")) badges.push("vision");
  if (opts.hasAuth === false) badges.push("no key");
  if (opts.isActive) badges.push("active");
  return badges.length > 0 ? `  [${badges.join(", ")}]` : "";
}

/** Plain-text row for a model: "provider/id  200K ctx  $3.00 / $15.00" + badges. */
export function rowLabel(
  model: AnyModel,
  opts: {
    isActive?: boolean;
    hasAuth?: boolean;
    /** Column widths (from columnWidths) so rows align into columns. */
    refWidth?: number;
    ctxWidth?: number;
    costWidth?: number;
  } = {},
): string {
  const badgeText = badgesFor(model, opts);
  // Refs longer than the column get an ellipsis rather than pushing the row
  // past the panel edge (a truncated styled row loses its ANSI reset and
  // leaves rendering artifacts).
  let ref = modelRef(model);
  if (opts.refWidth) {
    ref = ref.length > opts.refWidth ? `${ref.slice(0, Math.max(1, opts.refWidth - 1))}…` : ref;
    ref = ref.padEnd(opts.refWidth);
  }
  const ctx = formatContext(model.contextWindow);
  const ctxText = opts.ctxWidth ? ctx.padEnd(opts.ctxWidth) : ctx;
  const cost = formatCost(model.cost);
  const costText = opts.costWidth ? cost.padEnd(opts.costWidth) : cost;
  return `${ref}  ${ctxText} ctx  ${costText}${badgeText}`;
}

/**
 * Column widths that align the id, context, and cost columns across rows.
 * maxBadgeLen reserves room for the widest badge suffix; maxRefWidth caps the
 * ref column (rows must fit the panel, not the widest catalogue entry).
 */
export function columnWidths(
  models: AnyModel[],
  opts: { maxBadgeLen?: number; maxRefWidth?: number } = {},
): {
  refWidth: number;
  ctxWidth: number;
  costWidth: number;
} {
  let refWidth = models.length > 0 ? Math.max(...models.map((m) => modelRef(m).length)) : 1;
  if (opts.maxRefWidth !== undefined) refWidth = Math.min(refWidth, opts.maxRefWidth);
  return {
    refWidth: Math.max(1, refWidth),
    ctxWidth:
      models.length > 0 ? Math.max(...models.map((m) => formatContext(m.contextWindow).length)) : 1,
    costWidth: models.length > 0 ? Math.max(...models.map((m) => formatCost(m.cost).length)) : 1,
  };
}

/**
 * Character indices of query in text, in order (subsequence scan) — used to
 * highlight what the fuzzy filter matched.
 */
export function matchIndices(text: string, query: string): number[] {
  const lower = text.toLowerCase();
  const q = query.toLowerCase();
  // A contiguous hit wins — it's what the eye expects the highlight to mark.
  const contiguous = lower.indexOf(q);
  if (contiguous !== -1) return [...Array(q.length).keys()].map((i) => contiguous + i);
  const indices: number[] = [];
  let start = 0;
  for (const ch of q) {
    const at = lower.indexOf(ch, start);
    if (at === -1) return [];
    indices.push(at);
    start = at + 1;
  }
  return indices;
}

/**
 * Effort rendered as an intensity bar: one cell per non-"off" level, filled
 * count = the selected level's depth (off = empty bar).
 */
export function effortBar(
  levels: ModelThinkingLevel[],
  current: ModelThinkingLevel,
): {
  filled: number;
  total: number;
} {
  const total = Math.max(0, levels.length - 1);
  return { filled: Math.max(0, levels.indexOf(current)), total };
}

/** Muted "provider/" prefix + "no key" dimming for a row, ANSI-applied. */
function styleRowLabel(
  ref: string,
  rest: string,
  theme: Theme,
  opts: { hasAuth?: boolean; query?: string },
): string {
  if (opts.hasAuth === false) return theme.fg("dim", ref + rest);
  if (opts.query) {
    // Highlight what the filter matched; unmatched text stays plain.
    const hits = new Set(matchIndices(ref, opts.query));
    let out = "";
    for (let i = 0; i < ref.length; i++) {
      out += hits.has(i) ? theme.fg("accent", ref[i]) : ref[i];
    }
    return out + rest;
  }
  const slash = ref.indexOf("/");
  if (slash === -1) return ref + rest;
  return theme.fg("muted", ref.slice(0, slash + 1)) + ref.slice(slash + 1) + rest;
}

/** The models the picker offers: scoped models when non-empty, else the full catalogue. */
function pickerModels(ctx: ExtensionContext): AnyModel[] {
  const scoped = ctx.scopedModels.map((sm) => sm.model);
  const models = scoped.length > 0 ? scoped : ctx.modelRegistry.getAvailable();
  return dedupeAndSort(models);
}

/** Solid panel background, so the dialog contrasts with the terminal. */
const PANEL_BG = "customMessageBg" as const;

/**
 * Draw the dialog content as a solid floating panel: accent side borders,
 * every row bg-filled to the full panel width.
 */
function renderPanel(content: string[], width: number, theme: Theme): string[] {
  const inner = Math.max(1, width - 4); // │ + one space each side
  const filled = (line: string): string =>
    theme.bg(PANEL_BG, ` ${truncateToWidth(line, inner, "", true)} `);
  const edge = (l: string, r: string) =>
    theme.fg("accent", l) + theme.bg(PANEL_BG, " ".repeat(inner + 2)) + theme.fg("accent", r);
  return [
    edge("╭", "╮"),
    ...content.map((line) => `${theme.fg("accent", "│")}${filled(line)}${theme.fg("accent", "│")}`),
    edge("╰", "╯"),
  ];
}

export default function modelPicker(pi: ExtensionAPI) {
  async function openPicker(ctx: ExtensionContext): Promise<void> {
    if (ctx.mode !== "tui") {
      ctx.ui.notify("/models needs an interactive session", "warning");
      return;
    }

    const models = pickerModels(ctx);
    if (models.length === 0) {
      ctx.ui.notify("No models available", "warning");
      return;
    }

    const activeRef = ctx.model ? modelRef(ctx.model) : undefined;
    const authByProvider = new Map<string, boolean>();
    const hasAuth = (provider: string): boolean => {
      let status = authByProvider.get(provider);
      if (status === undefined) {
        status = ctx.modelRegistry.getProviderAuthStatus(provider).configured;
        authByProvider.set(provider, status);
      }
      return status;
    };

    const result = await ctx.ui.custom<{ model: AnyModel; level: ModelThinkingLevel } | null>(
      (tui: TUI, theme, _kb, done) => {
        let filter = "";
        let innerPanelWidth = 110;
        /** effort segment index per model ref, so it survives highlight moves */
        const effortIndexByRef = new Map<string, number>();
        const allRefs = models.map(modelRef);

        // Column widths are per-view: computed from the filtered models so a
        // narrow result doesn't inherit the full catalogue's wide ref column,
        // and capped so the widest row (badges included) always fits the
        // panel — a truncated styled row loses its ANSI reset and leaves
        // rendering artifacts.
        let widths = columnWidths(models);

        const buildItems = (query: string, columnWidthsForView: typeof widths): SelectItem[] =>
          fuzzyFilter(models, query, (m) => modelRef(m)).map((model) => {
            const ref = modelRef(model);
            const full = rowLabel(model, {
              isActive: ref === activeRef,
              hasAuth: hasAuth(model.provider),
              ...columnWidthsForView,
            });
            // full starts with the ref padded to the column width; keep that
            // padding by styling the padded prefix, not the bare ref.
            const paddedRef = full.slice(0, columnWidthsForView.refWidth);
            return {
              value: ref,
              label: styleRowLabel(paddedRef, full.slice(columnWidthsForView.refWidth), theme, {
                hasAuth: hasAuth(model.provider),
                query,
              }),
            };
          });

        // SelectList.setFilter is a prefix match on the item value, which
        // would make "glm" never match "opencode-go/glm-…", so filtering is
        // owned here (fuzzy over provider/id, the same primitive the built-in
        // selector uses) and the list is rebuilt per query. Selection resets
        // to the top on every filter change.
        const makeSelectList = (query: string, columnWidthsForView: typeof widths): SelectList => {
          const list = new SelectList(
            buildItems(query, columnWidthsForView),
            Math.min(models.length, 12),
            {
              selectedPrefix: (text) => theme.fg("accent", text),
              selectedText: (text) => theme.fg("accent", text),
              description: (text) => theme.fg("muted", text),
              scrollInfo: (text) => theme.fg("dim", text),
              noMatch: (text) => theme.fg("warning", text),
            },
          );
          list.onSelect = (item) => {
            const model = models.find((m) => modelRef(m) === item.value);
            if (!model) return;
            // Non-reasoning models explicitly get "off" — pi clamps regardless,
            // so switching away from a reasoning model never leaves a stale level.
            const level: ModelThinkingLevel = model.reasoning ? effortFor(model) : "off";
            // Close first, apply later: pi.setModel from inside the open overlay
            // froze the TUI (see file header).
            done({ model, level });
          };
          list.onCancel = () => done(null);
          list.onSelectionChange = () => {
            prefillHighlighted();
            refresh();
          };
          return list;
        };

        let selectList = makeSelectList("", widths);

        /** Recompute per-view widths and rebuild the list for the current filter. */
        function relayout(): void {
          const filtered = fuzzyFilter(models, filter, (m) => modelRef(m));
          const ctxWidth =
            filtered.length > 0
              ? Math.max(...filtered.map((m) => formatContext(m.contextWindow).length))
              : 1;
          const costWidth =
            filtered.length > 0 ? Math.max(...filtered.map((m) => formatCost(m.cost).length)) : 1;
          const badgeMax =
            filtered.length > 0
              ? Math.max(
                  ...filtered.map(
                    (m) =>
                      badgesFor(m, {
                        isActive: modelRef(m) === activeRef,
                        hasAuth: hasAuth(m.provider),
                      }).length,
                  ),
                )
              : 0;
          // Row budget: panel width minus borders/bg padding (4), the
          // SelectList selection prefix (2), and one safety column.
          const budget = Math.max(40, innerPanelWidth - 7);
          const refMax = budget - (2 + ctxWidth + 4 + 2 + costWidth + badgeMax);
          widths = columnWidths(filtered, {
            maxBadgeLen: badgeMax,
            maxRefWidth: Math.max(12, refMax),
          });
          widths = { ...widths, maxBadgeLen: undefined } as typeof widths;
          selectList = makeSelectList(filter, widths);
          prefillHighlighted();
        }

        const highlighted = (): AnyModel | undefined =>
          models.find((m) => modelRef(m) === selectList.getSelectedItem()?.value);

        const effortFor = (model: AnyModel): ModelThinkingLevel => {
          const levels = effortLevelsFor(model);
          const index = effortIndexByRef.get(modelRef(model)) ?? 0;
          return levels[Math.min(index, levels.length - 1)];
        };

        const prefillHighlighted = (): void => {
          const model = highlighted();
          if (!model) return;
          const ref = modelRef(model);
          if (effortIndexByRef.has(ref)) return;
          const prefill = prefillEffort(model, activeRef, pi.getThinkingLevel());
          effortIndexByRef.set(ref, effortLevelsFor(model).indexOf(prefill));
        };

        const refresh = () => tui.requestRender();

        /** Live line(s): filter indicator + reasoning-effort segments. */
        function statusLines(): string[] {
          const lines: string[] = [];
          if (filter) lines.push(`  ${theme.fg("muted", "filter:")} ${filter}`);
          const model = highlighted();
          if (!model) return lines;
          if (!model.reasoning) {
            lines.push(`  ${theme.fg("dim", "effort: not available for this model")}`);
            return lines;
          }
          const levels = effortLevelsFor(model);
          const current = effortFor(model);
          const { filled, total } = effortBar(levels, current);
          const deep = current === "high" || current === "xhigh" || current === "max";
          const color = filled === 0 ? "dim" : deep ? "success" : "accent";
          const bar =
            theme.fg(color, "▮".repeat(filled)) + theme.fg("dim", "▯".repeat(total - filled));
          lines.push(`  ${theme.fg("muted", "effort:")} ${bar} ${theme.fg(color, current)}`);
          return lines;
        }

        /** Rebuilt every render so highlight/filter/effort changes are visible. */
        const statusComponent = {
          render(): string[] {
            return statusLines();
          },
          invalidate(): void {},
        };

        function applyFilterDelta(delta: string): void {
          filter = delta;
          relayout();
          refresh();
        }

        function handleInput(data: string): void {
          if (matchesKey(data, Key.ctrl("s"))) {
            const model = highlighted();
            if (model) {
              void saveDefaultModel(ctx.cwd, model.provider, model.id).then((outcome) => {
                ctx.ui.notify(
                  outcome.ok
                    ? `Default model saved: ${modelRef(model)}`
                    : `Failed to save default model: ${outcome.error}`,
                  outcome.ok ? "info" : "error",
                );
                refresh();
              });
            }
            return;
          }
          if (matchesKey(data, Key.ctrl("a"))) {
            const ids = filter ? buildItems(filter, widths).map((item) => item.value) : allRefs;
            const allIds = allRefs;
            let currentPatterns: string[] | null;
            try {
              currentPatterns = SettingsManager.create(ctx.cwd).getEnabledModels() ?? null;
            } catch {
              currentPatterns = null;
            }
            void saveEnabledModels(ctx.cwd, unionEnabled(currentPatterns, ids, allIds)).then(
              (outcome) => {
                ctx.ui.notify(
                  outcome.ok
                    ? `Enabled models saved (${ids.length} shown)`
                    : `Failed to save enabled models: ${outcome.error}`,
                  outcome.ok ? "info" : "error",
                );
                refresh();
              },
            );
            return;
          }

          // Everything SelectList consumes: ↑/↓/Enter/Esc.
          if (
            matchesKey(data, Key.up) ||
            matchesKey(data, Key.down) ||
            matchesKey(data, Key.enter) ||
            matchesKey(data, Key.escape)
          ) {
            selectList.handleInput(data);
            prefillHighlighted();
            refresh();
            return;
          }

          // ←/→ are free (SelectList has no caret editing): move the effort segment.
          if (matchesKey(data, Key.left) || matchesKey(data, Key.right)) {
            const model = highlighted();
            if (model && model.reasoning) {
              const levels = effortLevelsFor(model);
              const index = effortIndexByRef.get(modelRef(model)) ?? 0;
              const next = matchesKey(data, Key.left)
                ? Math.max(0, index - 1)
                : Math.min(levels.length - 1, index + 1);
              effortIndexByRef.set(modelRef(model), next);
              refresh();
            }
            return;
          }

          if (data === "\x7f" || data === "\b") {
            applyFilterDelta(filter.slice(0, -1));
            return;
          }

          // Printable characters go to the filter; control chars (ctrl+…) are ignored.
          if (data.length === 1 && data >= " ") {
            applyFilterDelta(filter + data);
          }
        }

        const container = new Container();
        // Title row: left-aligned title, esc hint right-aligned.
        const title = "Select Model";
        const escHint = "esc";
        const titlePad = Math.max(1, innerPanelWidth - visibleWidth(title) - visibleWidth(escHint));
        container.addChild(
          new Text(
            theme.fg("accent", theme.bold(title)) + " ".repeat(titlePad) + theme.fg("dim", escHint),
          ),
        );
        // Column header aligned over the rows' columns. A row renders as
        // `<panel "│ "> <select "  "> <ref>  <ctx> ctx  <cost>` — the header
        // uses paddingX 0 (Text defaults to 1, which broke alignment) and
        // compensates for the SelectList prefix by hand.
        const headerHolder = {
          render(): string[] {
            return [
              "  " +
                theme.fg("dim", "model") +
                " ".repeat(Math.max(1, widths.refWidth + widths.ctxWidth - 2)) +
                theme.fg("dim", "ctx") +
                theme.fg("dim", "  ") +
                theme.fg("dim", "cost $in / $out per 1M tokens"),
            ];
          },
          invalidate(): void {},
        };
        container.addChild(headerHolder);
        // Wrapper so the container always renders the current list instance
        // (applyFilterDelta rebuilds it on every query change).
        const listHolder = {
          render(width: number): string[] {
            return selectList.render(width);
          },
          invalidate(): void {
            selectList.invalidate();
          },
        };
        container.addChild(listHolder);
        container.addChild(statusComponent);
        container.addChild(
          new Text(
            theme.fg(
              "dim",
              "↑↓ select · type to filter · ←→ effort · enter apply · ctrl+s default · ctrl+a enable all",
            ),
          ),
        );

        prefillHighlighted();

        return {
          render(width: number): string[] {
            innerPanelWidth = Math.max(1, width - 4);
            return renderPanel(container.render(innerPanelWidth), width, theme);
          },
          invalidate(): void {
            container.invalidate();
          },
          handleInput,
        };
      },
      {
        overlay: true,
        overlayOptions: { width: "80%", minWidth: 80 },
      },
    );

    if (!result) return;

    // Apply after the overlay is fully closed (see file header for why).
    if (modelRef(result.model) !== activeRef) {
      const ok = await pi.setModel(result.model);
      if (!ok) {
        ctx.ui.notify(`No API key for ${modelRef(result.model)}`, "warning");
        return;
      }
    }
    pi.setThinkingLevel(result.level);
    ctx.ui.notify(`Model: ${modelRef(result.model)} · effort: ${result.level}`, "info");
  }

  pi.registerCommand("models", {
    description: "Pick a model (shows context window, cost, reasoning effort)",
    handler: async (_args, ctx) => {
      await openPicker(ctx);
    },
  });

  pi.registerShortcut(Key.ctrlShift("m"), {
    description: "Open the model picker",
    handler: async (ctx) => {
      await openPicker(ctx);
    },
  });
}
