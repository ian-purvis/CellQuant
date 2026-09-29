"""Launch CellQuant, or run an experiment without the viewer."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cellquant")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Apply a recipe to an experiment")
    run.add_argument("--experiment", required=True)
    run.add_argument("--recipe")
    parser.add_argument("experiment", nargs="?", help="Open this experiment folder")
    args = parser.parse_args(argv)
    if args.command == "run":
        from cellquant.controller import process_experiment

        report = process_experiment(args.experiment, args.recipe)
        print(
            f"{report.run_id}: completed {report.completed}, "
            f"warnings {report.warnings}, failed {report.failed}"
        )
        if report.failed:
            sys.exit(1)
        return
    from cellquant.gui.app import launch

    launch(args.experiment)


if __name__ == "__main__":
    main()
