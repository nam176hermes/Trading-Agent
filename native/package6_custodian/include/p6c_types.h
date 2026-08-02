#ifndef P6C_TYPES_H
#define P6C_TYPES_H

#include "p6c_protocol.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>

#define P6C_INVALID_DESCRIPTOR (-1)
#define P6C_RECOVERY_TOKEN_BYTES P6C_OPERATION_ID_BYTES
#define P6C_JOURNAL_PAYLOAD_BYTES ((size_t)64)
#define P6C_JOURNAL_RECORD_BYTES ((size_t)200)
#define P6C_RESERVED_PAYLOAD_BYTES ((size_t)53)
#define P6C_REPLAY_CAPACITY ((size_t)64)
#define P6C_TOMBSTONE_CAPACITY ((size_t)16)
#define P6C_CGROUP_NAME_BYTES ((size_t)37)
#define P6C_CGROUP_QUARANTINE_NAME_BYTES ((size_t)41)
#define P6C_CGROUP_CREATED_PAYLOAD_BYTES ((size_t)52)
#define P6C_CGROUP_CREATED_NAME_OFFSET ((size_t)0)
#define P6C_CGROUP_CREATED_DEVICE_OFFSET ((size_t)36)
#define P6C_CGROUP_CREATED_INODE_OFFSET ((size_t)44)
#define P6C_MAX_TRANSCRIPT_RETAINED UINT64_C(1048576)
#define P6C_MAX_PUBLICATION_FILES ((size_t)16)
#define P6C_MAX_PUBLICATION_NAME ((size_t)64)
#define P6C_MAX_MANIFEST_BYTES ((size_t)32768)
#define P6C_TRANSCRIPTS_PAYLOAD_BYTES ((size_t)64)
#define P6C_RESULT_PAYLOAD_BYTES ((size_t)37)
#define P6C_RESULT_STDOUT_OBSERVED_OFFSET ((size_t)0)
#define P6C_RESULT_STDOUT_RETAINED_OFFSET ((size_t)8)
#define P6C_RESULT_STDERR_OBSERVED_OFFSET ((size_t)16)
#define P6C_RESULT_STDERR_RETAINED_OFFSET ((size_t)24)
#define P6C_RESULT_FLAGS_OFFSET ((size_t)32)
#define P6C_RESULT_EXIT_STATUS_OFFSET ((size_t)33)
#define P6C_RESULT_FLAG_STDOUT_TRUNCATED UINT8_C(0x01)
#define P6C_RESULT_FLAG_STDERR_TRUNCATED UINT8_C(0x02)
#define P6C_UNKNOWN_EXIT_STATUS INT32_MIN

#define P6C_JOURNAL_BUNDLE_COMMITTED UINT16_C(0x8001)
#define P6C_JOURNAL_TRANSCRIPT_DIGESTS UINT16_C(0x8002)
#define P6C_JOURNAL_CGROUP_REMOVAL_INTENT UINT16_C(0x8003)
#define P6C_JOURNAL_CGROUP_ALLOCATION_INTENT UINT16_C(0x8004)
#define P6C_CGROUP_REMOVAL_INTENT_BYTES ((size_t)53)
#define P6C_CGROUP_INTENT_DEVICE_OFFSET ((size_t)0)
#define P6C_CGROUP_INTENT_INODE_OFFSET ((size_t)8)
#define P6C_CGROUP_INTENT_RESULT_OFFSET ((size_t)16)

enum p6c_result {
    P6C_RESULT_OK = 0,
    P6C_RESULT_INVALID = -1,
    P6C_RESULT_SYSTEM = -2,
    P6C_RESULT_STALE = -3,
    P6C_RESULT_UNSAFE = -4,
    P6C_RESULT_LIMIT = -5,
    P6C_RESULT_RECOVERY_REQUIRED = -6,
    P6C_RESULT_CONFLICT = -7,
    P6C_RESULT_UNSUPPORTED = -8,
    P6C_RESULT_TIMEOUT = -9,
    P6C_RESULT_MALFORMED = -10,
    P6C_RESULT_UNAUTHORIZED = -11
};

enum p6c_descriptor_type {
    P6C_DESCRIPTOR_UNKNOWN = 0,
    P6C_DESCRIPTOR_REGULAR = 1,
    P6C_DESCRIPTOR_DIRECTORY = 2,
    P6C_DESCRIPTOR_SOCKET = 3,
    P6C_DESCRIPTOR_PIPE = 4,
    P6C_DESCRIPTOR_PIDFD = 5,
    P6C_DESCRIPTOR_CGROUP = 6
};

enum p6c_descriptor_lifecycle {
    P6C_DESCRIPTOR_EMPTY = 0,
    P6C_DESCRIPTOR_OWNED = 1,
    P6C_DESCRIPTOR_TRANSFERRED = 2,
    P6C_DESCRIPTOR_CLOSED = 3,
    P6C_DESCRIPTOR_RECOVERY = 4
};

struct p6c_owned_fd {
    int descriptor;
    dev_t device;
    ino_t inode;
    enum p6c_descriptor_type type;
    mode_t mode;
    enum p6c_descriptor_lifecycle lifecycle;
    bool closure_proven;
};

struct p6c_owned_pair {
    struct p6c_owned_fd first;
    struct p6c_owned_fd second;
};

enum p6c_failpoint {
    P6C_FAIL_NONE = 0,
    P6C_FAIL_PAIR_FIRST_ACQUIRE,
    P6C_FAIL_PAIR_SECOND_ACQUIRE,
    P6C_FAIL_PAIR_FIRST_FSTAT,
    P6C_FAIL_PAIR_SECOND_FSTAT,
    P6C_FAIL_PAIR_GETFL,
    P6C_FAIL_PAIR_SETFL,
    P6C_FAIL_JOURNAL_WRITE,
    P6C_FAIL_JOURNAL_FSYNC,
    P6C_FAIL_EXEC_HASH_READ,
    P6C_FAIL_TRANSCRIPT_WRITE,
    P6C_FAIL_TRANSCRIPT_READ,
    P6C_FAIL_TRANSCRIPT_DIGEST,
    P6C_FAIL_TRANSCRIPT_FSYNC,
    P6C_FAIL_TRANSCRIPT_CLOSE,
    P6C_FAIL_PUBLICATION_WRITE,
    P6C_FAIL_PUBLICATION_FILE_FSYNC,
    P6C_FAIL_PUBLICATION_MANIFEST,
    P6C_FAIL_PUBLICATION_RENAME,
    P6C_FAIL_PUBLICATION_ROOT_FSYNC,
    P6C_FAIL_PUBLICATION_VERIFY,
    P6C_FAIL_PIDFD_ACQUIRE,
    P6C_FAIL_STATUS_WRITER_CLOSE,
    P6C_FAIL_STDOUT_WRITER_CLOSE,
    P6C_FAIL_STDERR_WRITER_CLOSE,
    P6C_FAIL_SERVICE_RECEIVE,
    P6C_FAIL_SERVICE_SEND,
    P6C_FAIL_TRANSCRIPT_DIGEST_JOURNAL,
    P6C_FAIL_REMOVAL_INTENT_JOURNAL
};

void p6c_owned_fd_reset(struct p6c_owned_fd *owner);
enum p6c_result p6c_owned_fd_acquire(
    struct p6c_owned_fd *owner, int descriptor,
    enum p6c_descriptor_type type);
enum p6c_result p6c_owned_fd_close(struct p6c_owned_fd *owner);
enum p6c_result p6c_owned_pair_close(struct p6c_owned_pair *pair);
enum p6c_result p6c_owned_pipe_create(struct p6c_owned_pair *pair);
enum p6c_result p6c_openat2_owned(
    const struct p6c_owned_fd *root, const char *relative_path,
    int flags, mode_t mode, enum p6c_descriptor_type type,
    struct p6c_owned_fd *output);
bool p6c_owned_fd_is_live(const struct p6c_owned_fd *owner);

#ifdef P6C_TESTING
void p6c_test_failpoint_set(enum p6c_failpoint failpoint);
void p6c_test_failpoint_set_after(
    enum p6c_failpoint failpoint, unsigned int successful_hits);
bool p6c_failpoint_active(enum p6c_failpoint failpoint);
void p6c_test_exec_replacement_set(
    int root_descriptor, const char *approved_name,
    const char *replacement_name, const char *displaced_name);
void p6c_test_exec_hash_observe(void);
#else
static inline bool p6c_failpoint_active(enum p6c_failpoint failpoint)
{
    (void)failpoint;
    return false;
}
#endif

struct p6c_sha256 {
    uint32_t state[8];
    uint64_t bit_count;
    uint8_t buffer[64];
    size_t buffer_length;
    bool finalized;
};

void p6c_sha256_init(struct p6c_sha256 *context);
enum p6c_result p6c_sha256_update(
    struct p6c_sha256 *context, const void *data, size_t size);
enum p6c_result p6c_sha256_final(
    struct p6c_sha256 *context,
    uint8_t digest[static P6C_SHA256_BYTES]);
enum p6c_result p6c_sha256_fd(
    const struct p6c_owned_fd *owner,
    uint8_t digest[static P6C_SHA256_BYTES]);

struct p6c_executable {
    struct p6c_owned_fd file;
    dev_t device;
    ino_t inode;
    off_t size;
    struct timespec modification_time;
    struct timespec status_time;
    uid_t owner;
    mode_t mode;
    uint8_t digest[P6C_SHA256_BYTES];
};

enum p6c_result p6c_pin_executable(
    const struct p6c_owned_fd *source_root,
    const char *relative_path,
    uid_t approved_owner,
    const uint8_t expected_digest[static P6C_SHA256_BYTES],
    struct p6c_executable *executable);
enum p6c_result p6c_executable_close(struct p6c_executable *executable);
enum p6c_result p6c_execve_pinned(
    const struct p6c_executable *executable,
    char *const argv[], char *const environment[]);

struct p6c_peer_identity {
    pid_t process_id;
    uid_t user_id;
    gid_t group_id;
};

enum p6c_result p6c_authenticate_peer(
    const struct p6c_owned_fd *socket_owner,
    uid_t approved_user,
    struct p6c_peer_identity *peer);
#ifdef P6C_TESTING
void p6c_test_peer_override_set(
    bool enabled, const struct p6c_peer_identity *peer);
void p6c_test_service_io_set(
    const uint8_t *input, size_t input_size,
    uint8_t *output, size_t output_capacity, size_t *output_size);
void p6c_test_service_io_set_packets(
    const uint8_t *const *inputs, const size_t *input_sizes,
    size_t input_count, uint8_t *output, size_t output_capacity,
    size_t *output_sizes, size_t *output_count);
void p6c_test_service_disconnect_after_input(bool enabled);
void p6c_test_recovery_token_set(
    const uint8_t token[static P6C_RECOVERY_TOKEN_BYTES]);
enum p6c_result p6c_test_verify_credential_authority(
    int directory_descriptor, const uint8_t *manifest,
    size_t manifest_size);
#endif

struct p6c_replay_entry {
    bool occupied;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t request_digest[P6C_SHA256_BYTES];
    struct p6c_peer_identity peer;
};

struct p6c_replay_table {
    struct p6c_replay_entry entries[P6C_REPLAY_CAPACITY];
};

enum p6c_replay_result {
    P6C_REPLAY_NEW = 0,
    P6C_REPLAY_IDENTICAL = 1,
    P6C_REPLAY_DIGEST_MISMATCH = 2,
    P6C_REPLAY_DIFFERENT_PEER = 3,
    P6C_REPLAY_FULL = 4
};

void p6c_replay_table_init(struct p6c_replay_table *table);
enum p6c_replay_result p6c_replay_check(
    struct p6c_replay_table *table,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    const uint8_t request_digest[static P6C_SHA256_BYTES],
    const struct p6c_peer_identity *peer);

struct p6c_public_error {
    enum p6c_public_status status;
    char public_code[P6C_MAX_PUBLIC_CODE_BYTES + 1U];
    bool retryable;
    enum p6c_operation_state operation_state;
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
};

enum p6c_result p6c_public_error_set(
    struct p6c_public_error *error,
    enum p6c_public_status status,
    const char *public_code,
    bool retryable,
    enum p6c_operation_state operation_state,
    const uint8_t recovery_token[static P6C_RECOVERY_TOKEN_BYTES]);

struct p6c_journal {
    const struct p6c_owned_fd *directory;
    struct p6c_owned_fd file;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint64_t next_sequence;
    uint8_t prior_digest[P6C_SHA256_BYTES];
    enum p6c_operation_state durable_state;
    bool bundle_committed;
    bool recovery_required;
    uint8_t state_payloads[P6C_OPERATION_RECOVERY_REQUIRED + 1]
                          [P6C_JOURNAL_PAYLOAD_BYTES];
    uint8_t state_payload_lengths[P6C_OPERATION_RECOVERY_REQUIRED + 1];
    uint8_t publication_identity[P6C_SHA256_BYTES];
    uint8_t manifest_digest[P6C_SHA256_BYTES];
    bool transcript_digests_committed;
    uint8_t stdout_retained_digest[P6C_SHA256_BYTES];
    uint8_t stderr_retained_digest[P6C_SHA256_BYTES];
    bool cgroup_removal_intent;
    uint64_t cgroup_device;
    uint64_t cgroup_inode;
    uint8_t retained_result_payload[P6C_RESULT_PAYLOAD_BYTES];
    bool cgroup_allocation_intent;
    char cgroup_allocation_name[P6C_CGROUP_NAME_BYTES];
    bool cgroup_created_identity;
    uint64_t cgroup_created_device;
    uint64_t cgroup_created_inode;
};

enum p6c_journal_recovery {
    P6C_JOURNAL_COMPLETE = 0,
    P6C_JOURNAL_TORN_TAIL = 1,
    P6C_JOURNAL_INVALID = 2
};

bool p6c_transition_allowed(
    enum p6c_operation_state previous,
    enum p6c_operation_state next);
enum p6c_result p6c_journal_create(
    const struct p6c_owned_fd *directory,
    const char *name,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    uid_t approved_owner,
    struct p6c_journal *journal);
enum p6c_result p6c_journal_append(
    struct p6c_journal *journal,
    enum p6c_operation_state next_state,
    const void *payload,
    size_t payload_length);
enum p6c_result p6c_journal_append_bundle_committed(
    struct p6c_journal *journal,
    const uint8_t manifest_digest[static P6C_SHA256_BYTES]);
enum p6c_result p6c_journal_append_transcript_digests(
    struct p6c_journal *journal,
    const uint8_t stdout_retained_digest[static P6C_SHA256_BYTES],
    const uint8_t stderr_retained_digest[static P6C_SHA256_BYTES]);
enum p6c_result p6c_journal_append_cgroup_removal_intent(
    struct p6c_journal *journal, uint64_t cgroup_device,
    uint64_t cgroup_inode,
    const uint8_t result_payload[static P6C_RESULT_PAYLOAD_BYTES]);
enum p6c_result p6c_journal_append_cgroup_allocation_intent(
    struct p6c_journal *journal,
    const char cgroup_name[static P6C_CGROUP_NAME_BYTES]);
enum p6c_result p6c_journal_recover(
    const struct p6c_owned_fd *directory,
    const char *name,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    uid_t approved_owner,
    struct p6c_journal *journal,
    enum p6c_journal_recovery *recovery);
enum p6c_result p6c_journal_close(struct p6c_journal *journal);

enum p6c_stream_identity {
    P6C_STREAM_STDOUT = 1,
    P6C_STREAM_STDERR = 2
};

struct p6c_transcript {
    struct p6c_owned_fd sink;
    struct p6c_sha256 hash;
    enum p6c_stream_identity stream;
    uint64_t observed_size;
    uint64_t retained_size;
    uint64_t retained_limit;
    bool truncated;
    bool eof_observed;
    bool descendant_cleanup_proven;
    bool finalized;
    bool recovery_required;
    uint8_t digest[P6C_SHA256_BYTES];
    uint8_t retained_digest[P6C_SHA256_BYTES];
};

enum p6c_result p6c_transcript_create(
    const struct p6c_owned_fd *fallback_directory,
    enum p6c_stream_identity stream,
    uint64_t retained_limit,
    bool allow_test_fallback,
    struct p6c_transcript *transcript);
enum p6c_result p6c_transcript_ingest(
    struct p6c_transcript *transcript,
    const void *data,
    size_t size);
void p6c_transcript_observe_eof(struct p6c_transcript *transcript);
void p6c_transcript_prove_cleanup(struct p6c_transcript *transcript);
enum p6c_result p6c_transcript_finalize(
    struct p6c_transcript *transcript);
enum p6c_result p6c_transcript_read(
    struct p6c_transcript *transcript,
    uint64_t offset,
    void *output,
    size_t requested,
    size_t *read_size);
enum p6c_result p6c_transcript_close(
    struct p6c_transcript *transcript);
enum p6c_result p6c_transcript_link(
    const struct p6c_owned_fd *directory, const char *name,
    struct p6c_transcript *transcript);
enum p6c_result p6c_transcript_recover(
    const struct p6c_owned_fd *directory, const char *name,
    enum p6c_stream_identity stream, uint64_t observed_size,
    uint64_t retained_size, bool truncated,
    const uint8_t digest[static P6C_SHA256_BYTES],
    const uint8_t expected_retained_digest[static P6C_SHA256_BYTES],
    struct p6c_transcript *transcript);
enum p6c_result p6c_transcript_unlink(
    const struct p6c_owned_fd *directory, const char *name,
    const struct p6c_transcript *transcript);

struct p6c_operation;

enum p6c_exec_confirmation {
    P6C_EXEC_CONFIRM_CLEAN_EOF = 0,
    P6C_EXEC_CONFIRM_BYTES = 1,
    P6C_EXEC_CONFIRM_PARTIAL = 2,
    P6C_EXEC_CONFIRM_TIMEOUT = 3,
    P6C_EXEC_CONFIRM_QUICK_EXIT = 4,
    P6C_EXEC_CONFIRM_ERROR = 5
};

struct p6c_process_adapter {
    void *context;
    enum p6c_result (*clone_child)(
        void *context, struct p6c_operation *operation);
    enum p6c_exec_confirmation (*confirm_exec)(
        void *context, struct p6c_operation *operation);
    enum p6c_result (*wait_terminal)(
        void *context, struct p6c_operation *operation);
    enum p6c_result (*signal_term)(
        void *context, struct p6c_operation *operation);
    enum p6c_result (*wait_grace)(
        void *context, struct p6c_operation *operation);
    enum p6c_result (*freeze_cgroup)(
        void *context, struct p6c_operation *operation);
    enum p6c_result (*kill_cgroup)(
        void *context, struct p6c_operation *operation);
    enum p6c_result (*wait_cgroup_empty)(
        void *context, struct p6c_operation *operation);
    enum p6c_result (*observe_child)(
        void *context, struct p6c_operation *operation,
        int32_t *exit_status);
    enum p6c_result (*reap_child)(
        void *context, struct p6c_operation *operation);
    enum p6c_result (*finalize_transcripts)(
        void *context, struct p6c_operation *operation);
    enum p6c_result (*remove_cgroup)(
        void *context, struct p6c_operation *operation);
};

enum p6c_child_custody {
    P6C_CHILD_NONE = 0,
    P6C_CHILD_CGROUP_ONLY = 1,
    P6C_CHILD_PID_WAITABLE = 2,
    P6C_CHILD_PIDFD_OWNED = 3,
    P6C_CHILD_EXIT_OBSERVED = 4,
    P6C_CHILD_REAPED = 5
};

#ifdef P6C_TESTING
void p6c_test_service_process_adapter_set(
    const struct p6c_process_adapter *adapter);
#endif

struct p6c_operation {
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    struct p6c_journal *journal;
    struct p6c_executable *executable;
    struct p6c_owned_fd *cgroup;
    struct p6c_owned_pair *status_channel;
    struct p6c_owned_pair *stdout_channel;
    struct p6c_owned_pair *stderr_channel;
    struct p6c_owned_fd pidfd;
    pid_t child_pid;
    struct p6c_transcript *stdout_transcript;
    struct p6c_transcript *stderr_transcript;
    enum p6c_operation_state state;
    enum p6c_operation_state resume_state;
    int32_t exit_status;
    bool authority_retained;
    enum p6c_child_custody physical_custody;
};

enum p6c_result p6c_operation_init(
    struct p6c_operation *operation,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[static P6C_RECOVERY_TOKEN_BYTES],
    struct p6c_journal *journal,
    struct p6c_executable *executable,
    struct p6c_owned_fd *cgroup,
    struct p6c_owned_pair *status_channel,
    struct p6c_owned_pair *stdout_channel,
    struct p6c_owned_pair *stderr_channel,
    struct p6c_transcript *stdout_transcript,
    struct p6c_transcript *stderr_transcript);
enum p6c_result p6c_operation_start(
    struct p6c_operation *operation,
    const struct p6c_process_adapter *adapter);
enum p6c_result p6c_operation_stop(
    struct p6c_operation *operation,
    const struct p6c_process_adapter *adapter);
enum p6c_result p6c_operation_ack(
    struct p6c_operation *operation,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[static P6C_RECOVERY_TOKEN_BYTES]);

struct p6c_spawn_spec {
    char *const *argv;
    size_t argv_count;
    char *const *environment;
    size_t environment_count;
    const struct p6c_owned_fd *credential_directory;
    uint32_t exec_timeout_milliseconds;
};

enum p6c_result p6c_clone3_spawn(
    struct p6c_operation *operation,
    const struct p6c_spawn_spec *specification);
enum p6c_result p6c_classify_clone3_errno(int error_number);
enum p6c_exec_confirmation p6c_confirm_exec_status(
    struct p6c_operation *operation,
    uint32_t timeout_milliseconds);
enum p6c_result p6c_pidfd_signal(
    const struct p6c_owned_fd *pidfd, int signal_number);
enum p6c_result p6c_pidfd_observe(
    const struct p6c_owned_fd *pidfd, int32_t *exit_status);
enum p6c_result p6c_pidfd_reap(
    const struct p6c_owned_fd *pidfd);
enum p6c_result p6c_child_pid_observe(
    pid_t child_pid, int32_t *exit_status);
enum p6c_result p6c_child_pid_reap(pid_t child_pid);

enum p6c_result p6c_cgroup_freeze(
    const struct p6c_owned_fd *cgroup);
enum p6c_result p6c_cgroup_kill(
    const struct p6c_owned_fd *cgroup);
enum p6c_result p6c_cgroup_is_populated(
    const struct p6c_owned_fd *cgroup, bool *populated);
enum p6c_result p6c_cgroup_is_frozen(
    const struct p6c_owned_fd *cgroup, bool *frozen);
enum p6c_result p6c_cgroup_create(
    const struct p6c_owned_fd *root, const char *name,
    uid_t approved_owner, struct p6c_owned_fd *cgroup);
enum p6c_result p6c_cgroup_remove(
    const struct p6c_owned_fd *root, const char *name,
    struct p6c_owned_fd *cgroup);
enum p6c_result p6c_cgroup_quarantine_name(
    const char *name,
    char output[static P6C_CGROUP_QUARANTINE_NAME_BYTES]);
#ifdef P6C_TESTING
void p6c_test_cgroup_remove_substitution_set(
    int root_descriptor, const char *replacement_name,
    const char *displaced_name);
#endif

struct p6c_publication_item {
    const char *name;
    const uint8_t *content;
    size_t content_length;
    const char *candidate_identity;
};

struct p6c_publication_result {
    struct p6c_owned_fd staging_directory;
    struct p6c_owned_fd committed_directory;
    char staging_name[P6C_MAX_PUBLICATION_NAME + 1U];
    char generation_name[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
    uint8_t manifest_digest[P6C_SHA256_BYTES];
    bool renamed;
    bool verified;
    bool recovery_required;
};

enum p6c_result p6c_publish_bundle(
    const struct p6c_owned_fd *evidence_root,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    enum p6c_operation_state cleanup_state,
    const struct p6c_publication_item *items,
    size_t item_count,
    struct p6c_journal *journal,
    struct p6c_publication_result *result);
enum p6c_result p6c_publication_recover(
    const struct p6c_owned_fd *evidence_root,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    const uint8_t publication_identity[static P6C_SHA256_BYTES],
    const uint8_t manifest_digest[static P6C_SHA256_BYTES],
    struct p6c_publication_result *result);
enum p6c_result p6c_publication_close(
    struct p6c_publication_result *result);

struct p6c_service_config {
    struct p6c_owned_fd socket;
    struct p6c_owned_fd journal_root;
    struct p6c_owned_fd source_root;
    struct p6c_owned_fd cgroup_root;
    struct p6c_owned_fd evidence_root;
    uid_t controller_user;
};

enum p6c_result p6c_service_run(struct p6c_service_config *configuration);
enum p6c_result p6c_service_config_close(
    struct p6c_service_config *configuration);

#endif
