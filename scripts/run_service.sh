#!/usr/bin/env bash
set -euo pipefail
export MODEL_DIR=models
uvicorn src.service:app --host 0.0.0.0 --port 8000 --reload
