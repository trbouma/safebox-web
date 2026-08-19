# Credential fixtures

`example_mdl.mdoc` and `eudi_pid_synthetic.mdoc` are synthetic CBOR fixtures for
Safebox upload and preview tests. They resemble ISO mdoc device responses but do
not contain valid issuer or device signatures and must not be used for
cryptographic verification or interoperability conformance testing.

`eudi_pid_synthetic.mdoc` uses the EUDI PID document type and namespace
`eu.europa.ec.eudi.pid.1`. Its fictional attributes follow the claim identifiers
published by the European Commission's EUDI PID issuer reference implementation.

`w3c_degree.json` is a W3C Verifiable Credential example used for JSON credential
recognition and preview tests.
