"""Credential-free tests for Astred setup and per-user executable discovery."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "install-astred.py"
VERSION = "2.14.30"
PROBE_SYMBOLS = {
    "sample.py": [{"qualified_name": "shadowfrog_setup_probe"}],
    "sample.c": [{"qualified_name": "shadowfrog_c_probe"}],
    "sample.h": [{"qualified_name": "ShadowFrogSetup"}],
    "sample.cpp": [{"qualified_name": "ShadowFrogSetup.native_probe"}],
}
REAL_ASTRED = os.environ.get("SHADOWFROG_ASTRED_TEST_COMMAND")


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not REAL_ASTRED, reason="Set SHADOWFROG_ASTRED_TEST_COMMAND for the real tool")
def test_real_installer_probe_verifies_python_c_and_cpp(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("install_astred", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tool = Path(REAL_ASTRED).resolve()
    config = tmp_path / "astred.json"
    monkeypatch.setattr(module._astred, "tool_config_path", lambda: config)
    monkeypatch.setattr(module, "tempfile", SimpleNamespace(
        mkdtemp=lambda **kwargs: str(tool.parent),
        TemporaryDirectory=tempfile.TemporaryDirectory,
        NamedTemporaryFile=tempfile.NamedTemporaryFile,
    ))

    def installed(args, env, capture=False):
        if args[1:] == ["--list-sdks"]:
            return SimpleNamespace(stdout="10.0.403 [sdk]\n")
        if args[1:4] == ["tool", "list", "--global"]:
            return SimpleNamespace(stdout=f"{module.PROVIDER} 1.0.0 provider\n")
        if args[1:4] == ["tool", "list", "--tool-path"]:
            return SimpleNamespace(stdout="Astred.Tools 2.14.34 astred\n")
        pytest.fail(f"Probe verification must not change installed tools: {args}")

    monkeypatch.setattr(module, "run", installed)
    module.install()
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["command"] == str(tool)
    assert Path(saved["dotnet_root"]).is_dir()


@pytest.fixture
def setup(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("install_astred", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path / "user config" / "astred.json"
    monkeypatch.setattr(module._astred, "tool_config_path", lambda: config)
    monkeypatch.delenv("SHADOWFROG_ASTRED_COMMAND", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda name: str(tmp_path / name))
    calls = []

    def run(args, env, capture=False):
        args = [str(arg) for arg in args]
        calls.append((args, dict(env)))
        stdout = ""
        if "--list-sdks" in args:
            stdout = "10.0.402 [sdk]\n"
        elif args[1:4] == ["tool", "list", "--global"]:
            stdout = ""
        elif args[1:3] == ["tool", "install"] and "Astred.Tools" in args:
            directory = Path(args[args.index("--tool-path") + 1])
            (directory / ("astred.exe" if module.WINDOWS else "astred")).touch()
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(module, "run", run)

    def extract(root, paths, **kwargs):
        assert paths == list(PROBE_SYMBOLS)
        assert "shadowfrog_setup_probe" in (root / paths[0]).read_text(encoding="utf-8")
        assert Path(kwargs["command"]).is_file()
        project = json.loads((root / ".astred.project.json").read_text(encoding="utf-8"))
        assert [unit["language"] for unit in project["units"]] == ["C", "C++"]
        assert project["units"][1]["defines"] == {"SHADOWFROG_SETUP": "1"}
        assert '#include "sample.h"' in (root / "sample.cpp").read_text(encoding="utf-8")
        return PROBE_SYMBOLS, {"astred_release": VERSION}

    monkeypatch.setattr(module._astred, "extract", extract)
    return module, config, calls


def test_installs_pinned_package_and_publishes_discoverable_config(setup):
    module, config, calls = setup
    module.install(VERSION)
    expected = config.parent / "astred" / VERSION / ("astred.exe" if os.name == "nt" else "astred")
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "command": str(expected), "dotnet_root": str(config.parent.parent.resolve()),
    }
    assert module._astred.resolve_command() == str(expected)
    commands = [args for args, _ in calls]
    assert any(module.PROVIDER in args and args[-2:] == ["--source", module.NUGET] for args in commands)
    install = next(args for args in commands if "Astred.Tools" in args)
    assert install[install.index("--version") + 1] == VERSION
    assert "--interactive" in install
    assert install[install.index("--source") + 1] == module.FEED
    assert "--global" not in install
    assert all(str(Path(os.environ.get("DOTNET_CLI_HOME", Path.home())) / ".dotnet" / "tools")
               in env["PATH"].split(os.pathsep) for _, env in calls[1:])
    assert not list(config.parent.glob(".astred-*"))


def test_rerun_reuses_tools_but_still_probes(setup, monkeypatch):
    module, config, calls = setup
    module.install(VERSION)
    original = module.run

    def run(args, env, capture=False):
        if args[1:4] == ["tool", "list", "--global"]:
            return SimpleNamespace(stdout=f"{module.PROVIDER} 1.0.0 provider\n")
        if args[1:4] == ["tool", "list", "--tool-path"]:
            return SimpleNamespace(stdout=f"astred.tools {VERSION} astred\n")
        return original(args, env, capture)

    monkeypatch.setattr(module, "run", run)
    calls.clear()
    probes = []
    monkeypatch.setattr(module._astred, "extract", lambda *args, **kwargs: (
        probes.append(args) or PROBE_SYMBOLS,
        {"astred_release": VERSION},
    ))
    module.install(VERSION)
    assert probes
    assert not any("install" in args for args, _ in calls)
    assert config.is_file()


@pytest.mark.parametrize("failure", ["install", "probe", "missing-symbol", "native-symbol", "wrong-version"])
def test_failures_preserve_existing_configuration(setup, monkeypatch, failure):
    module, config, _ = setup
    config.parent.mkdir(parents=True)
    config.write_text('{"command": "previous"}\n', encoding="utf-8")
    before = config.read_bytes()
    original = module.run

    def run(args, env, capture=False):
        if failure == "install" and "Astred.Tools" in args:
            raise subprocess.CalledProcessError(1, args)
        if failure == "wrong-version" and args[1:4] == ["tool", "list", "--tool-path"]:
            return SimpleNamespace(stdout="astred.tools 1.0.0 astred\n")
        return original(args, env, capture)

    monkeypatch.setattr(module, "run", run)
    if failure == "probe":
        def extract(*args, **kwargs):
            raise module._astred.AstredError("incompatible export")
        monkeypatch.setattr(module._astred, "extract", extract)
    elif failure in {"missing-symbol", "native-symbol"}:
        missing = "sample.cpp" if failure == "native-symbol" else "sample.py"
        monkeypatch.setattr(module._astred, "extract", lambda *args, **kwargs: (
            {**PROBE_SYMBOLS, missing: []}, {"astred_release": VERSION},
        ))
    with pytest.raises((subprocess.CalledProcessError, module._astred.AstredError)):
        module.install(VERSION)
    assert config.read_bytes() == before


def test_broken_plugin_overrides_are_only_removed_from_child_environment(setup, monkeypatch, capsys):
    module, config, calls = setup
    missing = str(config.parent / "missing-provider.dll")
    monkeypatch.setenv("NUGET_NETCORE_PLUGIN_PATHS", missing)
    module.install(VERSION)
    assert os.environ["NUGET_NETCORE_PLUGIN_PATHS"] == missing
    assert all("NUGET_NETCORE_PLUGIN_PATHS" not in env for _, env in calls)
    assert "ignoring broken NUGET_NETCORE_PLUGIN_PATHS" in capsys.readouterr().err


def test_command_precedence_and_invalid_configuration(setup, monkeypatch):
    module, config, _ = setup
    assert module._astred.resolve_command() == "astred"
    config.parent.mkdir(parents=True)
    config.write_text("invalid", encoding="utf-8")
    with pytest.raises(module._astred.AstredError, match="configuration"):
        module._astred.resolve_command()
    monkeypatch.setenv("SHADOWFROG_ASTRED_COMMAND", "environment-tool")
    assert module._astred.resolve_command() == "environment-tool"
    assert module._astred.resolve_command("explicit-tool") == "explicit-tool"


@pytest.mark.parametrize("value", ["", "latest", "2.*", "../escape", "2.14.30\n"])
def test_cli_rejects_unpinned_versions(value):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--version", value],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "exact package version" in result.stderr


def test_missing_sdk_without_winget_has_actionable_error(setup, monkeypatch):
    module, _, _ = setup
    monkeypatch.setattr(module, "WINDOWS", True)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: SimpleNamespace(stdout=""))
    with pytest.raises(module._astred.AstredError, match="Install the .NET 10 SDK"):
        module.install(VERSION)


def test_latest_is_default_and_keeps_previous_installations(setup):
    module, config, calls = setup
    module.install()
    first = json.loads(config.read_text(encoding="utf-8"))
    first_command = Path(first["command"])
    assert first_command.parent.name.startswith("latest-")
    module.install()
    second = json.loads(config.read_text(encoding="utf-8"))
    assert second["command"] != first["command"] and first_command.is_file()
    installs = [args for args, _ in calls if "Astred.Tools" in args]
    assert len(installs) == 2
    assert all("--version" not in args and "--prerelease" not in args for args in installs)


def test_cli_defaults_to_latest(setup, monkeypatch):
    module, _, _ = setup
    calls = []
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    monkeypatch.setattr(module, "install", calls.append)
    assert module.main() == 0
    assert calls == [None]


@pytest.mark.parametrize("existing_sdk", [False, True])
def test_linux_provisions_or_reuses_private_sdk(setup, monkeypatch, existing_sdk):
    module, config, calls = setup
    monkeypatch.setattr(module, "WINDOWS", False)
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    original = module.run
    sdk = config.parent / "dotnet"
    provisions = []
    if existing_sdk:
        sdk.mkdir(parents=True)
        (sdk / "dotnet").touch()

    def provision(directory):
        provisions.append(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "dotnet").touch()
        return str(directory / "dotnet")

    def run(args, env, capture=False):
        if "--list-sdks" in args and str(args[0]) != str(sdk / "dotnet"):
            return SimpleNamespace(stdout="9.0.319 [sdk]\n")
        return original(args, env, capture)

    monkeypatch.setattr(module, "install_linux_sdk", provision)
    monkeypatch.setattr(module, "run", run)
    module.install()
    assert provisions == ([] if existing_sdk else [sdk])
    assert Path(json.loads(config.read_text(encoding="utf-8"))["command"]).name == "astred"
    assert json.loads(config.read_text(encoding="utf-8"))["dotnet_root"] == str(sdk.resolve())
    assert all(env["DOTNET_ROOT"] == str(sdk.resolve()) for _, env in calls)
    assert not any("winget" in args[0] for args, _ in calls)


@pytest.mark.parametrize("failure", [None, "hash", "traversal", "symlink", "device"])
def test_linux_sdk_download_is_verified_and_safely_extracted(setup, monkeypatch, tmp_path, failure):
    module, _, _ = setup
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(module.platform, "libc_ver", lambda: ("glibc", "2.35"))
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("../escape" if failure == "traversal" else "dotnet")
        member.mode = 0o755
        if failure == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "../../escape"
            archive.addfile(member)
        elif failure == "device":
            member.type = tarfile.CHRTYPE
            archive.addfile(member)
        else:
            member.size = 4
            archive.addfile(member, io.BytesIO(b"test"))
    package = stream.getvalue()
    digest = hashlib.sha512(package).hexdigest()
    metadata = {"latest-sdk": "10.0.402", "releases": [{"sdks": [{
        "version": "10.0.402", "files": [{
            "rid": "linux-x64", "name": "sdk.tar.gz", "url": "https://example.invalid/sdk",
            "hash": "0" * 128 if failure == "hash" else digest,
        }],
    }]}]}
    downloads = []

    def download(url, destination):
        downloads.append(url)
        destination.write_bytes(json.dumps(metadata).encode() if url == module.SDK_METADATA else package)

    monkeypatch.setattr(module, "download", download)
    directory = tmp_path / "private sdk"
    if failure:
        with pytest.raises(module._astred.AstredError, match="verification|Unsafe|Unsupported"):
            module.install_linux_sdk(directory)
        assert not (tmp_path / "escape").exists()
        assert not (directory / "dotnet").exists()
    else:
        assert module.install_linux_sdk(directory) == str(directory / "dotnet")
        assert (directory / "dotnet").read_bytes() == b"test"
        if os.name != "nt":
            assert os.access(directory / "dotnet", os.X_OK)
    assert downloads == [module.SDK_METADATA, "https://example.invalid/sdk"]


def test_sdk_download_rejects_insecure_urls(setup, tmp_path):
    module, _, _ = setup
    with pytest.raises(module._astred.AstredError, match="HTTPS"):
        module.download("http://example.invalid/sdk", tmp_path / "sdk")


@pytest.mark.skipif(os.name != "nt", reason="Windows prerequisite installation")
def test_windows_installs_missing_sdk_before_tools(setup, monkeypatch):
    module, config, calls = setup
    original = module.run
    checks = []

    def run(args, env, capture=False):
        if "--list-sdks" in args:
            checks.append(args)
            if len(checks) == 1:
                return SimpleNamespace(stdout="9.0.319 [sdk]\n")
        return original(args, env, capture)

    monkeypatch.setattr(module, "run", run)
    module.install(VERSION)
    assert len(checks) == 2
    assert any("Microsoft.DotNet.SDK.10" in args for args, _ in calls)
    assert config.is_file()


@pytest.mark.parametrize("content", ['[]', '{"command": ""}', '{"command": "relative"}'])
def test_invalid_setup_paths_are_not_used(setup, content):
    module, config, _ = setup
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")
    with pytest.raises(module._astred.AstredError, match="configuration"):
        module._astred.resolve_command()


def test_native_commands_are_bounded_and_run_outside_repository(setup, monkeypatch):
    module, _, _ = setup
    spec = importlib.util.spec_from_file_location("install_astred_runner", SCRIPT)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    directories = []

    def run(args, **kwargs):
        directories.append(Path(kwargs["cwd"]))
        assert directories[-1].is_dir()
        assert directories[-1] != SCRIPT.parent
        assert kwargs["check"] and kwargs["timeout"] == 900
        assert kwargs["env"] == {"TEST": "value"}
        return SimpleNamespace(stdout="ok")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert runner.run(["dotnet", "--list-sdks"], {"TEST": "value"}, capture=True).stdout == "ok"
    assert not directories[0].exists()
