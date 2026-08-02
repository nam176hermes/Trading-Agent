#include "p6c_types.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>


#define P6C_JOURNAL_VERSION UINT16_C(2)
#define P6C_JOURNAL_STATE_OFFSET ((size_t)10)
#define P6C_JOURNAL_OPERATION_OFFSET ((size_t)12)
#define P6C_JOURNAL_SEQUENCE_OFFSET ((size_t)28)
#define P6C_JOURNAL_PRIOR_OFFSET ((size_t)36)
#define P6C_JOURNAL_PAYLOAD_LENGTH_OFFSET ((size_t)68)
#define P6C_JOURNAL_PAYLOAD_OFFSET ((size_t)72)
#define P6C_JOURNAL_PAYLOAD_DIGEST_OFFSET ((size_t)136)
#define P6C_JOURNAL_RECORD_DIGEST_OFFSET ((size_t)168)
static const uint8_t P6C_JOURNAL_MAGIC[8] = {
    UINT8_C('P'), UINT8_C('6'), UINT8_C('C'), UINT8_C('J'),
    UINT8_C('N'), UINT8_C('L'), UINT8_C('2'), UINT8_C(0)
};

static uint16_t p6c_journal_load_u16(const uint8_t input[static 2])
{
    return (uint16_t)(((uint16_t)input[0] << 8) | (uint16_t)input[1]);
}

static uint32_t p6c_journal_load_u32(const uint8_t input[static 4])
{
    return ((uint32_t)input[0] << 24) | ((uint32_t)input[1] << 16) |
           ((uint32_t)input[2] << 8) | (uint32_t)input[3];
}

static uint64_t p6c_journal_load_u64(const uint8_t input[static 8])
{
    uint64_t value = UINT64_C(0);
    size_t index;

    for (index = 0U; index < 8U; ++index) {
        value = (value << 8) | (uint64_t)input[index];
    }
    return value;
}

static void p6c_journal_store_u16(uint8_t output[static 2], uint16_t value)
{
    output[0] = (uint8_t)(value >> 8);
    output[1] = (uint8_t)value;
}

static void p6c_journal_store_u32(uint8_t output[static 4], uint32_t value)
{
    output[0] = (uint8_t)(value >> 24);
    output[1] = (uint8_t)(value >> 16);
    output[2] = (uint8_t)(value >> 8);
    output[3] = (uint8_t)value;
}

static void p6c_journal_store_u64(uint8_t output[static 8], uint64_t value)
{
    size_t index;

    for (index = 0U; index < 8U; ++index) {
        output[7U - index] = (uint8_t)(value >> (index * 8U));
    }
}

static int p6c_journal_name_safe(const char *name)
{
    size_t length;
    size_t index;

    if (name == NULL) {
        return 0;
    }
    length = strnlen(name, 129U);
    if ((length == 0U) || (length > 128U) ||
        ((length == 1U) && (name[0] == '.')) ||
        ((length == 2U) && (name[0] == '.') && (name[1] == '.'))) {
        return 0;
    }
    for (index = 0U; index < length; ++index) {
        unsigned char character = (unsigned char)name[index];

        if (!(((character >= (unsigned char)'a') &&
               (character <= (unsigned char)'z')) ||
              ((character >= (unsigned char)'A') &&
               (character <= (unsigned char)'Z')) ||
              ((character >= (unsigned char)'0') &&
               (character <= (unsigned char)'9')) ||
              (character == (unsigned char)'.') ||
              (character == (unsigned char)'_') ||
              (character == (unsigned char)'-'))) {
            return 0;
        }
    }
    return 1;
}

static enum p6c_result p6c_journal_validate_file(
    const struct p6c_owned_fd *owner, uid_t approved_owner,
    struct stat *status)
{
    if ((owner == NULL) || (status == NULL) ||
        !p6c_owned_fd_is_live(owner) ||
        (fstat(owner->descriptor, status) != 0)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (!S_ISREG(status->st_mode) || (status->st_nlink != 1) ||
        (status->st_uid != approved_owner) ||
        ((status->st_mode & (mode_t)0777) != (mode_t)0600)) {
        return P6C_RESULT_UNSAFE;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_journal_digest(
    const void *data, size_t size,
    uint8_t digest[static P6C_SHA256_BYTES])
{
    struct p6c_sha256 context;

    p6c_sha256_init(&context);
    if (p6c_sha256_update(&context, data, size) != P6C_RESULT_OK) {
        return P6C_RESULT_INVALID;
    }
    return p6c_sha256_final(&context, digest);
}

bool p6c_transition_allowed(
    enum p6c_operation_state previous,
    enum p6c_operation_state next)
{
    static const enum p6c_operation_state NEXT[] = {
        P6C_OPERATION_RESERVED,
        P6C_OPERATION_EXECUTABLE_PINNED,
        P6C_OPERATION_CGROUP_CREATED,
        P6C_OPERATION_CHILD_CLONED,
        P6C_OPERATION_EXEC_CONFIRMED,
        P6C_OPERATION_RUNNING,
        P6C_OPERATION_STOP_REQUESTED,
        P6C_OPERATION_CGROUP_KILLED,
        P6C_OPERATION_CGROUP_EMPTY,
        P6C_OPERATION_CHILD_EXIT_OBSERVED,
        P6C_OPERATION_CHILD_REAPED,
        P6C_OPERATION_TRANSCRIPTS_FINAL,
        P6C_OPERATION_RESULT_RETAINED,
        P6C_OPERATION_ACKNOWLEDGED
    };

    if ((previous < P6C_OPERATION_ABSENT) ||
        (previous > P6C_OPERATION_RECOVERY_REQUIRED) ||
        (next < P6C_OPERATION_ABSENT) ||
        (next > P6C_OPERATION_RECOVERY_REQUIRED)) {
        return false;
    }
    if ((next == P6C_OPERATION_RECOVERY_REQUIRED) &&
        (previous != P6C_OPERATION_ACKNOWLEDGED)) {
        return true;
    }
    if ((previous == P6C_OPERATION_RECOVERY_REQUIRED) &&
        (next >= P6C_OPERATION_STOP_REQUESTED) &&
        (next <= P6C_OPERATION_ACKNOWLEDGED)) {
        return true;
    }
    if ((previous >= P6C_OPERATION_ABSENT) &&
        (previous < P6C_OPERATION_ACKNOWLEDGED)) {
        return NEXT[(size_t)previous] == next;
    }
    return false;
}

enum p6c_result p6c_journal_create(
    const struct p6c_owned_fd *directory,
    const char *name,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    uid_t approved_owner,
    struct p6c_journal *journal)
{
    int descriptor;
    struct stat status;
    enum p6c_result result;

    if ((directory == NULL) || (operation_id == NULL) ||
        (journal == NULL) || !p6c_owned_fd_is_live(directory) ||
        (directory->type != P6C_DESCRIPTOR_DIRECTORY) ||
        !p6c_journal_name_safe(name)) {
        return P6C_RESULT_INVALID;
    }
    memset(journal, 0, sizeof(*journal));
    p6c_owned_fd_reset(&journal->file);
    descriptor = openat(directory->descriptor, name,
                        O_RDWR | O_APPEND | O_CREAT | O_EXCL |
                            O_CLOEXEC | O_NOFOLLOW,
                        (mode_t)0600);
    if (descriptor < 0) {
        return (errno == EEXIST) ? P6C_RESULT_CONFLICT :
                                  P6C_RESULT_SYSTEM;
    }
    result = p6c_owned_fd_acquire(
        &journal->file, descriptor, P6C_DESCRIPTOR_REGULAR);
    journal->directory = directory;
    memcpy(journal->operation_id, operation_id, P6C_OPERATION_ID_BYTES);
    journal->next_sequence = UINT64_C(1);
    journal->durable_state = P6C_OPERATION_ABSENT;
    if (result != P6C_RESULT_OK) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_journal_validate_file(
        &journal->file, approved_owner, &status);
    if (result != P6C_RESULT_OK) {
        journal->recovery_required = true;
        return result;
    }
    if (fsync(directory->descriptor) != 0) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_journal_append(
    struct p6c_journal *journal,
    enum p6c_operation_state next_state,
    const void *payload,
    size_t payload_length)
{
    uint8_t record[P6C_JOURNAL_RECORD_BYTES];
    uint8_t payload_digest[P6C_SHA256_BYTES];
    uint8_t record_digest[P6C_SHA256_BYTES];
    ssize_t amount;

    if ((journal == NULL) || !p6c_owned_fd_is_live(&journal->file) ||
        ((payload == NULL) && (payload_length != 0U)) ||
        (payload_length > P6C_JOURNAL_PAYLOAD_BYTES) ||
        journal->recovery_required ||
        !p6c_transition_allowed(journal->durable_state, next_state)) {
        return P6C_RESULT_INVALID;
    }
    if ((next_state == P6C_OPERATION_CGROUP_CREATED) &&
        (!journal->cgroup_allocation_intent ||
         (payload == NULL) ||
         (payload_length != P6C_CGROUP_CREATED_PAYLOAD_BYTES) ||
         (memcmp(
              payload, journal->cgroup_allocation_name,
              P6C_CGROUP_NAME_BYTES - 1U) != 0))) {
        return P6C_RESULT_INVALID;
    }
    memset(record, 0, sizeof(record));
    memcpy(record, P6C_JOURNAL_MAGIC, sizeof(P6C_JOURNAL_MAGIC));
    p6c_journal_store_u16(&record[8], P6C_JOURNAL_VERSION);
    p6c_journal_store_u16(&record[P6C_JOURNAL_STATE_OFFSET],
                          (uint16_t)next_state);
    memcpy(&record[P6C_JOURNAL_OPERATION_OFFSET], journal->operation_id,
           P6C_OPERATION_ID_BYTES);
    p6c_journal_store_u64(&record[P6C_JOURNAL_SEQUENCE_OFFSET],
                          journal->next_sequence);
    memcpy(&record[P6C_JOURNAL_PRIOR_OFFSET], journal->prior_digest,
           P6C_SHA256_BYTES);
    p6c_journal_store_u32(&record[P6C_JOURNAL_PAYLOAD_LENGTH_OFFSET],
                          (uint32_t)payload_length);
    if (payload_length != 0U) {
        memcpy(&record[P6C_JOURNAL_PAYLOAD_OFFSET], payload, payload_length);
    }
    if ((p6c_journal_digest(payload, payload_length, payload_digest) !=
         P6C_RESULT_OK) ||
        (p6c_journal_digest(record, P6C_JOURNAL_RECORD_DIGEST_OFFSET,
                            record_digest) != P6C_RESULT_OK)) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    memcpy(&record[P6C_JOURNAL_PAYLOAD_DIGEST_OFFSET], payload_digest,
           P6C_SHA256_BYTES);
    if (p6c_journal_digest(record, P6C_JOURNAL_RECORD_DIGEST_OFFSET,
                           record_digest) != P6C_RESULT_OK) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    memcpy(&record[P6C_JOURNAL_RECORD_DIGEST_OFFSET], record_digest,
           P6C_SHA256_BYTES);
    if (p6c_failpoint_active(P6C_FAIL_JOURNAL_WRITE)) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    do {
        amount = write(journal->file.descriptor, record, sizeof(record));
    } while ((amount < 0) && (errno == EINTR));
    if (amount != (ssize_t)sizeof(record)) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (p6c_failpoint_active(P6C_FAIL_JOURNAL_FSYNC) ||
        (fsync(journal->file.descriptor) != 0)) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    memcpy(journal->prior_digest, record_digest, P6C_SHA256_BYTES);
    ++journal->next_sequence;
    journal->durable_state = next_state;
    journal->state_payload_lengths[(size_t)next_state] =
        (uint8_t)payload_length;
    if (payload_length != 0U) {
        memcpy(journal->state_payloads[(size_t)next_state],
               payload, payload_length);
    }
    if (next_state == P6C_OPERATION_CGROUP_CREATED) {
        const uint8_t *created = payload;

        journal->cgroup_created_identity = true;
        journal->cgroup_created_device = p6c_journal_load_u64(
            &created[P6C_CGROUP_CREATED_DEVICE_OFFSET]);
        journal->cgroup_created_inode = p6c_journal_load_u64(
            &created[P6C_CGROUP_CREATED_INODE_OFFSET]);
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_journal_append_internal(
    struct p6c_journal *journal, uint16_t record_type,
    const uint8_t *payload, size_t payload_length,
    enum p6c_failpoint failpoint)
{
    uint8_t record[P6C_JOURNAL_RECORD_BYTES];
    uint8_t payload_digest[P6C_SHA256_BYTES];
    uint8_t record_digest[P6C_SHA256_BYTES];
    ssize_t amount;

    if ((journal == NULL) || (payload == NULL) ||
        (payload_length > P6C_JOURNAL_PAYLOAD_BYTES) ||
        !p6c_owned_fd_is_live(&journal->file) ||
        journal->recovery_required) {
        return P6C_RESULT_INVALID;
    }
    memset(record, 0, sizeof(record));
    memcpy(record, P6C_JOURNAL_MAGIC, sizeof(P6C_JOURNAL_MAGIC));
    p6c_journal_store_u16(&record[8], P6C_JOURNAL_VERSION);
    p6c_journal_store_u16(&record[P6C_JOURNAL_STATE_OFFSET],
                          record_type);
    memcpy(&record[P6C_JOURNAL_OPERATION_OFFSET], journal->operation_id,
           P6C_OPERATION_ID_BYTES);
    p6c_journal_store_u64(&record[P6C_JOURNAL_SEQUENCE_OFFSET],
                          journal->next_sequence);
    memcpy(&record[P6C_JOURNAL_PRIOR_OFFSET], journal->prior_digest,
           P6C_SHA256_BYTES);
    p6c_journal_store_u32(&record[P6C_JOURNAL_PAYLOAD_LENGTH_OFFSET],
                          (uint32_t)payload_length);
    memcpy(&record[P6C_JOURNAL_PAYLOAD_OFFSET], payload, payload_length);
    if (p6c_journal_digest(payload, payload_length, payload_digest) !=
        P6C_RESULT_OK) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    memcpy(&record[P6C_JOURNAL_PAYLOAD_DIGEST_OFFSET],
           payload_digest, P6C_SHA256_BYTES);
    if (p6c_journal_digest(
            record, P6C_JOURNAL_RECORD_DIGEST_OFFSET,
            record_digest) != P6C_RESULT_OK) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    memcpy(&record[P6C_JOURNAL_RECORD_DIGEST_OFFSET], record_digest,
           P6C_SHA256_BYTES);
    if (p6c_failpoint_active(P6C_FAIL_JOURNAL_WRITE)) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    do {
        amount = write(journal->file.descriptor, record, sizeof(record));
    } while ((amount < 0) && (errno == EINTR));
    if (amount != (ssize_t)sizeof(record)) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (p6c_failpoint_active(failpoint) ||
        (fsync(journal->file.descriptor) != 0)) {
        journal->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    memcpy(journal->prior_digest, record_digest, P6C_SHA256_BYTES);
    ++journal->next_sequence;
    return P6C_RESULT_OK;
}

enum p6c_result p6c_journal_append_cgroup_allocation_intent(
    struct p6c_journal *journal,
    const char cgroup_name[static P6C_CGROUP_NAME_BYTES])
{
    enum p6c_result result;

    if ((journal == NULL) || (cgroup_name == NULL) ||
        journal->cgroup_allocation_intent ||
        (journal->durable_state != P6C_OPERATION_EXECUTABLE_PINNED) ||
        (strnlen(cgroup_name, P6C_CGROUP_NAME_BYTES) !=
         P6C_CGROUP_NAME_BYTES - 1U)) {
        return P6C_RESULT_INVALID;
    }
    result = p6c_journal_append_internal(
        journal, P6C_JOURNAL_CGROUP_ALLOCATION_INTENT,
        (const uint8_t *)cgroup_name, P6C_CGROUP_NAME_BYTES - 1U,
        P6C_FAIL_JOURNAL_FSYNC);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    journal->cgroup_allocation_intent = true;
    memcpy(
        journal->cgroup_allocation_name, cgroup_name,
        P6C_CGROUP_NAME_BYTES);
    return P6C_RESULT_OK;
}

enum p6c_result p6c_journal_append_bundle_committed(
    struct p6c_journal *journal,
    const uint8_t manifest_digest[static P6C_SHA256_BYTES])
{
    uint8_t bundle_payload[P6C_SHA256_BYTES * 2U];
    enum p6c_result result;

    if ((journal == NULL) || (manifest_digest == NULL) ||
        journal->bundle_committed ||
        (journal->durable_state != P6C_OPERATION_RESULT_RETAINED)) {
        return P6C_RESULT_INVALID;
    }
    memcpy(bundle_payload, journal->publication_identity,
           P6C_SHA256_BYTES);
    memcpy(&bundle_payload[P6C_SHA256_BYTES], manifest_digest,
           P6C_SHA256_BYTES);
    result = p6c_journal_append_internal(
        journal, P6C_JOURNAL_BUNDLE_COMMITTED,
        bundle_payload, sizeof(bundle_payload),
        P6C_FAIL_JOURNAL_FSYNC);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    journal->bundle_committed = true;
    memcpy(journal->manifest_digest, manifest_digest, P6C_SHA256_BYTES);
    return P6C_RESULT_OK;
}

enum p6c_result p6c_journal_append_transcript_digests(
    struct p6c_journal *journal,
    const uint8_t stdout_retained_digest[static P6C_SHA256_BYTES],
    const uint8_t stderr_retained_digest[static P6C_SHA256_BYTES])
{
    uint8_t payload[P6C_SHA256_BYTES * 2U];
    enum p6c_result result;

    if ((journal == NULL) || (stdout_retained_digest == NULL) ||
        (stderr_retained_digest == NULL) ||
        journal->transcript_digests_committed ||
        journal->cgroup_removal_intent ||
        (journal->durable_state != P6C_OPERATION_TRANSCRIPTS_FINAL)) {
        return P6C_RESULT_INVALID;
    }
    memcpy(payload, stdout_retained_digest, P6C_SHA256_BYTES);
    memcpy(&payload[P6C_SHA256_BYTES], stderr_retained_digest,
           P6C_SHA256_BYTES);
    result = p6c_journal_append_internal(
        journal, P6C_JOURNAL_TRANSCRIPT_DIGESTS,
        payload, sizeof(payload),
        P6C_FAIL_TRANSCRIPT_DIGEST_JOURNAL);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    journal->transcript_digests_committed = true;
    memcpy(journal->stdout_retained_digest, stdout_retained_digest,
           P6C_SHA256_BYTES);
    memcpy(journal->stderr_retained_digest, stderr_retained_digest,
           P6C_SHA256_BYTES);
    return P6C_RESULT_OK;
}

enum p6c_result p6c_journal_append_cgroup_removal_intent(
    struct p6c_journal *journal, uint64_t cgroup_device,
    uint64_t cgroup_inode,
    const uint8_t result_payload[static P6C_RESULT_PAYLOAD_BYTES])
{
    uint8_t payload[P6C_CGROUP_REMOVAL_INTENT_BYTES];
    enum p6c_result result;

    if ((journal == NULL) || (result_payload == NULL) ||
        !journal->transcript_digests_committed ||
        journal->cgroup_removal_intent ||
        (journal->durable_state != P6C_OPERATION_TRANSCRIPTS_FINAL)) {
        return P6C_RESULT_INVALID;
    }
    p6c_journal_store_u64(
        &payload[P6C_CGROUP_INTENT_DEVICE_OFFSET], cgroup_device);
    p6c_journal_store_u64(
        &payload[P6C_CGROUP_INTENT_INODE_OFFSET], cgroup_inode);
    memcpy(&payload[P6C_CGROUP_INTENT_RESULT_OFFSET],
           result_payload, P6C_RESULT_PAYLOAD_BYTES);
    result = p6c_journal_append_internal(
        journal, P6C_JOURNAL_CGROUP_REMOVAL_INTENT,
        payload, sizeof(payload),
        P6C_FAIL_REMOVAL_INTENT_JOURNAL);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    journal->cgroup_removal_intent = true;
    journal->cgroup_device = cgroup_device;
    journal->cgroup_inode = cgroup_inode;
    memcpy(journal->retained_result_payload, result_payload,
           P6C_RESULT_PAYLOAD_BYTES);
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_journal_validate_record(
    const uint8_t record[static P6C_JOURNAL_RECORD_BYTES],
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    uint64_t sequence,
    const uint8_t prior_digest[static P6C_SHA256_BYTES],
    enum p6c_operation_state prior_state,
    const struct p6c_journal *journal,
    enum p6c_operation_state *state,
    uint8_t digest[static P6C_SHA256_BYTES],
    uint16_t *internal_type)
{
    uint32_t payload_length;
    uint8_t calculated[P6C_SHA256_BYTES];
    uint16_t state_value;

    if ((memcmp(record, P6C_JOURNAL_MAGIC,
                sizeof(P6C_JOURNAL_MAGIC)) != 0) ||
        (p6c_journal_load_u16(&record[8]) != P6C_JOURNAL_VERSION) ||
        (memcmp(&record[P6C_JOURNAL_OPERATION_OFFSET], operation_id,
                P6C_OPERATION_ID_BYTES) != 0) ||
        (p6c_journal_load_u64(&record[P6C_JOURNAL_SEQUENCE_OFFSET]) !=
         sequence) ||
        (memcmp(&record[P6C_JOURNAL_PRIOR_OFFSET], prior_digest,
                P6C_SHA256_BYTES) != 0)) {
        return P6C_RESULT_MALFORMED;
    }
    payload_length = p6c_journal_load_u32(
        &record[P6C_JOURNAL_PAYLOAD_LENGTH_OFFSET]);
    if (payload_length > (uint32_t)P6C_JOURNAL_PAYLOAD_BYTES) {
        return P6C_RESULT_MALFORMED;
    }
    if ((p6c_journal_digest(&record[P6C_JOURNAL_PAYLOAD_OFFSET],
                            (size_t)payload_length, calculated) !=
         P6C_RESULT_OK) ||
        (memcmp(calculated, &record[P6C_JOURNAL_PAYLOAD_DIGEST_OFFSET],
                P6C_SHA256_BYTES) != 0) ||
        (p6c_journal_digest(record, P6C_JOURNAL_RECORD_DIGEST_OFFSET,
                            calculated) != P6C_RESULT_OK) ||
        (memcmp(calculated, &record[P6C_JOURNAL_RECORD_DIGEST_OFFSET],
                P6C_SHA256_BYTES) != 0)) {
        return P6C_RESULT_MALFORMED;
    }
    state_value = p6c_journal_load_u16(
        &record[P6C_JOURNAL_STATE_OFFSET]);
    *internal_type = UINT16_C(0);
    if (state_value == P6C_JOURNAL_BUNDLE_COMMITTED) {
        if ((prior_state != P6C_OPERATION_RESULT_RETAINED) ||
            (payload_length !=
             (uint32_t)(P6C_SHA256_BYTES * 2U)) ||
            (journal == NULL) || journal->bundle_committed) {
            return P6C_RESULT_MALFORMED;
        }
        *state = prior_state;
        *internal_type = state_value;
    } else if (state_value == P6C_JOURNAL_TRANSCRIPT_DIGESTS) {
        if ((prior_state != P6C_OPERATION_TRANSCRIPTS_FINAL) ||
            (payload_length !=
             (uint32_t)(P6C_SHA256_BYTES * 2U)) ||
            (journal == NULL) || journal->transcript_digests_committed ||
            journal->cgroup_removal_intent) {
            return P6C_RESULT_MALFORMED;
        }
        *state = prior_state;
        *internal_type = state_value;
    } else if (state_value == P6C_JOURNAL_CGROUP_REMOVAL_INTENT) {
        if ((prior_state != P6C_OPERATION_TRANSCRIPTS_FINAL) ||
            (payload_length !=
             (uint32_t)P6C_CGROUP_REMOVAL_INTENT_BYTES) ||
            (journal == NULL) || !journal->transcript_digests_committed ||
            journal->cgroup_removal_intent) {
            return P6C_RESULT_MALFORMED;
        }
        *state = prior_state;
        *internal_type = state_value;
    } else if (state_value ==
               P6C_JOURNAL_CGROUP_ALLOCATION_INTENT) {
        if ((prior_state != P6C_OPERATION_EXECUTABLE_PINNED) ||
            (payload_length !=
             (uint32_t)(P6C_CGROUP_NAME_BYTES - 1U)) ||
            (journal == NULL) || journal->cgroup_allocation_intent) {
            return P6C_RESULT_MALFORMED;
        }
        *state = prior_state;
        *internal_type = state_value;
    } else {
        if (state_value >
            (uint16_t)P6C_OPERATION_RECOVERY_REQUIRED) {
            return P6C_RESULT_MALFORMED;
        }
        *state = (enum p6c_operation_state)state_value;
        if (!p6c_transition_allowed(prior_state, *state)) {
            return P6C_RESULT_MALFORMED;
        }
    }
    memcpy(digest, &record[P6C_JOURNAL_RECORD_DIGEST_OFFSET],
           P6C_SHA256_BYTES);
    return P6C_RESULT_OK;
}

enum p6c_result p6c_journal_recover(
    const struct p6c_owned_fd *directory,
    const char *name,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    uid_t approved_owner,
    struct p6c_journal *journal,
    enum p6c_journal_recovery *recovery)
{
    int descriptor;
    struct stat status;
    enum p6c_result result;
    uint64_t record_count;
    uint64_t index;

    if ((directory == NULL) || (operation_id == NULL) ||
        (journal == NULL) || (recovery == NULL) ||
        !p6c_owned_fd_is_live(directory) ||
        !p6c_journal_name_safe(name)) {
        return P6C_RESULT_INVALID;
    }
    memset(journal, 0, sizeof(*journal));
    p6c_owned_fd_reset(&journal->file);
    descriptor = openat(directory->descriptor, name,
                        O_RDWR | O_APPEND | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) {
        return (errno == ELOOP) ? P6C_RESULT_UNSAFE : P6C_RESULT_SYSTEM;
    }
    result = p6c_owned_fd_acquire(
        &journal->file, descriptor, P6C_DESCRIPTOR_REGULAR);
    journal->directory = directory;
    memcpy(journal->operation_id, operation_id, P6C_OPERATION_ID_BYTES);
    journal->next_sequence = UINT64_C(1);
    journal->durable_state = P6C_OPERATION_ABSENT;
    *recovery = P6C_JOURNAL_COMPLETE;
    if (result != P6C_RESULT_OK) {
        journal->recovery_required = true;
        *recovery = P6C_JOURNAL_INVALID;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_journal_validate_file(
        &journal->file, approved_owner, &status);
    if (result != P6C_RESULT_OK) {
        journal->recovery_required = true;
        *recovery = P6C_JOURNAL_INVALID;
        return result;
    }
    if (status.st_size < 0) {
        journal->recovery_required = true;
        *recovery = P6C_JOURNAL_INVALID;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    record_count =
        (uint64_t)status.st_size / (uint64_t)P6C_JOURNAL_RECORD_BYTES;
    for (index = UINT64_C(0); index < record_count; ++index) {
        uint8_t record[P6C_JOURNAL_RECORD_BYTES];
        enum p6c_operation_state state;
        uint16_t internal_type;
        off_t offset = (off_t)(index *
                               (uint64_t)P6C_JOURNAL_RECORD_BYTES);
        ssize_t amount = pread(journal->file.descriptor, record,
                               sizeof(record), offset);

        if (amount != (ssize_t)sizeof(record)) {
            journal->recovery_required = true;
            *recovery = P6C_JOURNAL_INVALID;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        result = p6c_journal_validate_record(
            record, operation_id, index + UINT64_C(1),
            journal->prior_digest, journal->durable_state, journal, &state,
            journal->prior_digest, &internal_type);
        if (result != P6C_RESULT_OK) {
            journal->recovery_required = true;
            *recovery = P6C_JOURNAL_INVALID;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        {
            uint32_t payload_length = p6c_journal_load_u32(
                &record[P6C_JOURNAL_PAYLOAD_LENGTH_OFFSET]);

            if (internal_type == P6C_JOURNAL_BUNDLE_COMMITTED) {
                journal->bundle_committed = true;
                memcpy(journal->publication_identity,
                       &record[P6C_JOURNAL_PAYLOAD_OFFSET],
                       P6C_SHA256_BYTES);
                memcpy(journal->manifest_digest,
                       &record[P6C_JOURNAL_PAYLOAD_OFFSET +
                               P6C_SHA256_BYTES],
                       P6C_SHA256_BYTES);
            } else if (internal_type ==
                       P6C_JOURNAL_TRANSCRIPT_DIGESTS) {
                journal->transcript_digests_committed = true;
                memcpy(journal->stdout_retained_digest,
                       &record[P6C_JOURNAL_PAYLOAD_OFFSET],
                       P6C_SHA256_BYTES);
                memcpy(journal->stderr_retained_digest,
                       &record[P6C_JOURNAL_PAYLOAD_OFFSET +
                               P6C_SHA256_BYTES],
                       P6C_SHA256_BYTES);
            } else if (internal_type ==
                       P6C_JOURNAL_CGROUP_REMOVAL_INTENT) {
                journal->cgroup_removal_intent = true;
                journal->cgroup_device = p6c_journal_load_u64(
                    &record[P6C_JOURNAL_PAYLOAD_OFFSET +
                            P6C_CGROUP_INTENT_DEVICE_OFFSET]);
                journal->cgroup_inode = p6c_journal_load_u64(
                    &record[P6C_JOURNAL_PAYLOAD_OFFSET +
                            P6C_CGROUP_INTENT_INODE_OFFSET]);
                memcpy(journal->retained_result_payload,
                       &record[P6C_JOURNAL_PAYLOAD_OFFSET +
                               P6C_CGROUP_INTENT_RESULT_OFFSET],
                       P6C_RESULT_PAYLOAD_BYTES);
            } else if (internal_type ==
                       P6C_JOURNAL_CGROUP_ALLOCATION_INTENT) {
                journal->cgroup_allocation_intent = true;
                memcpy(
                    journal->cgroup_allocation_name,
                    &record[P6C_JOURNAL_PAYLOAD_OFFSET],
                    P6C_CGROUP_NAME_BYTES - 1U);
                journal->cgroup_allocation_name[
                    P6C_CGROUP_NAME_BYTES - 1U] = '\0';
            } else {
                journal->state_payload_lengths[(size_t)state] =
                    (uint8_t)payload_length;
                if (payload_length != 0U) {
                    memcpy(journal->state_payloads[(size_t)state],
                           &record[P6C_JOURNAL_PAYLOAD_OFFSET],
                           (size_t)payload_length);
                }
                if (state == P6C_OPERATION_CGROUP_CREATED) {
                    const uint8_t *created =
                        &record[P6C_JOURNAL_PAYLOAD_OFFSET];

                    if (!journal->cgroup_allocation_intent ||
                        (payload_length !=
                         (uint32_t)P6C_CGROUP_CREATED_PAYLOAD_BYTES) ||
                        (memcmp(
                             created,
                             journal->cgroup_allocation_name,
                             P6C_CGROUP_NAME_BYTES - 1U) != 0)) {
                        journal->recovery_required = true;
                        *recovery = P6C_JOURNAL_INVALID;
                        return P6C_RESULT_RECOVERY_REQUIRED;
                    }
                    journal->cgroup_created_identity = true;
                    journal->cgroup_created_device =
                        p6c_journal_load_u64(
                            &created[
                                P6C_CGROUP_CREATED_DEVICE_OFFSET]);
                    journal->cgroup_created_inode =
                        p6c_journal_load_u64(
                            &created[
                                P6C_CGROUP_CREATED_INODE_OFFSET]);
                }
            }
        }
        journal->durable_state = state;
        journal->next_sequence = index + UINT64_C(2);
    }
    if (((uint64_t)status.st_size %
         (uint64_t)P6C_JOURNAL_RECORD_BYTES) != UINT64_C(0)) {
        journal->recovery_required = true;
        *recovery = P6C_JOURNAL_TORN_TAIL;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_journal_close(struct p6c_journal *journal)
{
    if (journal == NULL) {
        return P6C_RESULT_INVALID;
    }
    if (!p6c_owned_fd_is_live(&journal->file)) {
        return P6C_RESULT_OK;
    }
    return p6c_owned_fd_close(&journal->file);
}
