#include "p6c_protocol.h"

#include <string.h>


#define P6C_FIELD_MASK(field_id) \
    (UINT32_C(1) << (unsigned int)(field_id))

static uint16_t p6c_load_u16_be(const uint8_t input[static 2])
{
    return (uint16_t)(((uint16_t)input[0] << 8) | (uint16_t)input[1]);
}

static uint32_t p6c_load_u32_be(const uint8_t input[static 4])
{
    return ((uint32_t)input[0] << 24) | ((uint32_t)input[1] << 16) |
           ((uint32_t)input[2] << 8) | (uint32_t)input[3];
}

static int p6c_bytes_nonzero(const uint8_t *data, size_t size)
{
    size_t index;

    for (index = 0; index < size; ++index) {
        if (data[index] != UINT8_C(0)) {
            return 1;
        }
    }
    return 0;
}

static enum p6c_parse_result p6c_validate_text(const uint8_t *text,
                                                size_t size)
{
    size_t index = 0U;

    if ((size == 0U) || (size > (size_t)P6C_MAX_STRING_BYTES)) {
        return P6C_PARSE_FIELD_BOUNDS;
    }
    while (index < size) {
        uint8_t first = text[index];
        size_t remaining = size - index;
        size_t width;
        uint32_t codepoint;

        if (first == UINT8_C(0)) {
            return P6C_PARSE_EMBEDDED_NUL;
        }
        if (first <= UINT8_C(0x7f)) {
            ++index;
            continue;
        }
        if ((first >= UINT8_C(0xc2)) && (first <= UINT8_C(0xdf))) {
            width = 2U;
            codepoint = (uint32_t)(first & UINT8_C(0x1f));
        } else if ((first >= UINT8_C(0xe0)) &&
                   (first <= UINT8_C(0xef))) {
            width = 3U;
            codepoint = (uint32_t)(first & UINT8_C(0x0f));
        } else if ((first >= UINT8_C(0xf0)) &&
                   (first <= UINT8_C(0xf4))) {
            width = 4U;
            codepoint = (uint32_t)(first & UINT8_C(0x07));
        } else {
            return P6C_PARSE_INVALID_UTF8;
        }
        if (remaining < width) {
            return P6C_PARSE_INVALID_UTF8;
        }
        {
            size_t continuation_index;

            for (continuation_index = 1U;
                 continuation_index < width;
                 ++continuation_index) {
                uint8_t continuation = text[index + continuation_index];

                if ((continuation & UINT8_C(0xc0)) != UINT8_C(0x80)) {
                    return P6C_PARSE_INVALID_UTF8;
                }
                codepoint = (codepoint << 6) |
                            (uint32_t)(continuation & UINT8_C(0x3f));
            }
        }
        if (((width == 2U) && (codepoint < UINT32_C(0x80))) ||
            ((width == 3U) && (codepoint < UINT32_C(0x800))) ||
            ((width == 4U) && (codepoint < UINT32_C(0x10000))) ||
            ((codepoint >= UINT32_C(0xd800)) &&
             (codepoint <= UINT32_C(0xdfff))) ||
            (codepoint > UINT32_C(0x10ffff))) {
            return P6C_PARSE_INVALID_UTF8;
        }
        index += width;
    }
    return P6C_PARSE_OK;
}

static enum p6c_parse_result p6c_validate_string_list(
    const uint8_t *value, size_t value_length, uint32_t maximum_count,
    int require_one, int environment)
{
    uint32_t count;
    uint32_t item_index;
    size_t offset = sizeof(uint32_t);
    size_t key_offsets[P6C_MAX_ENVIRONMENT_COUNT];
    size_t key_lengths[P6C_MAX_ENVIRONMENT_COUNT];

    if (value_length < sizeof(uint32_t)) {
        return P6C_PARSE_FIELD_BOUNDS;
    }
    count = p6c_load_u32_be(value);
    if ((count > maximum_count) || ((require_one != 0) && (count == 0U))) {
        return P6C_PARSE_LIST_COUNT;
    }
    for (item_index = 0U; item_index < count; ++item_index) {
        uint32_t item_length;
        enum p6c_parse_result result;

        if ((value_length - offset) < sizeof(uint32_t)) {
            return P6C_PARSE_FIELD_BOUNDS;
        }
        item_length = p6c_load_u32_be(&value[offset]);
        offset += sizeof(uint32_t);
        if (((size_t)item_length > value_length - offset) ||
            (item_length > P6C_MAX_STRING_BYTES)) {
            return P6C_PARSE_FIELD_BOUNDS;
        }
        result = p6c_validate_text(&value[offset], (size_t)item_length);
        if (result != P6C_PARSE_OK) {
            return result;
        }
        if (environment != 0) {
            size_t key_length = 0U;
            uint32_t prior_index;

            while ((key_length < (size_t)item_length) &&
                   (value[offset + key_length] != UINT8_C('='))) {
                ++key_length;
            }
            if ((key_length == 0U) ||
                (key_length == (size_t)item_length)) {
                return P6C_PARSE_FIELD_BOUNDS;
            }
            for (prior_index = 0U;
                 prior_index < item_index;
                 ++prior_index) {
                if ((key_lengths[prior_index] == key_length) &&
                    (memcmp(&value[key_offsets[prior_index]],
                            &value[offset], key_length) == 0)) {
                    return P6C_PARSE_DUPLICATE_FIELD;
                }
            }
            key_offsets[item_index] = offset;
            key_lengths[item_index] = key_length;
        }
        offset += (size_t)item_length;
    }
    if (offset != value_length) {
        return P6C_PARSE_TRAILING_BYTES;
    }
    return P6C_PARSE_OK;
}

static uint32_t p6c_allowed_fields(uint16_t message_type)
{
    uint32_t operation_and_token =
        P6C_FIELD_MASK(P6C_FIELD_OPERATION_ID) |
        P6C_FIELD_MASK(P6C_FIELD_RECOVERY_TOKEN);

    switch (message_type) {
    case P6C_REQUEST_START:
    case P6C_REQUEST_RUN_ONCE:
        return P6C_FIELD_MASK(P6C_FIELD_OPERATION_ID) |
               P6C_FIELD_MASK(P6C_FIELD_OPERATION_DIGEST) |
               P6C_FIELD_MASK(P6C_FIELD_EXECUTABLE) |
               P6C_FIELD_MASK(P6C_FIELD_ARGV) |
               P6C_FIELD_MASK(P6C_FIELD_ENVIRONMENT) |
               P6C_FIELD_MASK(P6C_FIELD_CREDENTIAL_MANIFEST);
    case P6C_REQUEST_STATUS:
    case P6C_REQUEST_STOP:
        return operation_and_token;
    case P6C_REQUEST_PUBLISH_BUNDLE:
    case P6C_REQUEST_ACK:
        return operation_and_token |
               P6C_FIELD_MASK(P6C_FIELD_PUBLICATION_ID);
    case P6C_REQUEST_READ_TRANSCRIPT:
        return operation_and_token |
               P6C_FIELD_MASK(P6C_FIELD_STREAM) |
               P6C_FIELD_MASK(P6C_FIELD_OFFSET) |
               P6C_FIELD_MASK(P6C_FIELD_LENGTH);
    default:
        return UINT32_C(0);
    }
}

static uint32_t p6c_required_fields(uint16_t message_type)
{
    uint32_t operation_and_token =
        P6C_FIELD_MASK(P6C_FIELD_OPERATION_ID) |
        P6C_FIELD_MASK(P6C_FIELD_RECOVERY_TOKEN);

    switch (message_type) {
    case P6C_REQUEST_START:
    case P6C_REQUEST_RUN_ONCE:
        return P6C_FIELD_MASK(P6C_FIELD_OPERATION_ID) |
               P6C_FIELD_MASK(P6C_FIELD_OPERATION_DIGEST) |
               P6C_FIELD_MASK(P6C_FIELD_EXECUTABLE) |
               P6C_FIELD_MASK(P6C_FIELD_ARGV) |
               P6C_FIELD_MASK(P6C_FIELD_ENVIRONMENT);
    case P6C_REQUEST_STATUS:
    case P6C_REQUEST_STOP:
        return operation_and_token;
    case P6C_REQUEST_PUBLISH_BUNDLE:
    case P6C_REQUEST_ACK:
        return operation_and_token |
               P6C_FIELD_MASK(P6C_FIELD_PUBLICATION_ID);
    case P6C_REQUEST_READ_TRANSCRIPT:
        return operation_and_token |
               P6C_FIELD_MASK(P6C_FIELD_STREAM) |
               P6C_FIELD_MASK(P6C_FIELD_OFFSET) |
               P6C_FIELD_MASK(P6C_FIELD_LENGTH);
    default:
        return UINT32_C(0);
    }
}

static enum p6c_parse_result p6c_validate_field(
    uint16_t field_id, const uint8_t *value, uint32_t value_length)
{
    switch (field_id) {
    case P6C_FIELD_OPERATION_ID:
    case P6C_FIELD_RECOVERY_TOKEN:
        if ((value_length != (uint32_t)P6C_OPERATION_ID_BYTES) ||
            (p6c_bytes_nonzero(value, (size_t)value_length) == 0)) {
            return P6C_PARSE_FIELD_BOUNDS;
        }
        return P6C_PARSE_OK;
    case P6C_FIELD_OPERATION_DIGEST:
        if (value_length != (uint32_t)P6C_SHA256_BYTES) {
            return P6C_PARSE_FIELD_BOUNDS;
        }
        return P6C_PARSE_OK;
    case P6C_FIELD_EXECUTABLE:
        return p6c_validate_text(value, (size_t)value_length);
    case P6C_FIELD_ARGV:
        return p6c_validate_string_list(
            value, (size_t)value_length, P6C_MAX_ARGV_COUNT, 1, 0);
    case P6C_FIELD_ENVIRONMENT:
        return p6c_validate_string_list(
            value, (size_t)value_length, P6C_MAX_ENVIRONMENT_COUNT, 0, 1);
    case P6C_FIELD_STREAM:
        if ((value_length != 1U) ||
            ((value[0] != UINT8_C(1)) && (value[0] != UINT8_C(2)))) {
            return P6C_PARSE_FIELD_BOUNDS;
        }
        return P6C_PARSE_OK;
    case P6C_FIELD_OFFSET:
        return (value_length == 8U) ? P6C_PARSE_OK :
                                     P6C_PARSE_FIELD_BOUNDS;
    case P6C_FIELD_LENGTH:
        if ((value_length != 4U) ||
            (p6c_load_u32_be(value) == 0U) ||
            (p6c_load_u32_be(value) > P6C_MAX_PAYLOAD_BYTES)) {
            return P6C_PARSE_FIELD_BOUNDS;
        }
        return P6C_PARSE_OK;
    case P6C_FIELD_PUBLICATION_ID:
        return (value_length == (uint32_t)P6C_SHA256_BYTES) ?
                   P6C_PARSE_OK :
                   P6C_PARSE_FIELD_BOUNDS;
    case P6C_FIELD_CREDENTIAL_MANIFEST:
        return ((value_length >= UINT32_C(37)) &&
                (value_length <= P6C_MAX_CREDENTIAL_MANIFEST_BYTES)) ?
                   P6C_PARSE_OK :
                   P6C_PARSE_FIELD_BOUNDS;
    default:
        return P6C_PARSE_UNKNOWN_FIELD;
    }
}

static enum p6c_parse_result p6c_parse_fields(
    uint16_t message_type, const uint8_t *payload, size_t payload_length,
    struct p6c_frame_view *frame)
{
    uint32_t allowed = p6c_allowed_fields(message_type);
    uint32_t required = p6c_required_fields(message_type);
    uint32_t seen = UINT32_C(0);
    uint16_t previous = UINT16_C(0);
    size_t offset = 0U;

    if ((message_type == (uint16_t)P6C_REQUEST_HELLO) ||
        (message_type == (uint16_t)P6C_REQUEST_RECOVER)) {
        if (message_type == (uint16_t)P6C_REQUEST_RECOVER) {
            return (payload_length == 0U) ? P6C_PARSE_OK :
                                            P6C_PARSE_UNKNOWN_FIELD;
        }
        if (payload_length == 0U) {
            return P6C_PARSE_OK;
        }
        return p6c_validate_text(payload, payload_length);
    }

    while (offset < payload_length) {
        uint16_t field_id;
        uint16_t reserved;
        uint32_t value_length;
        enum p6c_parse_result result;

        if (frame->field_count >= P6C_MAX_FIELDS) {
            return P6C_PARSE_LIST_COUNT;
        }
        if ((payload_length - offset) < P6C_FIELD_HEADER_SIZE) {
            return P6C_PARSE_TRAILING_BYTES;
        }
        field_id = p6c_load_u16_be(&payload[offset]);
        reserved = p6c_load_u16_be(&payload[offset + 2U]);
        value_length = p6c_load_u32_be(&payload[offset + 4U]);
        offset += P6C_FIELD_HEADER_SIZE;
        if (reserved != UINT16_C(0)) {
            return P6C_PARSE_FIELD_BOUNDS;
        }
        if ((field_id == UINT16_C(0)) ||
            (field_id > (uint16_t)P6C_FIELD_CREDENTIAL_MANIFEST) ||
            ((allowed & P6C_FIELD_MASK(field_id)) == 0U)) {
            return P6C_PARSE_UNKNOWN_FIELD;
        }
        if ((seen & P6C_FIELD_MASK(field_id)) != 0U) {
            return P6C_PARSE_DUPLICATE_FIELD;
        }
        if (field_id < previous) {
            return P6C_PARSE_TRAILING_BYTES;
        }
        if ((size_t)value_length > payload_length - offset) {
            return P6C_PARSE_FIELD_BOUNDS;
        }
        result = p6c_validate_field(field_id, &payload[offset],
                                    value_length);
        if (result != P6C_PARSE_OK) {
            return result;
        }
        frame->fields[frame->field_count].field_id = field_id;
        frame->fields[frame->field_count].value = &payload[offset];
        frame->fields[frame->field_count].value_length = value_length;
        ++frame->field_count;
        seen |= P6C_FIELD_MASK(field_id);
        previous = field_id;
        offset += (size_t)value_length;
    }
    if ((seen & required) != required) {
        return P6C_PARSE_FIELD_BOUNDS;
    }
    return P6C_PARSE_OK;
}

uint32_t p6c_crc32(const uint8_t *data, size_t size)
{
    uint32_t crc = UINT32_MAX;
    size_t byte_index;

    if ((data == NULL) && (size != 0U)) {
        return UINT32_C(0);
    }
    for (byte_index = 0; byte_index < size; ++byte_index) {
        unsigned int bit_index;

        crc ^= (uint32_t)data[byte_index];
        for (bit_index = 0; bit_index < 8U; ++bit_index) {
            if ((crc & UINT32_C(1)) != 0U) {
                crc = (crc >> 1) ^ UINT32_C(0xedb88320);
            } else {
                crc >>= 1;
            }
        }
    }
    return ~crc;
}

enum p6c_parse_result p6c_decode_request(
    const uint8_t *packet,
    size_t packet_size,
    struct p6c_frame_view *frame)
{
    struct p6c_frame_view decoded;
    enum p6c_parse_result parse_result;
    uint16_t message_type;
    uint32_t payload_length;
    uint32_t expected_crc;
    size_t expected_size;
    size_t index;
    int request_id_nonzero = 0;

    if ((packet == NULL) || (frame == NULL) ||
        (packet_size < P6C_HEADER_SIZE)) {
        return P6C_PARSE_TRUNCATED;
    }
    if (packet_size > (size_t)P6C_MAX_FRAME_BYTES) {
        return P6C_PARSE_OVERSIZED;
    }
    if (p6c_load_u32_be(&packet[P6C_HEADER_MAGIC_OFFSET]) !=
        P6C_PROTOCOL_MAGIC) {
        return P6C_PARSE_BAD_MAGIC;
    }
    if (p6c_load_u16_be(&packet[P6C_HEADER_VERSION_OFFSET]) !=
        P6C_PROTOCOL_VERSION) {
        return P6C_PARSE_UNSUPPORTED_VERSION;
    }
    if (p6c_load_u32_be(&packet[P6C_HEADER_FLAGS_OFFSET]) != P6C_V1_FLAGS) {
        return P6C_PARSE_NONZERO_FLAGS;
    }

    message_type =
        p6c_load_u16_be(&packet[P6C_HEADER_MESSAGE_TYPE_OFFSET]);
    if ((message_type & P6C_RESPONSE_BIT) != 0U) {
        return P6C_PARSE_RESPONSE_TYPE;
    }
    if ((message_type < (uint16_t)P6C_REQUEST_HELLO) ||
        (message_type > (uint16_t)P6C_REQUEST_RECOVER)) {
        return P6C_PARSE_REQUEST_TYPE;
    }

    payload_length =
        p6c_load_u32_be(&packet[P6C_HEADER_PAYLOAD_LENGTH_OFFSET]);
    if (payload_length > P6C_MAX_PAYLOAD_BYTES) {
        return P6C_PARSE_OVERSIZED;
    }
    expected_size = P6C_HEADER_SIZE + (size_t)payload_length;
    if (packet_size < expected_size) {
        return P6C_PARSE_TRUNCATED;
    }
    if (packet_size > expected_size) {
        return P6C_PARSE_TRAILING_BYTES;
    }

    for (index = 0; index < P6C_REQUEST_ID_BYTES; ++index) {
        if (packet[P6C_HEADER_REQUEST_ID_OFFSET + index] != UINT8_C(0)) {
            request_id_nonzero = 1;
        }
    }
    if (request_id_nonzero == 0) {
        return P6C_PARSE_REQUEST_ID;
    }

    expected_crc =
        p6c_load_u32_be(&packet[P6C_HEADER_PAYLOAD_CRC32_OFFSET]);
    if (p6c_crc32(&packet[P6C_HEADER_SIZE], (size_t)payload_length) !=
        expected_crc) {
        return P6C_PARSE_BAD_CRC;
    }

    memset(&decoded, 0, sizeof(decoded));
    decoded.message_type = message_type;
    memcpy(decoded.request_id, &packet[P6C_HEADER_REQUEST_ID_OFFSET],
           P6C_REQUEST_ID_BYTES);
    decoded.payload = &packet[P6C_HEADER_SIZE];
    decoded.payload_length = payload_length;
    parse_result = p6c_parse_fields(
        message_type, decoded.payload, (size_t)payload_length, &decoded);
    if (parse_result != P6C_PARSE_OK) {
        return parse_result;
    }
    *frame = decoded;
    return P6C_PARSE_OK;
}
