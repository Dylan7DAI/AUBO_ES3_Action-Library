"""验证根目录统一入口的子命令转发和交互菜单参数组装。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import main


class MainEntryTests(unittest.TestCase):
    """确保菜单只负责选择和转发，不重复实现机器人控制逻辑。"""

    def test_cli_dispatch_forwards_subcommand_arguments(self):
        """确认 diagnose 子命令后的参数原样交给诊断工具。"""

        with patch.object(main, "diagnose_main", return_value=7) as tool:
            result = main.dispatch_cli(["diagnose", "--json"])
        self.assertEqual(result, 7)
        tool.assert_called_once_with(["--json"])

    def test_diagnose_menu_calls_read_only_snapshot_tool(self):
        """确认菜单第 1 项调用会读取关节角和 TCP 位姿的诊断工具。"""

        answers = iter(["1", ""])
        with (
            patch.object(main, "configure_windows_console"),
            patch.object(main, "diagnose_main", return_value=0) as tool,
        ):
            result = main.run_interactive(lambda prompt: next(answers))
        self.assertEqual(result, 0)
        tool.assert_called_once_with([])
    def test_unknown_cli_command_is_rejected(self):
        """确认未知子命令不会调用任何机器人工具。"""

        self.assertEqual(main.dispatch_cli(["unknown"]), 2)

    def test_joint_menu_defaults_to_preview(self):
        """确认关节菜单直接回车时不附加真实运动授权参数。"""

        answers = iter(["2", "", ""])
        with (
            patch.object(main, "configure_windows_console"),
            patch.object(main, "joint_main", return_value=0) as tool,
        ):
            result = main.run_interactive(lambda prompt: next(answers))
        self.assertEqual(result, 0)
        tool.assert_called_once_with(["--delta", "0,0,0,0,0,0.02"])

    def test_nod_menu_adds_confirmation_only_after_move(self):
        """确认输入 MOVE 后才向点头工具传递 execute 与固定确认文本。"""

        answers = iter(["3", "", "", "", "MOVE"])
        with (
            patch.object(main, "configure_windows_console"),
            patch.object(main, "nod_main", return_value=0) as tool,
        ):
            result = main.run_interactive(lambda prompt: next(answers))
        self.assertEqual(result, 0)
        forwarded = tool.call_args.args[0]
        self.assertIn("--execute", forwarded)
        self.assertEqual(
            forwarded[-2:], ["--confirm-motion", main.NOD_CONFIRMATION_TEXT]
        )

    def test_cup_menu_defaults_to_offline_preview(self):
        """确认水杯菜单直接回车时仅传递停顿时间。"""

        answers = iter(["4", "", ""])
        with (
            patch.object(main, "configure_windows_console"),
            patch.object(main, "cup_main", return_value=0) as tool,
        ):
            result = main.run_interactive(lambda prompt: next(answers))
        self.assertEqual(result, 0)
        tool.assert_called_once_with(["--cup-hold", "2.0"])


if __name__ == "__main__":
    unittest.main()
