#!/usr/bin/env python3
"""Install and verify Astred for ShadowFrog (latest stable release by default)."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "skills" / "shadow-frog-init"))
import _astred

FEED = "https://pkgs.dev.azure.com/FoSSE/_packaging/FoSSE/nuget/v3/index.json"
NUGET = "https://api.nuget.org/v3/index.json"
PROVIDER = "Microsoft.Artifacts.CredentialProvider.NuGet.Tool"
SDK_METADATA = "https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json"
WINDOWS = os.name == "nt"


def download(url, destination):
    if not url.startswith("https://"):
        raise _astred.AstredError(f"Refusing non-HTTPS SDK download: {url}")
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def install_linux_sdk(directory):
    architecture = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64",
                    "arm64": "arm64", "armv7l": "arm"}.get(platform.machine().lower())
    if not architecture:
        raise _astred.AstredError(f"Unsupported Linux SDK architecture: {platform.machine()}.")
    musl = platform.libc_ver()[0] == "musl"
    os_release = Path("/etc/os-release")
    if os_release.is_file():
        musl = musl or bool(re.search(r'(?m)^ID=["\']?alpine["\']?$', os_release.read_text()))
    rid = f"linux-{'musl-' if musl else ''}{architecture}"
    with tempfile.TemporaryDirectory(prefix="shadowfrog-dotnet-") as temporary:
        root = Path(temporary)
        metadata_file = root / "releases.json"
        download(SDK_METADATA, metadata_file)
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        sdk = next((sdk for release in metadata["releases"]
                    for sdk in release.get("sdks", [release.get("sdk", {})])
                    if sdk.get("version") == metadata["latest-sdk"]), None)
        archive = next((file for file in sdk["files"]
                        if file["rid"] == rid and file["name"].endswith(".tar.gz")), None) if sdk else None
        if not archive:
            raise _astred.AstredError(f"No .NET 10 SDK archive is published for {rid}.")
        package = root / "sdk.tar.gz"
        print(f"Installing .NET SDK {sdk['version']} for {rid} into {directory}.", flush=True)
        download(archive["url"], package)
        digest = hashlib.sha512()
        with package.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != archive["hash"].lower():
            raise _astred.AstredError("The .NET SDK archive failed SHA-512 verification.")
        directory.mkdir(parents=True, exist_ok=True)
        with tarfile.open(package, "r:gz") as contents:
            # Keep Python 3.9 support while rejecting paths/links outside the SDK directory.
            for member in contents.getmembers():
                targets = [directory / member.name]
                if member.issym():
                    targets.append((directory / member.name).parent / member.linkname)
                elif member.islnk():
                    targets.append(directory / member.linkname)
                if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                    raise _astred.AstredError(f"Unsupported SDK archive entry: {member.name}")
                for target in targets:
                    try:
                        target.resolve().relative_to(directory.resolve())
                    except ValueError as exc:
                        raise _astred.AstredError(f"Unsafe SDK archive entry: {member.name}") from exc
                if hasattr(tarfile, "data_filter"):
                    contents.extract(member, directory, filter="data")
                else:
                    contents.extract(member, directory)
    return str(directory / "dotnet")


def run(args, env, capture=False):
    with tempfile.TemporaryDirectory(prefix="shadowfrog-astred-install-") as directory:
        return subprocess.run(
            [str(arg) for arg in args], env=env, cwd=directory, check=True, timeout=900,
            capture_output=capture, text=True, encoding="utf-8", errors="replace",
        )


def install(version=None):
    env = dict(os.environ)
    for key in ("NUGET_PLUGIN_PATHS", "NUGET_NETCORE_PLUGIN_PATHS", "NUGET_NETFX_PLUGIN_PATHS"):
        if env.get(key) and any(not Path(path).is_file() for path in env[key].split(os.pathsep)):
            print(f"Warning: ignoring broken {key} for setup subprocesses only.", file=sys.stderr)
            del env[key]

    dotnet = shutil.which("dotnet")
    if not dotnet and WINDOWS:
        candidate = Path(env.get("ProgramFiles", r"C:\Program Files")) / "dotnet" / "dotnet.exe"
        if candidate.is_file():
            dotnet = str(candidate)
    private_sdk = _astred.tool_config_path().parent / "dotnet"
    if not dotnet and not WINDOWS and (private_sdk / "dotnet").is_file():
        dotnet = str(private_sdk / "dotnet")
    sdks = run([dotnet, "--list-sdks"], env, capture=True).stdout if dotnet else ""
    if not re.search(r"(?m)^10\.\d+\.\d+", sdks) and not WINDOWS and (private_sdk / "dotnet").is_file():
        dotnet = str(private_sdk / "dotnet")
        env = _astred.runtime_environment(private_sdk, env)
        sdks = run([dotnet, "--list-sdks"], env, capture=True).stdout
    if not re.search(r"(?m)^10\.\d+\.\d+", sdks):
        if not WINDOWS and platform.system() == "Linux":
            dotnet = install_linux_sdk(private_sdk)
        else:
            winget = shutil.which("winget") if WINDOWS else None
            if not winget:
                raise _astred.AstredError(
                    "Install the .NET 10 SDK (https://dotnet.microsoft.com/download/dotnet/10.0), "
                    "then rerun this script. On Windows, automatic installation requires winget."
                )
            run([winget, "install", "--id", "Microsoft.DotNet.SDK.10", "--exact",
                 "--accept-package-agreements", "--accept-source-agreements"], env)
            dotnet = shutil.which("dotnet") or str(
                Path(env.get("ProgramFiles", r"C:\Program Files")) / "dotnet" / "dotnet.exe"
            )
        env = _astred.runtime_environment(Path(dotnet).resolve().parent, env)
        sdks = run([dotnet, "--list-sdks"], env, capture=True).stdout
        if not re.search(r"(?m)^10\.\d+\.\d+", sdks):
            raise _astred.AstredError("The .NET 10 SDK is still unavailable; restart the terminal and retry.")

    # A newly installed SDK must also be discoverable by the Astred tool shim.
    dotnet_root = Path(dotnet).resolve().parent
    env = _astred.runtime_environment(dotnet_root, env)
    global_tools = Path(env.get("DOTNET_CLI_HOME", Path.home())) / ".dotnet" / "tools"
    env["PATH"] = os.pathsep.join((str(Path(dotnet).parent), str(global_tools), env.get("PATH", "")))
    tools = run([dotnet, "tool", "list", "--global"], env, capture=True).stdout
    if not re.search(rf"(?im)^{re.escape(PROVIDER)}\s", tools):
        run([dotnet, "tool", "install", "--global", PROVIDER, "--source", NUGET], env)

    installations = _astred.tool_config_path().parent / "astred"
    installations.mkdir(parents=True, exist_ok=True)
    directory = installations / version if version else Path(
        tempfile.mkdtemp(prefix="latest-", dir=installations)
    )
    directory.mkdir(parents=True, exist_ok=True)
    tools = run([dotnet, "tool", "list", "--tool-path", directory], env, capture=True).stdout
    installed = re.search(r"(?im)^astred\.tools\s+(\S+)", tools)
    if installed and version and installed.group(1) != version:
        raise _astred.AstredError(
            f"{directory} contains Astred {installed.group(1)}, not {version}; "
            "use a clean version-specific tool directory."
        )
    if not installed:
        print("Install Astred: complete the credential provider's sign-in if prompted. "
              "Your identity must have FoSSE feed-reader access.", flush=True)
        arguments = [dotnet, "tool", "install", "Astred.Tools",
                     "--tool-path", directory, "--interactive", "--source", FEED]
        if version:
            arguments.extend(["--version", version])
        run(arguments, env)
    command = directory / ("astred.exe" if WINDOWS else "astred")
    if not command.is_file():
        raise _astred.AstredError(f"Installation did not produce {command}.")

    # Exercise the actual adapter, not just the CLI's help/version output.
    with tempfile.TemporaryDirectory(prefix="shadowfrog-astred-setup-") as temporary:
        root = Path(temporary)
        (root / "sample.py").write_text("def shadowfrog_setup_probe():\n    pass\n", encoding="utf-8")
        symbols, metadata = _astred.extract(root, ["sample.py"], command=str(command), env=env)
        if not any(symbol["name"] == "shadowfrog_setup_probe" for symbol in symbols["sample.py"]):
            raise _astred.AstredError("Astred's Python extraction probe did not return the expected symbol.")

    config = _astred.tool_config_path()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=config.parent, prefix=".astred-", delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump({"command": str(command), "dotnet_root": str(dotnet_root)}, output)
            output.write("\n")
        os.replace(temporary, config)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print(f"Astred {metadata['astred_release']} verified. Saved executable configuration to {config}.")
    if env.get("SHADOWFROG_ASTRED_COMMAND"):
        print("Warning: SHADOWFROG_ASTRED_COMMAND overrides this configuration; "
              "unset it to use this installation.", file=sys.stderr)
    print("New shadow: run shadow-init.py --symbol-backend astred. "
          "Existing shadow: use --preview-migration --symbol-backend astred; do not reset.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Exact Astred.Tools version (default: latest stable from the feed)")
    args = parser.parse_args()
    if args.version is not None and not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", args.version):
        parser.error("--version must be an exact package version, not a range or wildcard")
    try:
        install(args.version)
    except subprocess.CalledProcessError as exc:
        print(f"Setup failed: {exc.cmd[0]} exited with {exc.returncode}. "
              "Check the preceding diagnostics and FoSSE feed access, then rerun.", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1
    except (_astred.AstredError, OSError, ValueError, KeyError, tarfile.TarError,
            subprocess.TimeoutExpired) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Setup interrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
