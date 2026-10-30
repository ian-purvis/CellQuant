"""Launch CellQuant, or run an experiment without the viewer."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cellquant",
        description="Open the CellQuant window, or analyze an experiment without it.",
        epilog="Examples:\n  cellquant                      open the window\n  cellquant \"D:/My results\"     open that experiment\n"
        "  cellquant run --experiment \"D:/My results\" --recipe recipe.yaml\n"
        "  cellquant analyze               open QuantAnalysis on its own, without the image viewer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="run | analyze")
    run = sub.add_parser(
        "run",
        help="Analyze every included image of an experiment, without the window",
        description="Analyze every included image of an experiment with its settings (or the settings in --recipe).",
    )
    run.add_argument("--experiment", dest="run_experiment", required=True, metavar="FOLDER", help="The experiment's results folder (it holds experiment.json).")
    run.add_argument("--recipe", metavar="FILE", help="A recipe.yaml to use instead of the experiment's own settings, for example from an export.")
    analyze = sub.add_parser(
        "analyze",
        help="Open QuantAnalysis (statistics and graphs) on its own, without the image viewer or Cellpose",
        description="Open QuantAnalysis on its own. Results folders and tables are opened from its File menu.",
    )
    analyze.add_argument("folder", nargs="?", metavar="FOLDER", help="A CellQuant results or export folder to import.")
    parser.add_argument("experiment", nargs="?", metavar="FOLDER", help="Open this experiment's results folder in the window.")
    args = parser.parse_args(argv)
    if args.command == "run":
        from cellquant.controller import process_experiment

        from cellquant.errors import CellQuantError
        from cellquant.instances import lock_folder

        lock = lock_folder(args.run_experiment)
        if lock is None:
            print(f"cellquant: {args.run_experiment} is open in a CellQuant window. Close it there first.", file=sys.stderr)
            sys.exit(2)
        try:
            report = process_experiment(args.run_experiment, args.recipe)
        except (CellQuantError, OSError, ValueError) as exc:
            print(f"cellquant: {exc}", file=sys.stderr)
            sys.exit(2)
        print(
            f"Run {report.run_id}: {report.completed} finished, {report.warnings} need a look, {report.failed} failed. "
            f"Results are in {args.run_experiment}."
        )
        if report.failed:
            sys.exit(1)
        return
    if args.command == "analyze":
        from cellquant.gui.quantanalysis import launch_alone

        launch_alone(args.folder)
        return
    from cellquant.gui.app import launch

    launch(args.experiment)


if __name__ == "__main__":
    main()
