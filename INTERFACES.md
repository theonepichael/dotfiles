# INTERFACES.md

Scope: `claude/`, `copilot/`, `opencode/`, `agy/`, `pi/`, the shared scripts under
`claude/scripts/` that `links.toml` installs into `~/.claude/scripts/`, and the
repo-root installer entrypoints those harnesses are provisioned by.

**This file is generated. Do not edit it by hand — your edits will be
overwritten.** Regenerate it after changing any harness script:

```bash
python3 claude/scripts/gen_interfaces.py           # rewrite this file
python3 claude/scripts/gen_interfaces.py --check   # exit 1 if it is stale
```

Everything below is extracted statically, with `ast`, `tomllib`, and a small
frontmatter reader. No harness module is imported and no script is run — these
scripts mutate live state under `~/.claude`, so documenting them must not
execute them. The cost of that choice is that anything invisible to static
analysis is reported as unknown rather than guessed at: `choices=` computed at
runtime, hand-rolled `sys.argv` dispatch, and behaviour that only exists in
prose. Where this file says a detail is not statically visible, read the
source.

"Public" below means exactly one thing: defined at module level without a
leading underscore. That is a naming convention, not a curated API — a helper
that simply was never underscored will appear here.

House style for these interfaces is in `STYLE.md`.

---

## 1. Shared scripts (`claude/scripts/`)

| Module | Purpose |
| --- | --- |
| [`cli_common.py`](#claudescriptsclicommonpy) | Shared CLI helpers used across dotfiles scripts. |
| [`dev_status_sync.py`](#claudescriptsdevstatussyncpy) | dev_status_sync.py — cross-machine sync for dev_status.py's backlog/pending store. |
| [`gen_core_instructions.py`](#claudescriptsgencoreinstructionspy) | gen_core_instructions.py — compose CORE_INSTRUCTIONS.md + personal-overlay.md into claude/global-instructions.md. |
| [`gen_interfaces.py`](#claudescriptsgeninterfacespy) | gen_interfaces.py — regenerate INTERFACES.md mechanically from the sources. |
| [`opencode_skills_sync_activity.py`](#claudescriptsopencodeskillssyncactivitypy) | Print opencode-skills-sync's pause state and last known snapshot commit, so a session can tell whether the daemon is running and how current its mirror is -- mirrors watchcommit_activity.py's SessionStart banner role. |
| [`settings_seed_drift_check.py`](#claudescriptssettingsseeddriftcheckpy) | SessionStart hook + CLI: detect (and optionally fix) drift between the live ``~/.claude/settings.json`` / ``~/.config/opencode/opencode.jsonc`` / (under WSL) the Windows-side VS Code ``settings.json`` and ``keybindings.json`` and their seeds in the dotfiles repo. |
| [`watchcommit_activity.py`](#claudescriptswatchcommitactivitypy) | Print watchcommit's last known background pull/commit/push, so a session (or wc-status) can tell daemon-driven git state changes from manual ones instead of only seeing a clean/up-to-date working tree. |

### `claude/scripts/cli_common.py`

Shared CLI helpers used across dotfiles scripts.

- Installed at: `~/.claude/scripts/cli_common.py` (all harnesses)
- Entrypoint: not executable, no shebang
- CLI: none (library module).
- Public functions:
  - `add_verbosity_args(parser: argparse.ArgumentParser) -> None` — Add mutually-exclusive --quiet/-q and --verbose/-v flags to a parser.
  - `vprint(msg: str, *, verbose: bool, file: TextIO | None = None) -> None` — Print a diagnostic message when verbose mode is enabled.
  - `qprint(msg: str, *, quiet: bool, file: TextIO | None = None) -> None` — Print a message unless quiet mode is enabled.
- Tested by: `claude/scripts/test_cli_common.py`

### `claude/scripts/dev_status_sync.py`

dev_status_sync.py — cross-machine sync for dev_status.py's backlog/pending store.

- Installed at: `~/.claude/scripts/dev_status_sync.py` (not on work)
- Entrypoint: executable, `#!/usr/bin/env python3`
- CLI (`argparse`): Cross-machine sync for dev_status.py's backlog/pending store.
  - `--quiet/-q`
  - `--verbose/-v`
  - `--host` — SSH alias for the remote machine
  - `--remote-script`
  - `--local-user`
  - `--remote-user`
  - `--user-map` — JSON object overriding the default username->home-dir map
  - `--lock-timeout` (default: 10.0)
  - `--ssh-timeout` (default: 20.0)
  - `--max-retries` (default: 3)
- Subcommands:
  - `sync [--dry-run] [--no-artifacts] [--rsync-io-timeout <seconds>]` — merge against the other machine
    - `--no-artifacts` — skip grill/ artifact transfer (metadata-only sync)
    - `--rsync-io-timeout` — rsync I/O timeout (defaults to --ssh-timeout)
  - `status` — report divergence without merging
  - `export` — internal: dump local store+rev as JSON
  - `import --if-rev <N>` — internal: write a merged store from stdin
    - `--if-rev` (required)
- Environment: `LOGNAME`, `USER`
- Explicit exit codes: `1`, `2`
- Depends on: `cli_common.py`
- Exceptions:
  - `class SyncFatalError(Exception)` — A non-retryable sync failure.
  - `class SyncRetryableError(Exception)` — A retryable sync condition (stale rev, lock timeout, SSH hiccup).
- Public classes:
  - `class SyncComputation`
- Public functions:
  - `local_lock(timeout: float) -> Iterator[None]` — Hold this machine's exclusive backlog lock, polling with a deadline.
  - `load_sync_base(local_schema: dict[str, object]) -> tuple[list[dict[str, object]] | None, list[dict[str, object]] | None]` — Load ``_sync-base.json``, per-store, treating a schema-stale store as absent.
  - `save_sync_base(local_schema: dict[str, object], items: list[dict[str, object]], pending: list[dict[str, object]]) -> None` — Atomically persist the post-sync state as the new base snapshot.
  - `rewrite_related_files_paths(item: dict[str, object], from_home: str, to_home: str) -> dict[str, object]` — Rewrite a leading ``from_home`` prefix on ``related_files.path`` entries.
  - `rewrite_paths_list(items: list[dict[str, object]], from_home: str, to_home: str) -> list[dict[str, object]]` — Apply :func:`rewrite_related_files_paths` across a whole store.
  - `collect_artifact_paths(items: list[dict[str, object]], home: str) -> list[Path]` — Return distinct, sorted artifact paths to transfer for ``items``.
  - `remote_has_rsync(host: str, ssh_timeout: float) -> bool` — Preflight: is ``rsync`` available on the remote over SSH?
  - `push_artifacts(host: str, items: list[dict[str, object]], local_home: str, remote_home: str, ssh_timeout: float, rsync_io_timeout: float, *, quiet: bool, dry_run: bool) -> tuple[int, int]` — Push local ``grill/`` artifacts to ``host``.
  - `pull_artifacts(host: str, items: list[dict[str, object]], local_home: str, remote_home: str, ssh_timeout: float, rsync_io_timeout: float, *, quiet: bool, dry_run: bool) -> tuple[int, int]` — Pull ``grill/`` artifacts from ``host`` to local.
  - `assert_artifact_contract(merged: list[dict[str, object]], local_home: str) -> None` — Guard the path-form contract: merged is in *local* form.
  - `warn_nonlocal_related_paths(items: list[dict[str, object]], local_home: str) -> None` — Warn once if a merged ``grill/`` path was excluded by the resolve guard.
  - `artifact_preview(merged: list[dict[str, object]], local_home: str, remote_home: str, host: str, *, quiet: bool) -> None` — Print the would-transfer artifact set (no network I/O).
  - `merge_item(item_id: str, base_item: dict[str, object] | None, local_item: dict[str, object] | None, remote_item: dict[str, object] | None, store: str) -> tuple[dict[str, object] | None, dict[str, object] | None]` — Run the per-item 3-way merge (cases 0-6 of the plan).
  - `merge_store(base_list: list[dict[str, object]] | None, local_list: list[dict[str, object]], remote_list: list[dict[str, object]], store: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]` — Merge one store (items.json or pending_items.json) across all ids.
  - `merge_runs(local_runs: list[dict[str, object]], remote_runs: list[dict[str, object]]) -> list[dict[str, object]]` — Union two run-evidence lists by ``run_id`` — the runs.jsonl merge rule.
  - `compute_sync(base_items: list[dict[str, object]] | None, base_pending: list[dict[str, object]] | None, local_items: list[dict[str, object]], local_pending: list[dict[str, object]], remote_items: list[dict[str, object]], remote_pending: list[dict[str, object]], *, local_runs: list[dict[str, object]] | None = None, remote_runs: list[dict[str, object]] | None = None) -> SyncComputation` — Run the full merge: per-store 3-way merge, then graph integrity, then write-need.
  - `local_commit(local_schema: dict[str, object], result: SyncComputation, local_items_raw: list[dict[str, object]], local_pending_raw: list[dict[str, object]], base_items: list[dict[str, object]] | None, base_pending: list[dict[str, object]] | None, host: str | None = None) -> int | None` — Perform the three independently-conditioned writes, in crash-safe order.
  - `ssh_run(host: str, remote_script: str, remote_args: list[str], ssh_timeout: float, input_bytes: bytes | None = None) -> bytes` — Run ``remote_script`` on ``host`` over SSH, bounded against a hung network.
  - `ssh_export(host: str, remote_script: str, ssh_timeout: float) -> dict[str, object]`
  - `ssh_import(host: str, remote_script: str, ssh_timeout: float, items: list[dict[str, object]], pending: list[dict[str, object]], runs: list[dict[str, object]], schema: dict[str, object], if_rev: int) -> None`
  - `print_diff(result: SyncComputation, local_items: list[dict[str, object]], local_pending: list[dict[str, object]], remote_items: list[dict[str, object]], remote_pending: list[dict[str, object]], local_rev: int, remote_rev: int, header: str, quiet: bool = False) -> None`
  - `build_parser() -> argparse.ArgumentParser`
- Subcommand handlers: `cmd_export`, `cmd_import`, `cmd_status`, `cmd_sync`
- Tested by: `claude/scripts/test_dev_status_sync.py`

### `claude/scripts/gen_core_instructions.py`

gen_core_instructions.py — compose CORE_INSTRUCTIONS.md + personal-overlay.md into claude/global-instructions.md.

- Installed at: `~/.claude/scripts/gen_core_instructions.py` (all harnesses)
- Entrypoint: not executable, `#!/usr/bin/env python3`
- CLI (`argparse`): compose CORE_INSTRUCTIONS.md + personal-overlay.md into global-instructions.md
  - `--quiet/-q`
  - `--verbose/-v`
  - `--check` — exit 1 (with a diff-free notice on stderr) if the output is stale
  - `--stdout` — print the composed file, write nothing
  - `--repo-root` — repository root (default: inferred from this script's path)
- Explicit exit codes: `1`, `2`
- Depends on: `cli_common.py`
- Public functions:
  - `compose(repo_root: Path) -> str` — Read CORE_INSTRUCTIONS.md + personal-overlay.md, return the composed text.
  - `default_repo_root() -> Path` — Return the repo root inferred from this script's real location.
- Tested by: `claude/scripts/test_gen_core_instructions.py`

### `claude/scripts/gen_interfaces.py`

gen_interfaces.py — regenerate INTERFACES.md mechanically from the sources.

- Installed at: `~/.claude/scripts/gen_interfaces.py` (all harnesses)
- Entrypoint: not executable, `#!/usr/bin/env python3`
- CLI (`argparse`): regenerate INTERFACES.md from the harness sources
  - `--quiet/-q`
  - `--verbose/-v`
  - `--check` — exit 3 (with a report on stderr) if a doc's shown command example no longer matches its script, else exit 1 if the file on disk is stale
  - `--stdout` — print the document, write nothing
  - `--update-fingerprints` — recompute and record every in-scope script's contract fingerprint in contract_fingerprints.json, accepting its current behavior as the new baseline — run this only after re-reading the --check diff, never blind
  - `--repo-root` — repository root (default: inferred from this script's path)
  - `--output`
- Explicit exit codes: `1`, `2`, `3`
- Depends on: `cli_common.py`
- Public classes:
  - `class CliArgument` — One ``add_argument`` call, reduced to what a reader needs.
  - `class Subcommand` — One ``add_parser`` call and the arguments attached to it.
  - `class CliSpec` — The argparse surface of a module.
  - `class ApiSymbol` — A public module-level function or class.
  - `class LinkTarget` — One ``links.toml`` destination for a repo file, plus the gates on it.
  - `class ModuleInterface` — Everything statically known about one Python module.
  - `class HelperSpec` — A module function that adds arguments to a parser passed as its first arg.
  - `class DocInvocation` — One doc's code-span example of running a target script.
  - `class DriftProblem` — One doc invocation using a subcommand/flag the script doesn't have.
  - `class FingerprintProblem` — One contract-fingerprint failure for ``--check`` to report.
- Public functions:
  - `first_paragraph(text: str | None) -> str` — Collapse the first blank-line-delimited paragraph of ``text`` to one line.
  - `first_sentence(text: str | None) -> str | None` — Return the first sentence of ``text``, or ``None`` when it is empty.
  - `attr_path(node: ast.expr) -> str` — Render a dotted attribute/name expression, or ``""`` for anything else.
  - `literal_str(node: ast.expr | None, bindings: dict[str, str]) -> str | None` — Resolve ``node`` to a string, substituting ``bindings`` for bare names.
  - `literal_scalar(node: ast.expr | None) -> str | None` — Render a simple constant (str/int/float/bool/None) for display.
  - `literal_sequence(node: ast.expr | None) -> list[str] | None` — Render a literal list/tuple of constants, or ``None`` if it is dynamic.
  - `keyword_map(call: ast.Call) -> dict[str, ast.expr]` — Return the call's keyword arguments as a name -> node mapping.
  - `linear_statements(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]` — Yield statements in source order, descending into blocks but not defs.
  - `render_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str` — Render ``def`` as a one-line signature with annotations intact.
  - `render_arg(arg: ast.arg, default: ast.expr | None) -> str` — Render a single parameter with its annotation and default.
  - `render_class(node: ast.ClassDef) -> str` — Render a class header, bases included.
  - `is_exception(node: ast.ClassDef) -> bool` — Report whether a class derives from something named like an exception.
  - `parser_subclasses(tree: ast.Module) -> set[str]` — Return module-level class names that subclass ``ArgumentParser``.
  - `is_parser_constructor(node: ast.expr, subclasses: set[str]) -> bool` — Report whether ``node`` constructs an :class:`argparse.ArgumentParser`.
  - `collect_parser_helpers(tree: ast.Module) -> dict[str, HelperSpec]` — Find functions whose first parameter is a parser they add arguments to.
  - `helper_bindings(spec: HelperSpec, call: ast.Call) -> dict[str, str]` — Bind a helper's parameters to the literal values at one call site.
  - `build_argument(call: ast.Call, bindings: dict[str, str]) -> CliArgument | None` — Turn one ``add_argument`` call into a displayable argument record.
  - `extract_cli(tree: ast.Module, module_doc: str) -> CliSpec | None` — Extract the argparse surface, or ``None`` when the module has no CLI.
  - `leaf_subcommand_paths(subcommands: list[Subcommand]) -> set[tuple[str, ...]]` — Return the paths with no other subcommand nested under them.
  - `match_leaf_handlers(tree: ast.Module, cli: CliSpec) -> tuple[dict[tuple[str, ...], str], list[tuple[str, ...]]]` — Match every leaf subcommand to its ``cmd_<path>`` handler's docstring.
  - `fingerprint_lines(cli: CliSpec, leaf_docstrings: dict[tuple[str, ...], str], module_purpose: str, exit_codes: list[int]) -> list[str]` — Render a script's behavior-carrying CLI surface as sorted, diffable lines.
  - `extract_env_vars(tree: ast.Module) -> list[str]` — Collect the environment variable names the module reads.
  - `extract_exit_codes(tree: ast.Module) -> list[int]` — Collect the literal integer codes passed to ``sys.exit``/``SystemExit``.
  - `extract_path_constants(tree: ast.Module) -> list[str]` — Collect module-level uppercase constants that name a filesystem location.
  - `extract_internal_imports(tree: ast.Module, siblings: set[str]) -> list[str]` — Collect imports that resolve to sibling modules in the same directory.
  - `extract_api(tree: ast.Module, cli: CliSpec | None) -> tuple[list[ApiSymbol], list[ApiSymbol], list[ApiSymbol], list[str]]` — Split module-level public definitions into exceptions/classes/functions.
  - `uses_raw_argv(tree: ast.Module) -> bool` — Report whether the module reads ``sys.argv`` directly.
  - `link_gates(entry: dict[str, object]) -> list[str]` — Render one links.toml entry's harness/platform/profile conditions.
  - `load_link_table(repo_root: Path) -> LinkTable` — Map each repo-relative source in links.toml to its symlink destinations.
  - `render_link_targets(targets: list[LinkTarget]) -> str` — Render a source's install destinations as one comma-joined phrase.
  - `read_frontmatter(path: Path) -> dict[str, str]` — Read a markdown file's leading ``---`` frontmatter as flat key/value pairs.
  - `find_tests(module: Path, repo_root: Path) -> list[str]` — Find test modules covering ``module``, by filename and by import.
  - `analyze_module(path: Path, repo_root: Path, siblings: set[str], links: LinkTable) -> ModuleInterface` — Parse one script and collect every statically visible interface fact.
  - `inline(text: str) -> str` — Flatten text to a single line safe to drop into a markdown bullet.
  - `render_cli(module: ModuleInterface, lines: list[str]) -> None` — Append the CLI section for one module.
  - `render_notes(argument: CliArgument) -> str` — Render an argument's help text and parenthesised extras, if any.
  - `render_module(module: ModuleInterface) -> list[str]` — Render one module's full section.
  - `render_command_matrix(repo_root: Path, links: LinkTable) -> list[str]` — Render the per-harness skill/command parity matrix from frontmatter.
  - `is_generated_artifact(relpath: str) -> bool` — Report whether a path is build output or a dotfile rather than a source.
  - `tracked_files(repo_root: Path) -> set[str] | None` — Return every git-tracked path under ``repo_root``, or None if unavailable.
  - `render_assets(repo_root: Path, links: LinkTable, tracked: set[str] | None = None) -> list[str]` — Render the non-Python, non-skill harness assets and where they install.
  - `discover_doc_paths(repo_root: Path) -> list[Path]` — Return every skill/command doc across the four harnesses, sorted.
  - `code_regions(text: str) -> list[str]` — Return every inline code span and fenced code block's inner text.
  - `tokenize_invocation_line(line: str) -> list[str]` — Shell-tokenize one line, after stripping argparse-usage brackets.
  - `invocation_tokens(tokens: list[str], script_basename: str) -> list[str] | None` — Return the token stream starting at ``script_basename``, or None.
  - `find_invocations(doc_paths: Sequence[Path], repo_root: Path, script_basename: str) -> list[DocInvocation]` — Scan every doc for code-span invocations of ``script_basename``.
  - `flag_takes_value(argument: CliArgument) -> bool` — Report whether ``argument`` consumes a following token as its value.
  - `validate_invocation(cli: CliSpec, tokens: list[str]) -> list[str]` — Walk one invocation's tokens against ``cli``; return problem strings.
  - `check_doc_drift(repo_root: Path, modules: Sequence[ModuleInterface]) -> tuple[list[DriftProblem], dict[str, dict[str, bool]]]` — Validate every doc's shown invocations against each module's ``CliSpec``.
  - `render_doc_drift_section(coverage: dict[str, dict[str, bool]]) -> list[str]` — Render the per-script per-doc coverage summary, sorted for determinism.
  - `load_contract_fingerprints(repo_root: Path) -> dict[str, list[str]]` — Read ``contract_fingerprints.json``, treating anything unreadable as empty.
  - `load_repo_modules(repo_root: Path, links: LinkTable) -> list[ModuleInterface]` — Parse every shared script under ``SCRIPTS_DIR`` into a ``ModuleInterface``.
  - `check_contract_fingerprints(repo_root: Path, modules: Sequence[ModuleInterface], coverage: dict[str, dict[str, bool]]) -> list[FingerprintProblem]` — Check every in-scope script's live fingerprint against the recorded one.
  - `write_contract_fingerprints(repo_root: Path) -> dict[str, list[str]]` — Recompute and write ``contract_fingerprints.json`` for ``--update-fingerprints``.
  - `extract_skill_mentions(source: Path, names: Sequence[str]) -> list[str]` — Return which other skill names this skill's own file text mentions.
  - `render_skill_graph_section(repo_root: Path) -> list[str]` — Render which skill mentions which other skills, by name, in its own text.
  - `build_document(repo_root: Path) -> str` — Build the complete INTERFACES.md text for ``repo_root``.
  - `build_document_and_drift(repo_root: Path) -> tuple[str, list[DriftProblem], list[FingerprintProblem]]` — Build INTERFACES.md text alongside the doc-drift and fingerprint problems.
  - `anchor(relpath: str) -> str` — Return the GitHub heading anchor for a module section.
  - `default_repo_root() -> Path` — Return the repo root inferred from this script's real location.
- Subcommand handlers: `cmd_function_name`
- Tested by: `claude/scripts/test_gen_interfaces.py`

### `claude/scripts/opencode_skills_sync_activity.py`

Print opencode-skills-sync's pause state and last known snapshot commit, so a session can tell whether the daemon is running and how current its mirror is -- mirrors watchcommit_activity.py's SessionStart banner role.

- Installed at: `~/.claude/scripts/opencode_skills_sync_activity.py` (not on work)
- Entrypoint: executable, `#!/usr/bin/env python3`
- CLI: none (library module).
- Public functions:
  - `report(dest_worktree: Path) -> str`
- Tested by: `claude/scripts/test_opencode_skills_sync_activity.py`

### `claude/scripts/settings_seed_drift_check.py`

SessionStart hook + CLI: detect (and optionally fix) drift between the live ``~/.claude/settings.json`` / ``~/.config/opencode/opencode.jsonc`` / (under WSL) the Windows-side VS Code ``settings.json`` and ``keybindings.json`` and their seeds in the dotfiles repo.

- Installed at: `~/.claude/scripts/settings_seed_drift_check.py` (all harnesses)
- Entrypoint: executable, `#!/usr/bin/env python3`
- CLI (`argparse`): no `description=` set
  - `--quiet/-q`
  - `--verbose/-v`
- Subcommands:
  - `check`
  - `fix`
  - `sync-to-seed [--dotfiles-root <DOTFILES_ROOT>]`
  - `push-vscode [--dotfiles-root <DOTFILES_ROOT>] [--yes]`
- Filesystem constants:
  - `HOME = Path.home()`
  - `DOTFILES = Path(__file__).resolve().parents[2]`
  - `PROFILE_MARKER = HOME / '.local' / 'state' / 'dotfiles' / 'profile'`
- Depends on: `cli_common.py`
- Exceptions:
  - `class DriftCheckError(Exception)` — Raised when drift checking can't proceed (parse failure, not a missing file).
- Public functions:
  - `json_key_drift(seed: dict[str, object], live: dict[str, object]) -> list[str]` — Return the top-level keys whose values differ between seed and live.
  - `opencode_bypass_drift(seed: dict[str, object], live: dict[str, object]) -> list[str]` — Return allowlist-bypass bash patterns present live but not in the seed.
  - `resolve_profile() -> str` — Return "work" if this machine is work-provisioned, else "personal".
  - `settings_seed_path(root: Path | None = None) -> Path` — Return the seed settings.json path for this machine's profile, under ``root`` (default the ``DOTFILES`` module constant — resolved at call time, not bound at import, so callers that don't pass ``root`` still pick up a patched/overridden ``DOTFILES``).
  - `opencode_seed_path(root: Path | None = None) -> Path | None` — Return the opencode.jsonc seed path under ``root``, or None on a work machine.
  - `vscode_seed_path(name: str, root: Path | None = None) -> Path` — Return the seed path for a VS Code file (``settings.json`` or ``keybindings.json``) under ``root``.
  - `settings_drift(seed: Path, live: Path) -> list[str]` — Return the non-cosmetic settings.json keys that diverged, or [] if either file is missing.
  - `opencode_drift(seed: Path, live: Path) -> str` — Return a drift description for opencode.jsonc non-cosmetic keys, or "".
  - `vscode_drift(seed: Path, live: Path) -> str` — Describe how a live VS Code settings.json/keybindings.json diverged from its seed, or "" if there's nothing to compare or nothing drifted.
- Subcommand handlers: `cmd_check`, `cmd_fix`, `cmd_sync_to_seed`, `cmd_push_vscode`
- Tested by: `claude/scripts/test_settings_seed_drift_check.py`, `test/test_install.py`

### `claude/scripts/watchcommit_activity.py`

Print watchcommit's last known background pull/commit/push, so a session (or wc-status) can tell daemon-driven git state changes from manual ones instead of only seeing a clean/up-to-date working tree.

- Installed at: `~/.claude/scripts/watchcommit_activity.py` (not on work)
- Entrypoint: executable, `#!/usr/bin/env python3`
- CLI: none (library module).
- Environment: `XDG_STATE_HOME`
- Filesystem constants:
  - `STATE_DIR = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local' / 'state'))) / 'watchcommit'`
  - `ACTIVITY_STATE_FILE = STATE_DIR / 'last-activity.json'`
- Tested by: nothing

---

## 2. Skill and command surface

Each harness gets a port of the same skill surface. Presence below is
the file existing in the repo; the description is the canonical
`claude/commands/` frontmatter.

| Skill | claude | copilot | opencode | agy | pi |
| --- | --- | --- | --- | --- | --- |


---

## 3. Other harness assets

Everything under the harness directories that is neither a shared script
(section 1) nor a skill document (section 2). A source with no
destination is either read by another file in the repo or seeded by
install.py rather than symlinked — `settings.json` and `opencode.jsonc`
are copy-once seeds for exactly that reason.

| Source | Installed at |
| --- | --- |
| `claude/CORE_INSTRUCTIONS.md` | not symlinked by `links.toml` |
| `claude/global-instructions.md` | `~/.claude/CLAUDE.md` (claude), `~/.copilot/copilot-instructions.md` (copilot), `~/.gemini/GEMINI.md` (agy), `~/.pi/agent/AGENTS.md` (pi) |
| `claude/output-styles/PlainEngineer.md` | `~/.claude/output-styles/PlainEngineer.md` (claude) |
| `claude/personal-overlay.md` | not symlinked by `links.toml` |
| `claude/scripts/AGENTS.md` | not symlinked by `links.toml` |
| `claude/scripts/CLAUDE.md` | not symlinked by `links.toml` |
| `claude/scripts/contract_fingerprints.json` | not symlinked by `links.toml` |
| `claude/settings.json` | not symlinked by `links.toml` |
| `claude/settings.work.json` | not symlinked by `links.toml` |
| `opencode/opencode.jsonc` | not symlinked by `links.toml` |

---

## 4. Installer entrypoints

Not harness code, but the plumbing that puts everything above in
place. `install.sh` is a POSIX-sh bootstrap with no interface of its
own: it locates a Python 3.12+ interpreter and execs `install.py`,
forwarding argv unchanged.

### `install.py`

install.py — dotfiles + AI-harness provisioner for macOS and Linux/WSL.

- Installed at: not symlinked by `links.toml`
- Entrypoint: executable, `#!/usr/bin/env python3`
- CLI (`argparse`): no `description=` set
  - `--quiet/-q`
  - `--verbose/-v`
  - `--profile` (default: personal)
  - `--harness`
  - `--rollback`
  - `--wipe`
  - `--force`
  - `--dry-run`
  - `--no-nvim-pin`
  - `--reseed`
  - `--adopt`
  - `--depart`
  - `--yes`
  - `--check-links`
  - `--report-uninstalled`
  - `-h/--help`
- Environment: `LOGNAME`, `NO_COLOR`, `PATH`, `TERM`, `USER`, `WSL_DISTRO_NAME`
- Filesystem constants:
  - `GLOBAL_GIT_HOOKS_PATH_KEY = 'core.hooksPath'`
- Explicit exit codes: `0`, `2`
- Public classes:
  - `class Palette` — ANSI colorizer that no-ops when color isn't appropriate.
  - `class Reporter` — Collects every step that didn't run, for the end-of-run summary.
  - `class Manifest` — Append-only JSON Lines history of every file mutation, across all runs.
  - `class Options` — Validated command-line options for one invocation.
  - `class Context` — Everything a step needs: paths, options, history, and the skip tally.
  - `class CommandResult` — Outcome of one external command: whether it succeeded, and its stdout.
  - `class LinkSpec` — One row of ``links.toml``: a repo file and where it gets linked.
  - `class ManagedDirSpec` — One row of ``links.toml``: a directory dotfiles owns exclusively.
  - `class ManagedService` — One systemd --user service this installer enables/disables/tracks.
- Public functions:
  - `color_enabled(stream: object) -> bool` — Return whether ANSI codes should be emitted to ``stream``.
  - `detect_wsl(system: str) -> bool` — Return whether this is a WSL kernel (as opposed to native Linux).
  - `build_context(opts: Options, dotfiles: Path | None = None) -> Context` — Assemble a :class:`Context` for a real run on this machine.
  - `parse_args(argv: Sequence[str]) -> Options` — Parse and validate the command line.
  - `run_command(cmd: Sequence[str] | str, *, shell: bool = False, capture: bool = False) -> CommandResult` — Run an external command, returning success rather than raising.
  - `have(executable: str) -> bool` — Return whether ``executable`` is on PATH.
  - `install_mac_packages(ctx: Context) -> None` — Bootstrap Homebrew if needed, then install the formulae and casks.
  - `install_linux_packages(ctx: Context) -> None` — Install everything the Linux/WSL branch owns: distro packages and extras.
  - `install_node(ctx: Context) -> None` — Install NVM and a Node LTS — only for the harnesses that need npm.
  - `install_npm_harness(ctx: Context, harness: str, label: str, package: str) -> None` — Install one npm-distributed harness CLI, if it was selected.
  - `load_links(path: Path) -> list[LinkSpec]` — Parse ``links.toml`` into an ordered list of link specs.
  - `load_managed_dirs(path: Path) -> list[ManagedDirSpec]` — Parse the ``[[managed_dir]]`` rows declaring directories we own exclusively.
  - `link_applies(spec: LinkSpec, ctx: Context) -> bool` — Return whether ``spec`` should be linked for this run's machine/options.
  - `expand_dest(dest: str, home: Path) -> Path` — Expand a ``links.toml`` destination against ``home``.
  - `iter_concrete_links(spec: LinkSpec, ctx: Context) -> Iterator[tuple[Path, Path, str]]` — Expand one ``links.toml`` row into concrete ``(src, dest, relative_src)`` triples.
  - `gather_links(ctx: Context, specs: Sequence[LinkSpec]) -> list[tuple[Path, Path, str, bool]]` — Expand every ``links.toml`` row into concrete triples, once per run.
  - `symlink(ctx: Context, src: Path, dest: Path) -> bool` — Link ``dest`` → ``src``, backing up whatever non-symlink is in the way.
  - `install_symlinks(ctx: Context, links: Sequence[tuple[Path, Path, str, bool]]) -> None` — Link every applicable expanded ``links.toml`` entry.
  - `json_key_drift(seed: dict[str, object], live: dict[str, object]) -> list[str]` — Return the top-level keys whose values differ between seed and live.
  - `opencode_bypass_drift(seed: dict[str, object], live: dict[str, object]) -> list[str]` — Return allowlist-bypass bash patterns present live but not in the seed.
  - `describe_settings_drift(seed: Path, live: Path) -> str` — Describe how a live settings.json diverged from its seed.
  - `describe_opencode_drift(seed: Path, live: Path) -> str` — Describe how a live opencode.jsonc diverged from its seed.
  - `describe_vscode_drift(seed: Path, live: Path) -> str` — Describe how a live VS Code settings/keybindings file diverged from its seed.
  - `seed_vscode_settings(ctx: Context) -> list[tuple[str, tuple[str, str]]]` — Seed the Windows-side VS Code settings.json and keybindings.json under WSL.
  - `seed_file(ctx: Context, seed: Path, dest: Path, *, skip_label: str, drift: Callable[[Path, Path], str], adopt_drift: Callable[[str, str], str] | None = None, adopt_blocker: Callable[[Context, Path, Path, str, str], str | None] | None = None) -> str` — Copy ``seed`` to ``dest`` once, or report drift if it's already there.
  - `seed_claude_settings(ctx: Context) -> tuple[str, str]` — Seed ~/.claude/settings.json, if Claude Code was selected.
  - `seed_pi_settings(ctx: Context) -> tuple[str, str]` — Seed ~/.pi/agent/settings.json, if Pi was selected.
  - `seed_opencode_config(ctx: Context) -> tuple[str, str]` — Seed ~/.config/opencode/opencode.jsonc, if opencode was selected.
  - `capture_service_baseline(ctx: Context) -> None` — Capture every managed service's service/linger state, immediately before :func:`enable_managed_services` runs — capturing any later would record the post-install enabled state as baseline and departure would never disable anything.
  - `enable_managed_services(ctx: Context) -> None` — Enable and start every managed systemd --user unit (Linux, non-work).
  - `capture_git_hooks_path_baseline(ctx: Context) -> None` — Capture the pre-existing global ``core.hooksPath``, immediately before :func:`install_global_git_hooks_path` runs -- capturing any later would record dotfiles' own already-set value as if it were the original, which would make departure "restore" dotfiles' own path instead of the true pre-dotfiles value.
  - `install_global_git_hooks_path(ctx: Context) -> None` — Point global ``core.hooksPath`` at ``githooks-global/``, so every repo without its own local override picks up the no-commit-on-main hook.
  - `load_watchcommit_agent(ctx: Context) -> None` — (Re)load watchcommit's launchd agent (macOS, non-work).
  - `import_rectangle_prefs(ctx: Context) -> None` — Import the repo's Rectangle window-manager preferences.
  - `set_caps_lock_to_escape(ctx: Context) -> None` — Remap Caps Lock to Escape by rewriting the ByHost GlobalPreferences plist.
  - `install_vim_plug(ctx: Context) -> None` — Download vim-plug into ~/.vim/autoload, if it isn't there already.
  - `parse_neovim_version(output: str) -> tuple[int, int] | None` — Extract ``(major, minor)`` from ``nvim --version`` output.
  - `neovim_runtime_ok() -> bool` — Whether the Neovim binary on PATH can actually resolve its Lua runtime.
  - `bootstrap_neovim(ctx: Context) -> None` — Sync the vendored Neovim config's plugins with lazy.nvim.
  - `capture_departure_baseline(ctx: Context, specs: Sequence[LinkSpec]) -> None` — Capture this run's departure baseline layer before any install step runs.
  - `write_profile_marker(ctx: Context) -> None` — Mark this machine as work-provisioned, so later plain runs are guarded.
  - `work_guard_blocks(ctx: Context) -> bool` — Return whether a plain personal run must be refused on this machine.
  - `do_rollback(ctx: Context) -> int` — Reverse every file mutation recorded across every past run.
  - `print_summary(ctx: Context, settings: tuple[str, str], opencode: tuple[str, str], vscode: Sequence[tuple[str, tuple[str, str]]] = (), pi_settings: tuple[str, str] = ('', '')) -> None` — Print the loud end-of-run summary: skips, drift, and next steps.
  - `build_preflight_report(ctx: Context) -> dict[str, depart.Classification] | None` — Classify every tracked ownership key, or None if there's no baseline.
  - `build_package_preflight(ctx: Context) -> list[depart.PackageClassification] | None` — Classify every requested/introduced package, or None if there's no baseline.
  - `execute_service_phase(ctx: Context, baseline: depart.Baseline, ledger: depart.DepartureLedger) -> None` — Disable+stop every owned managed service, then reconcile linger once.
  - `execute_gitconfig_phase(ctx: Context, baseline: depart.Baseline, ledger: depart.DepartureLedger) -> None` — Restore the pre-dotfiles global core.hooksPath value, if this installer owns the current value.
  - `execute_file_symlink_phase(ctx: Context, baseline: depart.Baseline, report: dict[str, depart.Classification], ledger: depart.DepartureLedger) -> None` — Execute every owned ``file:``/``symlink:`` action, in pinned order.
  - `execute_directory_phase(ctx: Context, baseline: depart.Baseline, report: dict[str, depart.Classification], ledger: depart.DepartureLedger) -> None` — Execute every owned ``directory:`` action, deepest-path-first.
  - `execute_runtime_phase(ctx: Context, report: dict[str, depart.Classification], ledger: depart.DepartureLedger) -> None` — Remove the NVM root wholesale, if owned and not already done.
  - `live_package_snapshots(baseline: depart.Baseline) -> dict[str, dict[str, str] | None]` — Fresh probe results for every manager appearing in recorded transactions.
  - `execute_package_phase(ctx: Context, baseline: depart.Baseline, ledger: depart.DepartureLedger) -> bool` — Remove/downgrade owned packages, reverse transactions order.
  - `execute_departure(ctx: Context, baseline: depart.Baseline, report: dict[str, depart.Classification]) -> depart.DepartureLedger` — Perform every safe ``owned`` action, retry-safe via the departure ledger.
  - `do_depart(ctx: Context) -> int` — Preview and execute a pristine-state departure.
  - `do_check_links(ctx: Context) -> int` — Audit the live symlinks against ``links.toml`` and report, changing nothing.
  - `run_install(ctx: Context, specs: Sequence[LinkSpec]) -> int` — Run every install step in order and return the process exit status.
- Tested by: `test/test_install.py`

### `depart.py`

Pristine-state departure mode: baseline capture and ownership tracking.

- Installed at: not symlinked by `links.toml`
- Entrypoint: not executable, `#!/usr/bin/env python3`
- CLI: none (library module).
- Public classes:
  - `class Layer` — One capture pass: a timestamp and the ownership-key records from it.
  - `class Baseline` — The full departure baseline: immutable first layer, later supplements.
  - `class Transaction` — One package-manager operation's before/after state and provenance.
  - `class Classification`
  - `class DepartureLedger` — Crash-safe, fsynced-per-entry record of completed departure actions.
  - `class LockInfo`
  - `class PackageClassification` — One requested-or-introduced package's departure classification.
- Public functions:
  - `file_key(path: Path) -> str`
  - `symlink_key(path: Path) -> str`
  - `directory_key(path: Path) -> str`
  - `package_key(manager: str, name: str) -> str`
  - `service_key(manager: str, name: str) -> str`
  - `runtime_key(root: Path) -> str`
  - `gitconfig_key(name: str) -> str`
  - `key_type(key: str) -> str` — Return the ``type`` half of an ownership key string.
  - `capture_file(path: Path, *, blob_dir: Path | None = None) -> dict[str, object]` — Capture a ``file:`` record: present (with size+sha256), absent, or unknown.
  - `capture_symlink(path: Path) -> dict[str, object]` — Capture a ``symlink:`` record: present (with target text), absent, unknown.
  - `capture_directory(path: Path) -> dict[str, object]` — Capture a ``directory:`` record: present, absent, or unknown.
  - `ancestor_directories(path: Path, home: Path) -> list[Path]` — Every directory strictly between ``path``'s parent and ``home``.
  - `capture_tree_manifest(root: Path) -> dict[str, object]` — Full recursive manifest for a small, fully installer-controlled tree.
  - `tree_manifest_matches(root: Path, recorded: dict[str, object]) -> bool` — Whether ``root``'s current tree still matches a recorded tree manifest.
  - `capture_runtime_nvm(home: Path) -> dict[str, object]` — Capture the lightweight ``runtime:<root>`` record for NVM/Node.
  - `runtime_nvm_diverged(home: Path, recorded: dict[str, object]) -> bool` — Whether live NVM state has diverged from its recorded top-level markers.
  - `blob_path(state_dir: Path, digest: str) -> Path`
  - `write_blob(state_dir: Path, content: bytes) -> str` — Write a content-addressed blob, returning its SHA-256 hex digest.
  - `read_blob(state_dir: Path, digest: str) -> bytes | None` — Read a blob's content back, or None if it's missing/unreadable.
  - `record_installed_tree(baseline: Baseline, root: Path) -> None` — Snapshot ``root`` as the installer just produced it.
  - `installed_tree_verdict(baseline: Baseline, root: Path) -> str` — Classify a wholly installer-owned tree for safe wholesale removal.
  - `remove_manifest_tree(baseline: Baseline, path: Path) -> str` — Remove ``path`` wholesale, but only if its :func:`installed_tree_verdict` is ``TREE_UNCHANGED`` — the departure-time policy: refuse unless provably unchanged, since refusing is departure's safe terminal outcome.
  - `baseline_path(state_dir: Path) -> Path`
  - `baseline_to_dict(baseline: Baseline) -> dict[str, object]`
  - `baseline_from_dict(data: dict[str, object]) -> Baseline`
  - `save_baseline(state_dir: Path, baseline: Baseline) -> None` — Durably write ``baseline.json`` via a temp-file-then-replace swap.
  - `load_baseline(state_dir: Path) -> Baseline | None` — Load ``baseline.json``, or None if it's missing, empty, or unparseable.
  - `dpkg_query_command() -> list[str]` — List every installed apt package and its version, tab-separated.
  - `parse_dpkg_query(output: str) -> dict[str, str]` — Parse :func:`dpkg_query_command` output into ``{name: version}``.
  - `rpm_qa_command() -> list[str]` — List every installed dnf/rpm package and its version, tab-separated.
  - `parse_rpm_qa(output: str) -> dict[str, str]` — Parse :func:`rpm_qa_command` output into ``{name: version}``.
  - `npm_ls_global_command() -> list[str]` — List globally installed npm packages as JSON.
  - `parse_npm_ls_global(output: str) -> dict[str, str]` — Parse :func:`npm_ls_global_command` JSON output into ``{name: version}``.
  - `uv_tool_list_command() -> list[str]` — List installed ``uv tool`` packages and their versions.
  - `parse_uv_tool_list(output: str) -> dict[str, str]` — Parse :func:`uv_tool_list_command` output into ``{name: version}``.
  - `apt_remove_command(package: str) -> list[str]` — Pinned apt removal command — never ``purge`` or ``autoremove``.
  - `dnf_remove_command(package: str) -> list[str]` — Pinned dnf removal command — never ``autoremove``.
  - `npm_uninstall_command(package: str) -> list[str]`
  - `uv_tool_uninstall_command(name: str) -> list[str]`
  - `apt_downgrade_command(package: str, version: str) -> list[str]`
  - `dnf_downgrade_command(package: str, version: str) -> list[str]`
  - `apt_rdepends_command(package: str) -> list[str]`
  - `dnf_whatrequires_command(package: str) -> list[str]`
  - `classify_rdepends_result(ok: bool, stdout: str) -> str` — Classify a reverse-dependency probe's outcome for removal eligibility.
  - `transaction_to_dict(txn: Transaction) -> dict[str, object]`
  - `transaction_from_dict(data: dict[str, object]) -> Transaction`
  - `record_transaction(baseline: Baseline, *, manager: str, requested: list[str], before: dict[str, str], after: dict[str, str], captured_at: str, epoch: dict[str, str] | None = None) -> Transaction` — Build a :class:`Transaction`, append it to ``baseline``, and return it.
  - `earliest_recorded_version(baseline: Baseline, manager: str, name: str) -> str | None` — ``name``'s true pre-install version for ``manager``, if ever recorded.
  - `downgrade_candidates(baseline: Baseline, manager: str, name: str) -> list[str]` — Versions to try, in order, when downgrading an installer-upgraded package.
  - `classify_ownership_key(key: str, recorded: dict[str, object] | None, live: dict[str, object]) -> Classification` — Classify one ownership key for the departure preflight report.
  - `reclassify_rc_file(recorded: dict[str, object] | None, baseline_content: bytes | None, live_content: bytes | None) -> Classification | None` — Override the generic classification for one rc file, if warranted.
  - `reclassify_symlink_destination_pair(file_recorded: dict[str, object] | None, file_live: dict[str, object], symlink_recorded: dict[str, object] | None, symlink_live: dict[str, object]) -> tuple[Classification, Classification] | None` — Jointly reclassify a destination's ``file:``/``symlink:`` key pair.
  - `departure_ledger_path(state_dir: Path) -> Path`
  - `departure_lock_path(state_dir: Path) -> Path`
  - `parse_lock_text(text: str) -> LockInfo | None`
  - `read_lock(path: Path) -> LockInfo | None`
  - `process_start_time(pid: int) -> int | None` — Read a process's start-time field (field 22) from ``/proc/<pid>/stat``.
  - `is_lock_holder_alive(pid: int, recorded_start_time: int, *, start_time_fn: Callable[[int], int | None] = process_start_time) -> bool` — Whether the recorded lock holder is still running the same process.
  - `acquire_departure_lock(state_dir: Path, *, pid: int | None = None, start_time_fn: Callable[[int], int | None] = process_start_time) -> tuple[bool, LockInfo | None]` — Try to acquire the departure advisory lock.
  - `release_departure_lock(state_dir: Path, pid: int | None = None) -> None` — Release and unlink the lock, only if it's still recorded as ours.
  - `classify_package_transactions(baseline: Baseline, live_snapshots: dict[str, dict[str, str] | None]) -> list[PackageClassification]` — Classify every requested/introduced package, in departure removal order.
  - `build_service_record(*, enabled: bool | None, active: bool | None, linger: bool | None) -> dict[str, object]` — Build a ``service:`` record from already-probed values.
  - `classify_service(recorded: dict[str, object] | None, live: dict[str, object]) -> Classification` — Classify the watchcommit service/linger key.
  - `build_gitconfig_record(value: str | None) -> dict[str, object]` — Build a ``gitconfig:`` record from an already-read global config value.
  - `classify_gitconfig(recorded: dict[str, object] | None, live: dict[str, object], managed_value: str) -> Classification` — Classify a single global git config key this installer manages.
- Tested by: `test/test_depart.py`, `test/test_depart_transactions.py`, `test/test_install.py`

---

## 5. Skill/command doc contract coverage

For each backing script below, every skill/command doc that shows an
example of running it, and whether that example's subcommand and
flags still match the script's real CLI contract. A doc with no shown
invocation of a given script is not listed. `--check` exits `3` (not
`1`) when this section would change, since the fix is editing the
named doc, not regenerating this file.

---

## 6. Skill cross-reference graph

Built by scanning each `claude/commands/*.md` skill's own text for
whole-word mentions of the other skills' names (frontmatter
description included). This regenerates with the rest of the file,
so it cannot silently drift the way hand-written relationship notes
could — if a skill stops mentioning another, or starts mentioning a
new one, `--check` catches it the same as any other stale content.

| Skill | Mentions |
| --- | --- |
