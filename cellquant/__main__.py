"""Launch CellQuant, or run an experiment without the viewer."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cellquant",
        description="Open the CellQuant window, or analyze an experiment without it.",
        epilog="Examples:\n  cellquant                      open the window\n  cellquant \"D:/My results\"     open that experiment\n"
        "  cellquant run --experiment \"D:/My results\" --recipe recipe.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="run")
    run = sub.add_parser(
        "run",
        help="Analyze every included image of an experiment, without the window",
        description="Analyze every included image of an experiment with its settings (or the settings in --recipe).",
    )
    run.add_argument("--experiment", required=True, metavar="FOLDER", help="The experiment's results folder (it holds experiment.json).")
    run.add_argument("--recipe", metavar="FILE", help="A recipe.yaml to use instead of the experiment's own settings, for example from an export.")
    parser.add_argument("experiment", nargs="?", metavar="FOLDER", help="Open this experiment's results folder in the window.")
    args = parser.parse_args(argv)
    if args.command == "run":
        from cellquant.controller import process_experiment

        from cellquant.errors import CellQuantError

        try:
            report = process_experiment(args.experiment, args.recipe)
        except (CellQuantError, OSError, ValueError) as exc:
            print(f"cellquant: {exc}", file=sys.stderr)
            sys.exit(2)
        print(
            f"Run {report.run_id}: {report.completed} finished, {report.warnings} need a look, {report.failed} failed. "
            f"Results are in {args.experiment}."
        )
        if report.failed:
            sys.exit(1)
        return
    from cellquant.gui.app import launch

    launch(args.experiment)


if __name__ == "__main__":
    main()
