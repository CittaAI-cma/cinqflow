#!/bin/sh
# One image, any environment.
#
# `next build` inlines `process.env.CINQFLOW_API` into the client bundle, so
# the endpoint cannot simply be passed at run time — it is already baked. The
# build bakes a PLACEHOLDER instead, and this replaces it with the real value
# on the way up. Without this, "the same artifact that passed dev is what goes
# to prod" would be false: dev and prod would be different builds.
set -eu

PLACEHOLDER='__CINQFLOW_API__'
TARGET="${CINQFLOW_API:-http://backend:8000}"

if [ "$TARGET" != "$PLACEHOLDER" ]; then
  find .next -type f \( -name '*.js' -o -name '*.json' -o -name '*.html' \) \
    -exec grep -l "$PLACEHOLDER" {} + 2>/dev/null \
    | while IFS= read -r file; do
        sed -i "s|$PLACEHOLDER|$TARGET|g" "$file"
      done
fi

exec "$@"
