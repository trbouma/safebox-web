---
title: Getting Started
description: Run Safebox Web locally for development.
---

# Getting Started

Install dependencies:

```bash
cd /Users/trbouma/projects/safebox-web
poetry install
```

Create a local environment file:

```bash
cp .env.example .env
```

If no example file is present, create `.env` with the values needed for your
deployment. At minimum, local development normally needs a cookie key and a
default bootstrap relay.

Run the web app:

```bash
poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open:

```text
http://127.0.0.1:8000
```

## Work against a local Acorn checkout

For sibling development with `/Users/trbouma/projects/safebox-acorn`, install
Acorn into the Safebox Web environment as editable:

```bash
poetry run pip install -e /Users/trbouma/projects/safebox-acorn
```

This makes Acorn code changes visible to Safebox Web without rebuilding a
package.

## Build this site

```bash
poetry install --with docs
poetry run mkdocs serve
```

The documentation server will print a local URL, usually:

```text
http://127.0.0.1:8000
```
