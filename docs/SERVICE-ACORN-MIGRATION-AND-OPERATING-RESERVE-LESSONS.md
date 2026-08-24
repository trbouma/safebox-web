# Service Acorn Migration and Operating Reserve Lessons

## Purpose

This note records the operational findings from the August 2026 migration of
Safebox Web's Lightning gateway from its original service Acorn, relay, mint,
and reverse-proxy path to a newly deployed stack. The work began as timeout
troubleshooting and ultimately validated the complete external Lightning to
recipient-Acorn settlement path under realistic failure conditions.

The outcome was successful, but the route to success exposed several boundaries
that were easy to overlook while all infrastructure used permissive defaults:

- application health is distinct from network-path availability;
- configured service endpoints are distinct from persisted wallet identity;
- Lightning settlement is distinct from recipient delivery;
- recipient obligations are distinct from operator working capital; and
- a mint that charges input fees requires the gateway to hold an operating
  reserve.

These are not incidental implementation details. They are part of the provider
service's financial and operational model.

## Starting position

Safebox Web used a singleton service Acorn to:

1. request a Lightning invoice from a mint for a claimed Safebox address;
2. observe invoice settlement;
3. mint the settled value into the service Acorn;
4. issue an ecash transfer to the recipient Acorn;
5. publish the gift-wrapped transfer to the recipient relay; and
6. retain durable job state until the outcome was known.

The original service Acorn was persisted in `data/service-acorn.json` and used
the original Safebox relay and mint. The target deployment used:

```text
Service relay: wss://spurline.safebox.dev
Service mint:  https://mint.safebox.dev
State file:    data/service-acorn-spurline.json
```

## What prompted the work

User-facing pages and record operations had begun timing out intermittently.
Initial symptoms appeared application-related because some requests completed
quickly while others failed completely. Application optimizations were useful,
but repeated transport tests eventually isolated a separate infrastructure
problem:

- the Safebox Web container remained healthy;
- direct internal health checks completed in milliseconds;
- relay queries completed normally when the network path was available;
- TCP connections through the original reverse-proxy and VPN route failed
  intermittently before reaching the application; and
- failures affected both SSH and the published application port at the same
  time.

The decisive lesson was to test each boundary separately: browser to public
proxy, public proxy to VPN host, host to Docker-published port, container health,
relay access, and mint access. A fast `/health` handler cannot compensate for a
TCP connection that never reaches the host.

The reverse-proxy role was moved to a more reliable host and the application
was bound to `0.0.0.0` inside the VPN-contained deployment. Exact forwarded
header trust remained configured for the immediate proxy path.

## Persisted state overrides endpoint intent

Changing these environment variables did not silently move the existing
service wallet:

```env
SAFEBOX_SERVICE_ACORN_HOME_RELAY=wss://spurline.safebox.dev
SAFEBOX_SERVICE_ACORN_HOME_MINT=https://mint.safebox.dev
```

That behavior is intentional. The service Acorn state file contains the stable
key and authoritative endpoints needed to recover the same provider wallet.
Allowing ordinary configuration drift to replace that identity could abandon
funds and unsettled obligations.

The guarded migration path attempted to load and validate the existing service
Acorn before replacing it. Because the old path could not complete reliably,
the migration correctly retained the old state rather than pretending that a
replacement had succeeded.

The practical migration used a new state file instead:

```env
SAFEBOX_SERVICE_ACORN_MIGRATE=false
SAFEBOX_SERVICE_ACORN_STATE_FILE=data/service-acorn-spurline.json
SAFEBOX_SERVICE_ACORN_HOME_RELAY=wss://spurline.safebox.dev
SAFEBOX_SERVICE_ACORN_HOME_MINT=https://mint.safebox.dev
```

This created a clean service Acorn while preserving `service-acorn.json` as
recovery material for the previous component. Only one service worker was run
at a time. Old provider jobs were drained, cancelled, or quarantined before the
new service Acorn assumed new work.

## The mint-fee discovery

The first external Lightning payment through the new stack settled for 100
sats. The database proved that the new mint was being used, but delivery failed
with:

```text
Insufficient balance in a single keyset after mint input fees;
swap or add funds before retrying.
```

This was the critical finding. The service Acorn held the 100 sats associated
with the recipient obligation, but creating a 100-sat bearer token required a
proof swap. The mint charged an input fee for that swap. Exactly 100 sats of
recipient value was therefore insufficient to issue a 100-sat token while also
paying the issuance-side mint fee.

The failure occurred before the swap and before relay publication. The job had
no delivery event ID, so the funds remained under the service Acorn and the
recipient had not been paid twice. `DELIVERY_FAILED` accurately represented an
operator-review state, not loss of funds.

## Recipient obligations and operating capital

The incident clarified the service Acorn balance model:

```text
service Acorn balance
    = recipient delivery obligations
    + operator operating reserve
    - accumulated mint and gateway costs
```

Recipient funds must not be treated as the fee reserve needed to deliver those
same funds. The provider needs separately supplied working capital in the same
mint and usable keyset. That reserve is operator-owned capital and should be
monitored, replenished, and accounted for independently of recipient
obligations.

Safebox Web now provides an exclusive maintenance command that funds the
service Acorn without revealing or copying its `nsec`:

```sh
docker compose stop service-acorn-worker
docker compose run --rm --no-deps service-acorn-worker \
  python -m app.service_acorn_worker fund 100
docker compose up -d service-acorn-worker
```

The command recovers the configured service Acorn, requests and displays a
Lightning invoice and terminal QR code, waits for settlement, mints and
persists the reserve proofs, and records the deposit in transaction history.
It preserves the quote when confirmation times out so a possibly paid invoice
can be investigated rather than replaced blindly.

## Recovery and validation

A 100-sat operating-reserve deposit brought the service Acorn balance to 200
sats: 100 sats associated with the failed delivery and 100 sats of operator
reserve. The failed job was then deliberately moved back to `SETTLED` while the
worker was stopped.

On restart, the worker delivered the transfer on its first attempt. The durable
job record showed:

```text
status:            DELIVERED
delivery_attempts: 1
error:             none
delivery_event_id: 20436f36267e695b3ed388cbcbff9a2f172c77a7d2325df0534ba1b8d6b55a3c
```

The recipient Acorn discovered the pending transfer, accepted it, completed
mint confirmation, and displayed the finalized balance. The verified path was:

```text
external Lightning wallet
        |
        v
Safebox Web LNURL endpoint and durable provider job
        |
        v
service Acorn on wss://spurline.safebox.dev
        |
        v
Lightning settlement and proofs from https://mint.safebox.dev
        |
        v
gift-wrapped ecash transfer on the recipient relay
        |
        v
recipient sees pending funds immediately
        |
        v
recipient finalizes to mint-confirmed proofs
```

This validated both the new infrastructure and the separation between immediate
delivery assurance and final mint confirmation.

## Operational rules established

The following rules now form part of the deployment model:

1. Run exactly one service Acorn worker for a given state file.
2. Stop that worker before funding, migrating, inspecting through a mutating
   tool, or manually requeuing an obligation.
3. Treat the persisted state file as authoritative for the service identity and
   endpoints.
4. Keep old recovery state until all previous obligations have been reconciled.
5. Never delete or retry an ambiguous paid quote merely because an HTTP request
   timed out.
6. Distinguish invoice settlement, proof minting, recipient delivery, and
   recipient finalization in status and logs.
7. Require a delivery event ID before treating relay publication as complete.
8. Maintain an operator-funded reserve in every mint/keyset used for provider
   delivery.
9. Replenish the reserve before it becomes too small to cover the largest
   expected proof selection and input fee.
10. Diagnose transport, proxy, application, relay, and mint health as separate
    boundaries.

## Remaining engineering work

The migration and recovery succeeded, but production operation should add:

- an explicit reserve balance and low-reserve health indicator;
- configuration for a minimum reserve threshold;
- operator alerts before a recipient delivery fails for lack of fee capacity;
- accounting that separates recipient liabilities, operator reserve, mint
  input fees, and any explicit gateway fee;
- a supported review/requeue command instead of direct SQL;
- clearer detection of a worker stuck before its `worker ready` state;
- bounded startup diagnostics for old-wallet migration and relay readback; and
- reconciliation tooling for paid quotes whose proof publication outcome is
  uncertain.

The provider may eventually charge an explicit gateway fee, but it must remain
distinct from a mint input fee. The mint fee is a protocol-operation cost. A
gateway fee is an operator price. Combining the two would obscure both user
expectations and service accounting.

## Broader significance

This exercise moved Safebox Web beyond a demonstration that happened to work
with one permissive mint and one familiar relay. The service operated across a
new reverse proxy, a new relay, a fee-charging mint, a newly created provider
Acorn, a durable failed-delivery state, and a controlled recovery procedure.

The most important result was not that every step succeeded immediately. It
was that failures remained bounded and inspectable: the old service identity
was preserved, the paid value remained controlled, the recipient was not paid
twice, the obligation could be requeued, and the final transfer could be
independently observed and finalized. That is the practical foundation of a
reliable gateway rather than a fragile happy-path integration.
