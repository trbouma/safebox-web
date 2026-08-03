# Safebox Web

Safebox Web is a minimal, stateless FastAPI interface for the installable
[Safebox Acorn](https://github.com/trbouma/safebox-acorn) component.

This first implementation intentionally provides only:

- login with an `nsec` or Acorn-compatible BIP39 offline mnemonic;
- a bootstrap relay;
- an encrypted, authenticated browser cookie;
- request-scoped `Acorn` construction through FastAPI dependency injection;
- relay-backed wallet loading and balance display;
- private-record label listing and individual record retrieval;
- confirmed Lightning-address payments through Acorn;
- a connected-wallet identity page and redacted session API; and
- logout.

It does **not** maintain accounts, use a database, write Acorn configuration,
or store server-side sessions. The wallet page loads encrypted wallet and proof
events from the bootstrap relay into request-scoped memory to derive the
displayed balance. An explicitly confirmed payment delegates all proof,
locking, mint, journal, and relay mutations to Acorn.

## Stateless session boundary

After login, the browser cookie contains only:

```text
nsec
bootstrap_relay
session_format_version
```

The complete payload is encrypted and authenticated with a server-held Fernet
key. The cookie is `HttpOnly`, `SameSite=Strict`, and `Secure` everywhere
except direct loopback development at `http://127.0.0.1:<port>`.

The offline mnemonic is used only to derive the operational `nsec` during the
login request. It is not placed in the cookie. The application does not write
either secret to disk, but the decrypted `nsec` necessarily exists in process
memory while handling an authenticated request.

This architecture moves session custody to the browser; it does not eliminate
the need to trust the running web code or protect the cookie-encryption key.
Anyone who obtains that key and a session cookie can decrypt the contained
`nsec`.

## Local development

Install dependencies:

```sh
poetry install
```

To work against the sibling Acorn development repository, override the Git
installation inside this Poetry environment with an editable install:

```sh
poetry run pip install -e /Users/trbouma/projects/safebox-acorn
```

Generate an application cookie key:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy `.env.example` to `.env` and place that value in
`SAFEBOX_COOKIE_KEY`. The `.env` file is ignored by Git and loaded
automatically when the application starts from the repository directory.
Existing process-environment values take precedence over `.env` values.

Run the development server bound specifically to IPv4 loopback:

```sh
poetry run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000>. Do not replace the host with `0.0.0.0` while
using plain HTTP; the application rejects insecure non-loopback requests.

## Production transport

Production requests must arrive at the ASGI application with an `https`
scheme. Either run Uvicorn with a certificate or terminate TLS at a trusted
reverse proxy.

When using a reverse proxy, configure Uvicorn to trust forwarded headers only
from that proxy's exact address. Do not accept forwarded headers from arbitrary
clients. For example, when the proxy connects from loopback:

```sh
poetry run uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips=127.0.0.1
```

The proxy must set `X-Forwarded-Proto: https`. Direct public access to the
Uvicorn port should be blocked.

## FastAPI dependency boundary

Authenticated route functions request an `AcornDependency`:

```python
from app.dependencies import AcornDependency


async def route(acorn: AcornDependency):
    return {"npub": acorn.pubkey_bech32, "relay": acorn.home_relay}
```

For each request, the dependency:

1. reads the appropriate secure or loopback cookie;
2. authenticates, decrypts, and checks its age;
3. reconstructs an `Acorn` instance from the `nsec` and bootstrap relay; and
4. optionally loads its relay-backed state for routes that request a
   `LoadedAcornDependency`; and
5. passes that request-scoped component to the route.

The wallet route calls `Acorn.load_data()` through the loaded dependency with a
bounded timeout. This reads and decrypts relay events and derives balance from
proofs in memory. It does not refresh proofs at a mint or publish wallet state.

## Tests

Run:

```sh
poetry run pytest
```

The initial tests cover HTTPS enforcement, the tightly scoped loopback
exception, encrypted cookie contents and flags, dependency-injected identity,
mnemonic derivation, invalid secrets, and tampered cookies. They do not contact
a relay or mint.

## Current limitations

- The server cookie key has no rotation mechanism yet; rotation invalidates
  existing sessions.
- Browser-held encrypted private keys remain bearer credentials if the server
  key is compromised.
- Python cannot guarantee that decrypted secrets are zeroized from memory.
- Mnemonic derivation currently uses Acorn's internal recovery helper. Acorn
  should expose this operation as a stable public API before Safebox Web is
  released independently.
- There is no production account, multi-device, session-revocation, or HSM
  integration in this minimal shell.
