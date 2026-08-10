# Service Acorn Treasury Descriptor Design Note

## Status

This note describes a proposed implementation. Safebox Web does not currently
provision a Bitcoin treasury descriptor through the service Acorn or use that
descriptor to allocate sweep destinations.

The proposal refines the hot-gateway/cold-treasury boundary described in the
[Bitcoin Silent Payment Gateway Design Note](BITCOIN-SILENT-PAYMENT-GATEWAY-DESIGN-NOTE.md).
It does not give the service Acorn, web process, or service worker authority to
spend treasury funds.

## Decision summary

Safebox should provision a dedicated treasury **receive output descriptor**
once and store it as an encrypted, reserved record in the persistent service
Acorn. The descriptor should normally come from a dedicated Sparrow treasury
account and should describe the external receive branch only.

The standalone service worker retrieves the descriptor at startup and imports
or verifies it in a Bitcoin Core watch-only descriptor wallet. Bitcoin Core,
not an Acorn record, allocates fresh treasury addresses and owns the mutable
derivation-index state. The Sparrow seed, hardware signer, or extended private
key remains offline and outside every Safebox process.

The resulting division is:

```text
service Acorn
    encrypted descriptor + gateway configuration

Bitcoin Core watch-only descriptor wallet
    validated derivation + atomic fresh-address allocation

offline Sparrow wallet or hardware signer
    seed/private keys + treasury spending authority
```

## Why store a descriptor instead of an xpub

A bare account xpub does not fully define how addresses must be derived. The
consumer must also know the network, script type, key origin fingerprint,
derivation path, receive/change branch, and next unused index. Reconstructing
those assumptions independently creates avoidable recovery and compatibility
risk.

An output descriptor binds the public key material to its script construction
and derivation metadata and includes a checksum. For a mainnet BIP-86 Taproot
receive branch, the shape is similar to:

```text
tr([fingerprint/86h/0h/0h]xpub.../0/*)#checksum
```

The exact descriptor must be exported and validated by wallet software. The
operator should not manually assemble one from this example. Native SegWit
would use a different descriptor and produce `bc1q...` rather than `bc1p...`
addresses.

The first implementation should accept a ranged, single-key, receive-only
descriptor. Multisignature, change descriptors, embedded private keys, and
arbitrary descriptor expressions remain out of scope until explicitly
designed and tested.

## Dedicated treasury account

The operator should create a dedicated Sparrow account or wallet for Safebox
treasury receipts. Reusing an xpub from a personal wallet would allow anyone
who later obtains the descriptor to correlate that account's derived
addresses, balances, and transaction history.

The descriptor cannot spend treasury funds, but it is privacy-sensitive
operational data. It should not be published, committed to Git, placed in a
browser session, returned by an HTTP route, or logged. The corresponding seed,
mnemonic, hardware-wallet secret, `xprv`, or `tprv` must never enter Safebox at
all.

## Reserved service-Acorn record

The proposed reserved label is:

```text
service:bitcoin-treasury
```

Its decrypted, versioned payload should contain the minimum configuration
needed to validate and activate the public derivation policy:

```json
{
  "schema": "safebox.bitcoin-treasury.v1",
  "network": "mainnet",
  "receive_descriptor": "tr([fingerprint/86h/0h/0h]xpub.../0/*)#checksum",
  "active": true,
  "configured_at": "2026-08-09T00:00:00Z",
  "configuration_id": "operator-generated-unique-id"
}
```

The payload must not contain an xprv, seed, mnemonic, passphrase, Sparrow
wallet password, signing device backup, current address index, or treasury
spending policy that belongs exclusively to the offline signer.

Ordinary Acorn record encryption is appropriate for the initial design. A
protected-record key would add another worker recovery secret while providing
limited incremental protection for non-spending public key material. This can
be revisited if the record later includes materially more sensitive policy.

## Provisioning workflow

Provisioning is an explicit operator action, not automatic application
startup:

1. Create a dedicated treasury wallet or account in Sparrow.
2. Choose the intended Bitcoin network and script type. Use Taproot when the
   gateway specifically requires fresh `bc1p...` destinations.
3. Back up the wallet seed or hardware signer through the operator's offline
   custody procedure.
4. Export the checksummed receive output descriptor. Do not export private
   keys.
5. Run a worker-side provisioning command that reads the descriptor without
   placing it in a command-line argument or shell history.
6. Validate the descriptor and show the operator its network, script type,
   key fingerprint, origin path, and a short recognition fingerprint.
7. Require explicit confirmation before writing the reserved encrypted record
   to the service Acorn home relay.
8. Read the record back through the service Acorn and verify that the signed,
   decrypted payload is identical.
9. Import or reconcile it with the dedicated Bitcoin Core watch-only wallet.
10. Derive sample addresses in Bitcoin Core and Sparrow and require an exact
    match before activation.

The provisioning interface should accept standard input, an owner-only file,
or an interactive prompt. It should reject descriptor values supplied through
a public web route. Automation may later use a worker-scoped secret manager,
but the resulting descriptor record remains service-Acorn configuration.

## Validation requirements

Before storing or activating the record, the worker must fail closed unless:

- the descriptor checksum is present and valid;
- the descriptor contains public keys only;
- the configured Bitcoin network matches the descriptor and Bitcoin backend;
- the descriptor is ranged and has a wildcard receive branch;
- the expression is an explicitly supported single-key type;
- Taproot policy produces `bc1p...` addresses when Taproot is required;
- no hardened derivation is requested after the account xpub;
- the receive branch is external (`/0/*`) under the selected convention;
- Bitcoin Core accepts the descriptor as watch-only; and
- independently derived sample addresses match Sparrow.

Validation failures must not print the complete descriptor. Logs should use a
stable recognition fingerprint and non-sensitive structural metadata.

## Worker startup and runtime

Only the singleton service-Acorn worker may load this reserved record. The web
container must not receive the service `nsec` merely to read treasury
configuration.

At startup the worker should:

1. restore and load its persistent service Acorn;
2. retrieve and validate the active treasury record;
3. compare its configuration id and descriptor fingerprint with the imported
   Bitcoin Core descriptor;
4. refuse treasury sweeps if the Acorn record and Bitcoin Core wallet disagree;
5. expose only non-sensitive readiness state to health and operator status
   reporting; and
6. retain the descriptor only as long as required in process memory.

Normal web requests must never receive the descriptor. A job may refer to the
active configuration id and the allocated destination address, but not carry
the full descriptor.

## Address allocation and concurrency

The service Acorn must not store a mutable `next_index` record for routine
address allocation. Concurrent workers, retries, relay replacement semantics,
or stale reads could reuse an address or skip state unpredictably.

Bitcoin Core's descriptor wallet should be the authoritative address allocator.
The singleton worker asks it for a fresh address, then persists the allocation
in the durable gateway job before constructing or broadcasting a treasury
sweep. Idempotency must bind one logical sweep to one reserved address even if
the job is retried.

The database should retain at least:

- gateway job id;
- treasury configuration id;
- descriptor recognition fingerprint;
- derived address and derivation index when available;
- source transaction and outpoint;
- allocation, broadcast, and confirmation timestamps; and
- final transaction id and reconciliation state.

This operational ledger supports recovery and audit without granting spending
authority.

## Rotation and rollback

Replacing the treasury descriptor is a controlled migration:

1. provision a new dedicated account and validate its descriptor;
2. write a new version with a new configuration id but do not immediately
   discard the old one;
3. pause new allocations briefly and reconcile all in-flight jobs;
4. import the new descriptor into Bitcoin Core;
5. activate it for new sweeps;
6. retain the previous public descriptor offline and in auditable retired
   configuration until all old addresses are reconciled; and
7. remove or archive old watch-only state only under an explicit retention
   policy.

Rollback changes which descriptor receives future sweeps. It cannot reverse an
already broadcast Bitcoin transaction. Jobs therefore remain bound to the
configuration id and address selected when they were authorized.

If a descriptor is disclosed, existing funds are not directly spendable by
the observer, but the account loses privacy. The operator should create a new
dedicated account for future receipts and rotate deliberately.

## Recovery and availability

The service Acorn provides encrypted, relay-backed availability for the
descriptor, not its only backup. The operator must retain an offline copy of
the output descriptor alongside the treasury wallet recovery documentation.

Recovery requires three independent categories:

| Material | Purpose | Custody |
| --- | --- | --- |
| Service Acorn recovery material | Recover encrypted gateway configuration | Worker/operator secret custody |
| Treasury receive descriptor | Reconstruct watch-only address discovery and audit | Confidential offline operational backup |
| Sparrow seed or hardware signer | Spend treasury funds | Offline treasury custody; never Safebox |

A rogue or unavailable relay may delay configuration recovery. It cannot alter
the signed service-Acorn record undetectably, but it can withhold or delete it.
Replication and offline backups mitigate availability; neither replaces the
offline treasury signing material.

## Trust and threat boundaries

This design intentionally limits consequences:

- compromise of the descriptor reveals treasury activity but does not grant
  spending authority;
- compromise of the service Acorn may reveal the encrypted descriptor after
  decryption and can mutate gateway configuration, so startup reconciliation
  with Bitcoin Core and explicit operator activation remain necessary;
- compromise of Bitcoin Core's watch-only wallet reveals the same public
  derivation view but not the offline signing keys;
- compromise of the singleton worker can redirect future sweep destinations
  while active, even without a treasury xprv, so destination allocation and
  configuration changes require audit trails and monitoring; and
- compromise of the Sparrow seed or hardware signer grants treasury spending
  authority and is outside the protection offered by this design.

The descriptor is therefore **confidential but non-spending**. The seed and
extended private keys are **secret and spending-authoritative**. A generated
treasury address is **public**. Documentation, configuration, and incident
response must preserve these distinctions.

## Proposed implementation phases

### Phase 1: validation and provisioning

- define the reserved record schema;
- implement descriptor parsing, checksum and private-key rejection;
- add an interactive worker-side provision/show-status command;
- verify relay readback; and
- add deterministic tests using public test descriptors only.

### Phase 2: Bitcoin Core watch-only integration

- create a dedicated descriptor wallet;
- import and reconcile the receive descriptor;
- compare sample derivations with fixtures exported from Sparrow;
- allocate fresh addresses atomically; and
- persist configuration ids and allocations in gateway jobs.

### Phase 3: gateway use and operations

- use fresh allocated addresses for confirmed service-NSP treasury sweeps;
- add idempotent retry and reconciliation;
- implement descriptor rotation and retirement;
- add operator status without descriptor disclosure; and
- document backup, restoration, and privacy-loss response exercises.

## Release gates

The feature should not receive meaningful funds until all of the following are
demonstrated:

- no private descriptor or extended private key can pass validation;
- Sparrow and Bitcoin Core derive identical addresses from the exported
  descriptor;
- concurrent requests cannot reuse a destination address;
- retries preserve the original address and configuration id;
- worker and relay restarts preserve configuration and allocation continuity;
- a stale or conflicting service-Acorn record fails closed;
- descriptor rotation does not strand in-flight jobs;
- restoration succeeds from both service-Acorn and offline backups;
- logs, errors, health endpoints, and database rows do not expose the complete
  descriptor; and
- treasury spending remains possible only with the independently held Sparrow
  seed or hardware signer.

