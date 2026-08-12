#!/bin/sh

set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$repo_dir"

printf '%s\n' 'Pulling the latest changes...'
git pull

printf '%s\n' 'Building container images...'
docker compose build

printf '%s\n' 'Recreating Safebox containers...'
docker compose --profile service-acorn up --force-recreate --detach

printf '%s\n' 'Safebox containers refreshed.'
docker compose ps
