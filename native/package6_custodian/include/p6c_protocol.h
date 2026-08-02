#ifndef P6C_PROTOCOL_H
#define P6C_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define P6C_PROTOCOL_MAGIC UINT32_C(0x50364341)
#define P6C_PROTOCOL_VERSION UINT16_C(1)
#define P6C_REQUEST_ID_BYTES ((size_t)16)
#define P6C_OPERATION_ID_BYTES ((size_t)16)
#define P6C_SHA256_BYTES ((size_t)32)
#define P6C_V1_FLAGS UINT32_C(0)
#define P6C_RESPONSE_BIT UINT16_C(0x8000)
#define P6C_ERROR_MESSAGE_TYPE UINT16_C(0xffff)

#define P6C_HEADER_MAGIC_OFFSET ((size_t)0)
#define P6C_HEADER_VERSION_OFFSET ((size_t)4)
#define P6C_HEADER_MESSAGE_TYPE_OFFSET ((size_t)6)
#define P6C_HEADER_FLAGS_OFFSET ((size_t)8)
#define P6C_HEADER_REQUEST_ID_OFFSET ((size_t)12)
#define P6C_HEADER_PAYLOAD_LENGTH_OFFSET ((size_t)28)
#define P6C_HEADER_PAYLOAD_CRC32_OFFSET ((size_t)32)
#define P6C_HEADER_SIZE ((size_t)36)

#define P6C_MAX_PAYLOAD_BYTES UINT32_C(1048576)
#define P6C_MAX_FRAME_BYTES UINT32_C(1048612)
#define P6C_MAX_ARGV_COUNT UINT32_C(128)
#define P6C_MAX_ENVIRONMENT_COUNT UINT32_C(128)
#define P6C_MAX_STRING_BYTES UINT32_C(4096)
#define P6C_MAX_PUBLIC_CODE_BYTES UINT32_C(64)
#define P6C_MAX_CREDENTIAL_MANIFEST_BYTES UINT32_C(32768)
#define P6C_MAX_FIELDS ((size_t)16)
#define P6C_FIELD_HEADER_SIZE ((size_t)8)
#define P6C_OPERATION_SUMMARY_BYTES ((size_t)136)
#define P6C_MAX_OPERATIONS ((size_t)16)

#define P6C_SUMMARY_OPERATION_ID_OFFSET ((size_t)0)
#define P6C_SUMMARY_RECOVERY_TOKEN_OFFSET ((size_t)16)
#define P6C_SUMMARY_STATE_OFFSET ((size_t)32)
#define P6C_SUMMARY_RESUME_STATE_OFFSET ((size_t)33)
#define P6C_SUMMARY_FLAGS_OFFSET ((size_t)34)
#define P6C_SUMMARY_EXIT_STATUS_OFFSET ((size_t)36)
#define P6C_SUMMARY_REQUEST_DIGEST_OFFSET ((size_t)40)
#define P6C_SUMMARY_EXECUTABLE_DIGEST_OFFSET ((size_t)72)
#define P6C_SUMMARY_PUBLICATION_DIGEST_OFFSET ((size_t)104)

#define P6C_SUMMARY_FLAG_AUTHORITY_RETAINED UINT16_C(0x0001)
#define P6C_SUMMARY_FLAG_BUNDLE_COMMITTED UINT16_C(0x0002)
#define P6C_SUMMARY_FLAG_STDOUT_TRUNCATED UINT16_C(0x0004)
#define P6C_SUMMARY_FLAG_STDERR_TRUNCATED UINT16_C(0x0008)
#define P6C_SUMMARY_FLAG_ACKNOWLEDGED UINT16_C(0x0010)

#define P6C_TRANSCRIPT_METADATA_BYTES ((size_t)80)
#define P6C_TRANSCRIPT_OPERATION_ID_OFFSET ((size_t)0)
#define P6C_TRANSCRIPT_STREAM_OFFSET ((size_t)16)
#define P6C_TRANSCRIPT_FLAGS_OFFSET ((size_t)17)
#define P6C_TRANSCRIPT_OFFSET_OFFSET ((size_t)20)
#define P6C_TRANSCRIPT_COUNT_OFFSET ((size_t)28)
#define P6C_TRANSCRIPT_OBSERVED_SIZE_OFFSET ((size_t)32)
#define P6C_TRANSCRIPT_RETAINED_SIZE_OFFSET ((size_t)40)
#define P6C_TRANSCRIPT_DIGEST_OFFSET ((size_t)48)
#define P6C_TRANSCRIPT_FLAG_EOF UINT8_C(0x01)
#define P6C_TRANSCRIPT_FLAG_TRUNCATED UINT8_C(0x02)

enum p6c_request_message_type {
    P6C_REQUEST_HELLO = 1,
    P6C_REQUEST_START = 2,
    P6C_REQUEST_STATUS = 3,
    P6C_REQUEST_STOP = 4,
    P6C_REQUEST_RUN_ONCE = 5,
    P6C_REQUEST_READ_TRANSCRIPT = 6,
    P6C_REQUEST_PUBLISH_BUNDLE = 7,
    P6C_REQUEST_ACK = 8,
    P6C_REQUEST_RECOVER = 9
};

enum p6c_operation_state {
    P6C_OPERATION_ABSENT = 0,
    P6C_OPERATION_RESERVED = 1,
    P6C_OPERATION_EXECUTABLE_PINNED = 2,
    P6C_OPERATION_CGROUP_CREATED = 3,
    P6C_OPERATION_CHILD_CLONED = 4,
    P6C_OPERATION_EXEC_CONFIRMED = 5,
    P6C_OPERATION_RUNNING = 6,
    P6C_OPERATION_STOP_REQUESTED = 7,
    P6C_OPERATION_CGROUP_KILLED = 8,
    P6C_OPERATION_CGROUP_EMPTY = 9,
    P6C_OPERATION_CHILD_EXIT_OBSERVED = 10,
    P6C_OPERATION_CHILD_REAPED = 11,
    P6C_OPERATION_TRANSCRIPTS_FINAL = 12,
    P6C_OPERATION_RESULT_RETAINED = 13,
    P6C_OPERATION_ACKNOWLEDGED = 14,
    P6C_OPERATION_RECOVERY_REQUIRED = 15
};

enum p6c_public_status {
    P6C_STATUS_OK = 0,
    P6C_STATUS_INVALID_FRAME = 1,
    P6C_STATUS_UNSUPPORTED_VERSION = 2,
    P6C_STATUS_UNAUTHORIZED = 3,
    P6C_STATUS_INVALID_REQUEST = 4,
    P6C_STATUS_NOT_FOUND = 5,
    P6C_STATUS_CONFLICT = 6,
    P6C_STATUS_LIMIT_EXCEEDED = 7,
    P6C_STATUS_TIMEOUT = 8,
    P6C_STATUS_RECOVERY_REQUIRED = 9,
    P6C_STATUS_INTERNAL = 10
};

enum p6c_parse_result {
    P6C_PARSE_OK = 0,
    P6C_PARSE_TRUNCATED = 1,
    P6C_PARSE_OVERSIZED = 2,
    P6C_PARSE_BAD_MAGIC = 3,
    P6C_PARSE_UNSUPPORTED_VERSION = 4,
    P6C_PARSE_NONZERO_FLAGS = 5,
    P6C_PARSE_REQUEST_TYPE = 6,
    P6C_PARSE_RESPONSE_TYPE = 7,
    P6C_PARSE_PAYLOAD_LENGTH = 8,
    P6C_PARSE_BAD_CRC = 9,
    P6C_PARSE_REQUEST_ID = 10,
    P6C_PARSE_UNKNOWN_FIELD = 11,
    P6C_PARSE_DUPLICATE_FIELD = 12,
    P6C_PARSE_FIELD_BOUNDS = 13,
    P6C_PARSE_INVALID_UTF8 = 14,
    P6C_PARSE_EMBEDDED_NUL = 15,
    P6C_PARSE_LIST_COUNT = 16,
    P6C_PARSE_TRAILING_BYTES = 17
};

enum p6c_field_id {
    P6C_FIELD_OPERATION_ID = 1,
    P6C_FIELD_OPERATION_DIGEST = 2,
    P6C_FIELD_RECOVERY_TOKEN = 3,
    P6C_FIELD_EXECUTABLE = 4,
    P6C_FIELD_ARGV = 5,
    P6C_FIELD_ENVIRONMENT = 6,
    P6C_FIELD_STREAM = 7,
    P6C_FIELD_OFFSET = 8,
    P6C_FIELD_LENGTH = 9,
    P6C_FIELD_PUBLICATION_ID = 10,
    P6C_FIELD_CREDENTIAL_MANIFEST = 11
};

struct p6c_field_view {
    uint16_t field_id;
    const uint8_t *value;
    uint32_t value_length;
};

struct p6c_frame_view {
    uint16_t message_type;
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    const uint8_t *payload;
    uint32_t payload_length;
    struct p6c_field_view fields[P6C_MAX_FIELDS];
    size_t field_count;
};

uint32_t p6c_crc32(const uint8_t *data, size_t size);

enum p6c_parse_result p6c_decode_request(
    const uint8_t *packet,
    size_t packet_size,
    struct p6c_frame_view *frame);

_Static_assert(sizeof(uint8_t) == 1, "protocol requires 8-bit bytes");
_Static_assert(sizeof(uint16_t) == 2, "protocol requires 16-bit integers");
_Static_assert(sizeof(uint32_t) == 4, "protocol requires 32-bit integers");
_Static_assert(P6C_PROTOCOL_MAGIC == UINT32_C(0x50364341),
               "protocol magic drift");
_Static_assert(P6C_PROTOCOL_VERSION == UINT16_C(1),
               "protocol version drift");
_Static_assert(P6C_REQUEST_ID_BYTES == 16, "request ID width drift");
_Static_assert(P6C_OPERATION_ID_BYTES == 16, "operation ID width drift");
_Static_assert(P6C_SHA256_BYTES == 32, "SHA-256 width drift");
_Static_assert(P6C_V1_FLAGS == UINT32_C(0), "v1 flags must be zero");
_Static_assert(P6C_RESPONSE_BIT == UINT16_C(0x8000),
               "response bit drift");
_Static_assert(P6C_ERROR_MESSAGE_TYPE == UINT16_C(0xffff),
               "error message type drift");

_Static_assert(P6C_HEADER_MAGIC_OFFSET == 0, "magic offset drift");
_Static_assert(P6C_HEADER_VERSION_OFFSET == 4, "version offset drift");
_Static_assert(P6C_HEADER_MESSAGE_TYPE_OFFSET == 6,
               "message type offset drift");
_Static_assert(P6C_HEADER_FLAGS_OFFSET == 8, "flags offset drift");
_Static_assert(P6C_HEADER_REQUEST_ID_OFFSET == 12,
               "request ID offset drift");
_Static_assert(P6C_HEADER_PAYLOAD_LENGTH_OFFSET == 28,
               "payload length offset drift");
_Static_assert(P6C_HEADER_PAYLOAD_CRC32_OFFSET == 32,
               "payload CRC32 offset drift");
_Static_assert(P6C_HEADER_SIZE == 36, "wire header size drift");
_Static_assert(P6C_HEADER_VERSION_OFFSET ==
                   P6C_HEADER_MAGIC_OFFSET + sizeof(uint32_t),
               "version must follow magic");
_Static_assert(P6C_HEADER_MESSAGE_TYPE_OFFSET ==
                   P6C_HEADER_VERSION_OFFSET + sizeof(uint16_t),
               "message type must follow version");
_Static_assert(P6C_HEADER_FLAGS_OFFSET ==
                   P6C_HEADER_MESSAGE_TYPE_OFFSET + sizeof(uint16_t),
               "flags must follow message type");
_Static_assert(P6C_HEADER_REQUEST_ID_OFFSET ==
                   P6C_HEADER_FLAGS_OFFSET + sizeof(uint32_t),
               "request ID must follow flags");
_Static_assert(P6C_HEADER_PAYLOAD_LENGTH_OFFSET ==
                   P6C_HEADER_REQUEST_ID_OFFSET + P6C_REQUEST_ID_BYTES,
               "payload length must follow request ID");
_Static_assert(P6C_HEADER_PAYLOAD_CRC32_OFFSET ==
                   P6C_HEADER_PAYLOAD_LENGTH_OFFSET + sizeof(uint32_t),
               "payload CRC32 must follow payload length");
_Static_assert(P6C_HEADER_SIZE ==
                   P6C_HEADER_PAYLOAD_CRC32_OFFSET + sizeof(uint32_t),
               "header size must end after payload CRC32");

_Static_assert(P6C_REQUEST_HELLO == 1, "HELLO value drift");
_Static_assert(P6C_REQUEST_START == 2, "START value drift");
_Static_assert(P6C_REQUEST_STATUS == 3, "STATUS value drift");
_Static_assert(P6C_REQUEST_STOP == 4, "STOP value drift");
_Static_assert(P6C_REQUEST_RUN_ONCE == 5, "RUN_ONCE value drift");
_Static_assert(P6C_REQUEST_READ_TRANSCRIPT == 6,
               "READ_TRANSCRIPT value drift");
_Static_assert(P6C_REQUEST_PUBLISH_BUNDLE == 7,
               "PUBLISH_BUNDLE value drift");
_Static_assert(P6C_REQUEST_ACK == 8, "ACK value drift");
_Static_assert(P6C_REQUEST_RECOVER == 9, "RECOVER value drift");

_Static_assert(P6C_OPERATION_ABSENT == 0, "ABSENT value drift");
_Static_assert(P6C_OPERATION_RESERVED == 1, "RESERVED value drift");
_Static_assert(P6C_OPERATION_EXECUTABLE_PINNED == 2,
               "EXECUTABLE_PINNED value drift");
_Static_assert(P6C_OPERATION_CGROUP_CREATED == 3,
               "CGROUP_CREATED value drift");
_Static_assert(P6C_OPERATION_CHILD_CLONED == 4,
               "CHILD_CLONED value drift");
_Static_assert(P6C_OPERATION_EXEC_CONFIRMED == 5,
               "EXEC_CONFIRMED value drift");
_Static_assert(P6C_OPERATION_RUNNING == 6, "RUNNING value drift");
_Static_assert(P6C_OPERATION_STOP_REQUESTED == 7,
               "STOP_REQUESTED value drift");
_Static_assert(P6C_OPERATION_CGROUP_KILLED == 8,
               "CGROUP_KILLED value drift");
_Static_assert(P6C_OPERATION_CGROUP_EMPTY == 9,
               "CGROUP_EMPTY value drift");
_Static_assert(P6C_OPERATION_CHILD_EXIT_OBSERVED == 10,
               "CHILD_EXIT_OBSERVED value drift");
_Static_assert(P6C_OPERATION_CHILD_REAPED == 11,
               "CHILD_REAPED value drift");
_Static_assert(P6C_OPERATION_TRANSCRIPTS_FINAL == 12,
               "TRANSCRIPTS_FINAL value drift");
_Static_assert(P6C_OPERATION_RESULT_RETAINED == 13,
               "RESULT_RETAINED value drift");
_Static_assert(P6C_OPERATION_ACKNOWLEDGED == 14,
               "ACKNOWLEDGED value drift");
_Static_assert(P6C_OPERATION_RECOVERY_REQUIRED == 15,
               "RECOVERY_REQUIRED operation value drift");

_Static_assert(P6C_STATUS_OK == 0, "OK value drift");
_Static_assert(P6C_STATUS_INVALID_FRAME == 1, "INVALID_FRAME value drift");
_Static_assert(P6C_STATUS_UNSUPPORTED_VERSION == 2,
               "UNSUPPORTED_VERSION value drift");
_Static_assert(P6C_STATUS_UNAUTHORIZED == 3, "UNAUTHORIZED value drift");
_Static_assert(P6C_STATUS_INVALID_REQUEST == 4,
               "INVALID_REQUEST value drift");
_Static_assert(P6C_STATUS_NOT_FOUND == 5, "NOT_FOUND value drift");
_Static_assert(P6C_STATUS_CONFLICT == 6, "CONFLICT value drift");
_Static_assert(P6C_STATUS_LIMIT_EXCEEDED == 7,
               "LIMIT_EXCEEDED value drift");
_Static_assert(P6C_STATUS_TIMEOUT == 8, "TIMEOUT value drift");
_Static_assert(P6C_STATUS_RECOVERY_REQUIRED == 9,
               "RECOVERY_REQUIRED status value drift");
_Static_assert(P6C_STATUS_INTERNAL == 10, "INTERNAL value drift");

_Static_assert(P6C_MAX_PAYLOAD_BYTES == UINT32_C(1048576),
               "payload limit drift");
_Static_assert(P6C_MAX_FRAME_BYTES == UINT32_C(1048612),
               "frame limit drift");
_Static_assert(P6C_MAX_FRAME_BYTES ==
                   P6C_MAX_PAYLOAD_BYTES + P6C_HEADER_SIZE,
               "frame limit must include one header and maximum payload");
_Static_assert(P6C_MAX_ARGV_COUNT == UINT32_C(128),
               "argv count limit drift");
_Static_assert(P6C_MAX_ENVIRONMENT_COUNT == UINT32_C(128),
               "environment count limit drift");
_Static_assert(P6C_MAX_STRING_BYTES == UINT32_C(4096),
               "string limit drift");
_Static_assert(P6C_MAX_PUBLIC_CODE_BYTES == UINT32_C(64),
               "public code limit drift");
_Static_assert(P6C_MAX_CREDENTIAL_MANIFEST_BYTES == UINT32_C(32768),
               "credential manifest limit drift");
_Static_assert(P6C_OPERATION_SUMMARY_BYTES == 136,
               "operation summary width drift");
_Static_assert(P6C_MAX_OPERATIONS == 16, "operation registry limit drift");
_Static_assert(P6C_SUMMARY_PUBLICATION_DIGEST_OFFSET + P6C_SHA256_BYTES ==
                   P6C_OPERATION_SUMMARY_BYTES,
               "operation summary layout drift");
_Static_assert(P6C_TRANSCRIPT_DIGEST_OFFSET + P6C_SHA256_BYTES ==
                   P6C_TRANSCRIPT_METADATA_BYTES,
               "transcript metadata layout drift");

static inline void p6c_store_u16_be(uint8_t output[static 2], uint16_t value)
{
    output[0] = (uint8_t)(value >> 8);
    output[1] = (uint8_t)value;
}

static inline void p6c_store_u32_be(uint8_t output[static 4], uint32_t value)
{
    output[0] = (uint8_t)(value >> 24);
    output[1] = (uint8_t)(value >> 16);
    output[2] = (uint8_t)(value >> 8);
    output[3] = (uint8_t)value;
}

static inline void p6c_encode_header_v1(
    uint8_t output[static P6C_HEADER_SIZE],
    uint16_t message_type,
    const uint8_t request_id[static P6C_REQUEST_ID_BYTES],
    uint32_t payload_length,
    uint32_t payload_crc32)
{
    size_t index;

    p6c_store_u32_be(&output[P6C_HEADER_MAGIC_OFFSET], P6C_PROTOCOL_MAGIC);
    p6c_store_u16_be(&output[P6C_HEADER_VERSION_OFFSET],
                     P6C_PROTOCOL_VERSION);
    p6c_store_u16_be(&output[P6C_HEADER_MESSAGE_TYPE_OFFSET], message_type);
    p6c_store_u32_be(&output[P6C_HEADER_FLAGS_OFFSET], P6C_V1_FLAGS);
    for (index = 0; index < P6C_REQUEST_ID_BYTES; ++index) {
        output[P6C_HEADER_REQUEST_ID_OFFSET + index] = request_id[index];
    }
    p6c_store_u32_be(&output[P6C_HEADER_PAYLOAD_LENGTH_OFFSET],
                     payload_length);
    p6c_store_u32_be(&output[P6C_HEADER_PAYLOAD_CRC32_OFFSET],
                     payload_crc32);
}

#endif
