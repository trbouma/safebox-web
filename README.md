# Safebox Web

Safebox Web is a minimal, stateless FastAPI interface for the installable
[Safebox Acorn](https://github.com/trbouma/safebox-acorn) component.

This implementation intentionally provides:

- creation of a new Acorn with a selected home relay and home mint;
- login with an `nsec` or Acorn-compatible BIP39 offline mnemonic;
- a bootstrap relay;
- an encrypted, authenticated browser cookie;
- request-scoped `Acorn` construction through FastAPI dependency injection;
- relay-backed wallet loading and balance display;
- a responsive, read-only transaction-history view;
- authenticated NIP-05 handle claiming and public resolution;
- private-record label listing and individual record retrieval;
- user-confirmed Lightning deposits through the Acorn home mint;
- confirmed Lightning-address payments through Acorn;
- a connected-wallet identity page and redacted session API; and
- logout.

It does **not** maintain accounts, write Acorn configuration, or store
server-side sessions. The wallet page loads encrypted wallet and proof events
from the bootstrap relay into request-scoped memory to derive the displayed
balance. An explicitly confirmed payment delegates all proof, locking, mint,
journal, and relay mutations to Acorn. The one server-side database is a small
public NIP-05 directory containing only claimed handle, component `npub`, and
home relay mappings.

The deposit flow requests a Lightning invoice from the Acorn home mint and
renders it as both a QR code and copyable text. It performs no browser polling.
The user explicitly indicates that the invoice has been paid, after which
Safebox asks Acorn to check the quote once, mint the proofs when payment is
confirmed, and return to the wallet page for a freshly loaded balance. An
unconfirmed invoice remains available for another user-initiated check.

Deposit invoice creation, deposit confirmation, and outgoing payment forms
display an operation-specific progress message after submission and disable
their submit buttons while the request is running. This is progressive browser
behavior: server validation and transaction safety do not depend on JavaScript.

Deposit quote state is not kept in a database or server-side session. The quote
identifier, amount, mint, and invoice are encrypted and authenticated in a
short-lived hidden form token. This prevents a browser from altering the amount
or mint between invoice creation and confirmation while preserving the
stateless application boundary.

## Balance interpretation and payment safety

Safebox Web distinguishes two values:

- **Relay-visible proof total** is the sum of the current encrypted proof
  events returned by the home relay.
- **Mint-confirmed spendable balance** is the sum of proofs reported as
  `UNSPENT` by their issuing mints during a read-only check.

The wallet, deposit, and payment pages show both values. A difference can mean
that the relay retained stale proof history, omitted deletion events, or
returned an incomplete view. Safebox warns prominently rather than presenting
the relay total as confirmed value.

Outgoing Lightning payments are blocked when mint verification is unavailable
or the proof report is not clean. Deposits remain possible because they add new
proofs, but a deposit never authorizes automatic swapping or consolidation of
the existing wallet. `RECEIVE_PROOF_MAINTENANCE_ENABLED=false` is passed
explicitly by the Docker deployment as defense in depth.

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

When creating a new Acorn, Safebox generates the offline mnemonic and its
derived `nsec` in memory, writes the initial encrypted wallet metadata through
Acorn, and verifies relay readback before starting the session. The selected
home mint is stored in that relay-backed wallet metadata. Safebox displays the
recovery material on the creation result page; the session cookie still holds
only the `nsec`, bootstrap relay, and session format version. The user must save
the displayed recovery material securely before leaving the page.

This architecture moves session custody to the browser; it does not eliminate
the need to trust the running web code or protect the cookie-encryption key.
Anyone who obtains that key and a session cookie can decrypt the contained
`nsec`.

## NIP-05 handle directory

After connecting an Acorn, select **Claim or view a NIP-05 handle** on the
wallet page. Possession of the session's Acorn private key establishes control
of the component public key. Safebox normalizes the requested handle to
lowercase and stores only:

```text
claimed_handle
npub
home_relay
```

Database uniqueness constraints enforce one owner per handle and one active
handle per Acorn. The authenticated Acorn may refresh its relay, rename its
mapping to another unclaimed handle, or explicitly remove it. Rename and
removal release the previous name for future claims; neither operation assigns
multiple handles to the same component.

Public clients resolve a claimed name using the standard endpoint:

```text
GET /.well-known/nostr.json?name=alice
```

The response maps the name to the component's hexadecimal Nostr public key and
maps that key to its registered home relay. See
[NIP-05 Handle Directory](docs/NIP-05-HANDLE-DIRECTORY.md) for the data,
migration, trust, and backup model.

NIP-05 is a domain assertion, not an independent proof of human identity or
permanent ownership. Clients necessarily trust the domain owner controlling
DNS, the reverse-proxy operator controlling TLS termination and upstream
routing, and the application operator controlling the directory code and
database. Any of these layers can redirect a name while the original Acorn key
itself remains uncompromised. A proxy allowlist protects forwarded metadata; it
cannot force an authorized proxy to route to the intended application.

## Development and deployment modes

Safebox Web does not use a `development=true` switch that broadly disables
transport security. Development and deployment differ through narrowly defined
installation and network boundaries:

| Environment | Acorn source | Browser transport | Application listener |
| --- | --- | --- | --- |
| Local development | Editable local `safebox-acorn` checkout | Direct HTTP on IPv4 loopback only | `127.0.0.1:8000` |
| Docker deployment | GitHub commit pinned by `poetry.lock` | Public HTTPS through the trusted proxy | Container HTTP behind the proxy |

For local component development, install Acorn as an editable package inside
the Safebox Web Poetry environment:

```sh
poetry install
poetry run pip install -e /Users/trbouma/projects/safebox-acorn
```

Confirm that Python is loading the editable checkout:

```sh
poetry run python -c "import acorn; print(acorn.__file__)"
```

The printed path should be inside the local `safebox-acorn` repository. Source
changes there are then visible to Safebox Web immediately. This editable
installation affects only that local virtual environment.

The Dockerfile does not copy the host virtual environment or local Acorn
checkout. It creates a clean environment and installs the GitHub dependency
declared by `pyproject.toml` at the exact commit resolved in `poetry.lock`.
Before deploying new Acorn work, deliberately update and commit that lock:

```sh
poetry update safebox-acorn
git diff poetry.lock
```

The deployed browser connection remains HTTPS even though the trusted reverse
proxy forwards HTTP over the private network to Uvicorn:

```text
browser -> HTTPS -> trusted reverse proxy -> private HTTP -> Safebox Web
```

The proxy supplies `X-Forwarded-Proto: https`, and Uvicorn accepts that claim
only from `FORWARDED_ALLOW_IPS`. A direct remote HTTP request remains rejected.
Use different `SAFEBOX_COOKIE_KEY` values for development and deployment; do
not copy a development cookie key into production.

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

## Docker image

The Docker build installs Safebox Acorn directly from its GitHub repository.
`pyproject.toml` declares the Git dependency and `poetry.lock` pins the exact
resolved Acorn commit, so a committed Safebox Web revision produces a
repeatable dependency selection rather than silently following a moving
`main` branch.

Build the image:

```sh
docker build --tag safebox-web:local .
```

To update the Acorn commit used by the image, update and commit the lock file
before rebuilding:

```sh
poetry update safebox-acorn
docker build --tag safebox-web:local .
```

The build context excludes `.env`, virtual environments, tests, caches, and
local output. The runtime image contains neither Poetry nor Git and runs as the
unprivileged `safebox` user.

Supply the cookie key and other configuration only at runtime. For example:

```sh
docker run --rm \
  --name safebox-web \
  --env-file .env \
  --publish 127.0.0.1:8000:8000 \
  safebox-web:local
```

The published HTTP port is not a production public endpoint. Safebox Web
requires HTTPS for non-loopback clients, and a connection forwarded through
Docker is not treated as direct loopback development. Put the container behind
a TLS-terminating reverse proxy, block direct public access to port `8000`, and
set `FORWARDED_ALLOW_IPS` to the exact proxy address or trusted container
network. The proxy must send `X-Forwarded-Proto: https`. Do not use `*` on an
internet-accessible deployment.

The image health check calls `/health` internally over container loopback and
therefore does not bypass the external HTTPS policy.

### Docker Compose

Create the local runtime configuration before the first start:

```sh
cp .env.example .env
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Copy the generated value into `SAFEBOX_COOKIE_KEY` in `.env`. This key protects
the encrypted browser session containing the connected Acorn's `nsec`. Do not
commit, reuse publicly, or share the key. Changing it invalidates every current
browser session.

Review these deployment values in `.env` before starting:

```env
SAFEBOX_COOKIE_KEY=<generated Fernet-compatible key>
SAFEBOX_DEFAULT_BOOTSTRAP_RELAY=wss://relay.getsafebox.app
SAFEBOX_DEFAULT_HOME_MINT=https://mint.getsafebox.app
SAFEBOX_BIND_ADDRESS=127.0.0.1
SAFEBOX_PORT=8000
FORWARDED_ALLOW_IPS=127.0.0.1
```

`FORWARDED_ALLOW_IPS` identifies the immediate reverse proxy, not the browser
or public client. Replace the loopback default when the proxy connects from the
host's Docker bridge or from another container. Use the narrowest exact address
or container-network range supported by the deployment.

Build and start:

```sh
docker compose up --detach --build
docker compose ps
docker compose logs --follow safebox-web
```

Stop the service without deleting the image:

```sh
docker compose down
```

The Compose service requires `SAFEBOX_COOKIE_KEY`, runs with a read-only root
filesystem and a small temporary `/tmp`, binds the application port to host
loopback by default, and inherits the Dockerfile health check. A TLS reverse
proxy is still required for browser access through Docker.

For a tested deployment where Nginx and Safebox Web run on separate Tailscale
machines, including the negative and positive transport checks, see
[Tailscale Reverse-Proxy Deployment](docs/TAILSCALE-REVERSE-PROXY-DEPLOYMENT.md).

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

The tests cover HTTPS enforcement, the tightly scoped loopback exception,
encrypted cookie contents and flags, dependency-injected identity, mnemonic
derivation, Acorn creation, deposit invoice and confirmation flows, invalid
secrets, and tampered cookies. They do not contact a relay or mint.

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
