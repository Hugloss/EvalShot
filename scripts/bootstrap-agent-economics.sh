#!/bin/sh
set -eu

usage() {
  echo "usage: $0 --revision <commit> [--destination <dir>] [--repository <url>]" >&2
  exit 2
}

revision=
allow_short_revision=false
destination="${AGENT_ECONOMICS_ROOT:-.agent-economics}"
repository="${AGENT_ECONOMICS_REPOSITORY:-https://github.com/Hugloss/EvalShot.git}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --revision) [ "$#" -ge 2 ] || usage; revision=$2; shift 2 ;;
    --allow-short-revision) allow_short_revision=true; shift ;;
    --destination) [ "$#" -ge 2 ] || usage; destination=$2; shift 2 ;;
    --repository) [ "$#" -ge 2 ] || usage; repository=$2; shift 2 ;;
    *) usage ;;
  esac
done

[ -n "$revision" ] || usage
case "$revision" in
  *[!0-9a-fA-F]*|"") echo "agent-economics-bootstrap: revision must be a hexadecimal commit id" >&2; exit 2 ;;
esac
if [ "$allow_short_revision" = false ]; then
  [ "${#revision}" -eq 40 ] || {
    echo "agent-economics-bootstrap: exact mode requires a full 40-character commit id; use --allow-short-revision only for explicit relaxed use" >&2
    exit 2
  }
else
  [ "${#revision}" -ge 7 ] || { echo "agent-economics-bootstrap: revision is too short" >&2; exit 2; }
fi

[ ! -e "$destination" ] || { echo "agent-economics-bootstrap: destination already exists: $destination" >&2; exit 1; }

cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ -d "$destination" ]; then rm -rf "$destination"; fi
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

git init -q "$destination"
git -C "$destination" remote add origin "$repository"
if [ "$allow_short_revision" = true ] && [ "${#revision}" -lt 40 ]; then
  git -C "$destination" fetch --quiet --depth 1 origin
  requested=$(git -C "$destination" rev-parse "$revision^{commit}")
  git -C "$destination" checkout --quiet --detach "$requested"
else
  git -C "$destination" fetch --quiet --depth 1 origin "$revision"
  requested=$(git -C "$destination" rev-parse "$revision^{commit}")
  git -C "$destination" checkout --quiet --detach FETCH_HEAD
fi
resolved=$(git -C "$destination" rev-parse HEAD)
[ "$resolved" = "$requested" ] || { echo "agent-economics-bootstrap: resolved revision mismatch" >&2; exit 1; }
package="$destination/scripts/agent_economics"
[ -f "$package/__init__.py" ] || { echo "agent-economics-bootstrap: Agent Economics package missing at resolved revision" >&2; exit 1; }

trap - EXIT HUP INT TERM
printf '%s\n' "AGENT_ECONOMICS_REVISION=$resolved"
printf '%s\n' "AGENT_ECONOMICS_ROOT=$destination"
printf '%s\n' "AGENT_ECONOMICS_PYTHONPATH=$destination/scripts"
