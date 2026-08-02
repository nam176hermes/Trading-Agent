#include "p6c_protocol.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>


#define CHECK(condition)                                                      \
    do {                                                                      \
        if (!(condition)) {                                                   \
            (void)fprintf(stderr, "test_protocol: check failed at line %d\n", \
                          __LINE__);                                           \
            return EXIT_FAILURE;                                              \
        }                                                                     \
    } while (0)

struct enum_evidence {
    const char *key;
    int value;
};

static const uint8_t EXPECTED_CANONICAL_HEADER[P6C_HEADER_SIZE] = {
    UINT8_C(0x50), UINT8_C(0x36), UINT8_C(0x43), UINT8_C(0x41),
    UINT8_C(0x00), UINT8_C(0x01), UINT8_C(0x00), UINT8_C(0x01),
    UINT8_C(0x00), UINT8_C(0x00), UINT8_C(0x00), UINT8_C(0x00),
    UINT8_C(0x00), UINT8_C(0x01), UINT8_C(0x02), UINT8_C(0x03),
    UINT8_C(0x04), UINT8_C(0x05), UINT8_C(0x06), UINT8_C(0x07),
    UINT8_C(0x08), UINT8_C(0x09), UINT8_C(0x0a), UINT8_C(0x0b),
    UINT8_C(0x0c), UINT8_C(0x0d), UINT8_C(0x0e), UINT8_C(0x0f),
    UINT8_C(0x00), UINT8_C(0x00), UINT8_C(0x00), UINT8_C(0x03),
    UINT8_C(0x35), UINT8_C(0x24), UINT8_C(0x41), UINT8_C(0xc2)
};

static void print_enum_evidence(const struct enum_evidence *evidence,
                                size_t count)
{
    size_t index;

    for (index = 0; index < count; ++index) {
        (void)printf("%s=%d\n", evidence[index].key, evidence[index].value);
    }
}

static void print_canonical_header(const uint8_t header[static P6C_HEADER_SIZE])
{
    size_t index;

    (void)fputs("canonical_header=", stdout);
    for (index = 0; index < P6C_HEADER_SIZE; ++index) {
        (void)printf("%02x", (unsigned int)header[index]);
    }
    (void)fputc('\n', stdout);
}

static size_t test_store_field(uint8_t *output, uint16_t field_id,
                               const uint8_t *value, uint32_t value_length)
{
    p6c_store_u16_be(output, field_id);
    p6c_store_u16_be(&output[2], UINT16_C(0));
    p6c_store_u32_be(&output[4], value_length);
    if (value_length != 0U) {
        memcpy(&output[P6C_FIELD_HEADER_SIZE], value, (size_t)value_length);
    }
    return P6C_FIELD_HEADER_SIZE + (size_t)value_length;
}

static size_t test_build_frame(uint8_t *output, size_t output_capacity,
                               uint16_t message_type, const uint8_t *payload,
                               size_t payload_length)
{
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    size_t index;

    if ((payload_length > (size_t)UINT32_MAX) ||
        (output_capacity < P6C_HEADER_SIZE + payload_length)) {
        return 0U;
    }
    for (index = 0; index < P6C_REQUEST_ID_BYTES; ++index) {
        request_id[index] = (uint8_t)(index + 1U);
    }
    p6c_encode_header_v1(output, message_type, request_id,
                         (uint32_t)payload_length,
                         p6c_crc32(payload, payload_length));
    if (payload_length != 0U) {
        memcpy(&output[P6C_HEADER_SIZE], payload, payload_length);
    }
    return P6C_HEADER_SIZE + payload_length;
}

int main(void)
{
    static const struct enum_evidence REQUEST_EVIDENCE[] = {
        {"request.HELLO", P6C_REQUEST_HELLO},
        {"request.START", P6C_REQUEST_START},
        {"request.STATUS", P6C_REQUEST_STATUS},
        {"request.STOP", P6C_REQUEST_STOP},
        {"request.RUN_ONCE", P6C_REQUEST_RUN_ONCE},
        {"request.READ_TRANSCRIPT", P6C_REQUEST_READ_TRANSCRIPT},
        {"request.PUBLISH_BUNDLE", P6C_REQUEST_PUBLISH_BUNDLE},
        {"request.ACK", P6C_REQUEST_ACK},
        {"request.RECOVER", P6C_REQUEST_RECOVER},
    };
    static const struct enum_evidence OPERATION_EVIDENCE[] = {
        {"operation_state.ABSENT", P6C_OPERATION_ABSENT},
        {"operation_state.RESERVED", P6C_OPERATION_RESERVED},
        {"operation_state.EXECUTABLE_PINNED",
         P6C_OPERATION_EXECUTABLE_PINNED},
        {"operation_state.CGROUP_CREATED", P6C_OPERATION_CGROUP_CREATED},
        {"operation_state.CHILD_CLONED", P6C_OPERATION_CHILD_CLONED},
        {"operation_state.EXEC_CONFIRMED", P6C_OPERATION_EXEC_CONFIRMED},
        {"operation_state.RUNNING", P6C_OPERATION_RUNNING},
        {"operation_state.STOP_REQUESTED", P6C_OPERATION_STOP_REQUESTED},
        {"operation_state.CGROUP_KILLED", P6C_OPERATION_CGROUP_KILLED},
        {"operation_state.CGROUP_EMPTY", P6C_OPERATION_CGROUP_EMPTY},
        {"operation_state.CHILD_EXIT_OBSERVED",
         P6C_OPERATION_CHILD_EXIT_OBSERVED},
        {"operation_state.CHILD_REAPED", P6C_OPERATION_CHILD_REAPED},
        {"operation_state.TRANSCRIPTS_FINAL",
         P6C_OPERATION_TRANSCRIPTS_FINAL},
        {"operation_state.RESULT_RETAINED", P6C_OPERATION_RESULT_RETAINED},
        {"operation_state.ACKNOWLEDGED", P6C_OPERATION_ACKNOWLEDGED},
        {"operation_state.RECOVERY_REQUIRED",
         P6C_OPERATION_RECOVERY_REQUIRED},
    };
    static const struct enum_evidence STATUS_EVIDENCE[] = {
        {"public_status.OK", P6C_STATUS_OK},
        {"public_status.INVALID_FRAME", P6C_STATUS_INVALID_FRAME},
        {"public_status.UNSUPPORTED_VERSION",
         P6C_STATUS_UNSUPPORTED_VERSION},
        {"public_status.UNAUTHORIZED", P6C_STATUS_UNAUTHORIZED},
        {"public_status.INVALID_REQUEST", P6C_STATUS_INVALID_REQUEST},
        {"public_status.NOT_FOUND", P6C_STATUS_NOT_FOUND},
        {"public_status.CONFLICT", P6C_STATUS_CONFLICT},
        {"public_status.LIMIT_EXCEEDED", P6C_STATUS_LIMIT_EXCEEDED},
        {"public_status.TIMEOUT", P6C_STATUS_TIMEOUT},
        {"public_status.RECOVERY_REQUIRED", P6C_STATUS_RECOVERY_REQUIRED},
        {"public_status.INTERNAL", P6C_STATUS_INTERNAL},
    };
    static const uint8_t PAYLOAD[] = {
        UINT8_C(0x61), UINT8_C(0x62), UINT8_C(0x63)
    };
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t encoded_header[P6C_HEADER_SIZE];
    uint8_t complete_frame[P6C_HEADER_SIZE + sizeof(PAYLOAD)];
    uint8_t structured_frame[512];
    uint8_t structured_payload[256];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t operation_digest[P6C_SHA256_BYTES];
    uint8_t recovery_token[P6C_OPERATION_ID_BYTES];
    uint8_t invalid_text[2] = {UINT8_C(0xc0), UINT8_C(0xaf)};
    uint8_t embedded_nul[3] = {
        UINT8_C('a'), UINT8_C(0), UINT8_C('b')
    };
    uint8_t malformed_frame[P6C_HEADER_SIZE + sizeof(PAYLOAD)];
    struct p6c_frame_view decoded;
    uint32_t payload_crc32;
    size_t index;
    size_t payload_size;
    size_t frame_size;
    uint8_t list_value[64];

    CHECK(P6C_PROTOCOL_MAGIC == UINT32_C(0x50364341));
    CHECK(P6C_PROTOCOL_VERSION == UINT16_C(1));
    CHECK(P6C_REQUEST_ID_BYTES == 16);
    CHECK(P6C_OPERATION_ID_BYTES == 16);
    CHECK(P6C_SHA256_BYTES == 32);
    CHECK(P6C_V1_FLAGS == UINT32_C(0));
    CHECK(P6C_RESPONSE_BIT == UINT16_C(0x8000));
    CHECK(P6C_ERROR_MESSAGE_TYPE == UINT16_C(0xffff));
    CHECK(P6C_HEADER_MAGIC_OFFSET == 0);
    CHECK(P6C_HEADER_VERSION_OFFSET == 4);
    CHECK(P6C_HEADER_MESSAGE_TYPE_OFFSET == 6);
    CHECK(P6C_HEADER_FLAGS_OFFSET == 8);
    CHECK(P6C_HEADER_REQUEST_ID_OFFSET == 12);
    CHECK(P6C_HEADER_PAYLOAD_LENGTH_OFFSET == 28);
    CHECK(P6C_HEADER_PAYLOAD_CRC32_OFFSET == 32);
    CHECK(P6C_HEADER_SIZE == 36);
    CHECK(P6C_MAX_PAYLOAD_BYTES == UINT32_C(1048576));
    CHECK(P6C_MAX_FRAME_BYTES == UINT32_C(1048612));
    CHECK(P6C_MAX_ARGV_COUNT == UINT32_C(128));
    CHECK(P6C_MAX_ENVIRONMENT_COUNT == UINT32_C(128));
    CHECK(P6C_MAX_STRING_BYTES == UINT32_C(4096));
    CHECK(P6C_MAX_PUBLIC_CODE_BYTES == UINT32_C(64));

    CHECK(P6C_REQUEST_HELLO == 1);
    CHECK(P6C_REQUEST_START == 2);
    CHECK(P6C_REQUEST_STATUS == 3);
    CHECK(P6C_REQUEST_STOP == 4);
    CHECK(P6C_REQUEST_RUN_ONCE == 5);
    CHECK(P6C_REQUEST_READ_TRANSCRIPT == 6);
    CHECK(P6C_REQUEST_PUBLISH_BUNDLE == 7);
    CHECK(P6C_REQUEST_ACK == 8);
    CHECK(P6C_REQUEST_RECOVER == 9);

    CHECK(P6C_OPERATION_ABSENT == 0);
    CHECK(P6C_OPERATION_RESERVED == 1);
    CHECK(P6C_OPERATION_EXECUTABLE_PINNED == 2);
    CHECK(P6C_OPERATION_CGROUP_CREATED == 3);
    CHECK(P6C_OPERATION_CHILD_CLONED == 4);
    CHECK(P6C_OPERATION_EXEC_CONFIRMED == 5);
    CHECK(P6C_OPERATION_RUNNING == 6);
    CHECK(P6C_OPERATION_STOP_REQUESTED == 7);
    CHECK(P6C_OPERATION_CGROUP_KILLED == 8);
    CHECK(P6C_OPERATION_CGROUP_EMPTY == 9);
    CHECK(P6C_OPERATION_CHILD_EXIT_OBSERVED == 10);
    CHECK(P6C_OPERATION_CHILD_REAPED == 11);
    CHECK(P6C_OPERATION_TRANSCRIPTS_FINAL == 12);
    CHECK(P6C_OPERATION_RESULT_RETAINED == 13);
    CHECK(P6C_OPERATION_ACKNOWLEDGED == 14);
    CHECK(P6C_OPERATION_RECOVERY_REQUIRED == 15);

    CHECK(P6C_STATUS_OK == 0);
    CHECK(P6C_STATUS_INVALID_FRAME == 1);
    CHECK(P6C_STATUS_UNSUPPORTED_VERSION == 2);
    CHECK(P6C_STATUS_UNAUTHORIZED == 3);
    CHECK(P6C_STATUS_INVALID_REQUEST == 4);
    CHECK(P6C_STATUS_NOT_FOUND == 5);
    CHECK(P6C_STATUS_CONFLICT == 6);
    CHECK(P6C_STATUS_LIMIT_EXCEEDED == 7);
    CHECK(P6C_STATUS_TIMEOUT == 8);
    CHECK(P6C_STATUS_RECOVERY_REQUIRED == 9);
    CHECK(P6C_STATUS_INTERNAL == 10);

    for (index = 0; index < P6C_REQUEST_ID_BYTES; ++index) {
        request_id[index] = (uint8_t)index;
    }
    CHECK(sizeof(PAYLOAD) == 3);
    CHECK(memcmp(PAYLOAD, "abc", sizeof(PAYLOAD)) == 0);
    CHECK(p6c_crc32(PAYLOAD, sizeof(PAYLOAD)) ==
          UINT32_C(0x352441c2));
    payload_crc32 = p6c_crc32(PAYLOAD, sizeof(PAYLOAD));
    CHECK(payload_crc32 == UINT32_C(0x352441c2));

    p6c_encode_header_v1(encoded_header, (uint16_t)P6C_REQUEST_HELLO,
                         request_id, (uint32_t)sizeof(PAYLOAD),
                         payload_crc32);
    CHECK(memcmp(encoded_header, EXPECTED_CANONICAL_HEADER,
                 P6C_HEADER_SIZE) == 0);
    memcpy(complete_frame, encoded_header, P6C_HEADER_SIZE);
    memcpy(&complete_frame[P6C_HEADER_SIZE], PAYLOAD, sizeof(PAYLOAD));
    CHECK(p6c_decode_request(complete_frame, sizeof(complete_frame),
                             &decoded) == P6C_PARSE_OK);
    CHECK(decoded.message_type == (uint16_t)P6C_REQUEST_HELLO);
    CHECK(decoded.payload_length == (uint32_t)sizeof(PAYLOAD));
    CHECK(memcmp(decoded.payload, PAYLOAD, sizeof(PAYLOAD)) == 0);
    CHECK(p6c_decode_request(encoded_header, P6C_HEADER_SIZE, &decoded) ==
          P6C_PARSE_TRUNCATED);
    complete_frame[P6C_HEADER_SIZE] ^= UINT8_C(1);
    CHECK(p6c_decode_request(complete_frame, sizeof(complete_frame),
                             &decoded) == P6C_PARSE_BAD_CRC);
    complete_frame[P6C_HEADER_SIZE] ^= UINT8_C(1);

    memcpy(malformed_frame, complete_frame, sizeof(malformed_frame));
    memset(&malformed_frame[P6C_HEADER_REQUEST_ID_OFFSET], 0,
           P6C_REQUEST_ID_BYTES);
    CHECK(p6c_decode_request(malformed_frame, sizeof(malformed_frame),
                             &decoded) == P6C_PARSE_REQUEST_ID);
    memcpy(structured_frame, complete_frame, sizeof(complete_frame));
    structured_frame[sizeof(complete_frame)] = UINT8_C(0);
    CHECK(p6c_decode_request(structured_frame,
                             sizeof(complete_frame) + 1U, &decoded) ==
          P6C_PARSE_TRAILING_BYTES);

    memset(operation_id, UINT8_C(0x41), sizeof(operation_id));
    memset(operation_digest, UINT8_C(0x44), sizeof(operation_digest));
    memset(recovery_token, UINT8_C(0x52), sizeof(recovery_token));
    payload_size = test_store_field(
        structured_payload, (uint16_t)P6C_FIELD_OPERATION_ID,
        operation_id, (uint32_t)sizeof(operation_id));
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_RECOVERY_TOKEN, recovery_token,
        (uint32_t)sizeof(recovery_token));
    frame_size = test_build_frame(
        structured_frame, sizeof(structured_frame),
        (uint16_t)P6C_REQUEST_STATUS, structured_payload, payload_size);
    CHECK(frame_size != 0U);
    CHECK(p6c_decode_request(structured_frame, frame_size, &decoded) ==
          P6C_PARSE_OK);
    CHECK(decoded.field_count == 2U);
    CHECK(decoded.fields[0].field_id ==
          (uint16_t)P6C_FIELD_OPERATION_ID);
    CHECK(decoded.fields[1].field_id ==
          (uint16_t)P6C_FIELD_RECOVERY_TOKEN);

    p6c_store_u16_be(
        &structured_frame[P6C_HEADER_SIZE + P6C_FIELD_HEADER_SIZE +
                          P6C_OPERATION_ID_BYTES],
        (uint16_t)P6C_FIELD_OPERATION_ID);
    p6c_store_u32_be(&structured_frame[P6C_HEADER_PAYLOAD_CRC32_OFFSET],
                     p6c_crc32(&structured_frame[P6C_HEADER_SIZE],
                               payload_size));
    CHECK(p6c_decode_request(structured_frame, frame_size, &decoded) ==
          P6C_PARSE_DUPLICATE_FIELD);

    payload_size = test_store_field(
        structured_payload, UINT16_C(99), operation_id,
        (uint32_t)sizeof(operation_id));
    frame_size = test_build_frame(
        structured_frame, sizeof(structured_frame),
        (uint16_t)P6C_REQUEST_STATUS, structured_payload, payload_size);
    CHECK(p6c_decode_request(structured_frame, frame_size, &decoded) ==
          P6C_PARSE_UNKNOWN_FIELD);

    payload_size = test_store_field(
        structured_payload, (uint16_t)P6C_FIELD_OPERATION_ID,
        operation_id, (uint32_t)sizeof(operation_id));
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_EXECUTABLE, invalid_text,
        (uint32_t)sizeof(invalid_text));
    frame_size = test_build_frame(
        structured_frame, sizeof(structured_frame),
        (uint16_t)P6C_REQUEST_START, structured_payload, payload_size);
    CHECK(p6c_decode_request(structured_frame, frame_size, &decoded) ==
          P6C_PARSE_INVALID_UTF8);

    payload_size = test_store_field(
        structured_payload, (uint16_t)P6C_FIELD_OPERATION_ID,
        operation_id, (uint32_t)sizeof(operation_id));
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_EXECUTABLE, embedded_nul,
        (uint32_t)sizeof(embedded_nul));
    frame_size = test_build_frame(
        structured_frame, sizeof(structured_frame),
        (uint16_t)P6C_REQUEST_START, structured_payload, payload_size);
    CHECK(p6c_decode_request(structured_frame, frame_size, &decoded) ==
          P6C_PARSE_EMBEDDED_NUL);

    p6c_store_u32_be(list_value, P6C_MAX_ARGV_COUNT + UINT32_C(1));
    payload_size = test_store_field(
        structured_payload, (uint16_t)P6C_FIELD_OPERATION_ID,
        operation_id, (uint32_t)sizeof(operation_id));
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_OPERATION_DIGEST, operation_digest,
        (uint32_t)P6C_SHA256_BYTES);
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_EXECUTABLE, (const uint8_t *)"approved.elf",
        UINT32_C(12));
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_ARGV, list_value, UINT32_C(4));
    frame_size = test_build_frame(
        structured_frame, sizeof(structured_frame),
        (uint16_t)P6C_REQUEST_START, structured_payload, payload_size);
    CHECK(p6c_decode_request(structured_frame, frame_size, &decoded) ==
          P6C_PARSE_LIST_COUNT);

    p6c_store_u32_be(list_value, UINT32_C(2));
    p6c_store_u32_be(&list_value[4], UINT32_C(3));
    memcpy(&list_value[8], "A=1", 3U);
    p6c_store_u32_be(&list_value[11], UINT32_C(3));
    memcpy(&list_value[15], "A=2", 3U);
    payload_size = test_store_field(
        structured_payload, (uint16_t)P6C_FIELD_OPERATION_ID,
        operation_id, (uint32_t)sizeof(operation_id));
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_OPERATION_DIGEST, operation_digest,
        (uint32_t)P6C_SHA256_BYTES);
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_EXECUTABLE, (const uint8_t *)"approved.elf",
        UINT32_C(12));
    p6c_store_u32_be(&list_value[18], UINT32_C(1));
    p6c_store_u32_be(&list_value[22], UINT32_C(3));
    memcpy(&list_value[26], "elf", 3U);
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_ARGV, &list_value[18], UINT32_C(11));
    payload_size += test_store_field(
        &structured_payload[payload_size],
        (uint16_t)P6C_FIELD_ENVIRONMENT, list_value, UINT32_C(18));
    frame_size = test_build_frame(
        structured_frame, sizeof(structured_frame),
        (uint16_t)P6C_REQUEST_START, structured_payload, payload_size);
    CHECK(p6c_decode_request(structured_frame, frame_size, &decoded) ==
          P6C_PARSE_DUPLICATE_FIELD);

    memcpy(malformed_frame, complete_frame, sizeof(malformed_frame));
    malformed_frame[P6C_HEADER_MAGIC_OFFSET] ^= UINT8_C(1);
    CHECK(p6c_decode_request(malformed_frame, sizeof(malformed_frame),
                             &decoded) == P6C_PARSE_BAD_MAGIC);

    memcpy(malformed_frame, complete_frame, sizeof(malformed_frame));
    p6c_store_u16_be(&malformed_frame[P6C_HEADER_VERSION_OFFSET],
                     UINT16_C(2));
    CHECK(p6c_decode_request(malformed_frame, sizeof(malformed_frame),
                             &decoded) ==
          P6C_PARSE_UNSUPPORTED_VERSION);

    memcpy(malformed_frame, complete_frame, sizeof(malformed_frame));
    malformed_frame[P6C_HEADER_FLAGS_OFFSET + 3] = UINT8_C(1);
    CHECK(p6c_decode_request(malformed_frame, sizeof(malformed_frame),
                             &decoded) == P6C_PARSE_NONZERO_FLAGS);

    memcpy(malformed_frame, complete_frame, sizeof(malformed_frame));
    p6c_store_u32_be(&malformed_frame[P6C_HEADER_PAYLOAD_LENGTH_OFFSET],
                     P6C_MAX_PAYLOAD_BYTES + UINT32_C(1));
    CHECK(p6c_decode_request(malformed_frame, sizeof(malformed_frame),
                             &decoded) == P6C_PARSE_OVERSIZED);

    CHECK(p6c_decode_request(encoded_header, P6C_HEADER_SIZE - 1U,
                             &decoded) == P6C_PARSE_TRUNCATED);

    memcpy(malformed_frame, complete_frame, sizeof(malformed_frame));
    p6c_store_u16_be(
        &malformed_frame[P6C_HEADER_MESSAGE_TYPE_OFFSET],
        (uint16_t)(P6C_RESPONSE_BIT | (uint16_t)P6C_REQUEST_HELLO));
    CHECK(p6c_decode_request(malformed_frame, sizeof(malformed_frame),
                             &decoded) == P6C_PARSE_RESPONSE_TYPE);

    memcpy(malformed_frame, complete_frame, sizeof(malformed_frame));
    p6c_store_u16_be(&malformed_frame[P6C_HEADER_MESSAGE_TYPE_OFFSET],
                     UINT16_C(10));
    CHECK(p6c_decode_request(malformed_frame, sizeof(malformed_frame),
                             &decoded) == P6C_PARSE_REQUEST_TYPE);
    for (index = (size_t)P6C_REQUEST_START;
         index <= (size_t)P6C_REQUEST_ACK; ++index) {
        frame_size = test_build_frame(
            structured_frame, sizeof(structured_frame),
            (uint16_t)index, NULL, 0U);
        CHECK(frame_size == P6C_HEADER_SIZE);
        CHECK(p6c_decode_request(
                  structured_frame, frame_size, &decoded) ==
              P6C_PARSE_FIELD_BOUNDS);
    }
    frame_size = test_build_frame(
        structured_frame, sizeof(structured_frame),
        (uint16_t)P6C_REQUEST_RECOVER, PAYLOAD, sizeof(PAYLOAD));
    CHECK(frame_size != 0U);
    CHECK(p6c_decode_request(
              structured_frame, frame_size, &decoded) ==
          P6C_PARSE_UNKNOWN_FIELD);

    (void)printf("constant.magic=0x%08" PRIx32 "\n", P6C_PROTOCOL_MAGIC);
    (void)printf("constant.protocol_version=%" PRIu16 "\n",
                 P6C_PROTOCOL_VERSION);
    (void)printf("constant.request_id_bytes=%zu\n", P6C_REQUEST_ID_BYTES);
    (void)printf("constant.operation_id_bytes=%zu\n",
                 P6C_OPERATION_ID_BYTES);
    (void)printf("constant.sha256_bytes=%zu\n", P6C_SHA256_BYTES);
    (void)printf("constant.v1_flags=%" PRIu32 "\n", P6C_V1_FLAGS);
    (void)printf("constant.response_bit=0x%04" PRIx16 "\n",
                 P6C_RESPONSE_BIT);
    (void)printf("constant.error_message_type=0x%04" PRIx16 "\n",
                 P6C_ERROR_MESSAGE_TYPE);
    (void)printf("offset.magic=%zu\n", P6C_HEADER_MAGIC_OFFSET);
    (void)printf("offset.version=%zu\n", P6C_HEADER_VERSION_OFFSET);
    (void)printf("offset.message_type=%zu\n",
                 P6C_HEADER_MESSAGE_TYPE_OFFSET);
    (void)printf("offset.flags=%zu\n", P6C_HEADER_FLAGS_OFFSET);
    (void)printf("offset.request_id=%zu\n", P6C_HEADER_REQUEST_ID_OFFSET);
    (void)printf("offset.payload_length=%zu\n",
                 P6C_HEADER_PAYLOAD_LENGTH_OFFSET);
    (void)printf("offset.payload_crc32=%zu\n",
                 P6C_HEADER_PAYLOAD_CRC32_OFFSET);
    (void)printf("header_size=%zu\n", P6C_HEADER_SIZE);
    (void)printf("limit.max_payload_bytes=%" PRIu32 "\n",
                 P6C_MAX_PAYLOAD_BYTES);
    (void)printf("limit.max_frame_bytes=%" PRIu32 "\n",
                 P6C_MAX_FRAME_BYTES);
    (void)printf("limit.max_argv_count=%" PRIu32 "\n",
                 P6C_MAX_ARGV_COUNT);
    (void)printf("limit.max_environment_count=%" PRIu32 "\n",
                 P6C_MAX_ENVIRONMENT_COUNT);
    (void)printf("limit.max_string_bytes=%" PRIu32 "\n",
                 P6C_MAX_STRING_BYTES);
    (void)printf("limit.max_public_code_bytes=%" PRIu32 "\n",
                 P6C_MAX_PUBLIC_CODE_BYTES);
    print_enum_evidence(REQUEST_EVIDENCE,
                        sizeof(REQUEST_EVIDENCE) /
                            sizeof(REQUEST_EVIDENCE[0]));
    print_enum_evidence(OPERATION_EVIDENCE,
                        sizeof(OPERATION_EVIDENCE) /
                            sizeof(OPERATION_EVIDENCE[0]));
    print_enum_evidence(STATUS_EVIDENCE,
                        sizeof(STATUS_EVIDENCE) /
                            sizeof(STATUS_EVIDENCE[0]));
    print_canonical_header(encoded_header);
    (void)puts("malformed.bad_magic=rejected");
    (void)puts("malformed.unsupported_version=rejected");
    (void)puts("malformed.nonzero_flags=rejected");
    (void)puts("malformed.oversize_payload=rejected");
    (void)puts("malformed.truncated_header=rejected");
    (void)puts("malformed.response_request=rejected");
    (void)puts("malformed.unknown_request=rejected");
    (void)puts("test_protocol=PASS");

    return EXIT_SUCCESS;
}
