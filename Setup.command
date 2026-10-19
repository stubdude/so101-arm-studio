#!/bin/zsh
cd "${0:A:h}"
if command -v python3.12 >/dev/null; then
  python3.12 install.py --interactive
else
  python3 install.py --interactive
fi
read '?Press Enter to close this window…'
