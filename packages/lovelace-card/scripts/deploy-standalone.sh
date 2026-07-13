#!/bin/bash
# Builds the standalone shell and syncs it to the stable, nginx-served
# location -- decoupled from the git checkout so `git pull`/other branch
# work in the repo doesn't change what's live mid-deploy.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."
npm run build:standalone
rsync -a --delete dist-standalone/ /var/www/filament-iq-ops/
echo "Deployed to /var/www/filament-iq-ops/"
