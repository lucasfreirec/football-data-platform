#!/usr/bin/env bash
# Download one StatsBomb Open Data competition season, reproducibly.
#
# Usage:
#   ./scripts/fetch_statsbomb_data.sh [COMPETITION NAME] [SEASON NAME] [TARGET_DIR]
#   ./scripts/fetch_statsbomb_data.sh "FIFA World Cup" 2022 ./data/raw
#
# The competition name may be quoted or not. Names are matched case-insensitively
# against competitions.json, and default to FIFA World Cup, 2022, ./data/raw.
#
# Downloads are pinned to one commit of the open-data repository, recorded in
# TARGET_DIR/SOURCE_REVISION, so the same command yields the same files. A file
# already fetched at that revision is skipped.
#
# Environment:
#   STATSBOMB_REPO      GitHub repository          (default: statsbomb/open-data)
#   STATSBOMB_REF       branch, tag or commit      (default: master)
#   STATSBOMB_BASE_URL  use this data URL instead of GitHub; the revision is then
#                       only recorded if STATSBOMB_REVISION is also set
#   STATSBOMB_REVISION  revision label to record when STATSBOMB_BASE_URL is set
#
# Exit status is non-zero when competitions.json or the match list cannot be
# downloaded or parsed. Unavailable event or lineup files are reported, not fatal:
# StatsBomb Open Data does not hold every historical match.
set -euo pipefail

DEFAULT_COMPETITION="FIFA World Cup"
DEFAULT_SEASON="2022"
DEFAULT_TARGET="./data/raw"

die() {
  echo "error: $*" >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

is_season() {
  [[ "$1" =~ ^[0-9]{4}(/[0-9]{4})?$ ]]
}

# The season is the last argument, or the second to last when a target follows it.
# Everything before it is the competition name, so quoting it is optional.
COMPETITION="$DEFAULT_COMPETITION"
SEASON="$DEFAULT_SEASON"
TARGET_DIR="$DEFAULT_TARGET"

if [ "$#" -gt 0 ]; then
  season_index=0
  position=0
  for arg in "$@"; do
    position=$((position + 1))
    if is_season "$arg" && [ "$position" -ge $(($# - 1)) ]; then
      season_index=$position
    fi
  done
  [ "$season_index" -gt 0 ] || die "no season such as 2022 found in the arguments: $*"

  if [ "$season_index" -gt 1 ]; then
    COMPETITION="${*:1:$((season_index - 1))}"
  fi
  SEASON="${!season_index}"
  target_index=$((season_index + 1))
  if [ "$target_index" -le "$#" ]; then
    TARGET_DIR="${!target_index}"
  fi
fi

REPO="${STATSBOMB_REPO:-statsbomb/open-data}"
REF="${STATSBOMB_REF:-master}"
REVISION=""

if [ -n "${STATSBOMB_BASE_URL:-}" ]; then
  BASE_URL="${STATSBOMB_BASE_URL%/}"
  REVISION="${STATSBOMB_REVISION:-}"
else
  # Pin to one commit so a branch moving mid-download cannot mix two versions.
  if REVISION=$(curl -sSfL --max-time 30 --retry 2 \
    -H "Accept: application/vnd.github.sha" \
    "https://api.github.com/repos/$REPO/commits/$REF" 2>/dev/null) &&
    [[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]; then
    BASE_URL="https://raw.githubusercontent.com/$REPO/$REVISION/data"
  else
    REVISION=""
    BASE_URL="https://raw.githubusercontent.com/$REPO/$REF/data"
    echo "warning: could not resolve '$REF' to a commit; downloading its current head," >&2
    echo "         so this download is not pinned to a reproducible revision" >&2
  fi
fi

mkdir -p "$TARGET_DIR"

PREVIOUS_REVISION=""
if [ -f "$TARGET_DIR/SOURCE_REVISION" ]; then
  PREVIOUS_REVISION=$(tr -d '[:space:]' <"$TARGET_DIR/SOURCE_REVISION")
fi

echo "Fetching '$COMPETITION' '$SEASON' into $TARGET_DIR"
echo "  source   : $BASE_URL"
echo "  revision : ${REVISION:-unpinned}"

DOWNLOADED=0
SKIPPED=0
UNAVAILABLE=0
UNAVAILABLE_LIST=""
FETCH_STATUS=""
FETCH_REASON=""

# Sets FETCH_STATUS to downloaded, skipped, or unavailable.
fetch() {
  local rel="$1" dest="$TARGET_DIR/$1" part code rc=0
  FETCH_REASON=""

  if [ -n "$REVISION" ] && [ "$PREVIOUS_REVISION" = "$REVISION" ] && [ -s "$dest" ]; then
    FETCH_STATUS="skipped"
    return 0
  fi

  mkdir -p "$(dirname "$dest")"
  part="$dest.part"
  code=$(curl -sSL --fail --retry 3 --retry-delay 1 --max-time 300 \
    -o "$part" -w '%{http_code}' "$BASE_URL/$rel" 2>/dev/null) || rc=$?

  if [ "$rc" -eq 0 ]; then
    mv "$part" "$dest"
    FETCH_STATUS="downloaded"
  else
    rm -f "$part"
    FETCH_STATUS="unavailable"
    if [ "$rc" -eq 22 ]; then
      FETCH_REASON="HTTP $code"
    else
      FETCH_REASON="curl error $rc"
    fi
  fi
}

count() {
  case "$FETCH_STATUS" in
    downloaded) DOWNLOADED=$((DOWNLOADED + 1)) ;;
    skipped) SKIPPED=$((SKIPPED + 1)) ;;
    unavailable)
      UNAVAILABLE=$((UNAVAILABLE + 1))
      UNAVAILABLE_LIST="$UNAVAILABLE_LIST
    - $1 ($FETCH_REASON)"
      ;;
  esac
}

is_json_array() {
  python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    sys.exit(0 if isinstance(json.load(handle), list) else 1)
' "$1" 2>/dev/null
}

fetch_required() {
  local rel="$1"
  fetch "$rel"
  if [ "$FETCH_STATUS" = "unavailable" ]; then
    die "could not download $rel ($FETCH_REASON)"
  fi
  if ! is_json_array "$TARGET_DIR/$rel"; then
    rm -f "$TARGET_DIR/$rel"
    die "$rel is not a valid JSON array"
  fi
  count "$rel"
}

fetch_required "competitions.json"

if ! IDS=$(python3 - "$TARGET_DIR/competitions.json" "$COMPETITION" "$SEASON" <<'PY'
import json
import sys

path, competition, season = sys.argv[1:4]
with open(path, encoding="utf-8") as handle:
    entries = json.load(handle)

for entry in entries:
    if (
        str(entry.get("competition_name", "")).casefold() == competition.casefold()
        and str(entry.get("season_name", "")).casefold() == season.casefold()
    ):
        print(entry["competition_id"], entry["season_id"])
        break
else:
    sys.exit(f"error: no '{competition}' '{season}' entry in {path}")
PY
); then
  exit 1
fi
read -r COMPETITION_ID SEASON_ID <<EOF
$IDS
EOF
echo "  resolved : competition_id=$COMPETITION_ID season_id=$SEASON_ID"

MATCHES_REL="matches/$COMPETITION_ID/$SEASON_ID.json"
fetch_required "$MATCHES_REL"

if ! MATCH_IDS=$(python3 - "$TARGET_DIR/$MATCHES_REL" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    for match in json.load(handle):
        print(int(match["match_id"]))
PY
); then
  die "$MATCHES_REL could not be parsed as a list of matches"
fi

MATCH_COUNT=0
for match_id in $MATCH_IDS; do
  MATCH_COUNT=$((MATCH_COUNT + 1))
  for kind in events lineups; do
    rel="$kind/$match_id.json"
    fetch "$rel"
    count "$rel"
  done
done

# A stale revision would wrongly vouch for files fetched without one.
if [ -n "$REVISION" ]; then
  printf '%s\n' "$REVISION" >"$TARGET_DIR/SOURCE_REVISION"
else
  rm -f "$TARGET_DIR/SOURCE_REVISION"
fi

echo
echo "Matches in the source list : $MATCH_COUNT"
echo "Files downloaded           : $DOWNLOADED"
echo "Files skipped (up to date) : $SKIPPED"
echo "Files unavailable          : $UNAVAILABLE"
if [ "$UNAVAILABLE" -gt 0 ]; then
  echo "Unavailable files:$UNAVAILABLE_LIST"
fi
echo "Done. Source data is in $TARGET_DIR"
