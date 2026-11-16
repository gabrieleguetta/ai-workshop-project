"""Nutrition Planner CLI entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

from agent import Agent
from providers import ProviderError, select_provider
from tools import build_tools


def main(
    argv: Optional[list[str]] = None,
    input_fn: Callable = input,
    output_fn: Callable = print,
    provider_factory: Callable = select_provider,
) -> int:
    parser = argparse.ArgumentParser(description="Collaborative Nutrition Planner / מתכנן ארוחות")
    parser.add_argument("--provider", choices=("auto", "openrouter", "codex"), default=None)
    args = parser.parse_args(argv)
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    try:
        provider = provider_factory(args.provider or os.environ.get("PLANNER_PROVIDER", "auto"))
    except (ProviderError, OSError) as error:
        output_fn(f"Setup: {error}")
        return 2
    output_fn(f"Nutrition Planner — {provider.name}")
    output_fn("Fresh session. Type exit to leave. / תכנון חדש. אפשר לכתוב יציאה כדי לסיים.")
    output_fn(
        "What days and meals would you like to plan, and for how many diners?\n"
        "אילו ימים וארוחות תרצה לתכנן, ולכמה סועדים?"
    )
    agent = Agent(provider, build_tools())
    while True:
        try:
            text = input_fn("You / אתה: ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("Goodbye / להתראות")
            return 0
        if text.casefold() in ("exit", "quit", "יציאה", "להתראות"):
            output_fn("Goodbye / להתראות")
            return 0
        if not text:
            continue
        try:
            output_fn(agent.reply(text))
        except ProviderError as error:
            output_fn(str(error))
        except KeyboardInterrupt:
            output_fn("Goodbye / להתראות")
            return 0


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
