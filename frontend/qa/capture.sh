#!/usr/bin/env bash
#
# Phase 47 item 1 - browser QA capture.
#
# Loads every named screen at every named viewport in headless Chrome, twice:
#
#   --dump-dom     to read the overflow probe's findings as TEXT. A screenshot has to be
#                  eyeballed and an eye cannot see that a cell needs 197px and has 140;
#                  the probe can, and dump-dom carries its answer out of the browser.
#   --screenshot   for the visual review, which is what catches the faults no probe has a
#                  rule for: a heading that reads wrong, a control nobody would find.
#
# Findings are printed and also collected into $OUT/findings.txt. Exit status is 1 if any
# screen at any width reported an overflow, so this can gate a release.
#
# Usage: bash qa/capture.sh [output-dir]

set -uo pipefail
cd "$(dirname "$0")/.."

# Resolve Chrome across platforms. Hardcoding /usr/bin/google-chrome made this
# gate report every screen as a non-render on any machine without that exact
# path — a loud failure that reads as a layout defect rather than a missing
# binary. CHROME=/path/to/chrome still overrides.
CHROME_CANDIDATES=(
  /usr/bin/google-chrome
  /usr/bin/google-chrome-stable
  /usr/bin/chromium
  /usr/bin/chromium-browser
  /snap/bin/chromium
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  "/Applications/Chromium.app/Contents/MacOS/Chromium"
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
)
if [ -z "${CHROME:-}" ]; then
  for _c in "${CHROME_CANDIDATES[@]}"; do
    [ -x "$_c" ] && { CHROME=$_c; break; }
  done
fi
PORT=${PORT:-5199}
OUT=${1:-qa-out}
SCREENS=(# Phase 203 - the workspace: Home and the Work Queue.
         home work-queue documents-launcher memo-rail
         # Phase F - the mobile shell.
         mobile-home mobile-queue mobile-profile mobile-create
         dashboard create-minute edit-minute minute-detail archive-view
         attachment-panel member-register acknowledgement-register
         # Phase 49 - the memo module, which shares the same page chrome.
         memo-dashboard memo-detail memo-create
         # Phase 50 - the circular module.
         circular-dashboard circular-detail circular-create circular-broadcasted
         # Phase 70 - the inventory module.
         inventory-register inventory-dashboard inventory-requests inventory-maintenance
         inventory-reports
         # Phase ASSET-LIFECYCLE-DISPOSAL - the disposal queue, whose rows carry a
         # stage strip and a block of money figures that a narrow screen has to fit.
         inventory-disposals
         # Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD - the read-only register.
         inventory-visibility
         # Phase 100.1 - the three modules the Phase 100 audit could not measure,
         # now that backend/leaves/tests/test_qa_fixture.py generates their payloads
         # and qa/api-stub.js serves them to the imperative (non-react-query) pages.
         leave-dashboard leave-my-applications leave-pending leave-apply
         leave-review-drawer notifications reports-hub
         # Phase 100.1 - the memo decision path. The archived fixture exposes no
         # actions at all; this variant restores the permission flags so Support and
         # Reject exist to be clicked by the MODALS matrix below.
         memo-actionable)

# --- Phase 100.1: modal captures -------------------------------------------------
#
# A modal that is never opened is a modal that is never measured, which is how 13 of
# them reached the Phase 100 report with "audited by reading the CSS" against them.
# Each entry is `screen:button-text`; the harness clicks that control, waits for the
# portal to mount and settle, then probes -- and probeModalFooters() asserts the
# action row is sticky AND inside the viewport.
#
# Only phone widths: a modal footer is pinned at every width, but it is on a phone
# that an unpinned one actually pushes the buttons out of reach.
MODALS=(
  # The memo decision path: the four modals a memo can open on a phone.
  "memo-actionable:Support" "memo-actionable:Reject"
  "memo-actionable:Withdraw" "memo-actionable:Delete"
  # Blocker 8 - the preferences grid is behind a tab, so its 65 rows of checkboxes
  # are only measurable once that tab has been clicked.
  "notifications:Preferences"
  # Blocker 5 - the inbox's other action.
  "notifications:Mark all read"
  # Blocker 2 (leave) - the decision row at the foot of the review drawer.
  "leave-review-drawer:Reject" "leave-review-drawer:Approve"
  # Blocker 2 (leave) - the applicant-side document actions.
  "leave-my-applications:Preview PDF"
  # Phase D1 - the asset panel, which is the half of that phase a list capture
  # cannot see: four sections, a sticky action row, and a full-height drawer on
  # a phone.
  "inventory-register:Add asset"
  # Phase ASSET-LIFECYCLE-DISPOSAL - the raise-a-disposal form, which is the half
  # of that page a list capture cannot see.
  "inventory-disposals:New disposal"
)
# Override with a space-separated list, e.g. QA_MODALS="memo-actionable:Reject".
# Assigned AFTER the array above rather than as a ${...:-default}: a multi-line
# default would fold these comment lines into the array itself (the same trap the
# SCREENS list avoids).
[ -n "${QA_MODALS:-}" ] && read -r -a MODALS <<< "$QA_MODALS"
MODAL_WIDTHS=(${QA_MODAL_WIDTHS:-768 430 390 360})
# Desktop 1920 and 1440, tablet 1024 and 768, mobile 390.
#
# 1024 is the tightest DESKTOP case, not the widest tablet one: the sidebar is still in
# the flex flow at 1024 and becomes an off-canvas drawer at 1023, so the content column
# there is only 744px — narrower than at 768, where it is the full viewport. Both are
# captured because they exercise different layout paths, and the narrower of the two is
# the larger number.
WIDTHS=(${QA_WIDTHS:-1920 1440 1024 768 480 430 390 375 360})

if [ -z "${CHROME:-}" ] || { ! [ -x "$CHROME" ] && ! command -v "$CHROME" >/dev/null; }; then
  echo "No Chrome/Chromium found. Set CHROME=/path/to/chrome and re-run."
  printf '  looked in: %s\n' "${CHROME_CANDIDATES[@]}"
  exit 2
fi
[ -f dist-qa/visual-qa.html ] || { echo "run 'npm run qa:build' first"; exit 2; }

mkdir -p "$OUT"
: > "$OUT/findings.txt"

python3 -m http.server "$PORT" --directory dist-qa >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
# Wait for the port rather than sleeping a guess.
for _ in $(seq 1 50); do
  curl -sf "http://127.0.0.1:$PORT/visual-qa.html" >/dev/null && break
  sleep 0.1
done

# Chrome >=132 on macOS writes its output and then does NOT exit, so the plain
# command substitution this used to be blocked forever. Wait on the OUTPUT
# rather than the process: --dump-dom is finished when </html> reaches stdout,
# --screenshot when the PNG has landed and stopped growing. On Linux, where
# Chrome does exit, the `kill -0` check ends the wait on the first poll after
# exit, so behaviour there is unchanged.
#
# Each call gets its own --user-data-dir because we now SIGKILL Chrome, which
# leaves a SingletonLock behind that a shared profile would trip over.
run_chrome() {
  local shot="" arg tmp prof pid waited=0 size prev
  for arg in "$@"; do
    case "$arg" in --screenshot=*) shot=${arg#--screenshot=} ;; esac
  done
  tmp=$(mktemp); prof=$(mktemp -d)
  "$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
    --user-data-dir="$prof" --virtual-time-budget=8000 "$@" >"$tmp" 2>/dev/null &
  pid=$!
  prev=-1
  while [ "$waited" -lt 400 ]; do            # 40s ceiling, 0.1s steps
    if [ -n "$shot" ]; then
      # Two equal non-zero sizes in a row means the PNG is fully flushed;
      # killing mid-write would leave a truncated image.
      if [ -s "$shot" ]; then
        size=$(wc -c <"$shot" 2>/dev/null || echo 0)
        [ "$size" = "$prev" ] && break
        prev=$size
      fi
    else
      grep -q '</html>' "$tmp" 2>/dev/null && break
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
    waited=$((waited + 1))
  done
  kill -9 "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  cat "$tmp"
  rm -f "$tmp"; rm -rf "$prof"
}

# Pull the probe's findings line out of a --dump-dom capture. Its absence means the
# page never finished rendering, which is reported as such (and is a WORSE result
# than an overflow, not a pass).
extract_line() {
  printf '%s' "$1" | python3 -c '
import re, sys
html = sys.stdin.read()
m = re.search(r"<pre id=\"overflow-report\"[^>]*>(.*?)</pre>", html, re.S)
print(m.group(1).replace("&quot;", "\"").replace("&amp;", "&")
      .replace("&lt;", "<").replace("&gt;", ">") if m
      else "PROBE DID NOT RUN - the page did not finish rendering")'
}

# Render a URL and return the findings line, RETRYING on a non-render.
#
# `--virtual-time-budget` is a deadline, not a guarantee: under load a heavy page
# (circular-dashboard at 360px was the one that bit) occasionally does not commit its
# React tree and font layout before the budget expires, and the probe never runs. That
# is a transient capture failure, not a layout defect — the same page renders on the
# next attempt, confirmed 5/5 by hand. A single retry turns a flaky gate into a stable
# one WITHOUT weakening it: a genuinely blank page (a real render crash) fails all three
# attempts and is still reported, because the retry only forgives a result that then
# succeeds. The count floor below is what still catches a page that renders empty every
# time.
capture_line() {
  local url="$1" window="$2" line chars
  for attempt in 1 2 3; do
    line=$(extract_line "$(run_chrome --window-size="$window" --dump-dom "$url")")
    # [0-9][0-9]* rather than [0-9]\+ : \+ is a GNU extension that BSD/macOS sed
    # does not understand, so this silently produced an EMPTY count there. That
    # made capture_line retry every screen three times and then report a false
    # "only 0 characters rendered", failing the gate on layout that was fine.
    chars=$(printf '%s' "$line" | sed -n 's/.*, \([0-9][0-9]*\) chars.*/\1/p')
    # A rendered page with a real char count is the answer; anything else is retried.
    case "$line" in
      *"PROBE DID NOT RUN"*) ;;                       # never rendered — retry
      *) [ -n "$chars" ] && [ "$chars" -ge 300 ] && break ;;  # rendered — done
    esac
    [ "$attempt" -lt 3 ] && echo "  (retry $attempt: $screen @ ${width}px did not render)" >&2
  done
  printf '%s' "$line"
}

fail=0
for screen in "${SCREENS[@]}"; do
  for width in "${WIDTHS[@]}"; do
    # Headless Chrome clamps --window-size to a 500px minimum, so anything below that is
    # loaded inside frame.html, which sizes an iframe to the exact width. An iframe IS a
    # real viewport for CSS: media queries and innerWidth resolve against it.
    if [ "$width" -lt 520 ]; then
      url="http://127.0.0.1:$PORT/frame.html?screen=$screen&w=$width"
      window="900,3600"
    else
      url="http://127.0.0.1:$PORT/visual-qa.html?screen=$screen"
      window="$width,3600"
    fi
    line=$(capture_line "$url" "$window")

    printf '%s\n' "$line" | tee -a "$OUT/findings.txt"
    case "$line" in *"NO OVERFLOW FINDINGS"*) ;; *) fail=1 ;; esac

    # A blank page reports no findings. Every line carries the rendered character count
    # so that "clean" always has to mean "clean AND rendered" - the first Phase 47 run
    # passed minute-detail at every width while rendering nothing.
    # 300, not 400: a scope list with a single row legitimately renders about 380
    # characters (title, subtitle, search placeholder, one table row), and the
    # archive fixture has exactly one archived minute. The floor exists to catch a
    # BLANK page - the first Phase 47 run reported minute-detail clean at every
    # width while rendering nothing - so it needs to sit below any real page, not
    # above the smallest one.
    # [0-9][0-9]* rather than [0-9]\+ : \+ is a GNU extension that BSD/macOS sed
    # does not understand, so this silently produced an EMPTY count there. That
    # made capture_line retry every screen three times and then report a false
    # "only 0 characters rendered", failing the gate on layout that was fine.
    chars=$(printf '%s' "$line" | sed -n 's/.*, \([0-9][0-9]*\) chars.*/\1/p')
    if [ -z "$chars" ] || [ "$chars" -lt 300 ]; then
      echo "  ^^ SUSPICIOUS: only ${chars:-0} characters rendered" | tee -a "$OUT/findings.txt"
      fail=1
    fi

    run_chrome --window-size="$window" \
      --screenshot="$OUT/${screen}-${width}.png" "$url" >/dev/null
  done
done

# --- Phase 100.1: the modal pass -------------------------------------------------
for entry in "${MODALS[@]}"; do
  screen=${entry%%:*}
  label=${entry#*:}
  for width in "${MODAL_WIDTHS[@]}"; do
    # Always framed: a modal is only interesting below 500px, which is exactly where
    # headless Chrome's --window-size clamp bites, so frame.html does the sizing.
    url="http://127.0.0.1:$PORT/frame.html?screen=$screen&w=$width&click=$label"
    line=$(capture_line "$url" "900,3600")
    printf '%s\n' "$line" | tee -a "$OUT/findings.txt"
    case "$line" in *"NO OVERFLOW FINDINGS"*) ;; *) fail=1 ;; esac
    run_chrome --window-size=900,3600 \
      --screenshot="$OUT/modal-${screen}-${label}-${width}.png" "$url" >/dev/null
  done
done

echo "---"
echo "screenshots: $OUT/  findings: $OUT/findings.txt"
[ "$fail" -eq 0 ] && echo "RESULT: clean at every screen and width" \
                  || echo "RESULT: findings above need review"
exit "$fail"
