#!/usr/bin/env bash
#
# Safely remove the duplicate "Attendance KG" workspace.
#
# There are two workspace files with the same name. Frappe loads whichever wins
# the module-sync race, so they have been fighting. The OUTER one (directly
# under the app root) still points at pre-rename doctypes -- "Employee Shift",
# "Employee Shift Summary" -- whose links are dead.
#
#   DELETE:  kreativ_attendance/workspace/attendance_kg/attendance_kg.json
#   KEEP:    kreativ_attendance/kreativ_attendance/workspace/attendance_kg/attendance_kg.json
#                                ^^^^^^^^^^^^^^^^^^^^ note the nested directory
#
# The two paths differ by one directory level, which is exactly the kind of
# thing that goes wrong at 11pm. This script verifies before it removes and
# refuses to do anything if the tree does not look as expected.
#
# Usage, from the app root (~/frappe-bench/apps/kreativ_attendance):
#     bash kreativ_attendance/scripts/remove_duplicate_workspace.sh          # dry run
#     bash kreativ_attendance/scripts/remove_duplicate_workspace.sh --apply  # delete

set -euo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

OUTER="kreativ_attendance/workspace"
INNER="kreativ_attendance/kreativ_attendance/workspace"
OUTER_JSON="$OUTER/attendance_kg/attendance_kg.json"
INNER_JSON="$INNER/attendance_kg/attendance_kg.json"

fail() { echo "ABORT: $*" >&2; exit 1; }

echo "Working directory: $(pwd)"
echo

# --- Sanity: are we in the right place? ------------------------------------
[[ -f "kreativ_attendance/hooks.py" ]] \
  || fail "kreativ_attendance/hooks.py not found. Run this from the app root:
         cd ~/frappe-bench/apps/kreativ_attendance"

# --- The keeper must exist and be the NEW one ------------------------------
[[ -f "$INNER_JSON" ]] \
  || fail "The workspace to KEEP is missing: $INNER_JSON
         Apply the UI package first -- it ships that file."

if ! grep -q "KG Monthly Attendance Summary" "$INNER_JSON"; then
  fail "$INNER_JSON does not reference 'KG Monthly Attendance Summary',
         so it is not the updated workspace. Apply the UI package first.
         Nothing was deleted."
fi
echo "KEEP   $INNER_JSON"
echo "       (verified: references KG Monthly Attendance Summary)"
echo

# --- The one to delete -----------------------------------------------------
if [[ ! -e "$OUTER" ]]; then
  echo "Nothing to do: $OUTER does not exist (already removed?)."
  exit 0
fi

if [[ -f "$OUTER_JSON" ]]; then
  echo "DELETE $OUTER_JSON"
  if grep -qE '"link_to": "(Employee Shift|Employee Shift Summary)"' "$OUTER_JSON"; then
    echo "       (verified: contains dead pre-rename links)"
  else
    echo "       WARNING: expected pre-rename links were not found."
    echo "       Inspect this file yourself before deleting:"
    echo "         cat $OUTER_JSON"
    [[ $APPLY -eq 1 ]] && fail "Refusing to auto-delete an unexpected file."
  fi
else
  echo "NOTE   $OUTER exists but has no attendance_kg/attendance_kg.json."
  echo "       Contents:"
  find "$OUTER" -type f | sed 's/^/         /'
  [[ $APPLY -eq 1 ]] && fail "Refusing to delete a directory whose contents are unexpected."
fi
echo

# --- Act -------------------------------------------------------------------
if [[ $APPLY -eq 0 ]]; then
  echo "DRY RUN -- nothing was changed."
  echo "Re-run with --apply to delete:"
  echo "    bash kreativ_attendance/scripts/remove_duplicate_workspace.sh --apply"
  exit 0
fi

BACKUP="/tmp/kg_workspace_backup_$(date +%Y%m%d_%H%M%S).tar.gz"
tar czf "$BACKUP" "$OUTER"
echo "Backed up to $BACKUP"

rm -rf "$OUTER"
echo "Removed $OUTER"
echo
echo "Now run:"
echo "    bench --site <site> migrate"
echo "    bench --site <site> clear-cache"
echo
echo "If the workspace looks wrong afterwards, restore with:"
echo "    tar xzf $BACKUP -C ."
