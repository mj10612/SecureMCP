"""Tests for SecureMCP Click CLI commands."""

from click.testing import CliRunner
from secure_mcp.cli import main


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "SecureMCP" in result.output


def test_cli_mask_and_unmask():
    runner = CliRunner()
    mask_result = runner.invoke(
        main,
        [
            "mask",
            "Alice from Google sent $10,000 to Bob.",
            "--session-id",
            "cli_test_session",
        ],
    )
    assert mask_result.exit_code == 0
    assert "Masked Output" in mask_result.output

    # Find the masked text between panels or run on known text
    # In cli_test_session: Alice was [NOUN_1], Google was [ENT_1], $10,000 was [NUM_1], Bob was [ENT_2]
    unmask_result = runner.invoke(
        main,
        [
            "unmask",
            "[ENT_1] from [ENT_2] sent [NUM_1] to [ENT_3].",
            "--session-id",
            "cli_test_session",
        ],
    )
    assert unmask_result.exit_code == 0
    assert "Restored Original Text" in unmask_result.output
    assert "Alice from Google sent $10,000 to Bob." in unmask_result.output


def test_cli_benchmark():
    runner = CliRunner()
    result = runner.invoke(main, ["benchmark"])
    assert result.exit_code == 0
    assert "Benchmark Results" in result.output
    assert "100.0% (Zero Divergence)" in result.output
