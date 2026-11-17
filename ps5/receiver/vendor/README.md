SHA-256 from Alain Mosnier’s sha-2 project, unmodified.

Upstream: https://github.com/amosnier/sha-2
Pinned commit: 565f65009bdd98267361b17d50cddd7c9beb3e6c
Licence: 0BSD (see LICENSE.md).

The receiver uses RFC 2104 HMAC with fixed 32-byte keys.
Interoperability tests compare hashes and authenticators with Python hashlib/hmac.


BearSSL 0.6's MIT-licensed constant-time i31 arithmetic, headers and conditional
copy routine are vendored under `bearssl/`. Upstream archive:
https://bearssl.org/bearssl-0.6.tar.gz . `bearssl/manifest.json` records the archive
SHA-256 and every source/header hash; `LICENSE.txt` is retained. Upstream files
are unchanged. Two local forwarding headers adapt include paths, and `ccopy.c`
is relocated from upstream `src/codec/`; these are identified in the manifest.

`pairing_crypto.h` compiles this subset in the receiver translation unit for
SRP-6a and RSA-OAEP public exponentiation. It does not include the BearSSL TLS
stack or use a system OpenSSL dependency. Its wire composition is project code;
see the protocol and independent SRP vectors/security tests.
