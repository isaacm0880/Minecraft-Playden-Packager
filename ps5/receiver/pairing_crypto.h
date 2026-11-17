/* SRP-6a (RFC 5054 group 2048, SHA-256) and RSA-OAEP startup envelope.
 * BearSSL i31 arithmetic is constant-time for fixed public modulus/exponent length.
 * No password-derived value is used by the transfer HMAC protocol.
 */
#ifndef PLAYDEN_PAIRING_CRYPTO_H
#define PLAYDEN_PAIRING_CRYPTO_H
#undef MIN
#undef MAX
#include "vendor/bearssl/src/inner.h"
#include "vendor/bearssl/src/ccopy.c"
#include "vendor/bearssl/src/int/i31_add.c"
#include "vendor/bearssl/src/int/i31_bitlen.c"
#include "vendor/bearssl/src/int/i31_decmod.c"
#include "vendor/bearssl/src/int/i31_decode.c"
#include "vendor/bearssl/src/int/i31_encode.c"
#include "vendor/bearssl/src/int/i31_iszero.c"
#include "vendor/bearssl/src/int/i31_modpow.c"
#include "vendor/bearssl/src/int/i31_montmul.c"
#include "vendor/bearssl/src/int/i31_muladd.c"
#include "vendor/bearssl/src/int/i31_ninv31.c"
#include "vendor/bearssl/src/int/i31_sub.c"
#include "vendor/bearssl/src/int/i31_tmont.c"
#include "vendor/bearssl/src/int/i32_div32.c"

static const uint8_t srp_n[256] = {
    0xac, 0x6b, 0xdb, 0x41, 0x32, 0x4a, 0x9a, 0x9b, 0xf1, 0x66, 0xde, 0x5e, 0x13, 0x89, 0x58, 0x2f,
    0xaf, 0x72, 0xb6, 0x65, 0x19, 0x87, 0xee, 0x07, 0xfc, 0x31, 0x92, 0x94, 0x3d, 0xb5, 0x60, 0x50,
    0xa3, 0x73, 0x29, 0xcb, 0xb4, 0xa0, 0x99, 0xed, 0x81, 0x93, 0xe0, 0x75, 0x77, 0x67, 0xa1, 0x3d,
    0xd5, 0x23, 0x12, 0xab, 0x4b, 0x03, 0x31, 0x0d, 0xcd, 0x7f, 0x48, 0xa9, 0xda, 0x04, 0xfd, 0x50,
    0xe8, 0x08, 0x39, 0x69, 0xed, 0xb7, 0x67, 0xb0, 0xcf, 0x60, 0x95, 0x17, 0x9a, 0x16, 0x3a, 0xb3,
    0x66, 0x1a, 0x05, 0xfb, 0xd5, 0xfa, 0xaa, 0xe8, 0x29, 0x18, 0xa9, 0x96, 0x2f, 0x0b, 0x93, 0xb8,
    0x55, 0xf9, 0x79, 0x93, 0xec, 0x97, 0x5e, 0xea, 0xa8, 0x0d, 0x74, 0x0a, 0xdb, 0xf4, 0xff, 0x74,
    0x73, 0x59, 0xd0, 0x41, 0xd5, 0xc3, 0x3e, 0xa7, 0x1d, 0x28, 0x1e, 0x44, 0x6b, 0x14, 0x77, 0x3b,
    0xca, 0x97, 0xb4, 0x3a, 0x23, 0xfb, 0x80, 0x16, 0x76, 0xbd, 0x20, 0x7a, 0x43, 0x6c, 0x64, 0x81,
    0xf1, 0xd2, 0xb9, 0x07, 0x87, 0x17, 0x46, 0x1a, 0x5b, 0x9d, 0x32, 0xe6, 0x88, 0xf8, 0x77, 0x48,
    0x54, 0x45, 0x23, 0xb5, 0x24, 0xb0, 0xd5, 0x7d, 0x5e, 0xa7, 0x7a, 0x27, 0x75, 0xd2, 0xec, 0xfa,
    0x03, 0x2c, 0xfb, 0xdb, 0xf5, 0x2f, 0xb3, 0x78, 0x61, 0x60, 0x27, 0x90, 0x04, 0xe5, 0x7a, 0xe6,
    0xaf, 0x87, 0x4e, 0x73, 0x03, 0xce, 0x53, 0x29, 0x9c, 0xcc, 0x04, 0x1c, 0x7b, 0xc3, 0x08, 0xd8,
    0x2a, 0x56, 0x98, 0xf3, 0xa8, 0xd0, 0xc3, 0x82, 0x71, 0xae, 0x35, 0xf8, 0xe9, 0xdb, 0xfb, 0xb6,
    0x94, 0xb5, 0xc8, 0x03, 0xd8, 0x9f, 0x7a, 0xe4, 0x35, 0xde, 0x23, 0x6d, 0x52, 0x5f, 0x54, 0x75,
    0x9b, 0x65, 0xe3, 0x72, 0xfc, 0xd6, 0x8e, 0xf2, 0x0f, 0xa7, 0x11, 0x1f, 0x9e, 0x4a, 0xff, 0x73,
};
static void wipe(void *ptr, size_t size) {
    volatile uint8_t *p = ptr;
    while (size--) *p++ = 0;
}
static void hash_parts(const void *a, size_t an, const void *b, size_t bn,
                       const void *c, size_t cn, uint8_t out[32]) {
    struct Sha_256 hash;
    sha_256_init(&hash, out);
    sha_256_write(&hash, a, an);
    sha_256_write(&hash, b, bn);
    sha_256_write(&hash, c, cn);
    sha_256_close(&hash);
}
typedef uint32_t Big[101];
static void pow_mod(Big x, const uint8_t *exponent, size_t length, const Big n) {
    Big t1, t2;
    br_i31_modpow(x, exponent, length, n, br_i31_ninv31(n[1]), t1, t2);
    wipe(t1, sizeof(t1)); wipe(t2, sizeof(t2));
}
static void mul_mod(Big out, const Big x, const Big y, const Big n) {
    Big monty;
    memcpy(monty, x, sizeof(monty));
    br_i31_to_monty(monty, n);
    br_i31_montymul(out, monty, y, n, br_i31_ninv31(n[1]));
    wipe(monty, sizeof(monty));
}
/* Both public inputs and all private exponents have fixed widths. */
static int srp_public(const char code[7], const uint8_t salt[16], const uint8_t secret[32],
                      uint8_t B[256], uint8_t verifier[256]) {
    Big n = {0}, g = {0}, v = {0}, k = {0}, gb = {0}, kv = {0};
    uint8_t padded_g[256] = {0}, inner[32], x[32], multiplier[32];
    padded_g[255] = 2;
    br_i31_decode(n, srp_n, 256);
    br_i31_decode_mod(g, padded_g, 256, n);
    hash_parts("playden:", 8, code, 6, "", 0, inner);
    hash_parts(salt, 16, inner, 32, "", 0, x);
    memcpy(v, g, sizeof(v)); pow_mod(v, x, 32, n);
    br_i31_encode(verifier, 256, v);
    hash_parts(srp_n, 256, padded_g, 256, "", 0, multiplier);
    br_i31_decode_mod(k, multiplier, 32, n);
    memcpy(gb, g, sizeof(gb)); pow_mod(gb, secret, 32, n);
    mul_mod(kv, k, v, n);
    br_i31_add(gb, kv, 1);
    br_i31_sub(gb, n, 1 ^ br_i31_sub(gb, n, 0));
    br_i31_encode(B, 256, gb);
    int valid = !br_i31_iszero(gb);
    wipe(v, sizeof(v)); wipe(kv, sizeof(kv)); wipe(gb, sizeof(gb));
    wipe(inner, sizeof(inner)); wipe(x, sizeof(x));
    return valid;
}
static int srp_shared(const uint8_t A[256], const uint8_t B[256], const uint8_t verifier[256],
                      const uint8_t secret[32], uint8_t key[32]) {
    Big n = {0}, a = {0}, v = {0}, vu = {0}, s = {0};
    uint8_t u[32], padded_s[256];
    br_i31_decode(n, srp_n, 256);
    int valid = br_i31_decode_mod(a, A, 256, n) && !br_i31_iszero(a);
    if (!valid) return 0;
    hash_parts(A, 256, B, 256, "", 0, u);
    uint8_t zero[32] = {0};
    if (same(u, zero, 32)) return 0;
    br_i31_decode_mod(v, verifier, 256, n);
    memcpy(vu, v, sizeof(vu)); pow_mod(vu, u, 32, n);
    mul_mod(s, a, vu, n); pow_mod(s, secret, 32, n);
    br_i31_encode(padded_s, 256, s);
    hash_parts(padded_s, 256, "", 0, "", 0, key);
    wipe(v, sizeof(v)); wipe(vu, sizeof(vu)); wipe(s, sizeof(s));
    wipe(padded_s, sizeof(padded_s));
    return 1;
}
#define BOOT_MARKER "PDLAN-BOOTSTRAP-RSA-3072-V2:"
#define BOOT_MARKER_SIZE (sizeof(BOOT_MARKER) - 1)
/* Only PUBLIC RSA modulus and non-sensitive ports are patched into the ELF. */
static volatile uint8_t bootstrap_slot[BOOT_MARKER_SIZE + 384 + 4] = BOOT_MARKER;
static void mgf_xor(uint8_t *target, size_t length, const uint8_t *seed, size_t seed_size) {
    uint32_t counter = 0;
    while (length) {
        uint8_t c[4], digest[32];
        putnum(c, counter++, 4);
        hash_parts(seed, seed_size, c, 4, "", 0, digest);
        size_t amount = length < 32 ? length : 32;
        for (size_t i = 0; i < amount; ++i) target[i] ^= digest[i];
        target += amount; length -= amount;
        wipe(digest, sizeof(digest));
    }
}
static int bootstrap_encrypt(const uint8_t *payload, size_t length, uint8_t encrypted[384]) {
    uint8_t modulus[384], exponent[3] = {1, 0, 1};
    for (size_t i = 0; i < 384; ++i) modulus[i] = bootstrap_slot[BOOT_MARKER_SIZE + i];
    if (!(modulus[0] & 0x80) || !(modulus[383] & 1) || length > 318) return 0;
    uint8_t encoded[384] = {0};
    uint8_t *seed = encoded + 1, *db = encoded + 33;
    const char *label = "Playden receiver bootstrap v2";
    hash_parts(label, strlen(label), "", 0, "", 0, db);
    db[350 - length] = 1;
    memcpy(db + 351 - length, payload, length);
    arc4random_buf(seed, 32);
    mgf_xor(db, 351, seed, 32);
    mgf_xor(seed, 32, db, 351);
    Big n = {0}, message = {0};
    br_i31_decode(n, modulus, 384);
    if (!br_i31_decode_mod(message, encoded, 384, n)) return 0;
    pow_mod(message, exponent, 3, n);
    br_i31_encode(encrypted, 384, message);
    wipe(message, sizeof(message)); wipe(encoded, sizeof(encoded));
    return 1;
}
#endif
