# NIP-05 Handle Directory

## Purpose

Safebox Web provides a minimal NIP-05 directory for connected Acorn
components. A user-controlled session proves possession of an Acorn private
key. That component may claim one public handle if it is not already assigned.

This is a public naming service, not a Safebox account system. The application
does not store the Acorn private key, wallet proofs, records, configuration, or
server-side session state in the directory.

## Data model

The `claimed_handle` table contains only:

| Field | Purpose |
| --- | --- |
| `claimed_handle` | Normalized, public NIP-05 local name |
| `npub` | Bech32 public key of the controlling Acorn component |
| `home_relay` | Relay advertised for that component |

The numeric `id` is an internal database primary key. Database constraints
make both `claimed_handle` and `npub` unique. This provides one component per
handle and one handle per component, including when two claims race.

Handles are normalized to lowercase. Safebox accepts 1–64 letters, numbers,
dots, underscores, and hyphens, with a letter or number at each end. Consecutive
dots and the reserved `_` name are rejected.

## First-time onboarding name

The one-click invite onboarding flow, `/onboard/INVITEME` by default, assigns
the new Acorn a default handle so it is immediately reachable through the
provider directory. The configured invite-code list controls which onboarding
URLs are active, and matching is case-insensitive. Safebox follows the
mnemonic-name pattern introduced in Safebox-2: it splits the first 32 bits of
the Acorn public key into two 11-bit indexes into the BIP39 English word list
and a numeric suffix. Safebox Web constrains the suffix to `0` through `999`,
producing names such as:

```text
abandonabandon0
```

The public key makes this name deterministic and predictable; the name is not
a secret, authentication factor, or proof of a person's identity. If the
preferred name is already claimed, Safebox advances the numeric suffix until
it atomically claims an available candidate. A database uniqueness conflict
caused by a concurrent request is retried. If allocation cannot be completed,
wallet creation still succeeds and the authenticated Acorn can claim a name
manually.

Automatic assignment is an onboarding policy of Safebox Web, which owns the
domain namespace and directory. It is not Acorn relay state and is not imposed
by the Acorn component. Both the one-click onboarding form and the ordinary
new-Acorn creation form request automatic assignment. After creation, the
authenticated Acorn can use the ordinary handle page to keep the generated
name, rename it, or remove it.

## Claim authorization

The claim route requires an authenticated Acorn session and a valid form token.
The request-scoped Acorn derives its `npub` from the private key held in the
encrypted browser session. The submitted form cannot choose a different
public key or relay: Safebox takes both from that authenticated Acorn instance.

Submitting the existing handle with the same Acorn is idempotent and updates
the stored home relay. The same authenticated Acorn may rename its mapping to
another unclaimed handle or explicitly remove the mapping. Renaming or removal
immediately releases the old public name, which may then be claimed by another
Acorn. The interface warns about this consequence and requires explicit
confirmation before removal.

These operations authorize the current component key; they do not implement
private-key rotation or administrative recovery. Moving an existing handle to
a different `npub` requires a separate recovery policy and is not supported.

## Public resolution

Resolution uses:

```http
GET /.well-known/nostr.json?name=alice
```

For a registered name, Safebox returns the NIP-05 shape:

```json
{
  "names": {
    "alice": "<64-character hexadecimal public key>"
  },
  "relays": {
    "<64-character hexadecimal public key>": [
      "wss://relay.example"
    ]
  }
}
```

The endpoint permits cross-origin reads. Unknown valid names return `404`.

## Trust model

The claim operation proves that the connected Acorn key authorized the mapping
accepted by this Safebox Web instance. A relying client still receives that
mapping from provider-controlled infrastructure. The trustworthiness of a
NIP-05 address is therefore a function of three operational layers:

- the domain owner controls registration, DNS, and the TLS endpoint that tells
  clients which service represents the domain; and
- the reverse-proxy operator terminates TLS and decides which upstream
  application receives the request, and can instead route to or serve another
  application entirely; and
- the application operator controls the running Safebox code, claim policy,
  directory database, availability, backups, and responses returned by the
  well-known endpoint.

Any of these operators—or an attacker controlling one of these layers—can make
the domain return a different public key or relay. The original Acorn private
key can remain uncompromised while its public handle is redirected.

Safebox can restrict which immediate proxy addresses are allowed to supply
forwarded scheme and host metadata. This prevents arbitrary network peers from
claiming proxy authority. It cannot prevent an authorized proxy from selecting
a different backend, replacing a response, or presenting a different login
application. The reverse proxy is therefore part of the trusted application
delivery path, not merely a transparent network hop.

NIP-05 should consequently be interpreted as “this domain currently asserts
that this name maps to this public key.” It is useful discovery and naming, but
it is not independent proof of a person's identity, permanent ownership of the
name, or an end-to-end guarantee that bypasses the provider. Material transfers
should confirm the resolved `npub` through another trusted channel when
recipient certainty matters.

## SQLite and migrations

The default database URL is:

```text
sqlite:///data/database.db
```

At application startup, Safebox creates the parent directory when necessary
and runs `alembic upgrade head`. The initial migration creates the handle table
and its uniqueness constraints. Migration failure prevents application startup
instead of running against an uncertain schema.

For local development, the database appears at `data/database.db` relative to
the repository working directory. The `data/` directory is ignored by Git.
To run migrations manually:

```sh
poetry run alembic upgrade head
```

## Docker persistence and backup

Docker Compose mounts the named volume `safebox-web-data` at `/app/data` while
keeping the rest of the container filesystem read-only. Recreating the
container therefore retains `/app/data/database.db`. Removing the named volume
deletes the directory, so it must be treated as persistent service data.

Before upgrading or moving the service, stop writes and back up the SQLite file
from the volume. A database restore must preserve all three mapping fields and
the `alembic_version` table. The directory contains public mappings rather than
wallet secrets, but unauthorized modification could redirect discovery to an
attacker-controlled key or relay and is therefore security-sensitive.
