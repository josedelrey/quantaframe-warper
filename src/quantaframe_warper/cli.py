"""Command line entry point."""

import argparse
import json

from . import Engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Decode and analyze a video")
    inspect.add_argument("video")
    commands.add_parser("ui", help="Start a local browser workbench")
    args = parser.parse_args()
    if args.command == "ui":
        from .ui.server import main as serve

        serve()
        return
    try:
        with Engine(args.video) as engine:
            print(json.dumps(engine.info, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")
