#!/bin/bash
cd "$(dirname "$0")"
exec .venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 7800 --ws-max-size 65536 "$@"
