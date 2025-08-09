#!/usr/bin/env bash
set -euo pipefail
python src/train.py --input data/sample_logins.csv --model_dir models
