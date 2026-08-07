# OpenBao Integration Note

## Status and purpose

This note describes how OpenBao could be integrated with Safebox Web to
protect secrets owned by the trusted application operator. It is an
architecture and migration note, not a completed implementation or a claim
that the current deployment uses OpenBao.

The core decision is:

> OpenBao may protect the secrets of the Safebox execution environment. It
> must not silently turn user-controlled Acorns into centrally held accounts.

OpenBao therefore belongs on the trusted-provider side of the Safebox boundary.
It should not become a dependency of the installable `safebox-acorn` component
or the normal storage location for users' Acorn recovery material.

## Why OpenBao is relevant

The current deployment has two particularly sensitive operator-managed
secrets:

- `SAFEBOX_COOKIE_KEY`, which protects browser sessions containing an Acorn
  `nsec`, bootstrap relay, and optional record-protection key; and
- the service Acorn `nsec`, currently persisted in
  `data/service-acorn.json` so the Lightning-address worker retains the same
  authority across restarts.

Filesystem permissions and private environment files are reasonable pilot
controls, but they provide limited access separation, rotation support, and
audit evidence. OpenBao adds encrypted secret storage, authentication,
path-based authorization, versioning, leases, revocation, and auditable
access. Its encryption barrier treats the storage backend as untrusted and
encrypts data before persistence.

OpenBao complements Acorn rather than replacing it:

| Responsibility | Primary mechanism |
| --- | --- |
| User-controlled keys, funds, and records | Safebox Acorn |
| Browser transport and temporary execution | Safebox Web |
| Provider runtime secrets | OpenBao |
| Public encrypted event availability | Nostr relays |
| Bearer-proof issuance and settlement | Cashu mints |

## Trust boundary

```text
user-controlled domain                         trusted-provider domain

Acorn nsec in authenticated cookie ───────────> Safebox Web process
Acorn records and proof state ────────────────> relays and mints
                                                    ^
                                                    |
                                  cookie key ───────+── OpenBao
                           service Acorn nsec ──────+
                         database credentials ─────+
                             internal TLS keys ─────+
```

OpenBao should initially protect only operator-controlled material. It should
not ordinarily store:

- user Acorn `nsec` values;
- Safebox Acorn mnemonics;
- protected-record mnemonics or record-protection keys;
- users' Cashu proofs;
- private records or blob plaintext; or
- complete user wallet state.

Storing those objects centrally would enlarge the consequences of an OpenBao
or operator compromise and move Safebox toward a custodial service. Any future
custodial mode must be explicit, separately designed, and clearly disclosed.

OpenBao does not remove the existing execution-environment trust boundary.
Safebox Web must still hold a connected user's operational `nsec` in memory
while processing an authenticated request. The service worker must similarly
hold its provider `nsec` in memory while signing Nostr events.

## Recommended first integration

Use a version 2 key/value secrets engine with separate paths and policies for
the web and worker processes:

| OpenBao path | Consumer | Initial contents |
| --- | --- | --- |
| `kv/safebox-web/runtime` | `safebox-web` | `cookie_key` |
| `kv/safebox-web/service-acorn` | `service-acorn-worker` | `nsec` |

Possible later additions include PostgreSQL credentials and internal TLS
certificates. SQLite has no database password to move into OpenBao.

The two processes must use different OpenBao identities:

- the web identity may read only `kv/data/safebox-web/runtime`;
- the worker identity may read only
  `kv/data/safebox-web/service-acorn`;
- neither identity may list unrelated paths, modify secrets, manage policies,
  or administer OpenBao; and
- the reverse proxy receives no OpenBao identity or secret access.

Illustrative policies are intentionally narrow:

```hcl
# safebox-web policy
path "kv/data/safebox-web/runtime" {
  capabilities = ["read"]
}
```

```hcl
# service-acorn-worker policy
path "kv/data/safebox-web/service-acorn" {
  capabilities = ["read"]
}
```

Metadata and version-listing permissions should be added only if an
operational workflow actually requires them.

## Secret delivery

For the first implementation, use OpenBao Agent auto-authentication and file
templates rather than placing a long-lived OpenBao token in either application
container. AppRole is a reasonable initial authentication method for the
current Docker and FreeBSD service model. Certificate authentication may be a
better later choice when an internal PKI is operating.

Run separate agents for the web process and service worker. Each agent should:

1. authenticate using its own narrowly scoped machine identity;
2. renew or reacquire its short-lived OpenBao token;
3. read only its permitted KV path; and
4. render the secret into a process-specific `tmpfs` with restrictive
   ownership and permissions.

Safebox Web should gain file-backed configuration interfaces:

```env
SAFEBOX_COOKIE_KEY_FILE=/run/secrets/safebox-cookie-key
SAFEBOX_SERVICE_ACORN_NSEC_FILE=/run/secrets/service-acorn-nsec
```

The application should prefer the corresponding `_FILE` setting when it is
present and retain the direct environment variable for development and
backward compatibility. It should reject configurations that set both forms
to different values.

Secret files must:

- be regular files rather than directories or unexpected links;
- have an explicit maximum size;
- be read without logging their contents;
- be stripped only according to the secret's defined encoding;
- be validated before application startup completes; and
- be readable only by the intended process identity.

If the required secret cannot be obtained, the process should fail closed.
Once loaded, an already running process can continue through a short OpenBao
outage, but a restart should not fall back to generating a replacement key.

## Service Acorn state migration

The service Acorn recovery file currently combines secret key material with
lifecycle metadata. OpenBao integration should separate them:

```text
OpenBao KV
  `-- service Acorn nsec

worker state file or database
  |-- home relay
  |-- home mint
  |-- initialized status
  `-- non-secret operational metadata
```

Migration must be explicit and reversible:

1. stop the singleton service worker;
2. back up `service-acorn.json` securely;
3. write its existing `nsec` to the designated OpenBao KV path;
4. verify the value by deriving and comparing the expected `npub` without
   printing the `nsec`;
5. replace the local state with non-secret metadata;
6. start exactly one worker using `SAFEBOX_SERVICE_ACORN_NSEC_FILE`;
7. verify wallet loading, provider invoice settlement, and ecash delivery; and
8. retain the protected backup until restore testing succeeds.

Missing OpenBao data must never cause the worker to generate a new identity.
That would abandon the existing provider authority, events, handle mappings,
and potentially funds.

## Cookie-key lifecycle

Moving `SAFEBOX_COOKIE_KEY` into OpenBao improves its storage, access policy,
and auditability. It does not make casual rotation safe.

Safebox Web currently has one active cookie key. Replacing it immediately
invalidates every browser session because old cookies can no longer be
decrypted. Before automated rotation is enabled, the session format needs a
key identifier and the application needs a bounded key ring:

- encrypt new cookies with the current key;
- retain previous keys only for the maximum session lifetime;
- select the correct decryption key by a non-secret key version;
- retire old keys after all corresponding sessions have expired; and
- document emergency rotation as intentional mass session invalidation.

Until that design is implemented, use OpenBao versioning for recovery and
change the cookie key only through a planned session-reset procedure.

## Service-key rotation is identity migration

The service Acorn `nsec` is not an ordinary password. It establishes the
component's public key, Nostr authority, relay history, and control over its
funds and records. Rotation must therefore be treated as a service-identity
migration, including:

- sweeping or transferring remaining funds;
- updating handle and provider mappings;
- publishing any required continuity statements;
- moving required state to the new component;
- verifying the new service path; and
- preserving or destroying the old key according to a documented decision.

OpenBao can control access to the key, but it cannot make this semantic
migration automatic.

## Why Transit is deferred

OpenBao Transit can encrypt or decrypt application data without persistently
storing that data, and it supports key versioning and rewrapping. It should not
replace Safebox Web's local AES-256-GCM session cipher in the first integration.

Using Transit for every request would:

- put OpenBao in the interactive request path;
- make an OpenBao outage appear as widespread session failure;
- add network latency to every authenticated operation;
- submit each encrypted user session to another service for decryption; and
- require careful treatment of sensitive request and audit data.

The presently documented Transit signing key types also do not include
`secp256k1`, which Nostr uses. Transit therefore cannot currently keep an Acorn
private key non-exportable while directly producing compatible Nostr
signatures. The service key must still be delivered to the worker process
unless a separately reviewed signer or OpenBao plugin is introduced.

Envelope encryption with a Transit-managed wrapping key may be reconsidered
later, after the simpler KV integration and operational recovery procedures
are proven.

## OpenBao deployment requirements

An OpenBao deployment holding these secrets becomes security-critical
infrastructure. At minimum it needs:

- TLS even on a private VPN or container network;
- an authenticated and narrowly reachable listener;
- integrated Raft storage with protected snapshots;
- a documented initialization, seal, unseal, and recovery procedure;
- encrypted swap or disabled swapping for secret-bearing processes;
- declaratively configured audit devices;
- at least two audit destinations for a production design;
- monitoring for sealed state, leadership, storage health, token renewal, and
  agent template failure; and
- tested backup and restore procedures on a separate system.

A single node is acceptable for a laboratory proof of concept, but it is a
deliberate availability dependency. OpenBao recommends five integrated-storage
servers for production because that arrangement can tolerate two failed
nodes. The appropriate pilot topology should be chosen explicitly rather than
mistaken for production high availability.

FreeBSD is a supported installation path:

```sh
pkg install openbao
```

For a FreeBSD jail deployment, keep OpenBao in a separate jail from Safebox
Web where practical, expose only its TLS listener to authorized application
addresses, encrypt swap, and place its Raft dataset under a dedicated ZFS
dataset with controlled snapshots and replication. A replicated ZFS dataset
is a backup and disaster-recovery mechanism; it is not a substitute for an
OpenBao Raft quorum.

## Audit behavior

Enable OpenBao audit logging before migrating production secrets. Sensitive
values are HMACed by default; raw secret logging must remain disabled. Access
to audit-device configuration is itself privileged and must not be granted to
application roles.

Audit records should allow the operator to answer:

- which application identity read a provider secret;
- which path and version it read;
- when authentication or renewal failed;
- who changed a secret or policy; and
- whether an emergency or routine rotation occurred.

Do not copy browser session cookies, user `nsec` values, Cashu proofs, or
plaintext records into OpenBao audit metadata.

## Phased implementation

### Phase 0: laboratory deployment

- Install a single OpenBao node on an isolated system.
- Configure TLS, integrated storage, initialization, and audit logging.
- Create KV paths, policies, and non-production AppRoles.
- Exercise seal, unseal, restart, snapshot, and restore procedures.
- Use generated test secrets only.

### Phase 1: application interfaces

- Add and test the two `_FILE` configuration settings.
- Add strict secret-file validation and redacted error handling.
- Separate service Acorn secret material from non-secret worker metadata.
- Add startup tests for missing, malformed, inaccessible, and conflicting
  secret sources.

### Phase 2: controlled migration

- Migrate a disposable service Acorn first.
- Test web sessions, worker restarts, provider payments, and OpenBao outages.
- Verify process isolation: the web role cannot read the worker key and the
  worker role cannot read the cookie key.
- Complete a backup and restore exercise before using live funds.

### Phase 3: production hardening

- Choose and implement an availability topology.
- Move machine-identity bootstrap material out of ordinary `.env` files.
- Add monitoring, alerting, rotation runbooks, and incident procedures.
- Independently review policies, OpenBao configuration, Compose mounts, and
  the Safebox secret-loading code.

### Phase 4: optional advanced capabilities

- Dynamic PostgreSQL credentials.
- OpenBao PKI for internal service certificates and machine authentication.
- Versioned cookie-key rings.
- Transit-based envelope encryption for narrowly selected provider data.
- HSM-backed OpenBao unsealing or a separately reviewed Nostr signer.

## Acceptance tests

The initial integration is not complete until automated or repeatable tests
show that:

- each process can read only its own secret;
- neither process can list or mutate secret paths;
- secrets do not appear in logs, exceptions, environment dumps, image layers,
  or persistent shared volumes;
- an invalid cookie key prevents web startup;
- a missing service key prevents worker startup without generating a new key;
- existing cookies remain usable across ordinary restarts;
- the service Acorn retains the same `npub` across ordinary restarts;
- a running process has defined behavior during an OpenBao outage;
- a cold restart fails closed while OpenBao is unavailable;
- audit records capture permitted and denied reads without exposing plaintext;
- OpenBao snapshots restore onto a clean node; and
- the current non-OpenBao development path remains straightforward.

## Residual risks

After integration:

- secrets still exist in authorized application memory while in use;
- a compromised web process can decrypt current sessions and act as connected
  Acorns;
- a compromised worker can act as the provider Acorn;
- a sufficiently privileged OpenBao operator can release provider secrets;
- compromise of both the cookie key and captured session cookies exposes the
  user keys contained in those sessions;
- OpenBao failure can prevent clean process startup; and
- endpoint compromise, malicious application code, relay behavior, mint risk,
  and user recovery practices remain separate concerns.

OpenBao improves the handling of provider secrets. It does not make the
trusted execution environment unnecessary or turn a web session into a
non-custodial hardware boundary.

## Decision summary

The recommended direction is to prototype OpenBao in Safebox Web, beginning
with file-delivered `SAFEBOX_COOKIE_KEY` and service Acorn `nsec` values under
separate read-only policies. Do not add OpenBao to the Acorn package and do not
store user recovery material in it by default. Defer Transit, automatic key
rotation, and production clustering until the basic boundary, migration, and
recovery procedures have been proven.

## References

- [What is OpenBao?](https://openbao.org/docs/what-is-openbao/)
- [OpenBao architecture](https://openbao.org/docs/internals/architecture/)
- [KV version 2 secrets engine](https://openbao.org/docs/secrets/kv/kv-v2/)
- [AppRole authentication](https://openbao.org/docs/auth/approle/)
- [Agent auto-authentication](https://openbao.org/docs/agent-and-proxy/autoauth/)
- [Agent templates](https://openbao.org/docs/agent-and-proxy/agent/template/)
- [Transit secrets engine](https://openbao.org/docs/secrets/transit/)
- [Transit API and supported key types](https://openbao.org/api-docs/secret/transit/)
- [Audit devices](https://openbao.org/docs/2.4.x/audit/)
- [Integrated storage](https://openbao.org/docs/internals/integrated-storage/)
- [Installing OpenBao](https://openbao.org/docs/install/)
- [TCP listener and TLS configuration](https://openbao.org/docs/2.5.x/configuration/listener/tcp/)

## Related Safebox Web documents

- [Deployment Runbook](DEPLOYMENT.md)
- [Service Acorn Lifecycle](SERVICE-ACORN-LIFECYCLE.md)
- [Concurrency and Provider-Job Coordination](CONCURRENCY-AND-JOB-COORDINATION.md)
- [Hypermedia Architecture](HYPERMEDIA-ARCHITECTURE.md)
- [Tailscale Reverse-Proxy Deployment](TAILSCALE-REVERSE-PROXY-DEPLOYMENT.md)
