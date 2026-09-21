# Clear asynchronous acceptance reliability

Clear shares the [asynchronous receipt model](ASYNCHRONOUS-RECEIPT-ARCHITECTURE.md)
without using the Lightning provider's invoice polling queue. Applying the same
HTTP retry policy indiscriminately would be unsafe: Clear acceptance can submit
a mint swap, whereas quote discovery is read-only.

## Changes and boundaries

Safebox Web now reports Clear acceptance failures with the event ID, recipient
npub, phase, and exception category. When an HTTP error survives in the exception
chain, it also reports the status, host, and a restricted response summary.
Arbitrary exception text, response bodies, URL credentials, and query strings
are not copied into these web logs or job errors. Errors without structured
HTTP evidence are reported by exception type, without guessing their cause.
This is scoped to the web wrapper; independent kernel logging is unchanged.

The saved job remains FAILED/REVIEW after an error, or INTERRUPTED after
cancellation. It keeps the event reference. No automatic swap retry was added.
A user-initiated retry invokes the kernel's acceptance/recovery operation again.
The application's diagnostic row is not a replacement for the encrypted
receipt, proof state, or recovery material held by Acorn on relays.

Only `Pending Clear receipt was not found` triggers an event-specific relay
discovery followed by acceptance. Other ValueError messages containing “not
found” could refer to a keyset or other protocol issue, so they now go to review.
Ownership is checked at each phase boundary before further kernel calls; a
superseded owner cannot start loading or acceptance. Existing job heartbeats
and kernel wallet locks provide additional coordination during operations.

## Regression evidence

- HTTP server errors, timeouts, and ambiguous errors do not cause an automatic
  second acceptance call or relay discovery.
- Wrapped HTTP diagnostics omit injected bearer material and URL credentials.
- Cancellation records interruption; a replacement job re-enters kernel
  acceptance, and a stale owner cannot overwrite that job.
- The existing kernel test covers delayed relay proof visibility after a mint
  reports spent inputs.
- A new kernel test interrupts receipt-journal completion after proof and history
  publication. Retrying uses those records, and another duplicate acceptance
  returns the existing result: one swap, one proof publication, one history entry.

Kernel production code is unchanged. The kernel regression test protects its
existing recovery contract; the application changes remain orchestration and
diagnostics. Tests simulate external services. Live testing should still cover
each deployed mint, relay, fee policy, and retention configuration.
