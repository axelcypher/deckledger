# Server-provided set visuals

Files in this directory override DeckLedger's external and generated set-image
fallbacks. The preferred filename is the internal set ID, for example:

```text
one-piece-op-01.webp
lorcana-1.png
hololive-hbp01.svg
```

The set code is also accepted (`OP-01.webp`, case-insensitive). Supported
formats are AVIF, WebP, PNG, JPEG, and SVG. Replacing or adding a file does not
require rebuilding the Docker image because `compose.yaml` mounts this folder
read-only at `/app/public`.
