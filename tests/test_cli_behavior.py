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


def test_load_project_prints_deprecation_once(tmp_path, monkeypatch, capsys):
    toml_path = tmp_path / "depp.toml"
    toml_path.write_text(
        "[app]\nname = 'example'\nproject_root = '.'\n"
        "[host]\nfqdn = 'example.com'\ncaddy_host_port = 8100\n"
        "[acme]\ncontact_email = 'ops@example.com'\n"
    )
    monkeypatch.setattr(cli, "_PRINTED_DEPRECATIONS", set())
    args = Namespace(toml_file=toml_path)

    _path, config, _fqdn, _app = cli.load_project(args)
    cli.load_project(args)

    assert config.host.loopback_port == 8100
    err = capsys.readouterr().err
    assert err.count("caddy_host_port is deprecated") == 1


def test_resolve_toml_path_errors_when_nothing_found(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.Path, "cwd", classmethod(lambda _cls: tmp_path))

    with pytest.raises(SystemExit, match=str(cli.EXIT_ERROR)):
        cli.resolve_toml_path(Namespace(toml_file=None))

    assert "deploy/depp.toml" in capsys.readouterr().err


def test_resolve_toml_path_finds_deploy_dir_toml(tmp_path, monkeypatch):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "depp.toml").write_text("")
    monkeypatch.setattr(cli.Path, "cwd", classmethod(lambda _cls: tmp_path))

    found = cli.resolve_toml_path(Namespace(toml_file=None))

    assert found == (tmp_path / "deploy" / "depp.toml").resolve()
