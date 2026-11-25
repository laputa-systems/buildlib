"""Command line interface: `python -m buildlib <command>`.

Commands print results on stdout and errors on stderr. Bare invocation prints
help; usage errors exit 2 and runtime failures exit 1.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import locks, targets, tools


def _default_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    return Path(base) / "laputa-tools" if base else Path.home() / ".cache" / "laputa-tools"


def _parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(
        prog="buildlib",
        description="Shared helpers for the laputa-systems build repositories.",
    )
    subcommands = parser.add_subparsers(dest="command", metavar="COMMAND")
    tool_parser = subcommands.add_parser("tools", help="install owned tools from pinned releases")
    actions = tool_parser.add_subparsers(dest="action", metavar="ACTION")

    lock = actions.add_parser("lock", help="add or update a tools.lock.json entry from a release")
    lock.add_argument("tool", help="tool name from the buildlib tool table")
    lock.add_argument("--tag", required=True, help="release tag to lock, e.g. release-<sha>")
    lock.add_argument("--lock", default="tools.lock.json", help="lock file path (default: tools.lock.json)")

    install = actions.add_parser("install", help="install tools and print one bin directory per tool")
    install.add_argument("tools", nargs="+", metavar="TOOL", help="tool names from the lock")
    install.add_argument("--lock", required=True, help="tools.lock.json path")
    install.add_argument("--cache", help="cache directory (default: ~/.cache/laputa-tools)")
    install.add_argument("--target", help="target triple (default: this host)")

    key = actions.add_parser("cache-key", help="print a stable digest of the selected lock entries")
    key.add_argument("tools", nargs="+", metavar="TOOL", help="tool names from the lock")
    key.add_argument("--lock", required=True, help="tools.lock.json path")
    key.add_argument("--target", help="target triple (default: this host)")
    return parser, tool_parser


def _target_arg(value: str | None) -> str | targets.Target | None:
    return None if value is None else targets.target_for_triple(value)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; return the process exit code."""
    parser, tool_parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "tools" and args.action is None:
        tool_parser.print_help()
        return 0
    try:
        if args.action == "lock":
            entry = tools.lock_entry(args.tool, args.tag)
            tools.write_lock(args.lock, entry)
            print(f"locked {entry.tool} {entry.tag} -> {args.lock}")
        elif args.action == "install":
            lock = tools.load_lock(args.lock)
            cache = Path(args.cache) if args.cache else _default_cache()
            target = _target_arg(args.target)
            for tool in args.tools:
                print(tools.install(lock, tool, cache, target=target))
        elif args.action == "cache-key":
            target = _target_arg(args.target)
            print(tools.cache_key(args.lock, args.tools, target=target))
    except (locks.LockError, locks.FetchError, tools.ToolError, targets.UnsupportedTargetError) as error:
        print(f"buildlib: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
