import argparse
import csv
import datetime
import logging
import os
import sys

from dotenv import load_dotenv

from classifier import classify_email, verify_model_available
from mail_client import RunResult, load_config, process_account

HISTORY_FIELDS = ["timestamp", "account", "dry_run", "evaluated", "ham", "spam", "moved", "error"]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM-based IMAP spam filter using a local Ollama model.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env-file", default=".env", help="Path to .env file with account passwords")
    parser.add_argument("--account", help="Only process the account with this name")
    parser.add_argument("--dry-run", action="store_true", help="Classify and log only; do not modify mailboxes")
    parser.add_argument("--log-file", help="Optional path to write logs to, in addition to stdout")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--history-file", default="run_history.csv", help="CSV file to append per-account run stats to")
    parser.add_argument("--no-history", action="store_true", help="Skip writing to the run history CSV")
    return parser


def append_history(history_file: str, dry_run: bool, results: list[RunResult]) -> None:
    file_exists = os.path.isfile(history_file)
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(history_file, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if not file_exists:
            writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "timestamp": timestamp,
                    "account": r.account,
                    "dry_run": dry_run,
                    "evaluated": r.evaluated,
                    "ham": r.ham,
                    "spam": r.spam,
                    "moved": r.moved,
                    "error": r.error,
                }
            )


def setup_logging(log_file: str | None, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def main() -> int:
    args = build_arg_parser().parse_args()
    setup_logging(args.log_file, args.verbose)
    logger = logging.getLogger("spamfilter")

    load_dotenv(args.env_file)

    try:
        accounts, settings = load_config(args.config)
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        return 1

    if args.account:
        accounts = [a for a in accounts if a.name == args.account]
        if not accounts:
            logger.error("No account named '%s' found in config", args.account)
            return 1

    try:
        verify_model_available(settings.ollama_model, settings.ollama_url, timeout=settings.ollama_timeout_seconds)
    except RuntimeError as e:
        logger.error(str(e))
        return 1

    if args.dry_run:
        logger.info("Running in --dry-run mode: no messages will be flagged or moved")

    def classify_fn(body: str):
        return classify_email(
            body,
            model=settings.ollama_model,
            url=settings.ollama_url,
            timeout=settings.ollama_timeout_seconds,
        )

    exit_code = 0
    results: list[RunResult] = []
    for account in accounts:
        try:
            results.append(process_account(account, settings, classify_fn, dry_run=args.dry_run))
        except Exception as e:
            logger.exception("[%s] unexpected error: %s", account.name, e)
            results.append(RunResult(account=account.name, error=str(e)))
            exit_code = 1

    if not args.no_history:
        append_history(args.history_file, args.dry_run, results)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
