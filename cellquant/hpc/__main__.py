"""Command line for HPC preparation, the cluster worker and import. The window uses the same functions.

Exit codes: 0 done (including an accepted partial import); 2 invalid arguments,
package or profile; 3 runtime or preflight mismatch; 4 the computation ended
with failed or unfinished images; 5 publication, import I/O or integrity
failure; 130 cancelled.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

EXIT_OK, EXIT_INVALID, EXIT_RUNTIME, EXIT_UNFINISHED, EXIT_IO, EXIT_CANCELLED = 0, 2, 3, 4, 5, 130


def _print_issues(title: str, issues, stream=sys.stderr) -> None:
    if not issues:
        return
    print(title, file=stream)
    for issue in issues:
        where = f" [{issue.acquisition_id}]" if getattr(issue, "acquisition_id", None) else ""
        print(f"  {issue.code}{where}: {issue.message}", file=stream)
        if getattr(issue, "fix", None):
            print(f"      Fix: {issue.fix}", file=stream)


def _emit(value: dict, output: str | None) -> None:
    text = json.dumps(value, indent=2, default=str)
    if output in (None, "-"):
        print(text)
    else:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(text + "\n", encoding="utf-8")


def cmd_runtime_inspect(args) -> int:
    from cellquant.hpc.runtime import inspect_runtime

    try:
        contract = inspect_runtime(
            engine=args.engine,
            model=args.model,
            runtime_id=args.runtime_id,
            lock_file=args.lock,
            supported_modes=[mode for mode in (args.modes or "").split(",") if mode],
            models_dir=args.models_dir,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_RUNTIME
    Path(args.output).write_text(contract.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}. It records what is installed; it does not validate any mode.")
    print("Enable modes (supported_modes) and set runtime_validation_date only after the preflight and parity checks pass.")
    return EXIT_OK


def cmd_prepare(args) -> int:
    from cellquant.controller import AnalysisController
    from cellquant.hpc.prepare import PreparationError, plan_preparation, prepare_package, preparation_summary
    from cellquant.hpc.profiles import load_profile
    from cellquant.progress import AnalysisCancelled

    try:
        controller = AnalysisController.open(args.experiment)
        profile = load_profile(args.profile)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not open the experiment or profile: {exc}", file=sys.stderr)
        return EXIT_INVALID
    ids = [item.strip() for item in args.image_ids.split(",")] if args.image_ids else None
    plan = plan_preparation(controller.experiment, controller.recipe, profile, image_ids=ids, confirm_channel_layout=args.confirm_channel_layout)
    summary = preparation_summary(plan)
    print(f"{summary['images']} images, {summary['input_gib']} GiB of pixels, up to {summary['peak_memory_gib']} GiB of memory while preparing.")
    _print_issues("Warnings:", plan.all_warnings(), sys.stdout)
    if not plan.ok:
        _print_issues("The package cannot be prepared:", plan.all_errors())
        return EXIT_INVALID
    try:
        result = prepare_package(plan, args.output)
    except PreparationError as exc:
        print(str(exc), file=sys.stderr)
        _print_issues("Problems:", exc.issues)
        return EXIT_INVALID
    except AnalysisCancelled:
        return EXIT_CANCELLED
    except OSError as exc:
        print(f"Writing the package failed: {exc}", file=sys.stderr)
        return EXIT_IO
    print(f"Package ready: {result.package_dir}")
    print(f"Local record of the source files (not transferred): {result.sidecar}")
    print(f"Next: transfer the folder and follow {result.package_dir / 'README_SUBMIT.md'}")
    return EXIT_OK


def cmd_validate(args) -> int:
    from cellquant.hpc.validate import validate_bundle

    report, _bundle = validate_bundle(args.bundle, require_ready=True, deep=args.deep)
    if args.output:
        _emit(report.model_dump(mode="json"), args.output)
    _print_issues("Warnings:", report.warnings, sys.stdout)
    if not report.ok:
        _print_issues("The package is not valid:", report.errors)
        return EXIT_INVALID
    print(f"Package {report.bundle_id} is valid ({'every pixel checked' if args.deep else 'every file checked against its checksum'}).")
    return EXIT_OK


def cmd_allocate(args) -> int:
    from cellquant.hpc.common import read_json, sha256_file
    from cellquant.hpc.lease import current
    from cellquant.hpc.models import BundleManifest, RunAllocation, load_model
    from cellquant.hpc.runner import allocate_attempt, allocate_run

    bundle_dir = Path(args.bundle)
    manifest = load_model(BundleManifest, bundle_dir / "bundle.json")
    digest = sha256_file(bundle_dir / "checksums.json")
    ready = read_json(bundle_dir / "READY") if (bundle_dir / "READY").is_file() else {}
    if ready.get("checksums_sha256") != digest or ready.get("bundle_id") != manifest.bundle_id:  # type: ignore[union-attr]
        print("The package is not READY. Run validate first.", file=sys.stderr)
        return EXIT_INVALID
    results_root = Path(args.results)
    if args.resume:
        run_dir = results_root / args.resume
        if not (run_dir / "run.json").is_file():
            print(f"There is no run {args.resume} in {results_root}.", file=sys.stderr)
            return EXIT_INVALID
        allocation = load_model(RunAllocation, data=read_json(run_dir / "run.json"))
        if allocation.bundle_id != manifest.bundle_id or allocation.ready_digest != digest:  # type: ignore[union-attr]
            print("That run belongs to a different package (or a changed copy of this one).", file=sys.stderr)
            return EXIT_INVALID
        held = current(run_dir)
        if held is not None:
            print(f"Run {args.resume} is still owned by job {held.job_id or '?'} (attempt {held.attempt_id}).", file=sys.stderr)
            print("If that job has ended (check with: sacct -j JOB_ID), release it with:", file=sys.stderr)
            print(
                "  "
                + shlex.join(
                    [
                        "python", "-m", "cellquant.hpc", "recover-lease", "--bundle", str(bundle_dir), "--results", str(run_dir),
                        "--run-id", held.run_id, "--attempt-id", held.attempt_id, "--job-id", held.job_id or "JOB_ID",
                        "--expected-lease-token", held.lease_token,
                    ]
                ),
                file=sys.stderr,
            )
            return EXIT_INVALID
        run_id = allocation.run_id  # type: ignore[union-attr]
    else:
        run_id, run_dir = allocate_run(results_root, manifest.bundle_id, digest, manifest.package_name)  # type: ignore[union-attr]
    attempt_id, attempt_dir = allocate_attempt(run_dir, run_id, resume=bool(args.resume))
    values = {"RUN_ID": run_id, "ATTEMPT_ID": attempt_id, "RUN_DIR": str(run_dir), "LOG_DIR": str(attempt_dir / "logs")}
    if args.shell:
        print("\n".join(f"{name}={shlex.quote(value)}" for name, value in values.items()))
    else:
        print(json.dumps(values, indent=2))
    return EXIT_OK


def cmd_record_job(args) -> int:
    from cellquant.hpc.common import read_json, write_json_atomic

    path = Path(args.run_dir) / "attempts" / args.attempt_id / "attempt.json"
    data = read_json(path)
    data["job_id"] = str(args.job_id)
    write_json_atomic(path, data)
    return EXIT_OK


def cmd_mark_attempt(args) -> int:
    from cellquant.hpc.common import utc_now

    path = Path(args.run_dir) / "attempts" / args.attempt_id / "notes.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now()} {args.note}\n")
    return EXIT_OK


def cmd_preflight(args) -> int:
    from cellquant.hpc.preflight import run_preflight

    report = run_preflight(args.bundle, require_gpu=args.require_gpu)
    _emit(report.model_dump(mode="json"), args.output)
    if not report.ok:
        _print_issues("Preflight failed:", report.errors)
        codes = {issue.code for issue in report.errors}
        return EXIT_INVALID if codes & {"E_READY", "E_CHECKSUM", "E_SCHEMA", "E_MISSING_FILE", "E_PATH", "E_UNSUPPORTED_SCHEMA"} else EXIT_RUNTIME
    print("Preflight passed" + (" on the GPU." if args.require_gpu else " (no GPU work on this node)."), file=sys.stderr)
    return EXIT_OK


def cmd_run(args) -> int:
    from cellquant.hpc.runner import Runner, Stop

    stop = Stop()
    stop.install()
    runner = Runner(
        args.bundle,
        args.scratch_results,
        args.publish_to,
        attempt_id=args.attempt_id,
        resume=args.resume,
        require_gpu=not args.allow_cpu_for_testing,
        stop=stop,
    )
    return runner.run()


def cmd_recover_lease(args) -> int:
    from cellquant.hpc.common import sha256_file
    from cellquant.hpc.lease import RecoveryRefused, SlurmScheduler, recover

    digest = sha256_file(Path(args.bundle) / "checksums.json")
    try:
        evidence = recover(
            Path(args.results),
            run_id=args.run_id,
            attempt_id=args.attempt_id,
            job_id=args.job_id,
            expected_token=args.expected_lease_token,
            ready_digest=digest,
            scheduler=SlurmScheduler(),
        )
    except RecoveryRefused as exc:
        print(f"Not recovered: {exc}", file=sys.stderr)
        return EXIT_INVALID
    print(f"Released the lease of job {args.job_id}. Slurm reported: {', '.join(evidence['accounting_states'])}.")
    print(f"Continue with: bash scripts/submit.sh --resume {args.run_id}")
    return EXIT_OK


def cmd_import(args) -> int:
    from cellquant.hpc.import_results import ImportFailed, import_results
    from cellquant.progress import AnalysisCancelled

    try:
        report = import_results(args.bundle, args.results, args.destination, allow_partial=args.allow_partial)
    except ImportFailed as exc:
        print(str(exc), file=sys.stderr)
        _print_issues("Problems:", exc.issues)
        return exc.exit_code
    except AnalysisCancelled:
        return EXIT_CANCELLED
    print(report.summary_text())
    return EXIT_OK


def cmd_schemas(args) -> int:
    from cellquant.hpc.models import write_schemas

    for path in write_schemas(args.output):
        print(path)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cellquant.hpc", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    item = sub.add_parser("runtime-inspect", help="(cluster, maintainer) describe this environment as a runtime contract")
    item.add_argument("--engine", required=True, choices=["cellpose3", "cellpose4"])
    item.add_argument("--model", required=True)
    item.add_argument("--runtime-id", required=True)
    item.add_argument("--lock", required=True, help="the dependency lock file the environment was built from")
    item.add_argument("--modes", default="", help="comma-separated Z modes to enable (only after validation)")
    item.add_argument("--models-dir", help="model folder (default: CELLPOSE_LOCAL_MODELS_PATH or ~/.cellpose/models)")
    item.add_argument("--output", required=True)
    item.set_defaults(func=cmd_runtime_inspect)

    item = sub.add_parser("prepare", help="(your computer) make a package from an experiment")
    item.add_argument("--experiment", required=True)
    item.add_argument("--profile", required=True)
    item.add_argument("--output", required=True, help="folder in which the new package folder is created")
    item.add_argument("--image-ids", help="comma-separated image IDs (default: included images)")
    item.add_argument("--confirm-channel-layout", action="store_true", help="accept differing channel names in the same order")
    item.set_defaults(func=cmd_prepare)

    item = sub.add_parser("validate", help="check a package's files, records and READY binding")
    item.add_argument("--bundle", required=True)
    item.add_argument("--deep", action="store_true", help="also read every image and check its pixels")
    item.add_argument("--output")
    item.set_defaults(func=cmd_validate)

    item = sub.add_parser("allocate", help="(used by submit.sh) create a run and an attempt folder")
    item.add_argument("--bundle", required=True)
    item.add_argument("--results", required=True)
    item.add_argument("--resume")
    item.add_argument("--shell", action="store_true")
    item.set_defaults(func=cmd_allocate)

    item = sub.add_parser("record-job", help="(used by submit.sh) note the Slurm job ID of an attempt")
    item.add_argument("--run-dir", required=True)
    item.add_argument("--attempt-id", required=True)
    item.add_argument("--job-id", required=True)
    item.set_defaults(func=cmd_record_job)

    item = sub.add_parser("mark-attempt", help="(used by job.sbatch) add a note to an attempt")
    item.add_argument("--run-dir", required=True)
    item.add_argument("--attempt-id", required=True)
    item.add_argument("--note", required=True)
    item.set_defaults(func=cmd_mark_attempt)

    item = sub.add_parser("preflight", help="check the package and software; with --require-gpu, also the GPU")
    item.add_argument("--bundle", required=True)
    item.add_argument("--require-gpu", action="store_true")
    item.add_argument("--output", default="-")
    item.set_defaults(func=cmd_preflight)

    item = sub.add_parser("run", help="(inside the job) analyze and publish")
    item.add_argument("--bundle", required=True)
    item.add_argument("--scratch-results", required=True)
    item.add_argument("--publish-to", required=True)
    item.add_argument("--attempt-id")
    item.add_argument("--resume", action="store_true")
    item.add_argument("--allow-cpu-for-testing", action="store_true", help=argparse.SUPPRESS)
    item.set_defaults(func=cmd_run)

    item = sub.add_parser("recover-lease", help="(cluster) release the lease of a job that has ended")
    item.add_argument("--bundle", required=True)
    item.add_argument("--results", required=True, help="the run folder")
    item.add_argument("--run-id", required=True)
    item.add_argument("--attempt-id", required=True)
    item.add_argument("--job-id", required=True)
    item.add_argument("--expected-lease-token", required=True)
    item.set_defaults(func=cmd_recover_lease)

    item = sub.add_parser("import", help="(your computer) make a new experiment from a package and its results")
    item.add_argument("--bundle", required=True, help="the prepared package on this computer")
    item.add_argument("--results", required=True, help="the downloaded run folder")
    item.add_argument("--destination", required=True, help="a new, empty folder for the imported experiment")
    item.add_argument("--allow-partial", action="store_true", help="import finished images when some did not finish")
    item.set_defaults(func=cmd_import)

    item = sub.add_parser("schemas", help="write the JSON schemas of every record")
    item.add_argument("--output", required=True)
    item.set_defaults(func=cmd_schemas)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_INVALID if exc.code not in (0, None) else EXIT_OK
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
