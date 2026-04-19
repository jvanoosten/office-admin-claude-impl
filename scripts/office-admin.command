#!/bin/bash

cd ~/Documents/git/office-admin-claude-impl

(sleep 2 && open http://127.0.0.1:8000) &

uv run python main.py
