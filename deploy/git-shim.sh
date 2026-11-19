#!/bin/sh
# Stand-in for git inside the runtime image. v2/src/observability.py only asks two things:
#   git -C <v2> rev-parse HEAD        -> the v2 commit recorded by deploy/package_runtime.py
#   git -C <v2> status --porcelain    -> non-empty when the packaged checkout was dirty
# Anything else fails loudly so no other code path silently depends on it.
SNAPSHOT=/app/v2/.git-snapshot
[ "$1" = "-C" ] && shift 2
case "$1 $2" in
  "rev-parse HEAD") sed -n 1p "$SNAPSHOT" ;;
  "status --porcelain") [ "$(sed -n 2p "$SNAPSHOT")" = "dirty" ] && echo " M (packaged from a dirty checkout)" ;;
  *) echo "git-shim: unsupported command: $*" >&2; exit 2 ;;
esac
exit 0
