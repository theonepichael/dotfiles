import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from gen_shell_completion import (
    Option,
    _split_name_and_placeholder,
    _split_entry,
    parse_options,
    parse_commands,
    format_option,
    collect_goflag_sections,
    HARNESSES,
    generate,
    HarnessSpec,
    main,
)


class TestGenShellCompletion(unittest.TestCase):
    def test_split_name_and_placeholder_bracket(self):
        name, ph = _split_name_and_placeholder("--allow-tool[=tools...]")
        self.assertEqual(name, "--allow-tool")
        self.assertEqual(ph, "[=tools...]")

    def test_split_name_and_placeholder_space(self):
        name, ph = _split_name_and_placeholder("--effort <level>")
        self.assertEqual(name, "--effort")
        self.assertEqual(ph, "<level>")

    def test_split_entry_multiline_overflow(self):
        entry_lines = [
            "  --allowedTools, --allowed-tools <tools...>",
            '      Comma or space-separated list of tool names to allow (e.g. "Bash(git *) Edit")',
        ]
        defn, desc = _split_entry(entry_lines)
        self.assertEqual(defn, "--allowedTools, --allowed-tools <tools...>")
        self.assertEqual(
            desc,
            'Comma or space-separated list of tool names to allow (e.g. "Bash(git *) Edit")',
        )

    def test_parse_options_with_choices_and_multi_name(self):
        lines = [
            '  --effort, --reasoning-effort <level>  Set reasoning level (choices: "low", "medium", "high")',
            "  --add-dir <dir>                       Add a directory to allow tool access to",
            "  --allow-all                           Allow all permissions",
        ]
        opts = parse_options(lines)
        self.assertEqual(len(opts), 3)

        self.assertEqual(opts[0].names, ["--effort", "--reasoning-effort"])
        self.assertTrue(opts[0].takes_arg)
        self.assertEqual(opts[0].choices, ["low", "medium", "high"])

        self.assertEqual(opts[1].names, ["--add-dir"])
        self.assertTrue(opts[1].takes_arg)
        self.assertIsNone(opts[1].choices)

        self.assertEqual(opts[2].names, ["--allow-all"])
        self.assertFalse(opts[2].takes_arg)

    def test_format_option_specs(self):
        opt_simple = Option(
            names=["--verbose"],
            takes_arg=False,
            arg_placeholder=None,
            choices=None,
            desc="Enable verbose output",
        )
        self.assertEqual(format_option(opt_simple), "'--verbose[Enable verbose output]'")

        opt_dir = Option(
            names=["--add-dir"],
            takes_arg=True,
            arg_placeholder="<dir>",
            choices=None,
            desc="Add directory",
        )
        self.assertEqual(
            format_option(opt_dir),
            "'--add-dir=[Add directory]:dir:_files -/'",
        )

        opt_multi = Option(
            names=["--effort", "--reasoning-effort"],
            takes_arg=True,
            arg_placeholder="<level>",
            choices=["low", "high"],
            desc="Set effort",
        )
        self.assertEqual(
            format_option(opt_multi),
            "'(--effort --reasoning-effort)'{--effort,--reasoning-effort}'=[Set effort]:level:(low high)'",
        )

    def test_collect_goflag_sections(self):
        root_text = """Usage of agy:
  --agent  Agent for current CLI session
  --model  Model for current CLI session

Available subcommands:
  mcp     Manage MCP servers
  plugin  Manage plugins
"""
        sections = collect_goflag_sections(root_text, is_root=True)
        self.assertIn("Flags", sections)
        self.assertEqual(len(sections["Flags"]), 2)
        self.assertIn("Commands", sections)
        self.assertEqual(len(sections["Commands"]), 2)

    def test_generate_commander_mocked(self):
        help_text = """Usage: mockcli [options] [command]

Options:
  --verbose               Enable verbose logging
  --add-dir <dir>         Add a directory

Commands:
  sub                     Run subcommand
"""
        with patch("gen_shell_completion.run_help", return_value=help_text):
            spec = HarnessSpec(cli="mockcli", format="commander")
            script = generate(spec)
            self.assertIsNotNone(script)
            self.assertTrue(script.startswith("#compdef mockcli"))
            self.assertIn("--verbose", script)
            self.assertIn("--add-dir", script)

    def test_generate_goflag_mocked(self):
        root_text = """Usage of agy:
  --agent  Agent for CLI
  --model  Model for CLI

Available subcommands:
  mcp     Manage MCP
"""
        sub_text = """Usage: agy mcp [flags] [args]

Flags:
  --server  Server name
"""
        with patch("gen_shell_completion.run_help", return_value=root_text), patch(
            "gen_shell_completion.run_goflag_subcommand_help", return_value=sub_text
        ):
            spec = HarnessSpec(cli="agy", format="go-flag")
            script = generate(spec)
            self.assertIsNotNone(script)
            self.assertTrue(script.startswith("#compdef agy"))
            self.assertIn("_agy_mcp", script)

    def test_generate_native_passthrough_mocked(self):
        with patch(
            "gen_shell_completion._run",
            return_value="#compdef opencode\n_opencode() { true; }\n",
        ):
            spec = HarnessSpec(
                cli="opencode",
                format="native-passthrough",
                native_command=["completion"],
            )
            script = generate(spec)
            self.assertIsNotNone(script)
            self.assertTrue(script.startswith("#compdef opencode"))

    def test_main_cli_stdout(self):
        with patch(
            "gen_shell_completion.run_help",
            return_value="Usage: claude [options]\n\nOptions:\n  --version  Show version\n",
        ), patch("sys.stdout.write") as mock_stdout:
            with patch("sys.argv", ["gen_shell_completion.py", "--harness", "claude", "--stdout"]):
                code = main()
                self.assertEqual(code, 0)
                self.assertTrue(mock_stdout.called)


if __name__ == "__main__":
    unittest.main()
