#!/bin/sh
cd "$(dirname "$0")" && exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port "${PORT:-8000}"
