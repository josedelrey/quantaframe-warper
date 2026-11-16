"""Command line entry point."""

import argparse
import json

from PIL import Image

from . import Engine, Project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Decode and analyze a video")
    inspect.add_argument("video")
    for name in ("preview", "export"):
        command = commands.add_parser(name)
        command.add_argument("project")
        command.add_argument("output")
        if name == "preview":
            command.add_argument("--frame", type=int, required=True)
    commands.add_parser("ui", help="Start a local browser workbench")
    args = parser.parse_args()
    if args.command == "ui":
        from .ui.server import main as serve

        serve()
        return
    try:
        project = None if args.command == "inspect" else Project.load(args.project)
        with Engine(args.video if project is None else project.video) as engine:
            if args.command == "inspect":
                print(json.dumps(engine.info, indent=2))
            elif args.command == "preview":
                Image.fromarray(engine.render(project, args.frame)).save(args.output)
            else:
                engine.export(project, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")
