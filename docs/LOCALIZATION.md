# Server-Side Localization

Safebox Web localizes complete server-rendered representations. The browser
does not carry translation logic or a client-side language store. A connected
session's canonical BCP 47 language preference is held in its encrypted cookie
and is supplied to the Jinja renderer for each representation.

The initial supported preferences are English (`en`), French (`fr`), Spanish
(`es`), Portuguese (`pt`), German (`de`), and Italian (`it`). English remains
the source and fallback language while translation catalogs are developed. The
initial localized surface is deliberately limited to the connected-wallet heading and
short, stable controls: mode, balance headings, primary resource actions,
Preferences, Advisories, and Disconnect. The balance-status messages and the
Disconnect pane's recovery warning and acknowledgement are also translated as
complete messages. Other informational, recovery, security, payment, and error
text remains English until it receives contextual review.

## Runtime boundary

`app/localization.py` validates language tags, restricts rendering to supported
base languages, and caches immutable gettext translation catalogs. Each call
to `render_template()` receives its own bound `_()` and `ngettext()` functions.
The shared Jinja environment is never mutated per request, which keeps the
design safe across concurrent requests, threads, and worker processes.

Templates use gettext expressions:

```jinja2
{{ _("Manage Records") }}
```

The base template emits the selected language in `<html lang="…">`. Missing
catalogs or messages fall back to the original English text.

## Catalog workflow

Install the development dependencies, then extract source messages:

```bash
poetry install --with dev
poetry run pybabel extract -F babel.cfg -o messages.pot .
```

Initialize a catalog once:

```bash
poetry run pybabel init -i messages.pot -d app/locales -l fr
```

After interface text changes, update and compile the catalogs:

```bash
poetry run pybabel update -i messages.pot -d app/locales
poetry run pybabel compile -d app/locales
```

Compiled `.mo` files are committed beside their editable `.po` sources so the
existing Docker build includes them without installing Babel in the runtime
image. Translation review should cover buttons, headings, validation messages,
advisories, plural forms, and mobile layouts. User-authored records, handles,
keys, event identifiers, relay addresses, mint addresses, and exact protocol
errors are not translated.

Currency and language remain independent preferences. Locale-aware formatting
of dates, numbers, and fiat estimates can be introduced separately without
changing the encrypted-session boundary.
