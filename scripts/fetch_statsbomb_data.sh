#!/usr/bin/env bash
# Download one StatsBomb Open Data competition season into the local source directory.
#
# Usage:
#   ./scripts/fetch_statsbomb_data.sh [COMPETITION_NAME] [SEASON_NAME] [TARGET_DIR]
#
# Defaults to UEFA Champions League 2017/2018 in ./data/raw.
set -euo pipefail

COMPETITION_NAME="${1:-Champions League}"
SEASON_NAME="${2:-2017/2018}"
TARGET_DIR="${3:-./data/raw}"
BASE_URL="${STATSBOMB_BASE_URL:-https://raw.githubusercontent.com/statsbomb/open-data/master/data}"

mkdir -p "$TARGET_DIR"

echo "Downloading competitions.json"
curl -sSfL "$BASE_URL/competitions.json" -o "$TARGET_DIR/competitions.json"

# Resolve the competition/season names to StatsBomb identifiers.
read -r COMPETITION_ID SEASON_ID <<EOF
$(python3 - "$TARGET_DIR/competitions.json" "$COMPETITION_NAME" "$SEASON_NAME" <<'PY'
import json
import sys

path, competition_name, season_name = sys.argv[1:4]
for entry in json.load(open(path, encoding="utf-8")):
    if (
        entry.get("competition_name", "").casefold() == competition_name.casefold()
        and entry.get("season_name", "").casefold() == season_name.casefold()
    ):
        print(entry["competition_id"], entry["season_id"])
        break
else:
    sys.exit(f"No {competition_name} {season_name} entry in {path}")
PY
)
EOF

echo "Resolved '$COMPETITION_NAME' '$SEASON_NAME' to competition_id=$COMPETITION_ID season_id=$SEASON_ID"

mkdir -p "$TARGET_DIR/matches/$COMPETITION_ID" "$TARGET_DIR/events" "$TARGET_DIR/lineups"
MATCHES_FILE="$TARGET_DIR/matches/$COMPETITION_ID/$SEASON_ID.json"

echo "Downloading match list"
curl -sSfL "$BASE_URL/matches/$COMPETITION_ID/$SEASON_ID.json" -o "$MATCHES_FILE"

MATCH_IDS=$(python3 -c "
import json, sys
print(' '.join(str(m['match_id']) for m in json.load(open(sys.argv[1], encoding='utf-8'))))
" "$MATCHES_FILE")

for match_id in $MATCH_IDS; do
  echo "Downloading match $match_id"
  curl -sSfL "$BASE_URL/events/$match_id.json" -o "$TARGET_DIR/events/$match_id.json" \
    || echo "  no events available for $match_id"
  curl -sSfL "$BASE_URL/lineups/$match_id.json" -o "$TARGET_DIR/lineups/$match_id.json" \
    || echo "  no lineups available for $match_id"
done

echo "Done. Source data is in $TARGET_DIR"
