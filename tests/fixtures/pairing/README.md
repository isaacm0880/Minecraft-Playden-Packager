# Synthetic PAKE reference vectors

`srp-vectors.json` contains no operational credentials. Values are deterministic
synthetic test inputs, including six-digit codes with leading zeros and salts
with leading zero bytes. The SRP premaster secrets were generated and mutually
verified using the independent MIT-licensed cocagne/pysrp implementation at
commit `2f0211d7965290664af1537a0164dbb82a7270ff` (source SHA-256 in the JSON).

The product uses RFC 5054's 2048-bit group and SRP-6a with SHA-256, then fixed-width
`SHA256(PAD(S))` and separately labelled transcript-bound HMAC confirmation and
credential derivation. The reference fixture checks the mathematical shared
secret independently of our confirmation/envelope format.
