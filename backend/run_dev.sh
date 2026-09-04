#!/bin/bash
# AgentAlgo dev supervisor — runs uvicorn in ITS OWN SESSION so terminal/session
# cleanup can never kill it, and restarts it if it exits (crash resilience).
# Usage: ./run_dev.sh start | stop | status
cd "$(dirname "$0")"
PIDFILE=data/supervisor.pid

case "${1:-start}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "already running (supervisor pid $(cat "$PIDFILE"))"; exit 0
    fi
    # start_new_session=True detaches from the caller's process group entirely
    python3 - << 'PYEOF'
import subprocess, pathlib
script = r'''
echo $$ > data/supervisor.pid
( while true; do
    .venv/bin/python ../bridge/cowork_bridge.py >> data/bridge.log 2>&1
    echo "$(date) bridge exited ($?) — restarting in 2s" >> data/bridge.log
    sleep 2
  done ) &
while true; do
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >> data/server.log 2>&1
  echo "$(date) uvicorn exited ($?) — restarting in 2s" >> data/server.log
  sleep 2
done
'''
subprocess.Popen(["/bin/bash", "-c", script], start_new_session=True,
                 cwd=str(pathlib.Path(".").resolve()))
print("supervisor launched (own session: backend + cowork bridge)")
PYEOF
    sleep 3; curl -s -o /dev/null -w "health:%{http_code}\n" http://127.0.0.1:8000/api/health
    ;;
  stop)
    [ -f "$PIDFILE" ] && kill -- -"$(cat "$PIDFILE")" 2>/dev/null && rm -f "$PIDFILE" && echo stopped
    pkill -f "uvicorn app.main:app" 2>/dev/null
    pkill -f "bridge/cowork_bridge.py" 2>/dev/null
    ;;
  status)
    pgrep -fl "uvicorn app.main:app" || echo "not running"
    ;;
  restart)
    "$0" stop; sleep 2; "$0" start
    ;;
  *)
    echo "usage: $0 start|stop|status|restart"; exit 1
    ;;
esac
