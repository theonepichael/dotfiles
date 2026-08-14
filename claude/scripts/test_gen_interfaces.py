#!/usr/bin/env python3
"""Tests for gen_interfaces.py. Run with: python3 test_gen_interfaces.py

Exercises the extraction layer against small synthetic modules parsed with
`ast`, rather than asserting on INTERFACES.md's byte output — the document's
wording changes far more often than the facts pulled out of the source. Two
end-to-end tests are the exception: one parses the repo's real dev_status.py
(the module whose interface the hand-written INTERFACES.md got wrong), and one
asserts the committed INTERFACES.md is what the generator currently produces,
so a forgotten regeneration fails the suite instead of drifting quietly.

Nothing here imports or executes a harness module; every fixture is a string
parsed with ast.parse, exactly as the generator does it.
"""

import ast
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen_interfaces as gi

REPO_ROOT = Path(__file__).resolve().parents[2]


def parse(source: str) -> ast.Module:
    """Parse a dedented source fixture."""
    return ast.parse(textwrap.dedent(source))


def cli_of(source: str) -> gi.CliSpec:
    """Extract the CLI spec from a source fixture, failing if there is none."""
    tree = parse(source)
    spec = gi.extract_cli(tree, gi.first_paragraph(ast.get_docstring(tree)))
    assert spec is not None, "fixture was expected to define an argparse CLI"
    return spec


def subcommand(spec: gi.CliSpec, name: str) -> gi.Subcommand:
    """Return the subcommand with the given space-joined name."""
    for candidate in spec.subcommands:
        if candidate.name == name:
            return candidate
    raise AssertionError(
        f"no subcommand {name!r} in {[s.name for s in spec.subcommands]}"
    )


class TextHelperTests(unittest.TestCase):
    def test_first_paragraph_collapses_only_the_first_block(self) -> None:
        text = "line one\nline two\n\nsecond paragraph"
        self.assertEqual(gi.first_paragraph(text), "line one line two")

    def test_first_paragraph_of_nothing_is_empty(self) -> None:
        self.assertEqual(gi.first_paragraph(None), "")
        self.assertEqual(gi.first_paragraph("   "), "")

    def test_first_sentence_stops_at_terminal_punctuation(self) -> None:
        self.assertEqual(
            gi.first_sentence("Do the thing. Then do another thing."),
            "Do the thing.",
        )

    def test_first_sentence_without_punctuation_returns_whole_line(self) -> None:
        self.assertEqual(gi.first_sentence("no full stop here"), "no full stop here")

    def test_first_sentence_of_nothing_is_none(self) -> None:
        self.assertIsNone(gi.first_sentence(""))


class LiteralResolutionTests(unittest.TestCase):
    def resolve(self, expression: str, **bindings: str) -> str | None:
        node = ast.parse(expression, mode="eval").body
        return gi.literal_str(node, bindings)

    def test_plain_string(self) -> None:
        self.assertEqual(self.resolve("'hello'"), "hello")

    def test_bound_name(self) -> None:
        self.assertEqual(self.resolve("target", target="id"), "id")

    def test_unbound_name_is_unresolvable(self) -> None:
        self.assertIsNone(self.resolve("target"))

    def test_fstring_substitutes_bindings(self) -> None:
        self.assertEqual(
            self.resolve("f'required when <{name}> is numeric'", name="old_slug"),
            "required when <old_slug> is numeric",
        )

    def test_fstring_with_unresolvable_hole_is_unresolvable(self) -> None:
        self.assertIsNone(self.resolve("f'value is {compute()}'"))

    def test_concatenated_literals(self) -> None:
        self.assertEqual(self.resolve("'left ' + 'right'"), "left right")

    def test_runtime_call_is_unresolvable(self) -> None:
        self.assertIsNone(self.resolve("','.join(NAMES)"))

    def test_literal_sequence_of_constants(self) -> None:
        node = ast.parse("['a', 'b']", mode="eval").body
        self.assertEqual(gi.literal_sequence(node), ["a", "b"])

    def test_literal_sequence_of_a_call_is_none(self) -> None:
        node = ast.parse("sorted(VALID)", mode="eval").body
        self.assertIsNone(gi.literal_sequence(node))


class ArgumentRenderingTests(unittest.TestCase):
    def build(self, call_source: str, **bindings: str) -> gi.CliArgument:
        node = ast.parse(call_source, mode="eval").body
        assert isinstance(node, ast.Call)
        argument = gi.build_argument(node, bindings)
        assert argument is not None
        return argument

    def test_positional_uses_metavar_verbatim(self) -> None:
        argument = self.build("p.add_argument('id', metavar='<slug|N>')")
        self.assertEqual(argument.usage, "<slug|N>")
        self.assertEqual(argument.label, "id")

    def test_positional_without_metavar_is_angle_bracketed(self) -> None:
        self.assertEqual(self.build("p.add_argument('path')").usage, "<path>")

    def test_optional_positional_is_square_bracketed(self) -> None:
        argument = self.build("p.add_argument('decision_id', nargs='?')")
        self.assertEqual(argument.usage, "[<decision_id>]")

    def test_store_true_flag_takes_no_value(self) -> None:
        argument = self.build("p.add_argument('--apply', action='store_true')")
        self.assertEqual(argument.usage, "[--apply]")

    def test_required_flag_is_not_bracketed_and_is_noted(self) -> None:
        argument = self.build(
            "p.add_argument('--force', action='store_true', required=True)"
        )
        self.assertEqual(argument.usage, "--force")
        self.assertIn("required", argument.notes)

    def test_flag_value_falls_back_to_uppercased_dest(self) -> None:
        argument = self.build("p.add_argument('--focus-file', default=None)")
        self.assertEqual(argument.usage, "[--focus-file <FOCUS_FILE>]")

    def test_short_and_long_names_are_both_labelled(self) -> None:
        argument = self.build("p.add_argument('--session', '-s', default=None)")
        self.assertEqual(argument.label, "--session/-s")
        self.assertTrue(argument.usage.startswith("[--session"))

    def test_literal_choices_are_listed(self) -> None:
        argument = self.build("p.add_argument('--mode', choices=['a', 'b'])")
        self.assertIn("choices: a, b", argument.notes)

    def test_runtime_choices_are_flagged_not_guessed(self) -> None:
        argument = self.build("p.add_argument('--status', choices=sorted(VALID))")
        self.assertIn("choices computed at runtime", argument.notes)

    def test_non_none_default_is_noted(self) -> None:
        argument = self.build("p.add_argument('--retries', type=int, default=3)")
        self.assertIn("default: 3", argument.notes)

    def test_none_default_is_not_noted(self) -> None:
        argument = self.build("p.add_argument('--host', default=None)")
        self.assertEqual(argument.notes, [])

    def test_help_is_captured(self) -> None:
        argument = self.build("p.add_argument('--x', help='do the thing')")
        self.assertEqual(argument.help_text, "do the thing")


CLI_FIXTURE = """
    \"\"\"fixture.py — a small CLI.\"\"\"

    import argparse


    def _add_id_arg(parser, name="id"):
        parser.add_argument(name, metavar="<slug|N>")


    def _add_if_rev_arg(parser, id_name="id"):
        parser.add_argument(
            "--if-rev", type=int, default=None, metavar="<N>",
            help=f"required when <{id_name}> is numeric",
        )


    def main():
        parser = argparse.ArgumentParser(description="a small CLI")
        parser.add_argument("--verbose", action="store_true", help="be loud")
        sub = parser.add_subparsers(dest="cmd")

        sub.add_parser("render", help="render it")

        p = sub.add_parser("show", help="show one")
        _add_id_arg(p)

        p = sub.add_parser("rename", help="rename one")
        _add_id_arg(p, "old_slug")
        p.add_argument("new_slug")
        _add_if_rev_arg(p, "old_slug")

        nested = sub.add_parser("pending", help="nested group")
        nested_sub = nested.add_subparsers(dest="pending_cmd")
        nested_sub.add_parser("list", help="list them")
        q = nested_sub.add_parser("update", help="patch one")
        _add_id_arg(q)
"""


class CliExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = cli_of(CLI_FIXTURE)

    def test_root_description_and_global_option(self) -> None:
        self.assertEqual(self.spec.description, "a small CLI")
        self.assertEqual([o.label for o in self.spec.options], ["--verbose"])

    def test_all_subcommands_are_found_in_source_order(self) -> None:
        self.assertEqual(
            [s.name for s in self.spec.subcommands],
            ["render", "show", "rename", "pending", "pending list", "pending update"],
        )

    def test_bare_add_parser_without_assignment_is_registered(self) -> None:
        self.assertEqual(subcommand(self.spec, "render").help_text, "render it")

    def test_helper_added_arguments_are_attributed_to_the_caller(self) -> None:
        show = subcommand(self.spec, "show")
        self.assertEqual([a.usage for a in show.arguments], ["<slug|N>"])

    def test_helper_call_site_overrides_parameter_default(self) -> None:
        rename = subcommand(self.spec, "rename")
        if_rev = next(a for a in rename.arguments if a.label == "--if-rev")
        self.assertEqual(if_rev.help_text, "required when <old_slug> is numeric")

    def test_helper_default_is_used_when_call_site_omits_it(self) -> None:
        update = subcommand(self.spec, "pending update")
        self.assertEqual([a.usage for a in update.arguments], ["<slug|N>"])

    def test_argument_order_follows_source_order(self) -> None:
        rename = subcommand(self.spec, "rename")
        self.assertEqual(
            [a.usage for a in rename.arguments],
            ["<slug|N>", "<new_slug>", "[--if-rev <N>]"],
        )

    def test_nested_subparsers_get_a_joined_path(self) -> None:
        self.assertEqual(
            subcommand(self.spec, "pending list").path, ("pending", "list")
        )

    def test_description_can_come_from_the_module_docstring(self) -> None:
        spec = cli_of(
            """
            \"\"\"fixture docstring.\"\"\"

            import argparse


            def main():
                parser = argparse.ArgumentParser(description=__doc__)
            """
        )
        self.assertEqual(spec.description, "fixture docstring.")

    def test_module_without_argparse_has_no_cli(self) -> None:
        tree = parse("def helper():\n    return 1\n")
        self.assertIsNone(gi.extract_cli(tree, ""))

    def test_argument_parser_subclass_is_recognised(self) -> None:
        spec = cli_of(
            """
            import argparse


            class _Parser(argparse.ArgumentParser):
                pass


            def parse_args(argv):
                parser = _Parser(add_help=False)
                parser.add_argument("--profile", default="personal")
            """
        )
        self.assertEqual([o.label for o in spec.options], ["--profile"])
        self.assertIn("default: personal", spec.options[0].notes)

    def test_nested_helper_body_is_not_walked_as_caller_statements(self) -> None:
        # `add_flag`'s parameter is also named `p`; without skipping nested
        # function bodies its argument would land on whatever `p` points at.
        spec = cli_of(
            """
            import argparse


            def main():
                parser = argparse.ArgumentParser()
                sub = parser.add_subparsers()

                def add_flag(p):
                    p.add_argument("--session", help="which session")

                p = sub.add_parser("first")
                p = sub.add_parser("second")
                add_flag(p)
            """
        )
        self.assertEqual(subcommand(spec, "first").arguments, [])
        self.assertEqual(
            [a.label for a in subcommand(spec, "second").arguments], ["--session"]
        )


class ModuleFactExtractionTests(unittest.TestCase):
    def test_env_vars_from_every_access_form(self) -> None:
        tree = parse(
            """
            import os

            A = os.environ.get("ALPHA", "1")
            B = os.environ["BRAVO"]
            C = os.getenv("CHARLIE")
            D = os.environ.get(computed_name)
            """
        )
        self.assertEqual(gi.extract_env_vars(tree), ["ALPHA", "BRAVO", "CHARLIE"])

    def test_exit_codes_include_bare_exit_as_zero(self) -> None:
        tree = parse(
            """
            import sys

            def f():
                sys.exit(2)
                sys.exit()
                sys.exit(1)
                sys.exit(code)
            """
        )
        self.assertEqual(gi.extract_exit_codes(tree), [0, 1, 2])

    def test_raised_system_exit_counts_as_an_exit_code(self) -> None:
        tree = parse("def f():\n    raise SystemExit(2)\n")
        self.assertEqual(gi.extract_exit_codes(tree), [2])

    def test_path_constants_follow_derivation_chains(self) -> None:
        tree = parse(
            """
            from pathlib import Path

            DATA_DIR = Path.home() / ".claude"
            ITEMS_FILE = DATA_DIR / "items.json"
            lowercase = DATA_DIR / "ignored.json"
            UNRELATED = 42
            """
        )
        self.assertEqual(
            gi.extract_path_constants(tree),
            [
                "DATA_DIR = Path.home() / '.claude'",
                "ITEMS_FILE = DATA_DIR / 'items.json'",
            ],
        )

    def test_internal_imports_only_match_siblings(self) -> None:
        tree = parse(
            """
            import json
            import llm_backends
            from standup_adapters import ADAPTERS
            """
        )
        siblings = {"llm_backends", "standup_adapters", "grill"}
        self.assertEqual(
            gi.extract_internal_imports(tree, siblings),
            ["llm_backends", "standup_adapters"],
        )

    def test_uses_raw_argv(self) -> None:
        self.assertTrue(gi.uses_raw_argv(parse("import sys\nx = sys.argv[1]\n")))
        self.assertFalse(gi.uses_raw_argv(parse("x = 1\n")))


API_FIXTURE = """
    class BackendError(Exception):
        \"\"\"A backend failed. More detail here.\"\"\"


    class Session(TypedDict):
        \"\"\"One session.\"\"\"


    class _Private:
        pass


    def public_fn(a: int, b: str = "x") -> bool:
        \"\"\"Do a thing.\"\"\"


    def _private_fn() -> None:
        pass


    def main() -> None:
        pass


    def cmd_render(args) -> None:
        pass
"""


class ApiExtractionTests(unittest.TestCase):
    def test_symbols_are_split_by_kind(self) -> None:
        tree = parse(API_FIXTURE)
        exceptions, classes, functions, handlers = gi.extract_api(tree, gi.CliSpec())
        self.assertEqual(
            [s.signature for s in exceptions], ["class BackendError(Exception)"]
        )
        self.assertEqual([s.signature for s in classes], ["class Session(TypedDict)"])
        self.assertEqual(
            [s.signature for s in functions],
            ["public_fn(a: int, b: str = 'x') -> bool"],
        )
        self.assertEqual(handlers, ["cmd_render"])

    def test_summary_is_the_docstrings_first_sentence(self) -> None:
        tree = parse(API_FIXTURE)
        exceptions, _, _, _ = gi.extract_api(tree, gi.CliSpec())
        self.assertEqual(exceptions[0].summary, "A backend failed.")

    def test_handlers_stay_functions_when_there_is_no_cli(self) -> None:
        tree = parse(API_FIXTURE)
        _, _, functions, handlers = gi.extract_api(tree, None)
        self.assertEqual(handlers, [])
        self.assertIn("cmd_render(args) -> None", [s.signature for s in functions])

    def test_signature_renders_keyword_only_and_varargs(self) -> None:
        tree = parse("def f(a, *rest, model: str | None = None, **kw) -> str: ...\n")
        node = tree.body[0]
        assert isinstance(node, ast.FunctionDef)
        self.assertEqual(
            gi.render_signature(node),
            "f(a, *rest, model: str | None = None, **kw) -> str",
        )

    def test_signature_renders_bare_star_for_keyword_only(self) -> None:
        tree = parse("def f(prompt: str, *, timeout: float) -> str: ...\n")
        node = tree.body[0]
        assert isinstance(node, ast.FunctionDef)
        self.assertEqual(
            gi.render_signature(node), "f(prompt: str, *, timeout: float) -> str"
        )


class FrontmatterTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        target = directory / "doc.md"
        target.write_text(text, encoding="utf-8")
        return target

    def test_quoted_and_unquoted_values(self) -> None:
        path = self.write('---\nname: dashboard\ndescription: "shows it"\n---\nbody\n')
        self.assertEqual(
            gi.read_frontmatter(path), {"name": "dashboard", "description": "shows it"}
        )

    def test_missing_frontmatter_is_empty(self) -> None:
        self.assertEqual(gi.read_frontmatter(self.write("# just a heading\n")), {})

    def test_unterminated_frontmatter_is_empty(self) -> None:
        self.assertEqual(gi.read_frontmatter(self.write("---\nname: x\nbody\n")), {})


class LinkTableTests(unittest.TestCase):
    def load(self, toml_text: str) -> gi.LinkTable:
        root = Path(tempfile.mkdtemp())
        (root / "links.toml").write_text(textwrap.dedent(toml_text), encoding="utf-8")
        return gi.load_link_table(root)

    def test_one_source_with_several_destinations(self) -> None:
        table = self.load(
            """
            [[link]]
            src = "claude/CLAUDE.md"
            dest = "~/.claude/CLAUDE.md"
            harness = "claude"

            [[link]]
            src = "claude/CLAUDE.md"
            dest = "~/.gemini/GEMINI.md"
            harness = "agy"
            """
        )
        self.assertEqual(
            [t.dest for t in table["claude/CLAUDE.md"]],
            ["~/.claude/CLAUDE.md", "~/.gemini/GEMINI.md"],
        )

    def test_repeated_destination_merges_its_gates(self) -> None:
        table = self.load(
            """
            [[link]]
            src = "copilot/hooks/session-start.json"
            dest = "~/.copilot/hooks/session-start.json"
            harness = "copilot"
            platform = "mac"

            [[link]]
            src = "copilot/hooks/session-start.json"
            dest = "~/.copilot/hooks/session-start.json"
            harness = "copilot"
            platform = "linux"
            """
        )
        targets = table["copilot/hooks/session-start.json"]
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].gates, ["copilot", "mac", "linux"])

    def test_profile_exclude_becomes_a_gate(self) -> None:
        table = self.load(
            """
            [[link]]
            src = "claude/scripts/watchcommit_activity.py"
            dest = "~/.claude/scripts/watchcommit_activity.py"
            profile_exclude = ["work"]
            """
        )
        target = table["claude/scripts/watchcommit_activity.py"][0]
        self.assertEqual(target.gates, ["not on work"])

    def test_missing_links_file_is_empty(self) -> None:
        self.assertEqual(gi.load_link_table(Path(tempfile.mkdtemp())), {})

    def test_rendering_ungated_and_unlinked_targets(self) -> None:
        self.assertEqual(
            gi.render_link_targets([gi.LinkTarget("~/x", [])]), "`~/x` (all harnesses)"
        )
        self.assertEqual(gi.render_link_targets([]), "not symlinked by `links.toml`")


class AssetFilterTests(unittest.TestCase):
    def test_build_output_and_dotfiles_are_excluded(self) -> None:
        self.assertTrue(gi.is_generated_artifact("claude/scripts/__pycache__/x.pyc"))
        self.assertTrue(gi.is_generated_artifact("claude/.DS_Store"))
        self.assertFalse(gi.is_generated_artifact("copilot/aliases.zsh"))


class RealSourceTests(unittest.TestCase):
    """Parse the actual scripts — the drift these tests exist to catch."""

    def spec_for(self, name: str) -> gi.CliSpec:
        source = (REPO_ROOT / gi.SCRIPTS_DIR / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        spec = gi.extract_cli(tree, gi.first_paragraph(ast.get_docstring(tree)))
        assert spec is not None
        return spec

    def test_dev_status_subcommands_are_enumerated(self) -> None:
        names = {s.name for s in self.spec_for("dev_status.py").subcommands}
        self.assertGreaterEqual(len(names), 20)
        self.assertLessEqual(
            {
                "render",
                "add",
                "start",
                "done",
                "review",
                "approve",
                "reject",
                "gate-set",
                "gate-pass",
                "block",
                "unblock",
                "prune",
                "recap",
                "pending add",
                "pending update",
                "pending list",
            },
            names,
        )

    def test_dev_status_helper_arguments_are_resolved(self) -> None:
        spec = self.spec_for("dev_status.py")
        approve = subcommand(spec, "approve")
        self.assertEqual(
            [a.usage for a in approve.arguments], ["<slug|N>", "[--if-rev <N>]"]
        )

    def test_grill_session_flag_reaches_every_subcommand_that_takes_it(self) -> None:
        spec = self.spec_for("grill.py")
        decide = subcommand(spec, "decide")
        self.assertIn("--session/-s", [a.label for a in decide.arguments])
        self.assertEqual([a.label for a in subcommand(spec, "list").arguments], [])

    def test_install_py_cli_is_found_through_its_parser_subclass(self) -> None:
        source = (REPO_ROOT / "install.py").read_text(encoding="utf-8")
        spec = gi.extract_cli(ast.parse(source), "")
        assert spec is not None
        labels = [option.label for option in spec.options]
        self.assertIn("--harness", labels)
        self.assertIn("--dry-run", labels)
        self.assertIn("--rollback", labels)

    def test_llm_backends_is_a_library_with_no_cli(self) -> None:
        source = (REPO_ROOT / gi.SCRIPTS_DIR / "llm_backends.py").read_text(
            encoding="utf-8"
        )
        self.assertIsNone(gi.extract_cli(ast.parse(source), ""))


class GeneratedDocumentTests(unittest.TestCase):
    def test_document_has_every_module_section(self) -> None:
        document = gi.build_document(REPO_ROOT)
        for module in (REPO_ROOT / gi.SCRIPTS_DIR).glob("*.py"):
            if module.name.startswith("test_"):
                continue
            self.assertIn(f"### `claude/scripts/{module.name}`", document)
        for name in gi.ROOT_ENTRYPOINTS:
            self.assertIn(f"### `{name}`", document)

    def test_install_py_is_credited_to_its_tests_in_the_test_directory(self) -> None:
        self.assertIn(
            "test/test_install.py", gi.find_tests(REPO_ROOT / "install.py", REPO_ROOT)
        )

    def test_committed_interfaces_md_is_not_stale(self) -> None:
        committed = (REPO_ROOT / gi.OUTPUT_NAME).read_text(encoding="utf-8")
        self.assertEqual(
            committed,
            gi.build_document(REPO_ROOT),
            f"{gi.OUTPUT_NAME} is stale — run "
            f"`python3 {gi.SCRIPTS_DIR}/gen_interfaces.py`",
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
