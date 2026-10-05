#!/bin/bash
# Chrome talks to D-Bus while it starts. On Docker Desktop the system bus is
# missing, and a socket file left behind after the daemon exits is worse:
# Chrome waits 20s for that dead socket. Egress uses the same 20s budget to
# see "DevTools listening on", then reports "websocket url timeout reached"
# and writes no MP4.
# The image user cannot bind the system bus, so this wrapper starts it as
# root and then runs the service as the egress user.
# https://docs.livekit.io/transport/self-hosting/egress/
#
# The image entrypoint also starts PulseAudio, then the service:
# https://github.com/livekit/egress/blob/v1.14.1/build/egress/entrypoint.sh
# That script uses set -e and deletes /var/lib/pulse as the egress user.
# The pulseaudio package owns that directory as root, and the egress user
# cannot unlink it from /var/lib, so the script exits with
# "rm: cannot remove '/var/lib/pulse': Permission denied".
# The worker never registers on Redis. StartEgress waits about 21 seconds
# and returns "no response from servers".
set -euo pipefail

mkdir -p \
  /run/dbus \
  /tmp/runtime-egress \
  /home/egress/tmp \
  /home/egress/.cache/xdgr \
  /home/egress/.config
if id egress >/dev/null 2>&1; then
  chown -R egress:root \
    /tmp/runtime-egress \
    /home/egress \
    2>/dev/null || true
fi
chmod 700 /tmp/runtime-egress /home/egress/.cache/xdgr || true
if [ -d /out ]; then
  chown egress:root /out 2>/dev/null || true
fi

# Remove the root-owned PulseAudio state before the egress user starts.
# Do not recreate these directories. The user cannot delete them, and a
# user-mode PulseAudio daemon uses XDG_RUNTIME_DIR instead.
rm -rf \
  /var/run/pulse \
  /run/pulse \
  /var/lib/pulse \
  /home/egress/.config/pulse \
  /home/egress/.cache/xdgr/pulse

# docker restart keeps the socket file and kills the daemon. A file with
# nothing listening must be removed or the next Chrome waits out the deadline.
if ! dbus-send --system --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
  rm -f /run/dbus/pid /var/run/dbus/pid /run/dbus/system_bus_socket /var/run/dbus/system_bus_socket
  dbus-daemon --system --fork || true
fi

# Chrome connects to the session bus while it starts. The egress user has to
# own that socket. A path with no daemon makes the connect sit for about 20s,
# which is the same budget Egress uses to see "DevTools listening on". It
# then reports "page load error: websocket url timeout reached" and Stop
# finds a failed job instead of an MP4.
session_bus=/home/egress/.cache/xdgr/bus
rm -f "$session_bus"
if id egress >/dev/null 2>&1 && command -v runuser >/dev/null 2>&1; then
  runuser -u egress -- dbus-daemon --session --address="unix:path=${session_bus}" --fork --nopidfile
else
  dbus-daemon --session --address="unix:path=${session_bus}" --fork --nopidfile
fi
if [ ! -S "$session_bus" ]; then
  echo "Chrome session bus did not start at ${session_bus}" >&2
  exit 1
fi
export DBUS_SESSION_BUS_ADDRESS="unix:path=${session_bus}"
export DBUS_SYSTEM_BUS_ADDRESS="unix:path=${session_bus}"
export NO_AT_BRIDGE=1
# Chrome's default Pulse buffer is a few milliseconds. On a busy Docker host
# that underruns, so the recording is choppy and the MP4 timestamps jump.
export PULSE_LATENCY_MSEC=80
mkdir -p /etc/pulse/daemon.conf.d
cat > /etc/pulse/daemon.conf.d/recording.conf << 'EOF'
default-sample-rate = 48000
alternate-sample-rate = 48000
default-fragments = 8
default-fragment-size-msec = 25
EOF

# /usr/local/bin is ahead of /usr/bin, so Egress launches this instead of the
# distro wrapper. That wrapper sends stdout through `cat`, which hides
# "DevTools listening on" until the buffer fills. chromedp gives up at 20s
# and reports "page load error: websocket url timeout reached".
# Run the Chrome binary directly. Do not put stdbuf in front of it: the
# preload makes a headed launch take about 16s, and the same deadline is 20s.
# Keep the session bus started above. Do not replace it with a missing socket.
cat > /usr/local/bin/google-chrome << 'EOF'
#!/bin/bash
export CHROME_WRAPPER=/opt/google/chrome/google-chrome
export CHROME_VERSION_EXTRA=stable
export GNOME_DISABLE_CRASH_DIALOG=SET_BY_GOOGLE_CHROME
if [ -S /home/egress/.cache/xdgr/bus ]; then
  export DBUS_SESSION_BUS_ADDRESS=unix:path=/home/egress/.cache/xdgr/bus
  export DBUS_SYSTEM_BUS_ADDRESS=unix:path=/home/egress/.cache/xdgr/bus
fi
export NO_AT_BRIDGE=1
export PULSE_LATENCY_MSEC="${PULSE_LATENCY_MSEC:-80}"
exec -a google-chrome /opt/google/chrome/chrome "$@"
EOF
chmod 755 /usr/local/bin/google-chrome

export EGRESS_CONFIG_FILE="${EGRESS_CONFIG_FILE:-/etc/egress.yaml}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/home/egress/.cache/xdgr}"
export HOME=/home/egress
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/chrome"

# Same steps as the image entrypoint. The pulse cleanup is non-fatal so a
# directory the user still cannot unlink does not skip the service. Tini
# reaps Chrome. It is not PID 1 here, so it only does that with
# TINI_SUBREAPER=1. v1.14.1 execs tini; newer images exec egress directly.
launcher=(env
  HOME="$HOME"
  USER=egress
  LOGNAME=egress
  PATH="$PATH"
  XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"
  EGRESS_CONFIG_FILE="$EGRESS_CONFIG_FILE"
  LIVEKIT_API_KEY="${LIVEKIT_API_KEY:-}"
  LIVEKIT_API_SECRET="${LIVEKIT_API_SECRET:-}"
  LIVEKIT_WS_URL="${LIVEKIT_WS_URL:-}"
  CHROME_DEVEL_SANDBOX="${CHROME_DEVEL_SANDBOX:-/usr/local/sbin/chrome-devel-sandbox}"
  DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS}"
  DBUS_SYSTEM_BUS_ADDRESS="${DBUS_SYSTEM_BUS_ADDRESS}"
  NO_AT_BRIDGE=1
  PULSE_LATENCY_MSEC="${PULSE_LATENCY_MSEC:-80}"
  TINI_SUBREAPER=1
  bash -c '
    set -euo pipefail
    rm -rf /home/egress/tmp/* || true
    rm -rf /var/run/pulse /var/lib/pulse /home/egress/.config/pulse /home/egress/.cache/xdgr/pulse || true
    ulimit -n 65536 || true
    if ! pulseaudio -D --verbose --exit-idle-time=-1 --disallow-exit > /home/egress/tmp/pulse.log 2>&1; then
      cat /home/egress/tmp/pulse.log >&2 || true
      exit 1
    fi
    # The first headed Chrome launch reads its binaries and font cache.
    # On a cold container that uses most of the 20s DevTools budget, and a
    # busy host misses it. Open Chrome once now so a recording starts from
    # the warm cache. Failure here must not stop the service.
    warm_display=:61
    Xvfb "$warm_display" -screen 0 1280x720x24 -ac -nolisten tcp -nolisten unix >/tmp/xvfb-warm.log 2>&1 &
    warm_xvfb=$!
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      if [ -S /tmp/.X11-unix/X61 ]; then
        break
      fi
      sleep 0.1
    done
    env DISPLAY="$warm_display" /opt/google/chrome/chrome \
      --no-sandbox --disable-gpu --disable-dev-shm-usage --no-first-run \
      --user-data-dir=/tmp/chrome-warm --remote-debugging-port=0 \
      about:blank >/tmp/chrome-warm.log 2>&1 &
    warm_chrome=$!
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      if grep -q "DevTools listening" /tmp/chrome-warm.log 2>/dev/null; then
        break
      fi
      if ! kill -0 "$warm_chrome" 2>/dev/null; then
        break
      fi
      sleep 0.5
    done
    kill "$warm_chrome" "$warm_xvfb" 2>/dev/null || true
    wait "$warm_chrome" 2>/dev/null || true
    wait "$warm_xvfb" 2>/dev/null || true
    rm -rf /tmp/chrome-warm /tmp/chrome-warm.log /tmp/xvfb-warm.log || true
    if [ -x /tini ]; then
      exec /tini -- egress
    fi
    exec egress
  '
)

if command -v runuser >/dev/null 2>&1; then
  exec runuser -u egress -- "${launcher[@]}"
fi
exec su -s /bin/bash egress -c 'exec "$@"' -- "${launcher[@]}"
