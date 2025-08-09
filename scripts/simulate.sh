#!/usr/bin/env bash
set -euo pipefail
python src/simulate_logins.py --out data/sample_logins.csv --rows 20000
