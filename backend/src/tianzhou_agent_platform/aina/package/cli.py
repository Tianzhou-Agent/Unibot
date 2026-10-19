"""``aina`` developer CLI for AINA Protocol 2.0 packages (design §12).

``aina init DIR --id ID --name NAME`` creates a new project: ``package/`` and a Python MCP ``server/``.
``aina validate PATH [--live]`` runs the host's own package validation on a package folder or ``.aina.zip``;
``--live`` also lists the tools of the package's MCP server and checks the exposed ones as registration would.
``aina pack DIR -o FILE`` validates a folder and writes a reproducible package archive.
``aina publish PATH --host URL`` registers (or upgrades) the package on a Unibot host with a developer token.
``aina dev PATH`` runs a local host with a developer page for the package (see ``devhost``).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

from tianzhou_agent_platform.aina.package.mcp_client import McpToolClient
from tianzhou_agent_platform.aina.package.scaffold import PackageScaffoldRequest, scaffold_package
from tianzhou_agent_platform.aina.package.service import snapshot_tools
from tianzhou_agent_platform.aina.package.validation import (
    PackageValidationReport,
    validate_package_archive,
    validate_package_files,
)
from tianzhou_agent_platform.aina.project import build_project_archive
from tianzhou_agent_platform.core.errors import PlatformError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aina", description="Build and check AINA Protocol 2.0 packages.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a new AINA project (package and MCP server)")
    init.add_argument("path", type=Path)
    init.add_argument("--id", required=True, help="reverse-domain AINA id, e.g. com.example.notes")
    init.add_argument("--name", required=True)
    init.add_argument("--description", default="Write, find and remove short notes.")
    init.add_argument("--publisher-id", default="local")
    init.add_argument("--publisher-name", default="Local developer")
    init.add_argument("--mcp-url", default="http://127.0.0.1:8770/mcp")
    validate = commands.add_parser("validate", help="validate a package folder or .aina.zip")
    validate.add_argument("path", type=Path)
    validate.add_argument("--live", action="store_true", help="also check the tools listed by the MCP server")
    pack = commands.add_parser("pack", help="validate a package folder and write its .aina.zip")
    pack.add_argument("path", type=Path)
    pack.add_argument("-o", "--output", type=Path, required=True)
    publish = commands.add_parser("publish", help="register a package folder or .aina.zip on a Unibot host")
    publish.add_argument("path", type=Path)
    publish.add_argument("--host", required=True, help="the Unibot API base URL, e.g. https://unibot.example.com/api")
    publish.add_argument("--token", default=None, help="developer token (default: $UNIBOT_TOKEN)")
    publish.add_argument(
        "--credential", default=None, help="one-time credential for a bearer/api_key MCP server's tools/list"
    )
    dev = commands.add_parser("dev", help="run a local Unibot host with a developer page for a package folder")
    dev.add_argument("path", type=Path)
    dev.add_argument("--host", default="127.0.0.1")
    dev.add_argument("--port", type=int, default=8790)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init(args)
        if args.command == "validate":
            return _validate(args.path, live=args.live)
        if args.command == "dev":
            return _dev(args.path, host=args.host, port=args.port)
        if args.command == "publish":
            return _publish(args.path, host=args.host, token=args.token, credential=args.credential)
        return _pack(args.path, args.output)
    except PlatformError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        for error in exc.errors():
            print(f"error: {'.'.join(str(part) for part in error['loc'])}: {error['msg']}", file=sys.stderr)
        return 1


def _init(args: argparse.Namespace) -> int:
    path: Path = args.path
    if path.exists() and any(path.iterdir()):
        raise PlatformError("INVALID_REQUEST", f"{path} is not empty")
    request = PackageScaffoldRequest(
        aina_id=args.id,
        name=args.name,
        description=args.description,
        publisher_id=args.publisher_id,
        publisher_name=args.publisher_name,
        mcp_url=args.mcp_url,
    )
    for name, content in scaffold_package(request).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    print(f"created {request.aina_id} in {path}; see {path / 'README.md'}")
    return 0


def _dev(path: Path, *, host: str, port: int) -> int:
    import logging

    import uvicorn

    from tianzhou_agent_platform.aina.package.devhost import create_dev_app

    # Importing the host builds its production app, whose span processor reports every span it cannot hand to the
    # (never started) observability pipeline; the dev host has no such pipeline.
    logging.getLogger("tianzhou_agent_platform.observability.telemetry").setLevel(logging.CRITICAL)

    if not (path / "aina.json").is_file():
        raise PlatformError("INVALID_REQUEST", f"{path} has no aina.json; pass the package folder")
    print(f"aina dev: http://{host}:{port}  (package {path.resolve()}; Ctrl+C to stop)", flush=True)
    uvicorn.run(create_dev_app(path, host=host, port=port), host=host, port=port, log_level="warning")
    return 0


def _publish(path: Path, *, host: str, token: str | None, credential: str | None) -> int:
    # A local host without sign-in needs no token.
    token = token or os.environ.get("UNIBOT_TOKEN")
    if path.is_file():
        archive = path.read_bytes()
    else:
        files = read_package_folder(path)
        if _print(validate_package_files(files)):
            return 1
        archive = build_project_archive(files)
    response = httpx.post(
        f"{host.rstrip('/')}/ainas/packages",
        headers={"Authorization": f"Bearer {token}"} if token else None,
        files={"file": (f"{path.stem}.aina.zip", archive, "application/zip")},
        data={"credential": credential} if credential else None,
        timeout=120,
    )
    if response.status_code >= 400:
        error = (response.json() if response.headers.get("content-type", "").startswith("application/json") else {})
        message = (error.get("error") or {}).get("message") or response.text
        print(f"error: publishing failed ({response.status_code}): {message}", file=sys.stderr)
        return 1
    record = response.json()
    for warning in (record.get("last_health") or {}).get("warnings") or []:
        print(f"warning: {warning}")
    aina = record["manifest"]["aina"]
    print(f"published {aina['id']} {aina['version']}")
    return 0


def _validate(path: Path, *, live: bool) -> int:
    report = (
        validate_package_archive(path.read_bytes()) if path.is_file() else validate_package_files(read_package_folder(path))
    )
    if live and report.valid and report.manifest is not None:
        servers = report.manifest.servers()
        listed = {server.id: asyncio.run(McpToolClient().list_tools(server.url)).tools for server in servers}
        _snapshots, errors, warnings = snapshot_tools(report.manifest, listed)
        report.errors.extend(errors)
        report.warnings.extend(warnings)
        if not errors and servers:
            urls = ", ".join(server.url for server in servers)
            print(f"live: {urls} list every exposed tool ({len(report.manifest.exposed())})")
    return _print(report)


def _pack(path: Path, output: Path) -> int:
    files = read_package_folder(path)
    report = validate_package_files(files)
    if _print(report):
        return 1
    output.write_bytes(build_project_archive(files))
    print(f"wrote {output}")
    return 0


def read_package_folder(path: Path) -> dict[str, bytes]:
    if not path.is_dir():
        raise PlatformError("INVALID_REQUEST", f"{path} is neither a package folder nor a file")
    return {
        file.relative_to(path).as_posix(): file.read_bytes()
        for file in sorted(path.rglob("*"))
        if file.is_file() and not any(part.startswith(".") for part in file.relative_to(path).parts)
    }


def _print(report: PackageValidationReport) -> int:
    for warning in report.warnings:
        print(f"warning: {warning}")
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)
    if report.errors:
        print(f"invalid: {len(report.errors)} error(s)", file=sys.stderr)
        return 1
    manifest = report.manifest
    assert manifest is not None
    print(f"valid: {manifest.aina.id} {manifest.aina.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
