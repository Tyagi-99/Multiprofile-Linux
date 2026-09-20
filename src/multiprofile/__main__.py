from __future__ import annotations
import argparse
from .logging_setup import setup_logging
from .app import open_ui, run_server, start_background_server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Multiprofile Native (Linux)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--server-only", action="store_true", help="Run API/UI HTTP server without desktop window")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    setup_logging(args.log_level)
    url = f"http://{args.host}:{args.port}/"

    if args.server_only:
        run_server(args.host, args.port)
        return

    start_background_server(args.host, args.port)
    open_ui(url)


if __name__ == "__main__":
    main()
