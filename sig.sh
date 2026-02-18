bash -lc '
set -euo pipefail

DEVNAME="dev-pod-linux-cli"
LOG="${LOG:-/tmp/signal-link.$$.log}"
PNG="${PNG:-/tmp/signal-link.$$.png}"
DEST="${DEST:-kacper@192.168.1.201:~/signal-link.png}"

rm -f "$LOG" "$PNG"

# Start linking and log all output
(signal-cli link -n "$DEVNAME" 2>&1 | tee "$LOG") & pid=$!

# Extract the first sgnl:// URI (wait up to ~20s)
uri=""
for _ in $(seq 1 200); do
  uri="$(grep -m1 -oE "sgnl://[^[:space:]]+" "$LOG" || true)"
  [[ -n "$uri" ]] && break
  sleep 0.1
done

if [[ -z "$uri" ]]; then
  echo "Failed to capture provisioning URI. Full output:" >&2
  cat "$LOG" >&2 || true
  kill "$pid" 2>/dev/null || true
  exit 1
fi

qrencode -s 10 -m 2 -o "$PNG" "$uri"
scp "$PNG" "$DEST"

# Wait for linking to finish
wait "$pid"
'
