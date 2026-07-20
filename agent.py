#!/usr/bin/env python3
"""Entry point for the observable coding-agent interfaces."""

import argparse
from collections.abc import Sequence

from coding_agent.cli import main as run_classic
from coding_agent.tui import run_tui


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Observable coding agent")
    parser.add_argument("--classic", action="store_true", help="use the legacy Rich REPL")
    arguments = parser.parse_args(argv)
    if arguments.classic:
        run_classic()
    else:
        run_tui()


if __name__ == "__main__":
    main()
