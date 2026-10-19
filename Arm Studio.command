#!/bin/zsh
cd "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
  .venv/bin/python run.py "$@"
elif command -v python3.12 >/dev/null; then
  python3.12 run.py "$@"
else
  python3 run.py "$@"
fi
if [[ $? -ne 0 ]]; then
  read '?Press Enter to close this window…'
fi
