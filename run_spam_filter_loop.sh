#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -n "${SPAMFILTER_PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$SPAMFILTER_PYTHON_BIN"
elif [[ -x "/Users/gschaefer/miniconda3/envs/spamfilter/bin/python" ]]; then
  PYTHON_BIN="/Users/gschaefer/miniconda3/envs/spamfilter/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

MAX_RETRIES="${SPAMFILTER_MAX_RETRIES:-3}"
RETRY_DELAY_SECONDS="${SPAMFILTER_RETRY_DELAY_SECONDS:-10}"
LOG_ROOT="$SCRIPT_DIR/logs"
mkdir -p "$LOG_ROOT"

get_next_log_name() {
  local today
  today=$(date +%Y%m%d)
  local log_dir="$LOG_ROOT/$today"
  mkdir -p "$log_dir"
  local highest=0

  for path in "$log_dir"/${today}_*.log; do
    [[ -e "$path" ]] || continue
    local filename
    filename=$(basename "$path")
    local number=${filename#${today}_}
    number=${number%.log}

    if [[ "$number" =~ ^[0-9]+$ ]]; then
      local numeric_number=$((10#$number))
      if (( numeric_number > highest )); then
        highest=$numeric_number
      fi
    fi
  done

  printf '%03d\n' $((highest + 1))
}

log_contains_timeout() {
  local log_file="$1"
  if [[ ! -f "$log_file" ]]; then
    return 1
  fi

  grep -Eiq 'timed out|TimeoutError|Operation timed out|socket\.timeout|temporarily unavailable' "$log_file"
}

run_once() {
  local today
  today=$(date +%Y%m%d)
  local next_number
  next_number=$(get_next_log_name)
  local log_file="$LOG_ROOT/$today/${today}_${next_number}.log"
  local attempt
  local exit_code=0

  echo "Running spam_filter.py -> $log_file"

  for ((attempt = 1; attempt <= MAX_RETRIES; attempt++)); do
    : > "$log_file"
    echo "Attempt $attempt/$MAX_RETRIES for $log_file" >> "$log_file"

    set +e
    "$PYTHON_BIN" spam_filter.py --verbose >> "$log_file" 2>&1
    exit_code=$?
    set -e

    if [[ $exit_code -eq 0 ]]; then
      echo "Completed successfully." >> "$log_file"
      return 0
    fi

    if log_contains_timeout "$log_file" && [[ $attempt -lt $MAX_RETRIES ]]; then
      echo "Detected timeout failure; retrying in ${RETRY_DELAY_SECONDS}s..." >> "$log_file"
      sleep "$RETRY_DELAY_SECONDS"
      continue
    fi

    echo "Run failed with exit code $exit_code." >> "$log_file"
    return "$exit_code"
  done
}

if [[ "${1:-}" == "--once" ]]; then
  run_once
  exit 0
fi

while true; do
  run_once || true
  sleep 30
done
