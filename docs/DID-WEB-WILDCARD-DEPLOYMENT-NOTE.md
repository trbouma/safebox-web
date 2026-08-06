# DID Web Wildcard Deployment Note

## Status and scope

This is a deferred deployment note for Safebox Web. It records how
handle-specific Web DIDs could be introduced later without changing the
existing `acorn.safebox.dev` application deployment today.

This capability belongs to Safebox Web because DNS, TLS, HTTP resolution,
handle registration, and reverse-proxy trust are application and
infrastructure concerns. Acorn remains responsible for the component key and
its protocol state.

No wildcard DNS, certificate, proxy, database, or application change is
required until this work is deliberately scheduled.

## Intended identifier

For a claimed Safebox handle named `bob`, the intended identifier is:

```text
did:web:bob.acorn.safebox.dev
```

The [DID Web Method specification](https://w3c-ccg.github.io/did-method-web/#read-resolve)
maps that identifier to:

```text
https://bob.acorn.safebox.dev/.well-known/did.json
```

It does not define a public query-parameter mapping such as:

```text
https://acorn.safebox.dev/.well-known/did.json?name=bob
```

Safebox Web may use a shared internal function or internal rewrite equivalent
to `get_did_document(handle="bob")`, but the standards-facing resource must
remain available at the hostname-derived URL. The returned document's `id`
must exactly match the DID being resolved.

If wildcard hosts are ultimately undesirable, the standards-compliant
path-based alternative is:

```text
did:web:acorn.safebox.dev:bob
    -> https://acorn.safebox.dev/bob/did.json
```

This is a different DID, so the identifier form should be chosen before
publishing durable identifiers.

## Proposed isolated architecture

```text
Existing application
acorn.safebox.dev
    -> existing exact-name Nginx server
    -> complete Safebox Web application

Future DID resolution
bob.acorn.safebox.dev
    -> separate wildcard Nginx server
    -> only /.well-known/did.json
    -> same Safebox Web process
    -> lookup claimed_handle = bob
```

The existing exact-name Nginx server block should remain unchanged. Nginx
selects an exact server name before a wildcard server name, so a separate
`*.acorn.safebox.dev` block can be added without taking precedence over
`acorn.safebox.dev`. See the
[Nginx server-name documentation](https://nginx.org/en/docs/http/server_names.html).

The wildcard host should expose only DID resolution. Login, wallet, payment,
record, and session routes should remain available through the existing exact
application hostname. This avoids creating parallel cookie scopes or
accidentally presenting the entire application on every handle subdomain.

## Prerequisites

Before enabling resolution:

1. Confirm which provider is authoritative for `safebox.dev` DNS.
2. Confirm how the current `acorn.safebox.dev` certificate is issued and
   renewed.
3. Add wildcard DNS pointing to the existing public reverse proxy.
4. Obtain a separate wildcard certificate using DNS-01 validation.
5. Add and test a separate Nginx wildcard server block.
6. Implement the Safebox Web DID document endpoint and host validation.

A suitable DNS record would usually be one of:

```text
*.acorn.safebox.dev  CNAME  acorn.safebox.dev
```

or:

```text
*.acorn.safebox.dev  A  <public reverse-proxy address>
```

The existing exact DNS record remains in place. Confirm the wildcard before
continuing:

```sh
dig bob.acorn.safebox.dev
```

Wildcard certificates require ACME DNS-01 validation. A certificate for
`*.acorn.safebox.dev` does not cover `acorn.safebox.dev`; this proposal
therefore leaves the existing exact-host certificate in place and uses a
separate certificate for the wildcard server.

Automated DNS validation is strongly preferred over a manual challenge because
certificate renewal must remain reliable.

## Proposed Nginx isolation

The following is a future template, not a configuration to install without
reviewing the current Nginx layout and certificate paths:

```nginx
server {
    listen 443 ssl;
    server_name *.acorn.safebox.dev;

    ssl_certificate /path/to/wildcard/fullchain.pem;
    ssl_certificate_key /path/to/wildcard/privkey.pem;

    location = /.well-known/did.json {
        limit_except GET {
            deny all;
        }

        proxy_pass http://100.70.55.66:8100;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location / {
        return 404;
    }
}
```

Passing the original `Host` value is essential: Safebox Web needs to extract
`bob` from `bob.acorn.safebox.dev`, validate the suffix against its
configured DID base domain, normalize the handle, and look it up in the
`claimed_handle` table.

The existing Tailscale application path does not otherwise change. The public
Nginx proxy continues to terminate TLS and forwards the permitted request to
the same Safebox Web address. `FORWARDED_ALLOW_IPS` should continue to trust
only the immediate proxy.

## Handle compatibility

The existing NIP-05 handle syntax is broader than a single DNS label. Before
enabling subdomain DIDs, Safebox Web must define which claimed handles are
DID-host eligible.

A conservative DNS-label policy is:

- lowercase ASCII letters, digits, and hyphens only;
- begin and end with a letter or digit;
- no underscores;
- no periods or nested labels; and
- no more than 63 characters.

Existing handles that do not satisfy this policy can continue working as
NIP-05 and Lightning addresses, but cannot automatically become
`handle.acorn.safebox.dev` hostnames. The application must not silently
rewrite incompatible handles because doing so could create collisions.

## Non-breaking rollout

Use this sequence when the work is resumed:

1. Record the existing DNS, certificate, and Nginx configuration.
2. Add wildcard DNS without changing the exact record.
3. Obtain a separate wildcard certificate without replacing the current
   certificate.
4. Add a separate wildcard server block.
5. Run `sudo nginx -t`.
6. Reload Nginx rather than restarting it.
7. Initially serve only a harmless test response from the wildcard route.
8. Verify DNS, TLS, host forwarding, and route isolation.
9. Implement and enable DID document generation.
10. Test an unclaimed handle, a claimed compatible handle, and a malformed
    hostname.

Useful read-only discovery commands on the proxy are:

```sh
sudo nginx -T 2>&1 | grep -n -E 'server_name|ssl_certificate|acorn\.safebox\.dev'
sudo certbot certificates
dig NS safebox.dev
dig A acorn.safebox.dev
```

After configuration, test both the new and existing paths:

```sh
curl -i https://bob.acorn.safebox.dev/.well-known/did.json
curl -i https://acorn.safebox.dev/health
curl -i https://bob.acorn.safebox.dev/
```

The expected results are a DID document for a valid claimed handle, an
unchanged healthy application, and `404` for the wildcard root.

## Rollback

Rollback does not require changing Safebox Web or the existing exact host:

1. disable the wildcard Nginx server block;
2. run `sudo nginx -t`;
3. reload Nginx; and
4. remove the wildcard DNS record when it is no longer needed.

The exact `acorn.safebox.dev` record, certificate, and server block remain
independent throughout this procedure.

## Trust and security implications

A `did:web` identifier inherits trust from the domain, DNS provider,
certificate authority, reverse proxy, and application operator. Whoever can
change those layers can change the resolved DID document. This is consistent
with Safebox Web's existing trust boundary and should be explained to users.

The DID document must contain public key material only. It must never expose an
`nsec`, offline mnemonic, entropy, encrypted browser session, wallet proofs,
or private-record contents.

Claiming or updating the DID mapping should reuse the existing authenticated
Acorn-handle ownership rule. Removing a handle should make the corresponding
DID document unavailable. Operational logging must not introduce additional
secret material.

## Future application work

When deployment prerequisites are understood, the application work should:

- add an explicit configured DID base domain;
- accept only the exact wildcard DID hostname pattern;
- extract and normalize one DNS-safe handle;
- retrieve the existing claimed handle, `npub`, and home relay;
- generate a standards-shaped DID document from public material;
- ensure the document `id` matches the requested DID;
- return a suitable JSON media type and `Access-Control-Allow-Origin: *`;
- return `404` for unknown, malformed, or ineligible handles;
- add endpoint, key-encoding, host-validation, and negative tests; and
- document how Acorn's Nostr key is represented as DID verification material.

Key representation needs a deliberate interoperability decision. An Acorn
`npub` is a Nostr/BIP340 x-only secp256k1 public key; publishing it as a DID
verification method requires a precisely specified, tested representation and
must not imply signature capabilities that Acorn does not implement.
