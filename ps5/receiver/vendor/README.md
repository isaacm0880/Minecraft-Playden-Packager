SHA-256 from Alain Mosnier’s sha-2 project, unmodified.

Upstream: https://github.com/amosnier/sha-2
Pinned commit: 565f65009bdd98267361b17d50cddd7c9beb3e6c
Licence: 0BSD (see LICENSE.md).

Only SHA-256 is vendored; the receiver uses RFC 2104 HMAC with fixed 32-byte keys.
Interoperability tests compare hashes and authenticators with Python hashlib/hmac.
