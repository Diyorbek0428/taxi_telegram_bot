#!/usr/bin/env bash
# Adjust WORKDIR if you placed project elsewhere
WORKDIR="/home/youruser/taxi_bot"  # <-- change to your project path on server
cd "$WORKDIR" || exit 1

# Activate venv
if [ -f "$WORKDIR/venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  . "$WORKDIR/venv/bin/activate"
fi

# Exec bot (systemd will track the process)
exec python taxi_bot.py
