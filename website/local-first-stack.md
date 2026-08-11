---
title: Local-First Stack
description: Run Safebox Web with local Acorn, Spurline, and Grove components.
---

# Local-First Stack

Safebox Web can be run against local sibling services while the broader network
remains available for mints, public relays, and external verification.

## Start Spurline

```bash
cd /Users/trbouma/projects/spurline
poetry run spurline --host 127.0.0.1 --port 8080 --database ./data/spurline.sqlite3
```

## Start Grove

```bash
cd /Users/trbouma/projects/grove
poetry run grove --host 127.0.0.1 --port 8001 --data-dir ./data
```

## Start Safebox Web

Configure Safebox Web to use the local relay where appropriate:

```bash
cd /Users/trbouma/projects/safebox-web
poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

The exact environment variables depend on the workflow being tested. The
important local-family shape is:

```text
Safebox Web -> Acorn -> ws://127.0.0.1:8080
                      -> http://127.0.0.1:8001
```

## What this proves

A local stack can keep the user workflow, relay-backed metadata, and encrypted
blob availability near the person or community using it. External mints,
public relays, and OpenETR infrastructure can still be used when available,
but the local record/blob path does not require a single hosted Safebox
service.
