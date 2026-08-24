# Translation catalogs

Safebox Web uses gettext catalogs rooted in this directory. English text in
Python and Jinja templates is the source language. Translation files follow
the standard layout:

```text
fr/LC_MESSAGES/messages.po
fr/LC_MESSAGES/messages.mo
```

See `docs/LOCALIZATION.md` for the extraction, update, compilation, and review
workflow. Missing catalogs and untranslated messages deliberately fall back to
English.
