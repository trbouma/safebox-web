# Clear Transfer Product Milestone

Date: 2026-08-17

## Summary

Safebox Web now presents organization-issued Clear Mint Units (CMUs) alongside
cash without combining or confusing the two.

The wallet uses one singular **Cash Balance** for sat-denominated funds and
plural **Clear Balances** for credits issued by different organizations,
programs, mints, and keysets. Cash activity is described as payments. Clear
activity is described as transfers.

The working end-to-end flow begins with issuance at a public Clear mint and
ends with a pending Clear transfer visible and controllable by the recipient
in Safebox Web.

## Product significance

Many organizations can define useful units but do not need or want to operate a
general-purpose currency. Examples include:

- food and essential-needs credits;
- member or employee benefits;
- service hours or facility credits;
- event and hospitality allowances;
- local vouchers;
- reimbursements and program allocations; and
- credits recognized by a bounded provider network.

Clear makes those units portable bearer Mint Notes. Safebox Web gives them an
understandable place in a wallet while preserving who issued them and what
their acceptance depends on.

## The wallet model

```text
Cash Balance
  -> sat-denominated
  -> used for payments
  -> broadly transferable
  -> finalized through Bitcoin, Lightning, and Cashu mints

Clear Balances
  -> one balance per exact mint and CMU
  -> used for transfers
  -> defined by an issuing organization or program
  -> never summed across unrelated issuers or CMUs
```

The singular and plural labels are intentional. Cash can be presented as one
general-purpose amount. Clear balances remain plural because each is a
different issuer's promise under a different policy.

## Demonstrated user flow

### Sender

The organization operates Clear and uses its privileged lab treasury:

```sh
docker compose exec clear clear-lab issue 100 \
  --memo "Program allocation"

docker compose exec clear clear-lab send 100 \
  recipient@example.org \
  --memo "Program transfer"
```

The sender resolves the recipient's NIP-05 record, verifies advertised Clear
support, and publishes an encrypted kind `1059` gift wrap containing inner kind
`7379`.

### Recipient

Opening the wallet or **Clear Transactions** invokes Acorn's read-only Clear
preview, so a new transfer appears immediately without changing wallet state.
The page presents:

- Clear Balances grouped by exact mint and CMU;
- Pending Clear Transfers with accept and deletion controls;
- friendly program and unit aliases from the mint's `/v1/info`;
- canonical CMU and mint URL;
- pending amount and transfer count; and
- completed Clear Transaction History.

**Check for Clear Transfers** remains an explicit mutation that stores all
discovered receipts and advances the receive cursor. Ordinary page reads remain
non-mutating.

## NIP-05 capability discovery

An operator enables Clear receive advertisement with:

```env
SAFEBOX_CLEAR_RECEIVE_ENABLED=true
SAFEBOX_CLEAR_MINTS=
SAFEBOX_CLEAR_UNITS=
```

The NIP-05 response advertises:

```json
{
  "clear": {
    "alice": {
      "protocols": ["clear-token-transfer"],
      "transports": ["nip59"],
      "kinds": [7379]
    }
  }
}
```

Empty mint and unit restrictions advertise general Clear support. The wallet
still validates the decrypted mint, CMU, keyset IDs, amount, and token.

## Alias resolution

Safebox Web queries a permitted mint's `/v1/info` endpoint and may display:

```text
program: Clear Lab Credits
unit alias: credits
canonical CMU: cmu-000051c14ceac8ee
mint: https://clear.safebox.dev
```

Aliases improve readability but never replace canonical identity. Balances are
grouped only by exact `(normalized mint URL, canonical CMU)`.

## Pending transfer deletion

Before finalization, the user can choose **Delete Pending Transfer** and
explicitly confirm removal.

The Acorn operation:

1. verifies that the transfer is still pending;
2. erases its Cashu bearer token and transfer metadata;
3. retains a minimal event-ID tombstone in encrypted wallet metadata; and
4. prevents later relay scans from restoring the deleted transfer.

Finalized Clear proof state and Clear history cannot be deleted through this
pending action.

## Separation from ordinary ecash

The Clear feature does not alter the existing cash receive path:

```text
kind 7378 -> cash/ecash receive and refresh
kind 7379 -> pending Clear transfer receive
```

Cash finalization does not process Clear transfers. Clear relay checks do not
process kind `7378` payments. Tests cover both paths together.

## Demonstrated result

The deployed lab demonstrated:

- public Clear mint health through `https://clear.safebox.dev`;
- CMU issuance and treasury storage;
- exact-amount transfer to `trbouma@acorn.safebox.dev`;
- relay publication through `wss://relay.getsafebox.app`;
- Acorn pending receipt of multiple CMUs from multiple mints;
- mint alias lookup and separate Clear balance display;
- explicit web relay checking; and
- deletion that remained effective after another receive check.

This is an important product advance: one approachable wallet can now show
general-purpose cash and organization-defined transferable units without
claiming that they are the same thing.

## Current boundary

The recipient can now explicitly accept a pending transfer into spendable kind
`7380` Clear proof state, with an append-only kind `7381` history entry. Safebox
Web displays pending and spendable amounts separately for each exact mint and
CMU.

The next milestone requires the stronger pre-swap crash-recovery journal and
exact-balance onward spending from a Clear Balance.

All current Clear units should be treated as test units with no financial
value. Clear, Acorn, and Safebox Web remain unaudited developer-stage software.
