#include "p6c_types.h"

#include <errno.h>
#include <limits.h>
#include <string.h>
#include <unistd.h>


static const uint32_t P6C_SHA256_CONSTANTS[64] = {
    UINT32_C(0x428a2f98), UINT32_C(0x71374491), UINT32_C(0xb5c0fbcf),
    UINT32_C(0xe9b5dba5), UINT32_C(0x3956c25b), UINT32_C(0x59f111f1),
    UINT32_C(0x923f82a4), UINT32_C(0xab1c5ed5), UINT32_C(0xd807aa98),
    UINT32_C(0x12835b01), UINT32_C(0x243185be), UINT32_C(0x550c7dc3),
    UINT32_C(0x72be5d74), UINT32_C(0x80deb1fe), UINT32_C(0x9bdc06a7),
    UINT32_C(0xc19bf174), UINT32_C(0xe49b69c1), UINT32_C(0xefbe4786),
    UINT32_C(0x0fc19dc6), UINT32_C(0x240ca1cc), UINT32_C(0x2de92c6f),
    UINT32_C(0x4a7484aa), UINT32_C(0x5cb0a9dc), UINT32_C(0x76f988da),
    UINT32_C(0x983e5152), UINT32_C(0xa831c66d), UINT32_C(0xb00327c8),
    UINT32_C(0xbf597fc7), UINT32_C(0xc6e00bf3), UINT32_C(0xd5a79147),
    UINT32_C(0x06ca6351), UINT32_C(0x14292967), UINT32_C(0x27b70a85),
    UINT32_C(0x2e1b2138), UINT32_C(0x4d2c6dfc), UINT32_C(0x53380d13),
    UINT32_C(0x650a7354), UINT32_C(0x766a0abb), UINT32_C(0x81c2c92e),
    UINT32_C(0x92722c85), UINT32_C(0xa2bfe8a1), UINT32_C(0xa81a664b),
    UINT32_C(0xc24b8b70), UINT32_C(0xc76c51a3), UINT32_C(0xd192e819),
    UINT32_C(0xd6990624), UINT32_C(0xf40e3585), UINT32_C(0x106aa070),
    UINT32_C(0x19a4c116), UINT32_C(0x1e376c08), UINT32_C(0x2748774c),
    UINT32_C(0x34b0bcb5), UINT32_C(0x391c0cb3), UINT32_C(0x4ed8aa4a),
    UINT32_C(0x5b9cca4f), UINT32_C(0x682e6ff3), UINT32_C(0x748f82ee),
    UINT32_C(0x78a5636f), UINT32_C(0x84c87814), UINT32_C(0x8cc70208),
    UINT32_C(0x90befffa), UINT32_C(0xa4506ceb), UINT32_C(0xbef9a3f7),
    UINT32_C(0xc67178f2)
};

static uint32_t p6c_rotate_right(uint32_t value, unsigned int count)
{
    return (value >> count) | (value << (32U - count));
}

static uint32_t p6c_load_u32_be_sha(const uint8_t input[static 4])
{
    return ((uint32_t)input[0] << 24) | ((uint32_t)input[1] << 16) |
           ((uint32_t)input[2] << 8) | (uint32_t)input[3];
}

static void p6c_sha256_transform(struct p6c_sha256 *context,
                                 const uint8_t block[static 64])
{
    uint32_t words[64];
    uint32_t a = context->state[0];
    uint32_t b = context->state[1];
    uint32_t c = context->state[2];
    uint32_t d = context->state[3];
    uint32_t e = context->state[4];
    uint32_t f = context->state[5];
    uint32_t g = context->state[6];
    uint32_t h = context->state[7];
    size_t index;

    for (index = 0U; index < 16U; ++index) {
        words[index] = p6c_load_u32_be_sha(&block[index * 4U]);
    }
    for (index = 16U; index < 64U; ++index) {
        uint32_t s0 =
            p6c_rotate_right(words[index - 15U], 7U) ^
            p6c_rotate_right(words[index - 15U], 18U) ^
            (words[index - 15U] >> 3);
        uint32_t s1 =
            p6c_rotate_right(words[index - 2U], 17U) ^
            p6c_rotate_right(words[index - 2U], 19U) ^
            (words[index - 2U] >> 10);

        words[index] = words[index - 16U] + s0 +
                       words[index - 7U] + s1;
    }
    for (index = 0U; index < 64U; ++index) {
        uint32_t sum1 = p6c_rotate_right(e, 6U) ^
                        p6c_rotate_right(e, 11U) ^
                        p6c_rotate_right(e, 25U);
        uint32_t choice = (e & f) ^ ((~e) & g);
        uint32_t temporary1 = h + sum1 + choice +
                              P6C_SHA256_CONSTANTS[index] + words[index];
        uint32_t sum0 = p6c_rotate_right(a, 2U) ^
                        p6c_rotate_right(a, 13U) ^
                        p6c_rotate_right(a, 22U);
        uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        uint32_t temporary2 = sum0 + majority;

        h = g;
        g = f;
        f = e;
        e = d + temporary1;
        d = c;
        c = b;
        b = a;
        a = temporary1 + temporary2;
    }
    context->state[0] += a;
    context->state[1] += b;
    context->state[2] += c;
    context->state[3] += d;
    context->state[4] += e;
    context->state[5] += f;
    context->state[6] += g;
    context->state[7] += h;
}

void p6c_sha256_init(struct p6c_sha256 *context)
{
    if (context == NULL) {
        return;
    }
    memset(context, 0, sizeof(*context));
    context->state[0] = UINT32_C(0x6a09e667);
    context->state[1] = UINT32_C(0xbb67ae85);
    context->state[2] = UINT32_C(0x3c6ef372);
    context->state[3] = UINT32_C(0xa54ff53a);
    context->state[4] = UINT32_C(0x510e527f);
    context->state[5] = UINT32_C(0x9b05688c);
    context->state[6] = UINT32_C(0x1f83d9ab);
    context->state[7] = UINT32_C(0x5be0cd19);
}

enum p6c_result p6c_sha256_update(
    struct p6c_sha256 *context, const void *data, size_t size)
{
    const uint8_t *bytes = data;
    size_t offset = 0U;

    if ((context == NULL) || ((data == NULL) && (size != 0U)) ||
        context->finalized ||
        (size > (size_t)((UINT64_MAX - context->bit_count) / 8U))) {
        return P6C_RESULT_INVALID;
    }
    context->bit_count += (uint64_t)size * UINT64_C(8);
    while (offset < size) {
        size_t available = sizeof(context->buffer) -
                           context->buffer_length;
        size_t amount = size - offset;

        if (amount > available) {
            amount = available;
        }
        memcpy(&context->buffer[context->buffer_length], &bytes[offset],
               amount);
        context->buffer_length += amount;
        offset += amount;
        if (context->buffer_length == sizeof(context->buffer)) {
            p6c_sha256_transform(context, context->buffer);
            context->buffer_length = 0U;
        }
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_sha256_final(
    struct p6c_sha256 *context,
    uint8_t digest[static P6C_SHA256_BYTES])
{
    uint64_t bit_count;
    size_t index;

    if ((context == NULL) || (digest == NULL) || context->finalized) {
        return P6C_RESULT_INVALID;
    }
    bit_count = context->bit_count;
    context->buffer[context->buffer_length++] = UINT8_C(0x80);
    if (context->buffer_length > 56U) {
        memset(&context->buffer[context->buffer_length], 0,
               sizeof(context->buffer) - context->buffer_length);
        p6c_sha256_transform(context, context->buffer);
        context->buffer_length = 0U;
    }
    memset(&context->buffer[context->buffer_length], 0,
           56U - context->buffer_length);
    for (index = 0U; index < 8U; ++index) {
        context->buffer[63U - index] = (uint8_t)(bit_count >> (index * 8U));
    }
    p6c_sha256_transform(context, context->buffer);
    for (index = 0U; index < 8U; ++index) {
        digest[index * 4U] = (uint8_t)(context->state[index] >> 24);
        digest[(index * 4U) + 1U] =
            (uint8_t)(context->state[index] >> 16);
        digest[(index * 4U) + 2U] =
            (uint8_t)(context->state[index] >> 8);
        digest[(index * 4U) + 3U] = (uint8_t)context->state[index];
    }
    context->finalized = true;
    memset(context->buffer, 0, sizeof(context->buffer));
    context->buffer_length = 0U;
    return P6C_RESULT_OK;
}

enum p6c_result p6c_sha256_fd(
    const struct p6c_owned_fd *owner,
    uint8_t digest[static P6C_SHA256_BYTES])
{
    struct p6c_sha256 context;
    uint8_t buffer[16384];
    off_t offset = 0;
#ifdef P6C_TESTING
    bool observed = false;
#endif

    if ((owner == NULL) || (digest == NULL) ||
        !p6c_owned_fd_is_live(owner)) {
        return P6C_RESULT_INVALID;
    }
    p6c_sha256_init(&context);
    for (;;) {
        ssize_t amount;

        if (p6c_failpoint_active(P6C_FAIL_EXEC_HASH_READ)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        amount = pread(owner->descriptor, buffer, sizeof(buffer), offset);
        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (amount == 0) {
            break;
        }
#ifdef P6C_TESTING
        if (!observed) {
            p6c_test_exec_hash_observe();
            observed = true;
        }
#endif
        if (p6c_sha256_update(&context, buffer, (size_t)amount) !=
            P6C_RESULT_OK) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (offset > (off_t)(INT64_MAX - (int64_t)amount)) {
            return P6C_RESULT_LIMIT;
        }
        offset += amount;
    }
    return p6c_sha256_final(&context, digest);
}
