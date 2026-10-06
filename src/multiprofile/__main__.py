from __future__ import annotations

import argparse

from .app import open_ui, run_server, start_background_server
from .logging_setup import setup_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Multiprofile Native (Linux)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--server-only", action="store_true", help="Run API/UI HTTP server without desktop window")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--doctor", action="store_true",
        help="print a short diagnosis of this installation and exit "
             "(no network requests, nothing launched)")
    parser.add_argument(
        "--doctor-json", action="store_true",
        help="the same diagnosis as JSON, for a bug report")
    args = parser.parse_args(argv)

    if args.doctor or args.doctor_json:
        import json as _json

        from .doctor import format_report, run_doctor

        setup_logging("WARNING")
        report = run_doctor()
        print(_json.dumps(report.to_dict(), indent=2) if args.doctor_json
              else format_report(report))
        # Exit status is the useful part for a script: non-zero when a
        # profile will not work until something is fixed.
        raise SystemExit(0 if report.ok else 1)

    setup_logging(args.log_level)
    url = f"http://{args.host}:{args.port}/"

    if args.server_only:
        run_server(args.host, args.port)
        return

    start_background_server(args.host, args.port)
    open_ui(url)


if __name__ == "__main__":
    main()
