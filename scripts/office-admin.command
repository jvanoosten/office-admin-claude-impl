#!/bin/bash

# Initialize conda properly
source ~/miniconda3/etc/profile.d/conda.sh
conda activate office-admin-app-env

cd ~/Documents/office-admin-1.1.0

(sleep 2 && open http://127.0.0.1:8000) &

python -m uvicorn src.api:app