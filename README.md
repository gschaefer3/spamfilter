# Spam Filter

This repository contains a simple IMAP-based spam filter that classifies messages with a local Ollama model and moves spam to a configurable folder.

## What it does

- Connects to one or more IMAP accounts
- Reads unread messages from the inbox
- Sends the message body to Ollama for classification
- Marks messages as scanned with a custom IMAP flag
- Moves spam messages to a configured mailbox folder
- Can run once or repeatedly in a loop

## Requirements

- Python 3.10+
- An Ollama instance running locally (or a reachable Ollama URL)
- IMAP account credentials

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Copy the example config and adjust it for your accounts:

```bash
cp config.example.yaml config.yaml
```

Edit [config.yaml](config.yaml) and provide:

- IMAP server and port
- username and password (or password environment variable)
- inbox folder and spam folder
- Ollama URL/model settings

Example account fields:

```yaml
accounts:
  - name: example_account
    imap_server: imap.example.com
    imap_port: 993
    username: you@example.com
    password_env: EXAMPLE_PASSWORD
    inbox_folder: INBOX
    spam_folder: SpamFilter
    create_spam_folder: true
```

Set your password environment variable before running the script, for example:

```bash
export EXAMPLE_PASSWORD="your-app-password"
```

## Running once

Run the filter once:

```bash
python spam_filter.py --verbose
```

Optional flags:

- `--dry-run` to classify without moving or flagging messages
- `--account NAME` to process only one configured account
- `--log-file path/to/file.log` to also write logs to a file
- `--history-file path/to/file.csv` to change the history CSV location
- `--no-history` to skip writing history

## Running in loop mode

A helper script is included to run the filter repeatedly every 30 seconds:

```bash
./run_spam_filter_loop.sh
```

To run it once and then stop:

```bash
./run_spam_filter_loop.sh --once
```

You can also customize retry behavior for timeout-related failures:

```bash
SPAMFILTER_MAX_RETRIES=3 SPAMFILTER_RETRY_DELAY_SECONDS=10 ./run_spam_filter_loop.sh
```

## Notes

- The script uses an IMAP flag (default: `SpamFilterScanned`) so it does not reprocess the same unread messages repeatedly.
- Message evaluation uses IMAP `BODY.PEEK[]`, so reading messages for classification does not mark them as read.
- Sensitive files such as `.env`, `config.yaml`, logs, and history files are ignored by git.
