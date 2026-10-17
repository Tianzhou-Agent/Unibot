"""``aina`` developer CLI for AINA Protocol 2.0 packages (design §12).

``aina validate PATH [--live]`` runs the host's own package validation on a package folder or ``.aina.zip``;
``--live`` also lists the tools of the package's MCP server and checks the exposed ones as registration would.
``aina pack DIR -o FILE`` validates a folder and writes a reproducible package archive.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from tianzhou_agent_platform.aina.package.mcp_client import McpToolClient
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
    validate = commands.add_parser("validate", help="validate a package folder or .aina.zip")
    validate.add_argument("path", type=Path)
    validate.add_argument("--live", action="store_true", help="also check the tools listed by the MCP server")
    pack = commands.add_parser("pack", help="validate a package folder and write its .aina.zip")
    pack.add_argument("path", type=Path)
    pack.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _validate(args.path, live=args.live)
        return _pack(args.path, args.output)
    except PlatformError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1


def _validate(path: Path, *, live: bool) -> int:
    report = validate_package_archive(path.read_bytes()) if path.is_file() else validate_package_files(_read_folder(path))
    if live and report.valid and report.manifest is not None:
        url = report.manifest.tools.mcp.url
        info = asyncio.run(McpToolClient().list_tools(url))
        _snapshots, errors, warnings = snapshot_tools(report.manifest, info.tools)
        report.errors.extend(errors)
        report.warnings.extend(warnings)
        if not errors:
            print(f"live: {url} lists every exposed tool ({len(report.manifest.tools.expose)})")
    return _print(report)


def _pack(path: Path, output: Path) -> int:
    files = _read_folder(path)
    report = validate_package_files(files)
    if _print(report):
        return 1
    output.write_bytes(build_project_archive(files))
    print(f"wrote {output}")
    return 0


def _read_folder(path: Path) -> dict[str, bytes]:
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
