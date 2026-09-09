"""Tests for top-level CLI behavior and exit codes."""

from argparse import Namespace

import pytest

from depp import cli


class FakeStdin:
    def __init__(self, *, interactive: bool, answer: str = ""):
        self.interactive = interactive
        self.answer = answer

    def isatty(self):
        return self.interactive

    def readline(self):
        return self.answer


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y\n", True), ("Y\n", True), ("n\n", False), ("\n", False)],
)
def test_confirm_accepts_only_explicit_yes(monkeypatch, answer, expected):
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin(interactive=True, answer=answer))

    assert cli.confirm("Continue?") is expected


def test_confirm_rejects_noninteractive_input(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin(interactive=False))

    with pytest.raises(cli.ConfirmationUnavailableError, match="--yes"):
        cli.confirm("Continue?")


def test_confirm_rejects_closed_interactive_input(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin(interactive=True, answer=""))

    with pytest.raises(cli.ConfirmationUnavailableError, match="input closed"):
        cli.confirm("Continue?")


def test_deploy_rejects_noninteractive_input_before_loading_project(
    monkeypatch,
):
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin(interactive=False))
    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _args: pytest.fail("project loaded before confirmation check"),
    )

    with pytest.raises(cli.ConfirmationUnavailableError, match="--yes"):
        cli.run_deployment(Namespace(check=False, yes=False))


def test_main_dispatches_registered_handler(monkeypatch):
    args = Namespace(handler=lambda received: cli.EXIT_SUCCESS)
    monkeypatch.setattr(cli, "parse_args", lambda: args)

    with pytest.raises(SystemExit, match=str(cli.EXIT_SUCCESS)):
        cli.main()


def test_main_reports_unavailable_confirmation(monkeypatch, capsys):
    def unavailable(_args):
        raise cli.ConfirmationUnavailableError("rerun with --yes")

    monkeypatch.setattr(cli, "parse_args", lambda: Namespace(handler=unavailable))

    with pytest.raises(SystemExit, match=str(cli.EXIT_ERROR)):
        cli.main()

    assert "rerun with --yes" in capsys.readouterr().err


def test_main_maps_keyboard_interrupt_to_130(monkeypatch, capsys):
    def interrupted(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "parse_args", lambda: Namespace(handler=interrupted))

    with pytest.raises(SystemExit, match=str(cli.EXIT_INTERRUPTED)):
        cli.main()

    assert "Interrupted" in capsys.readouterr().err
