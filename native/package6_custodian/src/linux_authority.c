#include "p6c_types.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/openat2.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>


#define P6C_JOURNAL_NAME_BYTES ((size_t)41)
#define P6C_TRANSCRIPT_NAME_BYTES ((size_t)40)
#define P6C_CGROUP_RANDOM_BYTES ((size_t)16)
#define P6C_CGROUP_CREATE_ATTEMPTS UINT32_C(8)
#define P6C_PRODUCTION_EXEC_TIMEOUT_MS UINT32_C(5000)
#define P6C_PRODUCTION_RUN_ONCE_POLLS UINT32_C(50)
#define P6C_PRODUCTION_RUN_ONCE_POLL_MS UINT32_C(100)
#define P6C_PRODUCTION_STOP_GRACE_MS UINT32_C(100)
#define P6C_PRODUCTION_CGROUP_POLLS UINT32_C(500)
#define P6C_PRODUCTION_CGROUP_POLL_NS 10000000L
#define P6C_DEGRADED_BACKOFF_INITIAL_NS 1000000L
#define P6C_DEGRADED_BACKOFF_MAX_NS 100000000L
#define P6C_REPLAY_LEDGER_NAME ".p6c-replay-ledger"
#define P6C_REPLAY_LEDGER_VERSION UINT16_C(2)
#define P6C_REPLAY_HEADER_BYTES ((size_t)64)
#define P6C_REPLAY_RECORD_BYTES ((size_t)184)
#define P6C_REPLAY_LEDGER_BYTES                                      \
    (P6C_REPLAY_HEADER_BYTES +                                      \
     (P6C_REPLAY_CAPACITY * P6C_REPLAY_RECORD_BYTES))
#define P6C_REPLAY_HEADER_DIGEST_OFFSET ((size_t)32)
#define P6C_REPLAY_RECORD_OPCODE_OFFSET ((size_t)10)
#define P6C_REPLAY_RECORD_UID_OFFSET ((size_t)12)
#define P6C_REPLAY_RECORD_SEQUENCE_OFFSET ((size_t)16)
#define P6C_REPLAY_RECORD_REQUEST_ID_OFFSET ((size_t)24)
#define P6C_REPLAY_RECORD_REQUEST_DIGEST_OFFSET ((size_t)40)
#define P6C_REPLAY_RECORD_OPERATION_OFFSET ((size_t)72)
#define P6C_REPLAY_RECORD_COMMAND_OFFSET ((size_t)88)
#define P6C_REPLAY_RECORD_PRIOR_OFFSET ((size_t)120)
#define P6C_REPLAY_RECORD_DIGEST_OFFSET ((size_t)152)
#define P6C_MAX_RECEIVED_DESCRIPTORS ((size_t)16)
#ifdef P6C_TESTING
#define P6C_TEST_INPUT_COMPLETE ((ssize_t)-2)
#endif

struct p6c_service_entry {
    bool occupied;
    bool cgroup_allocated;
    uid_t opening_user;
    uint8_t request_digest[P6C_SHA256_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t publication_identity[P6C_SHA256_BYTES];
    uint8_t publication_digest[P6C_SHA256_BYTES];
    char journal_name[P6C_JOURNAL_NAME_BYTES];
    char cgroup_name[P6C_CGROUP_NAME_BYTES];
    char stdout_name[P6C_TRANSCRIPT_NAME_BYTES];
    char stderr_name[P6C_TRANSCRIPT_NAME_BYTES];
    char executable_path[P6C_MAX_STRING_BYTES + 1U];
    char *argv[P6C_MAX_ARGV_COUNT + 1U];
    char *environment[P6C_MAX_ENVIRONMENT_COUNT + 1U];
    char *authority_storage;
    struct p6c_owned_fd credential_directory;
    uint8_t credential_manifest[P6C_MAX_CREDENTIAL_MANIFEST_BYTES];
    size_t credential_manifest_size;
    struct p6c_journal journal;
    struct p6c_executable executable;
    struct p6c_owned_fd cgroup;
    struct p6c_owned_pair status_channel;
    struct p6c_owned_pair stdout_channel;
    struct p6c_owned_pair stderr_channel;
    struct p6c_transcript stdout_transcript;
    struct p6c_transcript stderr_transcript;
    struct p6c_operation operation;
    struct p6c_spawn_spec spawn;
    struct p6c_publication_result publication;
};

struct p6c_received_authority {
    int credential_directory;
    bool invalid;
};

struct p6c_service_tombstone {
    bool occupied;
    uid_t opening_user;
    uint8_t request_digest[P6C_SHA256_BYTES];
    uint8_t summary[P6C_OPERATION_SUMMARY_BYTES];
};

struct p6c_durable_replay_entry {
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uid_t controller_user;
    uint16_t opcode;
    uint8_t request_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t command_identity[P6C_SHA256_BYTES];
};

struct p6c_replay_ledger {
    struct p6c_owned_fd file;
    struct p6c_durable_replay_entry entries[P6C_REPLAY_CAPACITY];
    size_t count;
    uint8_t prior_digest[P6C_SHA256_BYTES];
};

struct p6c_service_registry {
    struct p6c_service_entry entries[P6C_MAX_OPERATIONS];
    struct p6c_service_tombstone tombstones[P6C_TOMBSTONE_CAPACITY];
    size_t count;
    size_t tombstone_count;
    bool start_blocked;
    struct p6c_replay_ledger replay;
    struct p6c_service_config *configuration;
};

struct p6c_production_context {
    struct p6c_service_registry *registry;
    struct p6c_service_entry *entry;
    bool terminal_observed;
};

enum p6c_disconnect_reason {
    P6C_DISCONNECT_RECEIVE_EOF = 0,
    P6C_DISCONNECT_RECEIVE_FAILURE = 1,
    P6C_DISCONNECT_SEND_FAILURE = 2
};

static void p6c_service_degraded_backoff(long *backoff_ns)
{
    struct timespec delay;
    int sleep_result;

    if (backoff_ns == NULL) {
        return;
    }
    delay.tv_sec = (time_t)0;
    delay.tv_nsec = *backoff_ns;
    do {
        sleep_result = nanosleep(&delay, &delay);
    } while ((sleep_result != 0) && (errno == EINTR));
    if (*backoff_ns < P6C_DEGRADED_BACKOFF_MAX_NS / 2L) {
        *backoff_ns *= 2L;
    } else {
        *backoff_ns = P6C_DEGRADED_BACKOFF_MAX_NS;
    }
}

static enum p6c_result p6c_service_cleanup_after_disconnect(
    struct p6c_service_registry *registry,
    enum p6c_disconnect_reason reason);


#ifdef P6C_TESTING
static enum p6c_failpoint p6c_current_failpoint = P6C_FAIL_NONE;
static unsigned int p6c_failpoint_successful_hits = 0U;
static bool p6c_peer_override_enabled = false;
static struct p6c_peer_identity p6c_peer_override;
static const uint8_t *p6c_service_test_input = NULL;
static size_t p6c_service_test_input_size = 0U;
static bool p6c_service_test_input_consumed = false;
static uint8_t *p6c_service_test_output = NULL;
static size_t p6c_service_test_output_capacity = 0U;
static size_t *p6c_service_test_output_size = NULL;
static bool p6c_service_test_adapter_enabled = false;
static struct p6c_process_adapter p6c_service_test_adapter;
static const uint8_t *const *p6c_service_test_inputs = NULL;
static const size_t *p6c_service_test_input_sizes = NULL;
static size_t p6c_service_test_input_count = 0U;
static size_t p6c_service_test_input_index = 0U;
static size_t *p6c_service_test_output_sizes = NULL;
static size_t *p6c_service_test_output_count = NULL;
static size_t p6c_service_test_output_offset = 0U;
static bool p6c_service_test_disconnect_after_input = false;
static bool p6c_service_test_token_enabled = false;
static uint8_t p6c_service_test_token[P6C_RECOVERY_TOKEN_BYTES];
static int p6c_test_exec_root = P6C_INVALID_DESCRIPTOR;
static char p6c_test_exec_approved[129];
static char p6c_test_exec_replacement[129];
static char p6c_test_exec_displaced[129];
static bool p6c_test_exec_replacement_pending = false;
#endif

static enum p6c_descriptor_type p6c_type_from_mode(
    mode_t mode, enum p6c_descriptor_type requested)
{
    if (S_ISREG(mode)) {
        return P6C_DESCRIPTOR_REGULAR;
    }
    if (S_ISDIR(mode)) {
        if (requested == P6C_DESCRIPTOR_CGROUP) {
            return P6C_DESCRIPTOR_CGROUP;
        }
        return P6C_DESCRIPTOR_DIRECTORY;
    }
    if (S_ISSOCK(mode)) {
        return P6C_DESCRIPTOR_SOCKET;
    }
    if (S_ISFIFO(mode)) {
        return P6C_DESCRIPTOR_PIPE;
    }
    return requested;
}

#ifdef P6C_TESTING
void p6c_test_failpoint_set(enum p6c_failpoint failpoint)
{
    p6c_current_failpoint = failpoint;
    p6c_failpoint_successful_hits = 0U;
}

void p6c_test_failpoint_set_after(
    enum p6c_failpoint failpoint, unsigned int successful_hits)
{
    p6c_current_failpoint = failpoint;
    p6c_failpoint_successful_hits = successful_hits;
}

bool p6c_failpoint_active(enum p6c_failpoint failpoint)
{
    if (failpoint == P6C_FAIL_NONE) {
        return false;
    }
    if (p6c_current_failpoint != failpoint) {
        return false;
    }
    if (p6c_failpoint_successful_hits != 0U) {
        --p6c_failpoint_successful_hits;
        return false;
    }
    return true;
}

void p6c_test_exec_replacement_set(
    int root_descriptor, const char *approved_name,
    const char *replacement_name, const char *displaced_name)
{
    p6c_test_exec_root = P6C_INVALID_DESCRIPTOR;
    p6c_test_exec_replacement_pending = false;
    memset(p6c_test_exec_approved, 0,
           sizeof(p6c_test_exec_approved));
    memset(p6c_test_exec_replacement, 0,
           sizeof(p6c_test_exec_replacement));
    memset(p6c_test_exec_displaced, 0,
           sizeof(p6c_test_exec_displaced));
    if ((root_descriptor < 0) || (approved_name == NULL) ||
        (replacement_name == NULL) || (displaced_name == NULL) ||
        (strnlen(approved_name, sizeof(p6c_test_exec_approved)) >=
         sizeof(p6c_test_exec_approved)) ||
        (strnlen(replacement_name,
                 sizeof(p6c_test_exec_replacement)) >=
         sizeof(p6c_test_exec_replacement)) ||
        (strnlen(displaced_name, sizeof(p6c_test_exec_displaced)) >=
         sizeof(p6c_test_exec_displaced))) {
        return;
    }
    p6c_test_exec_root = root_descriptor;
    (void)strcpy(p6c_test_exec_approved, approved_name);
    (void)strcpy(p6c_test_exec_replacement, replacement_name);
    (void)strcpy(p6c_test_exec_displaced, displaced_name);
    p6c_test_exec_replacement_pending = true;
}

void p6c_test_exec_hash_observe(void)
{
    if (!p6c_test_exec_replacement_pending) {
        return;
    }
    p6c_test_exec_replacement_pending = false;
    if (renameat(
            p6c_test_exec_root, p6c_test_exec_approved,
            p6c_test_exec_root, p6c_test_exec_displaced) != 0) {
        return;
    }
    (void)renameat(
        p6c_test_exec_root, p6c_test_exec_replacement,
        p6c_test_exec_root, p6c_test_exec_approved);
}
#endif

void p6c_owned_fd_reset(struct p6c_owned_fd *owner)
{
    if (owner == NULL) {
        return;
    }
    memset(owner, 0, sizeof(*owner));
    owner->descriptor = P6C_INVALID_DESCRIPTOR;
    owner->lifecycle = P6C_DESCRIPTOR_EMPTY;
}

bool p6c_owned_fd_is_live(const struct p6c_owned_fd *owner)
{
    return (owner != NULL) &&
           ((owner->lifecycle == P6C_DESCRIPTOR_OWNED) ||
            (owner->lifecycle == P6C_DESCRIPTOR_RECOVERY)) &&
           (owner->descriptor >= 0);
}

enum p6c_result p6c_owned_fd_acquire(
    struct p6c_owned_fd *owner, int descriptor,
    enum p6c_descriptor_type type)
{
    struct stat status;

    if ((owner == NULL) || (descriptor < 0) ||
        ((owner->lifecycle != P6C_DESCRIPTOR_EMPTY) &&
         (owner->descriptor != P6C_INVALID_DESCRIPTOR))) {
        return P6C_RESULT_INVALID;
    }
    p6c_owned_fd_reset(owner);
    owner->descriptor = descriptor;
    owner->type = type;
    owner->lifecycle = P6C_DESCRIPTOR_OWNED;
    if (fstat(descriptor, &status) != 0) {
        owner->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    owner->device = status.st_dev;
    owner->inode = status.st_ino;
    owner->mode = status.st_mode;
    owner->type = p6c_type_from_mode(status.st_mode, type);
    return P6C_RESULT_OK;
}

enum p6c_result p6c_owned_fd_close(struct p6c_owned_fd *owner)
{
    struct stat status;

    if (!p6c_owned_fd_is_live(owner)) {
        return P6C_RESULT_INVALID;
    }
    if (fstat(owner->descriptor, &status) != 0) {
        owner->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        owner->closure_proven = false;
        return (errno == EBADF) ? P6C_RESULT_STALE :
                                 P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((status.st_dev != owner->device) ||
        (status.st_ino != owner->inode) ||
        ((status.st_mode & S_IFMT) != (owner->mode & S_IFMT))) {
        owner->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        owner->closure_proven = false;
        return P6C_RESULT_STALE;
    }
    if (close(owner->descriptor) != 0) {
        owner->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        owner->closure_proven = false;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    owner->descriptor = P6C_INVALID_DESCRIPTOR;
    owner->lifecycle = P6C_DESCRIPTOR_CLOSED;
    owner->closure_proven = true;
    return P6C_RESULT_OK;
}

enum p6c_result p6c_owned_pair_close(struct p6c_owned_pair *pair)
{
    enum p6c_result first_result = P6C_RESULT_OK;
    enum p6c_result second_result = P6C_RESULT_OK;

    if (pair == NULL) {
        return P6C_RESULT_INVALID;
    }
    if (p6c_owned_fd_is_live(&pair->first)) {
        first_result = p6c_owned_fd_close(&pair->first);
    }
    if (p6c_owned_fd_is_live(&pair->second)) {
        second_result = p6c_owned_fd_close(&pair->second);
    }
    if ((first_result != P6C_RESULT_OK) ||
        (second_result != P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

static void p6c_owned_pipe_abort(
    struct p6c_owned_pair *pair, int descriptors[static 2])
{
    if (descriptors[0] >= 0) {
        (void)close(descriptors[0]);
        descriptors[0] = P6C_INVALID_DESCRIPTOR;
    }
    if (descriptors[1] >= 0) {
        (void)close(descriptors[1]);
        descriptors[1] = P6C_INVALID_DESCRIPTOR;
    }
    p6c_owned_fd_reset(&pair->first);
    p6c_owned_fd_reset(&pair->second);
}

enum p6c_result p6c_owned_pipe_create(struct p6c_owned_pair *pair)
{
    int descriptors[2] = {
        P6C_INVALID_DESCRIPTOR, P6C_INVALID_DESCRIPTOR
    };
    struct stat identities[2];
    int read_flags;

    if (pair == NULL) {
        return P6C_RESULT_INVALID;
    }
    p6c_owned_fd_reset(&pair->first);
    p6c_owned_fd_reset(&pair->second);
    if (pipe2(descriptors, O_CLOEXEC) != 0) {
        return P6C_RESULT_SYSTEM;
    }
    if (p6c_failpoint_active(P6C_FAIL_PAIR_FIRST_ACQUIRE) ||
        p6c_failpoint_active(P6C_FAIL_PAIR_FIRST_FSTAT) ||
        (fstat(descriptors[0], &identities[0]) != 0)) {
        p6c_owned_pipe_abort(pair, descriptors);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (p6c_failpoint_active(P6C_FAIL_PAIR_SECOND_ACQUIRE) ||
        p6c_failpoint_active(P6C_FAIL_PAIR_SECOND_FSTAT) ||
        (fstat(descriptors[1], &identities[1]) != 0) ||
        !S_ISFIFO(identities[0].st_mode) ||
        !S_ISFIFO(identities[1].st_mode)) {
        p6c_owned_pipe_abort(pair, descriptors);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    read_flags = p6c_failpoint_active(P6C_FAIL_PAIR_GETFL) ?
                     -1 :
                     fcntl(descriptors[0], F_GETFL);
    if ((read_flags < 0) ||
        p6c_failpoint_active(P6C_FAIL_PAIR_SETFL) ||
        (fcntl(
             descriptors[0], F_SETFL,
             read_flags | O_NONBLOCK) != 0)) {
        p6c_owned_pipe_abort(pair, descriptors);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    pair->first.descriptor = descriptors[0];
    pair->first.device = identities[0].st_dev;
    pair->first.inode = identities[0].st_ino;
    pair->first.type = P6C_DESCRIPTOR_PIPE;
    pair->first.mode = identities[0].st_mode;
    pair->first.lifecycle = P6C_DESCRIPTOR_OWNED;
    pair->first.closure_proven = false;
    pair->second.descriptor = descriptors[1];
    pair->second.device = identities[1].st_dev;
    pair->second.inode = identities[1].st_ino;
    pair->second.type = P6C_DESCRIPTOR_PIPE;
    pair->second.mode = identities[1].st_mode;
    pair->second.lifecycle = P6C_DESCRIPTOR_OWNED;
    pair->second.closure_proven = false;
    descriptors[0] = P6C_INVALID_DESCRIPTOR;
    descriptors[1] = P6C_INVALID_DESCRIPTOR;
    return P6C_RESULT_OK;
}

enum p6c_result p6c_openat2_owned(
    const struct p6c_owned_fd *root, const char *relative_path,
    int flags, mode_t mode, enum p6c_descriptor_type type,
    struct p6c_owned_fd *output)
{
    struct open_how how;
    size_t path_length;
    int descriptor;
    enum p6c_result result;

    if ((root == NULL) || (relative_path == NULL) || (output == NULL) ||
        !p6c_owned_fd_is_live(root) ||
        ((root->type != P6C_DESCRIPTOR_DIRECTORY) &&
         (root->type != P6C_DESCRIPTOR_CGROUP))) {
        return P6C_RESULT_INVALID;
    }
    path_length = strnlen(relative_path, (size_t)PATH_MAX + 1U);
    if ((path_length == 0U) || (path_length > (size_t)PATH_MAX) ||
        (relative_path[0] == '/') ||
        ((path_length == 1U) && (relative_path[0] == '.'))) {
        return P6C_RESULT_UNSAFE;
    }
    memset(&how, 0, sizeof(how));
    how.flags = (uint64_t)(unsigned int)(flags | O_CLOEXEC);
    how.mode = (uint64_t)mode;
    how.resolve = (uint64_t)(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS |
                             RESOLVE_NO_MAGICLINKS);
    p6c_owned_fd_reset(output);
    descriptor = (int)syscall(SYS_openat2, root->descriptor, relative_path,
                              &how, sizeof(how));
    if (descriptor < 0) {
        if ((errno == ENOSYS) || (errno == EINVAL) || (errno == E2BIG)) {
            return P6C_RESULT_UNSUPPORTED;
        }
        return ((errno == ELOOP) || (errno == EXDEV)) ?
                   P6C_RESULT_UNSAFE :
                   P6C_RESULT_SYSTEM;
    }
    result = p6c_owned_fd_acquire(output, descriptor, type);
    return result;
}

enum p6c_result p6c_authenticate_peer(
    const struct p6c_owned_fd *socket_owner,
    uid_t approved_user,
    struct p6c_peer_identity *peer)
{
    struct ucred credentials;
    socklen_t size = (socklen_t)sizeof(credentials);
    int socket_type = 0;
    socklen_t type_size = (socklen_t)sizeof(socket_type);

    if ((socket_owner == NULL) || (peer == NULL) ||
        !p6c_owned_fd_is_live(socket_owner) ||
        (socket_owner->type != P6C_DESCRIPTOR_SOCKET)) {
        return P6C_RESULT_INVALID;
    }
#ifdef P6C_TESTING
    if (p6c_peer_override_enabled) {
        if (p6c_peer_override.user_id != approved_user) {
            return P6C_RESULT_UNAUTHORIZED;
        }
        *peer = p6c_peer_override;
        return P6C_RESULT_OK;
    }
#endif
    if ((getsockopt(socket_owner->descriptor, SOL_SOCKET, SO_TYPE,
                    &socket_type, &type_size) != 0) ||
        (type_size != (socklen_t)sizeof(socket_type)) ||
        (socket_type != SOCK_SEQPACKET)) {
        return P6C_RESULT_UNSAFE;
    }
    memset(&credentials, 0, sizeof(credentials));
    if ((getsockopt(socket_owner->descriptor, SOL_SOCKET, SO_PEERCRED,
                    &credentials, &size) != 0) ||
        (size != (socklen_t)sizeof(credentials))) {
        return P6C_RESULT_UNAUTHORIZED;
    }
    if (credentials.uid != approved_user) {
        return P6C_RESULT_UNAUTHORIZED;
    }
    peer->process_id = credentials.pid;
    peer->user_id = credentials.uid;
    peer->group_id = credentials.gid;
    return P6C_RESULT_OK;
}

#ifdef P6C_TESTING
void p6c_test_peer_override_set(
    bool enabled, const struct p6c_peer_identity *peer)
{
    p6c_peer_override_enabled = enabled;
    memset(&p6c_peer_override, 0, sizeof(p6c_peer_override));
    if (enabled && (peer != NULL)) {
        p6c_peer_override = *peer;
    }
}

void p6c_test_service_io_set(
    const uint8_t *input, size_t input_size,
    uint8_t *output, size_t output_capacity, size_t *output_size)
{
    p6c_service_test_inputs = NULL;
    p6c_service_test_input_sizes = NULL;
    p6c_service_test_input_count = 0U;
    p6c_service_test_input_index = 0U;
    p6c_service_test_output_sizes = NULL;
    p6c_service_test_output_count = NULL;
    p6c_service_test_output_offset = 0U;
    p6c_service_test_input = input;
    p6c_service_test_input_size = input_size;
    p6c_service_test_input_consumed = false;
    p6c_service_test_output = output;
    p6c_service_test_output_capacity = output_capacity;
    p6c_service_test_output_size = output_size;
    if (output_size != NULL) {
        *output_size = 0U;
    }
}

void p6c_test_service_io_set_packets(
    const uint8_t *const *inputs, const size_t *input_sizes,
    size_t input_count, uint8_t *output, size_t output_capacity,
    size_t *output_sizes, size_t *output_count)
{
    p6c_service_test_input = NULL;
    p6c_service_test_input_size = 0U;
    p6c_service_test_input_consumed = false;
    p6c_service_test_output_size = NULL;
    p6c_service_test_inputs = inputs;
    p6c_service_test_input_sizes = input_sizes;
    p6c_service_test_input_count = input_count;
    p6c_service_test_input_index = 0U;
    p6c_service_test_output = output;
    p6c_service_test_output_capacity = output_capacity;
    p6c_service_test_output_sizes = output_sizes;
    p6c_service_test_output_count = output_count;
    p6c_service_test_output_offset = 0U;
    if (output_count != NULL) {
        *output_count = 0U;
    }
}

void p6c_test_service_disconnect_after_input(bool enabled)
{
    p6c_service_test_disconnect_after_input = enabled;
}

void p6c_test_recovery_token_set(
    const uint8_t token[static P6C_RECOVERY_TOKEN_BYTES])
{
    memcpy(p6c_service_test_token, token, P6C_RECOVERY_TOKEN_BYTES);
    p6c_service_test_token_enabled = true;
}

void p6c_test_service_process_adapter_set(
    const struct p6c_process_adapter *adapter)
{
    memset(&p6c_service_test_adapter, 0,
           sizeof(p6c_service_test_adapter));
    p6c_service_test_adapter_enabled = adapter != NULL;
    if (adapter == NULL) {
        p6c_service_test_token_enabled = false;
        memset(p6c_service_test_token, 0,
               sizeof(p6c_service_test_token));
    }
    if (adapter != NULL) {
        p6c_service_test_adapter = *adapter;
    }
}
#endif

void p6c_replay_table_init(struct p6c_replay_table *table)
{
    if (table != NULL) {
        memset(table, 0, sizeof(*table));
    }
}

enum p6c_replay_result p6c_replay_check(
    struct p6c_replay_table *table,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    const uint8_t request_digest[static P6C_SHA256_BYTES],
    const struct p6c_peer_identity *peer)
{
    size_t index;
    size_t free_index = P6C_REPLAY_CAPACITY;

    if ((table == NULL) || (operation_id == NULL) ||
        (request_digest == NULL) || (peer == NULL)) {
        return P6C_REPLAY_FULL;
    }
    for (index = 0U; index < P6C_REPLAY_CAPACITY; ++index) {
        struct p6c_replay_entry *entry = &table->entries[index];

        if (!entry->occupied) {
            if (free_index == P6C_REPLAY_CAPACITY) {
                free_index = index;
            }
            continue;
        }
        if (memcmp(entry->operation_id, operation_id,
                   P6C_OPERATION_ID_BYTES) != 0) {
            continue;
        }
        if ((entry->peer.process_id != peer->process_id) ||
            (entry->peer.user_id != peer->user_id) ||
            (entry->peer.group_id != peer->group_id)) {
            return P6C_REPLAY_DIFFERENT_PEER;
        }
        if (memcmp(entry->request_digest, request_digest,
                   P6C_SHA256_BYTES) != 0) {
            return P6C_REPLAY_DIGEST_MISMATCH;
        }
        return P6C_REPLAY_IDENTICAL;
    }
    if (free_index == P6C_REPLAY_CAPACITY) {
        return P6C_REPLAY_FULL;
    }
    table->entries[free_index].occupied = true;
    memcpy(table->entries[free_index].operation_id, operation_id,
           P6C_OPERATION_ID_BYTES);
    memcpy(table->entries[free_index].request_digest, request_digest,
           P6C_SHA256_BYTES);
    table->entries[free_index].peer = *peer;
    return P6C_REPLAY_NEW;
}

enum p6c_result p6c_public_error_set(
    struct p6c_public_error *error,
    enum p6c_public_status status,
    const char *public_code,
    bool retryable,
    enum p6c_operation_state operation_state,
    const uint8_t recovery_token[static P6C_RECOVERY_TOKEN_BYTES])
{
    size_t length;
    size_t index;

    if ((error == NULL) || (public_code == NULL) ||
        (recovery_token == NULL) ||
        (status < P6C_STATUS_OK) || (status > P6C_STATUS_INTERNAL) ||
        (operation_state < P6C_OPERATION_ABSENT) ||
        (operation_state > P6C_OPERATION_RECOVERY_REQUIRED)) {
        return P6C_RESULT_INVALID;
    }
    length = strnlen(public_code, (size_t)P6C_MAX_PUBLIC_CODE_BYTES + 1U);
    if ((length == 0U) ||
        (length > (size_t)P6C_MAX_PUBLIC_CODE_BYTES)) {
        return P6C_RESULT_INVALID;
    }
    for (index = 0U; index < length; ++index) {
        unsigned char character = (unsigned char)public_code[index];

        if (!(((character >= (unsigned char)'A') &&
               (character <= (unsigned char)'Z')) ||
              ((character >= (unsigned char)'0') &&
               (character <= (unsigned char)'9')) ||
              (character == (unsigned char)'_'))) {
            return P6C_RESULT_INVALID;
        }
    }
    memset(error, 0, sizeof(*error));
    error->status = status;
    memcpy(error->public_code, public_code, length);
    error->public_code[length] = '\0';
    error->retryable = retryable;
    error->operation_state = operation_state;
    memcpy(error->recovery_token, recovery_token,
           P6C_RECOVERY_TOKEN_BYTES);
    return P6C_RESULT_OK;
}

static int p6c_timespec_equal(struct timespec left, struct timespec right)
{
    return (left.tv_sec == right.tv_sec) &&
           (left.tv_nsec == right.tv_nsec);
}

static enum p6c_result p6c_executable_fail(
    struct p6c_executable *executable, enum p6c_result result,
    bool retain)
{
    if ((executable != NULL) && p6c_owned_fd_is_live(&executable->file)) {
        if (retain) {
            executable->file.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (p6c_owned_fd_close(&executable->file) != P6C_RESULT_OK) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
    }
    return result;
}

enum p6c_result p6c_pin_executable(
    const struct p6c_owned_fd *source_root,
    const char *relative_path,
    uid_t approved_owner,
    const uint8_t expected_digest[static P6C_SHA256_BYTES],
    struct p6c_executable *executable)
{
    static const uint8_t ELF_MAGIC[4] = {
        UINT8_C(0x7f), UINT8_C('E'), UINT8_C('L'), UINT8_C('F')
    };
    struct stat before;
    struct stat after;
    struct p6c_owned_fd named_file;
    uint8_t magic[sizeof(ELF_MAGIC)];
    ssize_t magic_size;
    enum p6c_result result;

    if ((source_root == NULL) || (relative_path == NULL) ||
        (expected_digest == NULL) || (executable == NULL)) {
        return P6C_RESULT_INVALID;
    }
    memset(executable, 0, sizeof(*executable));
    p6c_owned_fd_reset(&executable->file);
    result = p6c_openat2_owned(
        source_root, relative_path, O_RDONLY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_REGULAR, &executable->file);
    if (result != P6C_RESULT_OK) {
        if (result == P6C_RESULT_SYSTEM) {
            return P6C_RESULT_UNSAFE;
        }
        return result;
    }
    if (fstat(executable->file.descriptor, &before) != 0) {
        return p6c_executable_fail(
            executable, P6C_RESULT_RECOVERY_REQUIRED, true);
    }
    if (!S_ISREG(before.st_mode) || (before.st_nlink != 1) ||
        (before.st_uid != approved_owner) ||
        ((before.st_mode & (S_IWGRP | S_IWOTH)) != 0) ||
        ((before.st_mode & (S_IXUSR | S_IXGRP | S_IXOTH)) == 0) ||
        ((before.st_mode & (S_ISUID | S_ISGID)) != 0)) {
        return p6c_executable_fail(executable, P6C_RESULT_UNSAFE, false);
    }
    do {
        magic_size = pread(executable->file.descriptor, magic,
                           sizeof(magic), (off_t)0);
    } while ((magic_size < 0) && (errno == EINTR));
    if ((magic_size != (ssize_t)sizeof(magic)) ||
        (memcmp(magic, ELF_MAGIC, sizeof(magic)) != 0)) {
        return p6c_executable_fail(executable, P6C_RESULT_UNSAFE, false);
    }
    result = p6c_sha256_fd(&executable->file, executable->digest);
    if (result != P6C_RESULT_OK) {
        return p6c_executable_fail(
            executable, P6C_RESULT_RECOVERY_REQUIRED, true);
    }
    if (fstat(executable->file.descriptor, &after) != 0) {
        return p6c_executable_fail(
            executable, P6C_RESULT_RECOVERY_REQUIRED, true);
    }
    if ((before.st_dev != after.st_dev) ||
        (before.st_ino != after.st_ino) ||
        (before.st_size != after.st_size) ||
        (before.st_nlink != after.st_nlink) ||
        (before.st_uid != after.st_uid) ||
        (before.st_mode != after.st_mode) ||
        !p6c_timespec_equal(before.st_mtim, after.st_mtim) ||
        !p6c_timespec_equal(before.st_ctim, after.st_ctim) ||
        (memcmp(executable->digest, expected_digest,
                P6C_SHA256_BYTES) != 0)) {
        return p6c_executable_fail(executable, P6C_RESULT_UNSAFE, false);
    }
    result = p6c_openat2_owned(
        source_root, relative_path, O_RDONLY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_REGULAR, &named_file);
    if (result != P6C_RESULT_OK) {
        return p6c_executable_fail(
            executable, P6C_RESULT_UNSAFE, false);
    }
    if ((named_file.device != after.st_dev) ||
        (named_file.inode != after.st_ino) ||
        ((named_file.mode & S_IFMT) != (after.st_mode & S_IFMT))) {
        if (p6c_owned_fd_close(&named_file) != P6C_RESULT_OK) {
            return p6c_executable_fail(
                executable, P6C_RESULT_RECOVERY_REQUIRED, true);
        }
        return p6c_executable_fail(
            executable, P6C_RESULT_UNSAFE, false);
    }
    if (p6c_owned_fd_close(&named_file) != P6C_RESULT_OK) {
        return p6c_executable_fail(
            executable, P6C_RESULT_RECOVERY_REQUIRED, true);
    }
    executable->device = before.st_dev;
    executable->inode = before.st_ino;
    executable->size = before.st_size;
    executable->modification_time = before.st_mtim;
    executable->status_time = before.st_ctim;
    executable->owner = before.st_uid;
    executable->mode = before.st_mode;
    return P6C_RESULT_OK;
}

enum p6c_result p6c_executable_close(struct p6c_executable *executable)
{
    if (executable == NULL) {
        return P6C_RESULT_INVALID;
    }
    if (!p6c_owned_fd_is_live(&executable->file)) {
        return P6C_RESULT_OK;
    }
    return p6c_owned_fd_close(&executable->file);
}

static enum p6c_result p6c_validate_exec_vector(
    char *const vector[], size_t maximum, bool environment)
{
    size_t index;

    if (vector == NULL) {
        return P6C_RESULT_INVALID;
    }
    for (index = 0U; index <= maximum; ++index) {
        size_t length;

        if (vector[index] == NULL) {
            if ((!environment && (index == 0U)) || (index > maximum)) {
                return P6C_RESULT_INVALID;
            }
            return P6C_RESULT_OK;
        }
        length = strnlen(vector[index], (size_t)P6C_MAX_STRING_BYTES + 1U);
        if ((length == 0U) ||
            (length > (size_t)P6C_MAX_STRING_BYTES)) {
            return P6C_RESULT_LIMIT;
        }
        if (environment &&
            ((strcmp(vector[index], "LIVE_EXECUTION=1") == 0) ||
             (strcmp(vector[index], "LIVE_TRADING=1") == 0))) {
            return P6C_RESULT_UNSAFE;
        }
    }
    return P6C_RESULT_LIMIT;
}

static int p6c_environment_hex_digest(const char *value)
{
    size_t index;

    if ((value == NULL) || (strlen(value) != P6C_SHA256_BYTES * 2U)) {
        return 0;
    }
    for (index = 0U; index < P6C_SHA256_BYTES * 2U; ++index) {
        unsigned char character = (unsigned char)value[index];

        if (!(((character >= (unsigned char)'0') &&
               (character <= (unsigned char)'9')) ||
              ((character >= (unsigned char)'a') &&
               (character <= (unsigned char)'f')))) {
            return 0;
        }
    }
    return 1;
}

static int p6c_environment_absolute_path(const char *value)
{
    size_t index;
    size_t length;

    if ((value == NULL) || (value[0] != '/')) {
        return 0;
    }
    length = strlen(value);
    if ((length < 2U) || (length > (size_t)P6C_MAX_STRING_BYTES)) {
        return 0;
    }
    for (index = 0U; index < length; ++index) {
        unsigned char character = (unsigned char)value[index];

        if ((character < UINT8_C(0x21)) ||
            (character > UINT8_C(0x7e)) ||
            (character == (unsigned char)'=')) {
            return 0;
        }
        if ((character == (unsigned char)'/') &&
            (index + 1U < length) &&
            (value[index + 1U] == '/')) {
            return 0;
        }
    }
    if ((strstr(value, "/../") != NULL) ||
        (strstr(value, "/./") != NULL) ||
        (length >= 3U &&
         (strcmp(&value[length - 3U], "/..") == 0)) ||
        (length >= 2U &&
         (strcmp(&value[length - 2U], "/.") == 0))) {
        return 0;
    }
    return 1;
}

static enum p6c_result p6c_validate_live_environment_count(
    char *const environment[], size_t count)
{
    static const char *const REQUIRED[] = {
        "HOME=/tmp",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "LIVE_EXECUTION_ENABLED=false",
        "LIVE_TRADING_APPROVED=false",
        "LIVE_TRADING_ENABLED=false",
        "PATH=/usr/bin:/bin"
    };
    bool approval = false;
    bool credentials = false;
    bool fixture = false;
    bool staging_activation = false;
    bool staging_authority = false;
    bool staging_scope = false;
    bool trading_mode = false;
    bool timezone = false;
    size_t index;
    size_t required_index;

    if ((environment == NULL) ||
        ((count != 13U) && (count != 14U) && (count != 15U))) {
        return P6C_RESULT_UNSAFE;
    }
    for (index = 1U; index < count; ++index) {
        if (strcmp(environment[index - 1U], environment[index]) >= 0) {
            return P6C_RESULT_UNSAFE;
        }
    }
    for (required_index = 0U;
         required_index < sizeof(REQUIRED) / sizeof(REQUIRED[0]);
         ++required_index) {
        bool found = false;

        for (index = 0U; index < count; ++index) {
            if (strcmp(environment[index], REQUIRED[required_index]) == 0) {
                found = true;
                break;
            }
        }
        if (!found) {
            return P6C_RESULT_UNSAFE;
        }
    }
    for (index = 0U; index < count; ++index) {
        const char *item = environment[index];
        const char *value;

        if (strncmp(
                item, "TRADING_PACKAGE6_APPROVAL_SHA256=", 33U) == 0) {
            value = &item[33];
            if (approval || !p6c_environment_hex_digest(value)) {
                return P6C_RESULT_UNSAFE;
            }
            approval = true;
        } else if (strncmp(
                       item,
                       "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH=",
                       41U) == 0) {
            value = &item[41];
            if (staging_activation ||
                !p6c_environment_absolute_path(value)) {
                return P6C_RESULT_UNSAFE;
            }
            staging_activation = true;
        } else if (strncmp(
                       item,
                       "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH=",
                       40U) == 0) {
            value = &item[40];
            if (staging_authority ||
                !p6c_environment_absolute_path(value)) {
                return P6C_RESULT_UNSAFE;
            }
            staging_authority = true;
        } else if (strncmp(
                       item,
                       "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH=",
                       40U) == 0) {
            value = &item[40];
            if (fixture || !p6c_environment_absolute_path(value)) {
                return P6C_RESULT_UNSAFE;
            }
            fixture = true;
        } else if (strcmp(
                       item,
                       "TRADING_PACKAGE6_STAGING_SCOPE="
                       "PACKAGE6_STAGING_V2") == 0) {
            if (staging_scope) {
                return P6C_RESULT_UNSAFE;
            }
            staging_scope = true;
        } else if (strcmp(item, "TRADING_MODE=paper") == 0) {
            if (trading_mode) {
                return P6C_RESULT_UNSAFE;
            }
            trading_mode = true;
        } else if (strcmp(item, "TZ=UTC") == 0) {
            if (timezone) {
                return P6C_RESULT_UNSAFE;
            }
            timezone = true;
        } else if (strcmp(
                       item,
                       "CREDENTIALS_DIRECTORY=/proc/self/fd/5") == 0) {
            if (credentials) {
                return P6C_RESULT_UNSAFE;
            }
            credentials = true;
        } else {
            bool required = false;

            for (required_index = 0U;
                 required_index <
                     sizeof(REQUIRED) / sizeof(REQUIRED[0]);
                 ++required_index) {
                if (strcmp(item, REQUIRED[required_index]) == 0) {
                    required = true;
                    break;
                }
            }
            if (!required) {
                return P6C_RESULT_UNSAFE;
            }
        }
    }
    if (!approval || !staging_activation || !staging_authority ||
        !staging_scope || !trading_mode || !timezone ||
        (count != 13U + (credentials ? 1U : 0U) +
                      (fixture ? 1U : 0U))) {
        return P6C_RESULT_UNSAFE;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_validate_live_environment(
    char *const environment[])
{
    size_t count = 0U;

    if (environment == NULL) {
        return P6C_RESULT_INVALID;
    }
    while ((count <= (size_t)P6C_MAX_ENVIRONMENT_COUNT) &&
           (environment[count] != NULL)) {
        ++count;
    }
    if (count > (size_t)P6C_MAX_ENVIRONMENT_COUNT) {
        return P6C_RESULT_LIMIT;
    }
    return p6c_validate_live_environment_count(environment, count);
}

enum p6c_result p6c_execve_pinned(
    const struct p6c_executable *executable,
    char *const argv[], char *const environment[])
{
    struct stat status;
    enum p6c_result result;

    if ((executable == NULL) ||
        !p6c_owned_fd_is_live(&executable->file)) {
        return P6C_RESULT_INVALID;
    }
    result = p6c_validate_exec_vector(
        argv, (size_t)P6C_MAX_ARGV_COUNT, false);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    result = p6c_validate_exec_vector(
        environment, (size_t)P6C_MAX_ENVIRONMENT_COUNT, true);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    result = p6c_validate_live_environment(environment);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    if ((fstat(executable->file.descriptor, &status) != 0) ||
        (status.st_dev != executable->device) ||
        (status.st_ino != executable->inode) ||
        (status.st_size != executable->size) ||
        (status.st_uid != executable->owner) ||
        (status.st_mode != executable->mode) ||
        !p6c_timespec_equal(status.st_mtim,
                            executable->modification_time) ||
        !p6c_timespec_equal(status.st_ctim, executable->status_time)) {
        return P6C_RESULT_STALE;
    }
    (void)syscall(SYS_execveat, executable->file.descriptor, "", argv,
                  environment, AT_EMPTY_PATH);
    return P6C_RESULT_SYSTEM;
}

static uint32_t p6c_service_load_u32(const uint8_t input[static 4])
{
    return ((uint32_t)input[0] << 24) |
           ((uint32_t)input[1] << 16) |
           ((uint32_t)input[2] << 8) |
           (uint32_t)input[3];
}

static uint64_t p6c_service_load_u64(const uint8_t input[static 8])
{
    uint64_t value = UINT64_C(0);
    size_t index;

    for (index = 0U; index < 8U; ++index) {
        value = (value << 8) | (uint64_t)input[index];
    }
    return value;
}

static void p6c_service_store_u64(
    uint8_t output[static 8], uint64_t value)
{
    size_t index;

    for (index = 0U; index < 8U; ++index) {
        output[7U - index] =
            (uint8_t)(value >> (index * 8U));
    }
}

static void p6c_service_hex(
    const uint8_t *input, size_t input_size, char *output)
{
    static const char HEX[] = "0123456789abcdef";
    size_t index;

    for (index = 0U; index < input_size; ++index) {
        output[index * 2U] = HEX[input[index] >> 4];
        output[(index * 2U) + 1U] =
            HEX[input[index] & UINT8_C(0x0f)];
    }
    output[input_size * 2U] = '\0';
}

static int p6c_service_cgroup_name_valid(const char *name)
{
    size_t index;

    if ((name == NULL) ||
        (strnlen(name, P6C_CGROUP_NAME_BYTES) !=
         P6C_CGROUP_NAME_BYTES - 1U) ||
        (memcmp(name, "p6c-", 4U) != 0)) {
        return 0;
    }
    for (index = 4U; index < P6C_CGROUP_NAME_BYTES - 1U; ++index) {
        unsigned char character = (unsigned char)name[index];

        if (!(((character >= (unsigned char)'0') &&
               (character <= (unsigned char)'9')) ||
              ((character >= (unsigned char)'a') &&
               (character <= (unsigned char)'f')))) {
            return 0;
        }
    }
    return 1;
}

static enum p6c_result p6c_service_random_cgroup_name(
    char name[static P6C_CGROUP_NAME_BYTES])
{
    uint8_t random_bytes[P6C_CGROUP_RANDOM_BYTES];
    size_t offset = 0U;

    while (offset < sizeof(random_bytes)) {
        ssize_t amount = (ssize_t)syscall(
            SYS_getrandom, &random_bytes[offset],
            sizeof(random_bytes) - offset, 0U);

        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            return P6C_RESULT_SYSTEM;
        }
        if (amount == 0) {
            return P6C_RESULT_SYSTEM;
        }
        offset += (size_t)amount;
    }
    memcpy(name, "p6c-", 4U);
    p6c_service_hex(
        random_bytes, sizeof(random_bytes), &name[4]);
    return P6C_RESULT_OK;
}

static int p6c_service_hex_value(unsigned char character)
{
    if ((character >= (unsigned char)'0') &&
        (character <= (unsigned char)'9')) {
        return (int)(character - (unsigned char)'0');
    }
    if ((character >= (unsigned char)'a') &&
        (character <= (unsigned char)'f')) {
        return (int)(character - (unsigned char)'a') + 10;
    }
    return -1;
}

static int p6c_service_parse_journal_name(
    const char *name,
    uint8_t operation_id[static P6C_OPERATION_ID_BYTES])
{
    static const char SUFFIX[] = ".journal";
    size_t index;

    if ((name == NULL) ||
        (strlen(name) != (P6C_OPERATION_ID_BYTES * 2U) +
                             sizeof(SUFFIX) - 1U) ||
        (memcmp(&name[P6C_OPERATION_ID_BYTES * 2U], SUFFIX,
                sizeof(SUFFIX)) != 0)) {
        return 0;
    }
    for (index = 0U; index < P6C_OPERATION_ID_BYTES; ++index) {
        int high = p6c_service_hex_value(
            (unsigned char)name[index * 2U]);
        int low = p6c_service_hex_value(
            (unsigned char)name[(index * 2U) + 1U]);

        if ((high < 0) || (low < 0)) {
            return 0;
        }
        operation_id[index] =
            (uint8_t)(((unsigned int)high << 4) | (unsigned int)low);
    }
    return 1;
}

static uint16_t p6c_service_load_u16(
    const uint8_t input[static 2])
{
    return (uint16_t)(((uint16_t)input[0] << 8) |
                      (uint16_t)input[1]);
}

static enum p6c_result p6c_replay_digest(
    const void *data, size_t size,
    uint8_t digest[static P6C_SHA256_BYTES])
{
    struct p6c_sha256 hash;

    p6c_sha256_init(&hash);
    if ((p6c_sha256_update(&hash, data, size) != P6C_RESULT_OK) ||
        (p6c_sha256_final(&hash, digest) != P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_replay_pwrite_all(
    int descriptor, const uint8_t *data, size_t size, off_t offset)
{
    size_t written = 0U;

    while (written < size) {
        ssize_t amount = pwrite(
            descriptor, &data[written], size - written,
            offset + (off_t)written);

        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (amount == 0) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        written += (size_t)amount;
    }
    return P6C_RESULT_OK;
}

static int p6c_replay_record_empty(
    const uint8_t record[static P6C_REPLAY_RECORD_BYTES])
{
    size_t index;

    for (index = 0U; index < P6C_REPLAY_RECORD_BYTES; ++index) {
        if (record[index] != UINT8_C(0)) {
            return 0;
        }
    }
    return 1;
}

static enum p6c_result p6c_replay_ledger_validate_owner(
    const struct p6c_service_config *configuration,
    const struct p6c_owned_fd *owner, struct stat *status)
{
    struct stat named_status;

    if ((configuration == NULL) || (owner == NULL) ||
        (status == NULL) || !p6c_owned_fd_is_live(owner) ||
        (fstat(owner->descriptor, status) != 0) ||
        (fstatat(
             configuration->journal_root.descriptor,
             P6C_REPLAY_LEDGER_NAME, &named_status,
             AT_SYMLINK_NOFOLLOW) != 0) ||
        !S_ISREG(status->st_mode) || !S_ISREG(named_status.st_mode) ||
        (status->st_nlink != 1) || (named_status.st_nlink != 1) ||
        (status->st_uid != geteuid()) ||
        (named_status.st_uid != geteuid()) ||
        ((status->st_mode & (mode_t)0777) != (mode_t)0600) ||
        ((named_status.st_mode & (mode_t)0777) != (mode_t)0600) ||
        (status->st_dev != named_status.st_dev) ||
        (status->st_ino != named_status.st_ino) ||
        (status->st_size != (off_t)P6C_REPLAY_LEDGER_BYTES) ||
        (named_status.st_size != (off_t)P6C_REPLAY_LEDGER_BYTES)) {
        return P6C_RESULT_UNSAFE;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_replay_ledger_create(
    struct p6c_service_registry *registry)
{
    uint8_t ledger_bytes[P6C_REPLAY_LEDGER_BYTES];
    uint8_t *header = ledger_bytes;
    uint8_t digest[P6C_SHA256_BYTES];
    int descriptor;
    enum p6c_result result;

    memset(ledger_bytes, 0, sizeof(ledger_bytes));
    memcpy(header, "P6CRPL2", 7U);
    p6c_store_u16_be(&header[8], P6C_REPLAY_LEDGER_VERSION);
    p6c_store_u16_be(
        &header[10], (uint16_t)P6C_REPLAY_HEADER_BYTES);
    p6c_store_u32_be(
        &header[12], (uint32_t)P6C_REPLAY_RECORD_BYTES);
    p6c_store_u32_be(
        &header[16], (uint32_t)P6C_REPLAY_CAPACITY);
    result = p6c_replay_digest(
        header, P6C_REPLAY_HEADER_DIGEST_OFFSET, digest);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    memcpy(
        &header[P6C_REPLAY_HEADER_DIGEST_OFFSET], digest,
        P6C_SHA256_BYTES);
    descriptor = openat(
        registry->configuration->journal_root.descriptor,
        P6C_REPLAY_LEDGER_NAME,
        O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
        (mode_t)0600);
    if (descriptor < 0) {
        return (errno == EEXIST) ? P6C_RESULT_CONFLICT :
                                  P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_owned_fd_acquire(
        &registry->replay.file, descriptor,
        P6C_DESCRIPTOR_REGULAR);
    if (result != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((p6c_replay_pwrite_all(
             descriptor, ledger_bytes, sizeof(ledger_bytes),
             (off_t)0) != P6C_RESULT_OK) ||
        (fsync(descriptor) != 0) ||
        (fsync(
             registry->configuration->journal_root.descriptor) != 0)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    memcpy(registry->replay.prior_digest, digest, P6C_SHA256_BYTES);
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_replay_ledger_open(
    struct p6c_service_registry *registry)
{
    uint8_t header[P6C_REPLAY_HEADER_BYTES];
    uint8_t digest[P6C_SHA256_BYTES];
    struct stat status;
    enum p6c_result result;
    size_t index;
    bool empty_seen = false;

    memset(&registry->replay, 0, sizeof(registry->replay));
    p6c_owned_fd_reset(&registry->replay.file);
    result = p6c_openat2_owned(
        &registry->configuration->journal_root,
        P6C_REPLAY_LEDGER_NAME, O_RDWR | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_REGULAR, &registry->replay.file);
    if (result != P6C_RESULT_OK) {
        if (fstatat(
                registry->configuration->journal_root.descriptor,
                P6C_REPLAY_LEDGER_NAME, &status,
                AT_SYMLINK_NOFOLLOW) == 0) {
            return P6C_RESULT_UNSAFE;
        }
        if (errno != ENOENT) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        result = p6c_replay_ledger_create(registry);
        if (result == P6C_RESULT_CONFLICT) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (result != P6C_RESULT_OK) {
            return result;
        }
    }
    result = p6c_replay_ledger_validate_owner(
        registry->configuration, &registry->replay.file, &status);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    if ((pread(
             registry->replay.file.descriptor, header,
             sizeof(header), (off_t)0) != (ssize_t)sizeof(header)) ||
        (memcmp(header, "P6CRPL2", 7U) != 0) ||
        (header[7] != UINT8_C(0)) ||
        (p6c_service_load_u16(&header[8]) !=
         P6C_REPLAY_LEDGER_VERSION) ||
        (p6c_service_load_u16(&header[10]) !=
         (uint16_t)P6C_REPLAY_HEADER_BYTES) ||
        (p6c_service_load_u32(&header[12]) !=
         (uint32_t)P6C_REPLAY_RECORD_BYTES) ||
        (p6c_service_load_u32(&header[16]) !=
         (uint32_t)P6C_REPLAY_CAPACITY) ||
        (p6c_replay_digest(
             header, P6C_REPLAY_HEADER_DIGEST_OFFSET,
             digest) != P6C_RESULT_OK) ||
        (memcmp(
             digest, &header[P6C_REPLAY_HEADER_DIGEST_OFFSET],
             P6C_SHA256_BYTES) != 0)) {
        return P6C_RESULT_UNSAFE;
    }
    for (index = 20U;
         index < P6C_REPLAY_HEADER_DIGEST_OFFSET; ++index) {
        if (header[index] != UINT8_C(0)) {
            return P6C_RESULT_UNSAFE;
        }
    }
    memcpy(registry->replay.prior_digest, digest, P6C_SHA256_BYTES);
    for (index = 0U; index < P6C_REPLAY_CAPACITY; ++index) {
        uint8_t record[P6C_REPLAY_RECORD_BYTES];
        uint8_t calculated[P6C_SHA256_BYTES];
        struct p6c_durable_replay_entry *entry;
        off_t offset = (off_t)(
            P6C_REPLAY_HEADER_BYTES +
            (index * P6C_REPLAY_RECORD_BYTES));

        if (pread(
                registry->replay.file.descriptor, record,
                sizeof(record), offset) != (ssize_t)sizeof(record)) {
            return P6C_RESULT_UNSAFE;
        }
        if (p6c_replay_record_empty(record)) {
            empty_seen = true;
            continue;
        }
        if (empty_seen ||
            (memcmp(record, "P6CRPL2", 7U) != 0) ||
            (record[7] != UINT8_C(0)) ||
            (p6c_service_load_u16(&record[8]) !=
             P6C_REPLAY_LEDGER_VERSION) ||
            (p6c_service_load_u64(
                 &record[P6C_REPLAY_RECORD_SEQUENCE_OFFSET]) !=
             (uint64_t)index + UINT64_C(1)) ||
            (memcmp(
                 &record[P6C_REPLAY_RECORD_PRIOR_OFFSET],
                 registry->replay.prior_digest,
                 P6C_SHA256_BYTES) != 0) ||
            (p6c_replay_digest(
                 record, P6C_REPLAY_RECORD_DIGEST_OFFSET,
                 calculated) != P6C_RESULT_OK) ||
            (memcmp(
                 calculated,
                 &record[P6C_REPLAY_RECORD_DIGEST_OFFSET],
                 P6C_SHA256_BYTES) != 0)) {
            return P6C_RESULT_UNSAFE;
        }
        entry = &registry->replay.entries[index];
        memcpy(
            entry->request_id,
            &record[P6C_REPLAY_RECORD_REQUEST_ID_OFFSET],
            P6C_REQUEST_ID_BYTES);
        entry->controller_user = (uid_t)p6c_service_load_u32(
            &record[P6C_REPLAY_RECORD_UID_OFFSET]);
        entry->opcode = p6c_service_load_u16(
            &record[P6C_REPLAY_RECORD_OPCODE_OFFSET]);
        memcpy(
            entry->request_digest,
            &record[P6C_REPLAY_RECORD_REQUEST_DIGEST_OFFSET],
            P6C_SHA256_BYTES);
        memcpy(
            entry->operation_id,
            &record[P6C_REPLAY_RECORD_OPERATION_OFFSET],
            P6C_OPERATION_ID_BYTES);
        memcpy(
            entry->command_identity,
            &record[P6C_REPLAY_RECORD_COMMAND_OFFSET],
            P6C_SHA256_BYTES);
        memcpy(
            registry->replay.prior_digest,
            &record[P6C_REPLAY_RECORD_DIGEST_OFFSET],
            P6C_SHA256_BYTES);
        ++registry->replay.count;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_replay_ledger_reserve(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer, uint16_t opcode,
    const uint8_t request_id[static P6C_REQUEST_ID_BYTES],
    const uint8_t request_digest[static P6C_SHA256_BYTES],
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    const uint8_t command_identity[static P6C_SHA256_BYTES])
{
    uint8_t record[P6C_REPLAY_RECORD_BYTES];
    uint8_t digest[P6C_SHA256_BYTES];
    struct p6c_durable_replay_entry *entry;
    off_t offset;

    if ((registry == NULL) || (peer == NULL) ||
        (request_id == NULL) || (request_digest == NULL) ||
        (operation_id == NULL) || (command_identity == NULL) ||
        !p6c_owned_fd_is_live(&registry->replay.file) ||
        (registry->replay.count >= P6C_REPLAY_CAPACITY)) {
        return P6C_RESULT_LIMIT;
    }
    memset(record, 0, sizeof(record));
    memcpy(record, "P6CRPL2", 7U);
    p6c_store_u16_be(&record[8], P6C_REPLAY_LEDGER_VERSION);
    p6c_store_u16_be(
        &record[P6C_REPLAY_RECORD_OPCODE_OFFSET], opcode);
    p6c_store_u32_be(
        &record[P6C_REPLAY_RECORD_UID_OFFSET],
        (uint32_t)peer->user_id);
    p6c_service_store_u64(
        &record[P6C_REPLAY_RECORD_SEQUENCE_OFFSET],
        (uint64_t)registry->replay.count + UINT64_C(1));
    memcpy(
        &record[P6C_REPLAY_RECORD_REQUEST_ID_OFFSET], request_id,
        P6C_REQUEST_ID_BYTES);
    memcpy(
        &record[P6C_REPLAY_RECORD_REQUEST_DIGEST_OFFSET],
        request_digest, P6C_SHA256_BYTES);
    memcpy(
        &record[P6C_REPLAY_RECORD_OPERATION_OFFSET], operation_id,
        P6C_OPERATION_ID_BYTES);
    memcpy(
        &record[P6C_REPLAY_RECORD_COMMAND_OFFSET],
        command_identity, P6C_SHA256_BYTES);
    memcpy(
        &record[P6C_REPLAY_RECORD_PRIOR_OFFSET],
        registry->replay.prior_digest, P6C_SHA256_BYTES);
    if (p6c_replay_digest(
            record, P6C_REPLAY_RECORD_DIGEST_OFFSET,
            digest) != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    memcpy(
        &record[P6C_REPLAY_RECORD_DIGEST_OFFSET], digest,
        P6C_SHA256_BYTES);
    offset = (off_t)(
        P6C_REPLAY_HEADER_BYTES +
        (registry->replay.count * P6C_REPLAY_RECORD_BYTES));
    if ((p6c_replay_pwrite_all(
             registry->replay.file.descriptor, record,
             sizeof(record), offset) != P6C_RESULT_OK) ||
        (fsync(registry->replay.file.descriptor) != 0)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    entry = &registry->replay.entries[registry->replay.count];
    memcpy(entry->request_id, request_id, P6C_REQUEST_ID_BYTES);
    entry->controller_user = peer->user_id;
    entry->opcode = opcode;
    memcpy(
        entry->request_digest, request_digest, P6C_SHA256_BYTES);
    memcpy(
        entry->operation_id, operation_id, P6C_OPERATION_ID_BYTES);
    memcpy(
        entry->command_identity, command_identity,
        P6C_SHA256_BYTES);
    memcpy(registry->replay.prior_digest, digest, P6C_SHA256_BYTES);
    ++registry->replay.count;
    return P6C_RESULT_OK;
}

static void p6c_service_entry_reset(struct p6c_service_entry *entry)
{
    if (entry == NULL) {
        return;
    }
    memset(entry, 0, sizeof(*entry));
    p6c_owned_fd_reset(&entry->journal.file);
    p6c_owned_fd_reset(&entry->executable.file);
    p6c_owned_fd_reset(&entry->credential_directory);
    p6c_owned_fd_reset(&entry->cgroup);
    p6c_owned_fd_reset(&entry->status_channel.first);
    p6c_owned_fd_reset(&entry->status_channel.second);
    p6c_owned_fd_reset(&entry->stdout_channel.first);
    p6c_owned_fd_reset(&entry->stdout_channel.second);
    p6c_owned_fd_reset(&entry->stderr_channel.first);
    p6c_owned_fd_reset(&entry->stderr_channel.second);
    p6c_owned_fd_reset(&entry->stdout_transcript.sink);
    p6c_owned_fd_reset(&entry->stderr_transcript.sink);
    p6c_owned_fd_reset(&entry->operation.pidfd);
    p6c_owned_fd_reset(&entry->publication.staging_directory);
    p6c_owned_fd_reset(&entry->publication.committed_directory);
}

static void p6c_service_registry_init(
    struct p6c_service_registry *registry,
    struct p6c_service_config *configuration)
{
    size_t index;

    memset(registry, 0, sizeof(*registry));
    registry->configuration = configuration;
    p6c_owned_fd_reset(&registry->replay.file);
    for (index = 0U; index < P6C_MAX_OPERATIONS; ++index) {
        p6c_service_entry_reset(&registry->entries[index]);
    }
}

static void p6c_service_entry_close(struct p6c_service_entry *entry)
{
    if (entry == NULL) {
        return;
    }
    if (p6c_owned_fd_is_live(&entry->operation.pidfd)) {
        (void)p6c_owned_fd_close(&entry->operation.pidfd);
    }
    (void)p6c_owned_pair_close(&entry->status_channel);
    (void)p6c_owned_pair_close(&entry->stdout_channel);
    (void)p6c_owned_pair_close(&entry->stderr_channel);
    (void)p6c_transcript_close(&entry->stdout_transcript);
    (void)p6c_transcript_close(&entry->stderr_transcript);
    (void)p6c_executable_close(&entry->executable);
    if (p6c_owned_fd_is_live(&entry->credential_directory)) {
        (void)p6c_owned_fd_close(&entry->credential_directory);
    }
    if (p6c_owned_fd_is_live(&entry->cgroup)) {
        (void)p6c_owned_fd_close(&entry->cgroup);
    }
    (void)p6c_publication_close(&entry->publication);
    (void)p6c_journal_close(&entry->journal);
    free(entry->authority_storage);
    entry->authority_storage = NULL;
}

static void p6c_service_registry_close(
    struct p6c_service_registry *registry)
{
    size_t index;

    if (registry == NULL) {
        return;
    }
    (void)p6c_service_cleanup_after_disconnect(
        registry, P6C_DISCONNECT_RECEIVE_FAILURE);
    for (index = 0U; index < P6C_MAX_OPERATIONS; ++index) {
        struct p6c_service_entry *entry =
            &registry->entries[index];

        if (!entry->occupied) {
            continue;
        }
        p6c_service_entry_close(entry);
    }
    if (p6c_owned_fd_is_live(&registry->replay.file)) {
        (void)p6c_owned_fd_close(&registry->replay.file);
    }
}

static int p6c_service_peer_matches(
    const struct p6c_service_entry *entry,
    const struct p6c_peer_identity *peer)
{
    return (entry != NULL) && (peer != NULL) &&
           (entry->opening_user == peer->user_id);
}

static struct p6c_service_entry *p6c_service_find_entry(
    struct p6c_service_registry *registry,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES])
{
    size_t index;

    for (index = 0U; index < P6C_MAX_OPERATIONS; ++index) {
        struct p6c_service_entry *entry = &registry->entries[index];

        if (entry->occupied &&
            (memcmp(entry->operation.operation_id, operation_id,
                    P6C_OPERATION_ID_BYTES) == 0)) {
            return entry;
        }
    }
    return NULL;
}

static struct p6c_service_entry *p6c_service_reserve_entry(
    struct p6c_service_registry *registry)
{
    size_t index;

    if ((registry == NULL) || (registry->count >= P6C_MAX_OPERATIONS)) {
        return NULL;
    }
    for (index = 0U; index < P6C_MAX_OPERATIONS; ++index) {
        if (!registry->entries[index].occupied) {
            p6c_service_entry_reset(&registry->entries[index]);
            registry->entries[index].occupied = true;
            ++registry->count;
            return &registry->entries[index];
        }
    }
    return NULL;
}

static enum p6c_result p6c_service_random_token(
    uint8_t token[static P6C_RECOVERY_TOKEN_BYTES])
{
    size_t offset = 0U;
    bool nonzero = false;

#ifdef P6C_TESTING
    if (p6c_service_test_token_enabled) {
        memcpy(token, p6c_service_test_token,
               P6C_RECOVERY_TOKEN_BYTES);
        for (offset = 0U; offset < P6C_RECOVERY_TOKEN_BYTES; ++offset) {
            if (token[offset] != UINT8_C(0)) {
                nonzero = true;
            }
        }
        return nonzero ? P6C_RESULT_OK : P6C_RESULT_SYSTEM;
    }
#endif
    while (offset < P6C_RECOVERY_TOKEN_BYTES) {
        ssize_t amount = (ssize_t)syscall(
            SYS_getrandom, &token[offset],
            P6C_RECOVERY_TOKEN_BYTES - offset, 0U);

        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            return P6C_RESULT_SYSTEM;
        }
        if (amount == 0) {
            return P6C_RESULT_SYSTEM;
        }
        offset += (size_t)amount;
    }
    for (offset = 0U; offset < P6C_RECOVERY_TOKEN_BYTES; ++offset) {
        if (token[offset] != UINT8_C(0)) {
            nonzero = true;
        }
    }
    return nonzero ? P6C_RESULT_OK : P6C_RESULT_SYSTEM;
}

static enum p6c_result p6c_service_request_digest(
    const struct p6c_frame_view *frame,
    uint8_t digest[static P6C_SHA256_BYTES])
{
    struct p6c_sha256 hash;
    uint8_t message_type[2];

    if ((frame == NULL) || (digest == NULL)) {
        return P6C_RESULT_INVALID;
    }
    p6c_store_u16_be(message_type, frame->message_type);
    p6c_sha256_init(&hash);
    if ((p6c_sha256_update(
             &hash, message_type, sizeof(message_type)) != P6C_RESULT_OK) ||
        (p6c_sha256_update(
             &hash, frame->payload,
             (size_t)frame->payload_length) != P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return p6c_sha256_final(&hash, digest);
}

static enum p6c_result p6c_service_parse_vector(
    const struct p6c_field_view *field, char **vector,
    size_t maximum_count, char *storage, size_t storage_capacity,
    size_t *storage_used, size_t *count)
{
    uint32_t item_count;
    size_t field_offset = 4U;
    size_t item_index;

    if ((field == NULL) || (vector == NULL) || (storage == NULL) ||
        (storage_used == NULL) || (count == NULL) ||
        (field->value_length < UINT32_C(4))) {
        return P6C_RESULT_INVALID;
    }
    item_count = p6c_service_load_u32(field->value);
    if ((size_t)item_count > maximum_count) {
        return P6C_RESULT_LIMIT;
    }
    for (item_index = 0U; item_index < (size_t)item_count; ++item_index) {
        uint32_t item_length;

        if ((size_t)field->value_length - field_offset < 4U) {
            return P6C_RESULT_MALFORMED;
        }
        item_length = p6c_service_load_u32(
            &field->value[field_offset]);
        field_offset += 4U;
        if (((size_t)item_length >
             (size_t)field->value_length - field_offset) ||
            ((size_t)item_length + 1U >
             storage_capacity - *storage_used)) {
            return P6C_RESULT_MALFORMED;
        }
        vector[item_index] = &storage[*storage_used];
        memcpy(vector[item_index], &field->value[field_offset],
               (size_t)item_length);
        vector[item_index][item_length] = '\0';
        *storage_used += (size_t)item_length + 1U;
        field_offset += (size_t)item_length;
    }
    if (field_offset != (size_t)field->value_length) {
        return P6C_RESULT_MALFORMED;
    }
    vector[item_count] = NULL;
    *count = (size_t)item_count;
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_service_validate_environment(
    char *const environment[], size_t count)
{
    return p6c_validate_live_environment_count(environment, count);
}

static enum p6c_result p6c_service_prepare_authority(
    struct p6c_service_entry *entry,
    const struct p6c_field_view *executable_field,
    const struct p6c_field_view *argv_field,
    const struct p6c_field_view *environment_field)
{
    size_t storage_capacity;
    size_t storage_used = 0U;
    size_t argv_count = 0U;
    size_t environment_count = 0U;
    enum p6c_result result;

    if ((entry == NULL) || (executable_field == NULL) ||
        (argv_field == NULL) || (environment_field == NULL) ||
        ((size_t)executable_field->value_length >
         (size_t)P6C_MAX_STRING_BYTES)) {
        return P6C_RESULT_INVALID;
    }
    memcpy(entry->executable_path, executable_field->value,
           (size_t)executable_field->value_length);
    entry->executable_path[executable_field->value_length] = '\0';
    storage_capacity = (size_t)argv_field->value_length +
                       (size_t)environment_field->value_length + 2U;
    entry->authority_storage = malloc(storage_capacity);
    if (entry->authority_storage == NULL) {
        return P6C_RESULT_SYSTEM;
    }
    result = p6c_service_parse_vector(
        argv_field, entry->argv, (size_t)P6C_MAX_ARGV_COUNT,
        entry->authority_storage, storage_capacity, &storage_used,
        &argv_count);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    result = p6c_service_parse_vector(
        environment_field, entry->environment,
        (size_t)P6C_MAX_ENVIRONMENT_COUNT, entry->authority_storage,
        storage_capacity, &storage_used, &environment_count);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    result = p6c_service_validate_environment(
        entry->environment, environment_count);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    {
        bool environment_has_credentials = false;
        size_t environment_index;

        for (environment_index = 0U;
             environment_index < environment_count;
             ++environment_index) {
            if (strcmp(
                    entry->environment[environment_index],
                    "CREDENTIALS_DIRECTORY=/proc/self/fd/5") == 0) {
                environment_has_credentials = true;
                break;
            }
        }
        if (environment_has_credentials !=
            p6c_owned_fd_is_live(&entry->credential_directory)) {
            return P6C_RESULT_UNSAFE;
        }
    }
    entry->spawn.argv = entry->argv;
    entry->spawn.argv_count = argv_count;
    entry->spawn.environment = entry->environment;
    entry->spawn.environment_count = environment_count;
    entry->spawn.credential_directory =
        p6c_owned_fd_is_live(&entry->credential_directory) ?
            &entry->credential_directory :
            NULL;
    entry->spawn.exec_timeout_milliseconds =
        P6C_PRODUCTION_EXEC_TIMEOUT_MS;
    return P6C_RESULT_OK;
}

static int64_t p6c_service_stat_nanoseconds(
    time_t seconds, long nanoseconds)
{
    return ((int64_t)seconds * INT64_C(1000000000)) +
           (int64_t)nanoseconds;
}

static int p6c_service_credential_metadata_matches(
    const struct stat *status, const uint8_t *encoded)
{
    int64_t mtime;
    int64_t ctime;

    if ((status == NULL) || (encoded == NULL)) {
        return 0;
    }
    mtime = p6c_service_stat_nanoseconds(
        status->st_mtim.tv_sec, status->st_mtim.tv_nsec);
    ctime = p6c_service_stat_nanoseconds(
        status->st_ctim.tv_sec, status->st_ctim.tv_nsec);
    return ((uint64_t)status->st_dev ==
            p6c_service_load_u64(&encoded[0])) &&
           ((uint64_t)status->st_ino ==
            p6c_service_load_u64(&encoded[8])) &&
           ((uint32_t)status->st_uid ==
            p6c_service_load_u32(&encoded[16])) &&
           ((uint32_t)status->st_gid ==
            p6c_service_load_u32(&encoded[20])) &&
           ((uint32_t)status->st_mode ==
            p6c_service_load_u32(&encoded[24])) &&
           ((uint64_t)status->st_nlink ==
            p6c_service_load_u64(&encoded[28])) &&
           ((uint64_t)status->st_size ==
            p6c_service_load_u64(&encoded[36])) &&
           ((uint64_t)mtime ==
            p6c_service_load_u64(&encoded[44])) &&
           ((uint64_t)ctime ==
            p6c_service_load_u64(&encoded[52]));
}

static enum p6c_result p6c_service_verify_credentials(
    struct p6c_service_entry *entry)
{
    const uint8_t *manifest;
    const uint8_t *names[64];
    size_t name_lengths[64];
    struct stat directory_before;
    struct stat directory_after;
    uint32_t count;
    size_t offset = 37U;
    size_t index;
    int listing_descriptor = P6C_INVALID_DESCRIPTOR;
    DIR *listing = NULL;
    size_t observed_names = 0U;
    enum p6c_result result = P6C_RESULT_UNSAFE;

    if (entry == NULL) {
        return P6C_RESULT_INVALID;
    }
    if (!p6c_owned_fd_is_live(&entry->credential_directory)) {
        return (entry->credential_manifest_size == 0U) ?
                   P6C_RESULT_OK :
                   P6C_RESULT_UNSAFE;
    }
    if ((entry->credential_manifest_size < 37U) ||
        (entry->credential_manifest_size >
         (size_t)P6C_MAX_CREDENTIAL_MANIFEST_BYTES)) {
        return P6C_RESULT_UNSAFE;
    }
    manifest = entry->credential_manifest;
    if (memcmp(manifest, "P6CM1", 5U) != 0) {
        return P6C_RESULT_UNSAFE;
    }
    count = p6c_service_load_u32(&manifest[5]);
    if ((count == 0U) || (count > UINT32_C(64)) ||
        (fstat(
             entry->credential_directory.descriptor,
             &directory_before) != 0) ||
        !S_ISDIR(directory_before.st_mode) ||
        (directory_before.st_uid != geteuid()) ||
        (directory_before.st_gid != getegid()) ||
        ((directory_before.st_mode & (mode_t)07777) != (mode_t)0700) ||
        ((uint64_t)directory_before.st_dev !=
         p6c_service_load_u64(&manifest[9])) ||
        ((uint64_t)directory_before.st_ino !=
         p6c_service_load_u64(&manifest[17])) ||
        ((uint32_t)directory_before.st_uid !=
         p6c_service_load_u32(&manifest[25])) ||
        ((uint32_t)directory_before.st_gid !=
         p6c_service_load_u32(&manifest[29])) ||
        ((uint32_t)directory_before.st_mode !=
         p6c_service_load_u32(&manifest[33]))) {
        return P6C_RESULT_UNSAFE;
    }
    for (index = 0U; index < (size_t)count; ++index) {
        uint32_t name_length;
        char name[256];
        const uint8_t *metadata;
        const uint8_t *expected_digest;
        struct p6c_owned_fd leaf;
        struct stat before;
        struct stat after;
        uint8_t digest[P6C_SHA256_BYTES];
        size_t character;

        p6c_owned_fd_reset(&leaf);
        if (entry->credential_manifest_size - offset < 4U) {
            return P6C_RESULT_UNSAFE;
        }
        name_length = p6c_service_load_u32(&manifest[offset]);
        offset += 4U;
        if ((name_length == 0U) || (name_length >= sizeof(name)) ||
            (entry->credential_manifest_size - offset <
             (size_t)name_length + 60U + P6C_SHA256_BYTES)) {
            return P6C_RESULT_UNSAFE;
        }
        names[index] = &manifest[offset];
        name_lengths[index] = (size_t)name_length;
        for (character = 0U;
             character < (size_t)name_length;
             ++character) {
            uint8_t value = manifest[offset + character];

            if (!(((value >= UINT8_C('A')) &&
                   (value <= UINT8_C('Z'))) ||
                  ((value >= UINT8_C('a')) &&
                   (value <= UINT8_C('z'))) ||
                  ((value >= UINT8_C('0')) &&
                   (value <= UINT8_C('9'))) ||
                  (value == UINT8_C('_')) ||
                  (value == UINT8_C('-')) ||
                  (value == UINT8_C('.')))) {
                return P6C_RESULT_UNSAFE;
            }
        }
        if ((index > 0U) &&
            ((name_lengths[index - 1U] > (size_t)name_length) ?
                 (memcmp(
                      names[index - 1U], names[index],
                      (size_t)name_length) >= 0) :
                 ((memcmp(
                       names[index - 1U], names[index],
                       name_lengths[index - 1U]) > 0) ||
                  ((memcmp(
                        names[index - 1U], names[index],
                        name_lengths[index - 1U]) == 0) &&
                   (name_lengths[index - 1U] ==
                    (size_t)name_length))))) {
            return P6C_RESULT_UNSAFE;
        }
        memcpy(name, &manifest[offset], (size_t)name_length);
        name[name_length] = '\0';
        offset += (size_t)name_length;
        metadata = &manifest[offset];
        expected_digest = &manifest[offset + 60U];
        result = p6c_openat2_owned(
            &entry->credential_directory, name,
            O_RDONLY | O_NOFOLLOW, (mode_t)0,
            P6C_DESCRIPTOR_REGULAR, &leaf);
        if ((result != P6C_RESULT_OK) ||
            (leaf.type != P6C_DESCRIPTOR_REGULAR) ||
            (fstat(leaf.descriptor, &before) != 0) ||
            !S_ISREG(before.st_mode) ||
            (before.st_uid != geteuid()) ||
            (before.st_gid != getegid()) ||
            (((before.st_mode & (mode_t)07777) != (mode_t)0400) &&
             ((before.st_mode & (mode_t)07777) != (mode_t)0600)) ||
            (before.st_nlink != (nlink_t)1) ||
            (before.st_size < (off_t)1) ||
            (before.st_size > (off_t)4096) ||
            !p6c_service_credential_metadata_matches(
                &before, metadata) ||
            (p6c_sha256_fd(&leaf, digest) != P6C_RESULT_OK) ||
            (fstat(leaf.descriptor, &after) != 0) ||
            !p6c_service_credential_metadata_matches(
                &after, metadata) ||
            (memcmp(
                 digest, expected_digest,
                 P6C_SHA256_BYTES) != 0)) {
            if (p6c_owned_fd_is_live(&leaf)) {
                (void)p6c_owned_fd_close(&leaf);
            }
            return P6C_RESULT_UNSAFE;
        }
        if (p6c_owned_fd_close(&leaf) != P6C_RESULT_OK) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        offset += 60U + P6C_SHA256_BYTES;
    }
    if (offset != entry->credential_manifest_size) {
        return P6C_RESULT_UNSAFE;
    }
    listing_descriptor = openat(
        entry->credential_directory.descriptor, ".",
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (listing_descriptor < 0) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    listing = fdopendir(listing_descriptor);
    if (listing == NULL) {
        (void)close(listing_descriptor);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    listing_descriptor = P6C_INVALID_DESCRIPTOR;
    for (;;) {
        struct dirent *item;
        bool found = false;

        errno = 0;
        item = readdir(listing);
        if (item == NULL) {
            if (errno != 0) {
                result = P6C_RESULT_RECOVERY_REQUIRED;
                goto listing_cleanup;
            }
            break;
        }
        if ((strcmp(item->d_name, ".") == 0) ||
            (strcmp(item->d_name, "..") == 0)) {
            continue;
        }
        for (index = 0U; index < (size_t)count; ++index) {
            size_t observed_length = strlen(item->d_name);

            if ((observed_length == name_lengths[index]) &&
                (memcmp(
                     item->d_name, names[index],
                     observed_length) == 0)) {
                found = true;
                break;
            }
        }
        if (!found || (++observed_names > (size_t)count)) {
            result = P6C_RESULT_UNSAFE;
            goto listing_cleanup;
        }
    }
    if ((observed_names != (size_t)count) ||
        (fstat(
             entry->credential_directory.descriptor,
             &directory_after) != 0) ||
        (directory_before.st_dev != directory_after.st_dev) ||
        (directory_before.st_ino != directory_after.st_ino) ||
        (directory_before.st_uid != directory_after.st_uid) ||
        (directory_before.st_gid != directory_after.st_gid) ||
        (directory_before.st_mode != directory_after.st_mode) ||
        (directory_before.st_mtim.tv_sec !=
         directory_after.st_mtim.tv_sec) ||
        (directory_before.st_mtim.tv_nsec !=
         directory_after.st_mtim.tv_nsec) ||
        (directory_before.st_ctim.tv_sec !=
         directory_after.st_ctim.tv_sec) ||
        (directory_before.st_ctim.tv_nsec !=
         directory_after.st_ctim.tv_nsec)) {
        result = P6C_RESULT_UNSAFE;
        goto listing_cleanup;
    }
    result = P6C_RESULT_OK;

listing_cleanup:
    if ((listing != NULL) && (closedir(listing) != 0) &&
        (result == P6C_RESULT_OK)) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    return result;
}

#ifdef P6C_TESTING
enum p6c_result p6c_test_verify_credential_authority(
    int directory_descriptor, const uint8_t *manifest,
    size_t manifest_size)
{
    struct p6c_service_entry entry;
    int duplicate;
    enum p6c_result result;

    if ((directory_descriptor < 0) || (manifest == NULL) ||
        (manifest_size == 0U) ||
        (manifest_size > (size_t)P6C_MAX_CREDENTIAL_MANIFEST_BYTES)) {
        return P6C_RESULT_INVALID;
    }
    duplicate = fcntl(directory_descriptor, F_DUPFD_CLOEXEC, 3);
    if (duplicate < 0) {
        return P6C_RESULT_SYSTEM;
    }
    memset(&entry, 0, sizeof(entry));
    p6c_owned_fd_reset(&entry.credential_directory);
    result = p6c_owned_fd_acquire(
        &entry.credential_directory, duplicate,
        P6C_DESCRIPTOR_DIRECTORY);
    if (result != P6C_RESULT_OK) {
        (void)close(duplicate);
        return result;
    }
    memcpy(entry.credential_manifest, manifest, manifest_size);
    entry.credential_manifest_size = manifest_size;
    result = p6c_service_verify_credentials(&entry);
    if (p6c_owned_fd_close(&entry.credential_directory) !=
        P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return result;
}
#endif

static enum p6c_result p6c_service_mark_recovery(
    struct p6c_service_entry *entry,
    enum p6c_operation_state resume_state)
{
    uint8_t payload[1];

    if (entry == NULL) {
        return P6C_RESULT_INVALID;
    }
    payload[0] = (uint8_t)resume_state;
    if (p6c_owned_fd_is_live(&entry->journal.file) &&
        !entry->journal.recovery_required &&
        (entry->journal.durable_state !=
         P6C_OPERATION_RECOVERY_REQUIRED)) {
        if (p6c_journal_append(
                &entry->journal, P6C_OPERATION_RECOVERY_REQUIRED,
                payload, sizeof(payload)) != P6C_RESULT_OK) {
            entry->journal.recovery_required = true;
        }
    }
    entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
    entry->operation.resume_state = resume_state;
    entry->operation.authority_retained = true;
    return P6C_RESULT_RECOVERY_REQUIRED;
}

static enum p6c_result p6c_service_drain_one(
    struct p6c_owned_fd *reader, struct p6c_transcript *transcript)
{
    uint8_t buffer[16384];

    if ((reader == NULL) || (transcript == NULL)) {
        return P6C_RESULT_INVALID;
    }
    if (!p6c_owned_fd_is_live(reader)) {
        return transcript->eof_observed ?
                   P6C_RESULT_OK :
                   P6C_RESULT_RECOVERY_REQUIRED;
    }
    for (;;) {
        ssize_t amount = read(reader->descriptor, buffer, sizeof(buffer));

        if (amount > 0) {
            if (p6c_transcript_ingest(
                    transcript, buffer, (size_t)amount) !=
                P6C_RESULT_OK) {
                return P6C_RESULT_RECOVERY_REQUIRED;
            }
            continue;
        }
        if (amount == 0) {
            p6c_transcript_observe_eof(transcript);
            return (p6c_owned_fd_close(reader) == P6C_RESULT_OK) ?
                       P6C_RESULT_OK :
                       P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (errno == EINTR) {
            continue;
        }
        if ((errno == EAGAIN) || (errno == EWOULDBLOCK)) {
            return P6C_RESULT_OK;
        }
        reader->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        transcript->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
}

static enum p6c_result p6c_service_drain_entry(
    struct p6c_service_entry *entry)
{
    enum p6c_result first;
    enum p6c_result second;

    first = p6c_service_drain_one(
        &entry->stdout_channel.first, &entry->stdout_transcript);
    second = p6c_service_drain_one(
        &entry->stderr_channel.first, &entry->stderr_transcript);
    return ((first == P6C_RESULT_OK) &&
            (second == P6C_RESULT_OK)) ?
               P6C_RESULT_OK :
               P6C_RESULT_RECOVERY_REQUIRED;
}

static enum p6c_result p6c_production_clone(
    void *context, struct p6c_operation *operation)
{
    struct p6c_production_context *production = context;

    if ((production == NULL) ||
        (production->entry == NULL) ||
        (&production->entry->operation != operation)) {
        return P6C_RESULT_INVALID;
    }
    if (p6c_service_verify_credentials(production->entry) !=
        P6C_RESULT_OK) {
        return P6C_RESULT_UNSAFE;
    }
    return p6c_clone3_spawn(operation, &production->entry->spawn);
}

static enum p6c_exec_confirmation p6c_production_confirm(
    void *context, struct p6c_operation *operation)
{
    struct p6c_production_context *production = context;

    if ((production == NULL) || (production->entry == NULL) ||
        (&production->entry->operation != operation)) {
        return P6C_EXEC_CONFIRM_ERROR;
    }
    return p6c_confirm_exec_status(
        operation, production->entry->spawn.exec_timeout_milliseconds);
}

static enum p6c_result p6c_production_signal(
    void *context, struct p6c_operation *operation)
{
    struct p6c_production_context *production = context;

    if ((production == NULL) || (production->entry == NULL) ||
        (&production->entry->operation != operation)) {
        return P6C_RESULT_INVALID;
    }
    if (production->terminal_observed) {
        return P6C_RESULT_OK;
    }
    if (p6c_owned_fd_is_live(&operation->pidfd)) {
        return p6c_pidfd_signal(&operation->pidfd, SIGTERM);
    }
    if (operation->child_pid <= (pid_t)0) {
        return P6C_RESULT_INVALID;
    }
    return (kill(operation->child_pid, SIGTERM) == 0) ?
               P6C_RESULT_OK :
               P6C_RESULT_SYSTEM;
}

static enum p6c_result p6c_production_wait_terminal(
    void *context, struct p6c_operation *operation)
{
    struct p6c_production_context *production = context;
    uint32_t attempt;

    if ((production == NULL) || (production->entry == NULL) ||
        (&production->entry->operation != operation)) {
        return P6C_RESULT_INVALID;
    }
    for (attempt = UINT32_C(0);
         attempt < P6C_PRODUCTION_RUN_ONCE_POLLS; ++attempt) {
        struct pollfd descriptor;
        int poll_result;

        memset(&descriptor, 0, sizeof(descriptor));
        descriptor.fd = operation->pidfd.descriptor;
        descriptor.events = (short)(POLLIN | POLLHUP | POLLERR);
        do {
            poll_result = poll(
                &descriptor, 1U,
                (int)P6C_PRODUCTION_RUN_ONCE_POLL_MS);
        } while ((poll_result < 0) && (errno == EINTR));
        if ((p6c_service_drain_entry(production->entry) !=
             P6C_RESULT_OK) ||
            (poll_result < 0)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if ((poll_result > 0) && (descriptor.revents != 0)) {
            production->terminal_observed = true;
            return P6C_RESULT_OK;
        }
    }
    return P6C_RESULT_TIMEOUT;
}

static enum p6c_result p6c_production_grace(
    void *context, struct p6c_operation *operation)
{
    struct p6c_production_context *production = context;
    struct pollfd descriptor;
    int result;

    if ((production == NULL) || (production->entry == NULL) ||
        (&production->entry->operation != operation)) {
        return P6C_RESULT_INVALID;
    }
    if (production->terminal_observed) {
        return (p6c_service_drain_entry(production->entry) ==
                P6C_RESULT_OK) ?
                   P6C_RESULT_OK :
                   P6C_RESULT_RECOVERY_REQUIRED;
    }
    memset(&descriptor, 0, sizeof(descriptor));
    descriptor.fd = operation->pidfd.descriptor;
    descriptor.events = (short)(POLLIN | POLLHUP | POLLERR);
    do {
        result = poll(
            &descriptor, 1U, (int)P6C_PRODUCTION_STOP_GRACE_MS);
    } while ((result < 0) && (errno == EINTR));
    if (p6c_service_drain_entry(production->entry) != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (result < 0) {
        return P6C_RESULT_SYSTEM;
    }
    return (result == 0) ? P6C_RESULT_TIMEOUT : P6C_RESULT_OK;
}

static enum p6c_result p6c_production_freeze(
    void *context, struct p6c_operation *operation)
{
    struct p6c_production_context *production = context;
    uint32_t attempt;

    if ((production == NULL) || (production->entry == NULL) ||
        (&production->entry->operation != operation) ||
        (p6c_cgroup_freeze(operation->cgroup) != P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    for (attempt = UINT32_C(0);
         attempt < P6C_PRODUCTION_CGROUP_POLLS; ++attempt) {
        struct timespec delay;
        bool frozen = false;
        int sleep_result;

        if ((p6c_service_drain_entry(production->entry) !=
             P6C_RESULT_OK) ||
            (p6c_cgroup_is_frozen(
                 operation->cgroup, &frozen) != P6C_RESULT_OK)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (frozen) {
            return P6C_RESULT_OK;
        }
        delay.tv_sec = (time_t)0;
        delay.tv_nsec = P6C_PRODUCTION_CGROUP_POLL_NS;
        do {
            sleep_result = nanosleep(&delay, &delay);
        } while ((sleep_result != 0) && (errno == EINTR));
        if (sleep_result != 0) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
    }
    return P6C_RESULT_TIMEOUT;
}

static enum p6c_result p6c_production_kill(
    void *context, struct p6c_operation *operation)
{
    (void)context;
    return p6c_cgroup_kill(operation->cgroup);
}

static enum p6c_result p6c_production_empty(
    void *context, struct p6c_operation *operation)
{
    struct p6c_production_context *production = context;
    uint32_t attempt;

    if ((production == NULL) || (production->entry == NULL) ||
        (&production->entry->operation != operation)) {
        return P6C_RESULT_INVALID;
    }
    for (attempt = UINT32_C(0);
         attempt < P6C_PRODUCTION_CGROUP_POLLS; ++attempt) {
        struct timespec delay;
        bool populated = true;
        int sleep_result;

        if ((p6c_service_drain_entry(production->entry) !=
             P6C_RESULT_OK) ||
            (p6c_cgroup_is_populated(
                 operation->cgroup, &populated) != P6C_RESULT_OK)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (!populated) {
            return P6C_RESULT_OK;
        }
        delay.tv_sec = (time_t)0;
        delay.tv_nsec = P6C_PRODUCTION_CGROUP_POLL_NS;
        do {
            sleep_result = nanosleep(&delay, &delay);
        } while ((sleep_result != 0) && (errno == EINTR));
        if (sleep_result != 0) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
    }
    return P6C_RESULT_TIMEOUT;
}

static enum p6c_result p6c_production_observe(
    void *context, struct p6c_operation *operation,
    int32_t *exit_status)
{
    (void)context;
    return p6c_owned_fd_is_live(&operation->pidfd) ?
               p6c_pidfd_observe(&operation->pidfd, exit_status) :
               p6c_child_pid_observe(
                   operation->child_pid, exit_status);
}

static enum p6c_result p6c_production_reap(
    void *context, struct p6c_operation *operation)
{
    (void)context;
    return p6c_owned_fd_is_live(&operation->pidfd) ?
               p6c_pidfd_reap(&operation->pidfd) :
               p6c_child_pid_reap(operation->child_pid);
}

static enum p6c_result p6c_production_finalize(
    void *context, struct p6c_operation *operation)
{
    struct p6c_production_context *production = context;

    if ((production == NULL) || (production->entry == NULL) ||
        (&production->entry->operation != operation) ||
        (p6c_service_drain_entry(production->entry) != P6C_RESULT_OK) ||
        !production->entry->stdout_transcript.eof_observed ||
        !production->entry->stderr_transcript.eof_observed) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    p6c_transcript_prove_cleanup(
        &production->entry->stdout_transcript);
    p6c_transcript_prove_cleanup(
        &production->entry->stderr_transcript);
    if ((p6c_transcript_finalize(
             &production->entry->stdout_transcript) != P6C_RESULT_OK) ||
        (p6c_transcript_finalize(
             &production->entry->stderr_transcript) != P6C_RESULT_OK) ||
        (p6c_transcript_link(
             &production->registry->configuration->journal_root,
             production->entry->stdout_name,
             &production->entry->stdout_transcript) != P6C_RESULT_OK) ||
        (p6c_transcript_link(
             &production->registry->configuration->journal_root,
             production->entry->stderr_name,
             &production->entry->stderr_transcript) != P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_production_remove(
    void *context, struct p6c_operation *operation)
{
    struct p6c_production_context *production = context;
    enum p6c_result result;

    if ((production == NULL) || (production->entry == NULL) ||
        (&production->entry->operation != operation)) {
        return P6C_RESULT_INVALID;
    }
    result = p6c_cgroup_remove(
        &production->registry->configuration->cgroup_root,
        production->entry->cgroup_name, operation->cgroup);
    if (result == P6C_RESULT_OK) {
        production->entry->cgroup_allocated = false;
    }
    return result;
}

static struct p6c_process_adapter p6c_service_process_adapter(
    struct p6c_production_context *context)
{
    struct p6c_process_adapter adapter;

#ifdef P6C_TESTING
    if (p6c_service_test_adapter_enabled) {
        return p6c_service_test_adapter;
    }
#endif
    memset(&adapter, 0, sizeof(adapter));
    adapter.context = context;
    adapter.clone_child = p6c_production_clone;
    adapter.confirm_exec = p6c_production_confirm;
    adapter.wait_terminal = p6c_production_wait_terminal;
    adapter.signal_term = p6c_production_signal;
    adapter.wait_grace = p6c_production_grace;
    adapter.freeze_cgroup = p6c_production_freeze;
    adapter.kill_cgroup = p6c_production_kill;
    adapter.wait_cgroup_empty = p6c_production_empty;
    adapter.observe_child = p6c_production_observe;
    adapter.reap_child = p6c_production_reap;
    adapter.finalize_transcripts = p6c_production_finalize;
    adapter.remove_cgroup = p6c_production_remove;
    return adapter;
}

static uint16_t p6c_service_summary_flags(
    const struct p6c_service_entry *entry)
{
    uint16_t flags = UINT16_C(0);

    if (entry->operation.authority_retained) {
        flags |= P6C_SUMMARY_FLAG_AUTHORITY_RETAINED;
    }
    if (entry->journal.bundle_committed) {
        flags |= P6C_SUMMARY_FLAG_BUNDLE_COMMITTED;
    }
    if (entry->stdout_transcript.truncated) {
        flags |= P6C_SUMMARY_FLAG_STDOUT_TRUNCATED;
    }
    if (entry->stderr_transcript.truncated) {
        flags |= P6C_SUMMARY_FLAG_STDERR_TRUNCATED;
    }
    if ((entry->operation.state == P6C_OPERATION_ACKNOWLEDGED) ||
        (entry->journal.durable_state == P6C_OPERATION_ACKNOWLEDGED)) {
        flags |= P6C_SUMMARY_FLAG_ACKNOWLEDGED;
    }
    return flags;
}

static void p6c_service_encode_summary(
    const struct p6c_service_entry *entry,
    uint8_t summary[static P6C_OPERATION_SUMMARY_BYTES])
{
    memset(summary, 0, P6C_OPERATION_SUMMARY_BYTES);
    memcpy(&summary[P6C_SUMMARY_OPERATION_ID_OFFSET],
           entry->operation.operation_id, P6C_OPERATION_ID_BYTES);
    memcpy(&summary[P6C_SUMMARY_RECOVERY_TOKEN_OFFSET],
           entry->operation.recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    summary[P6C_SUMMARY_STATE_OFFSET] =
        (uint8_t)entry->operation.state;
    summary[P6C_SUMMARY_RESUME_STATE_OFFSET] =
        (uint8_t)entry->operation.resume_state;
    p6c_store_u16_be(
        &summary[P6C_SUMMARY_FLAGS_OFFSET],
        p6c_service_summary_flags(entry));
    p6c_store_u32_be(
        &summary[P6C_SUMMARY_EXIT_STATUS_OFFSET],
        (uint32_t)entry->operation.exit_status);
    memcpy(&summary[P6C_SUMMARY_REQUEST_DIGEST_OFFSET],
           entry->request_digest, P6C_SHA256_BYTES);
    memcpy(&summary[P6C_SUMMARY_EXECUTABLE_DIGEST_OFFSET],
           entry->executable_digest, P6C_SHA256_BYTES);
    memcpy(&summary[P6C_SUMMARY_PUBLICATION_DIGEST_OFFSET],
           entry->publication_digest, P6C_SHA256_BYTES);
}

static struct p6c_service_tombstone *p6c_service_find_tombstone(
    struct p6c_service_registry *registry,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES])
{
    size_t index;

    for (index = 0U; index < registry->tombstone_count; ++index) {
        if (memcmp(
                &registry->tombstones[index]
                     .summary[P6C_SUMMARY_OPERATION_ID_OFFSET],
                operation_id, P6C_OPERATION_ID_BYTES) == 0) {
            return &registry->tombstones[index];
        }
    }
    return NULL;
}

static enum p6c_result p6c_service_add_tombstone(
    struct p6c_service_registry *registry,
    uid_t opening_user,
    const uint8_t request_digest[static P6C_SHA256_BYTES],
    const uint8_t summary[static P6C_OPERATION_SUMMARY_BYTES])
{
    size_t position = 0U;

    if ((registry == NULL) || (request_digest == NULL) ||
        (summary == NULL)) {
        return P6C_RESULT_INVALID;
    }
    while ((position < registry->tombstone_count) &&
           (memcmp(
                &registry->tombstones[position]
                     .summary[P6C_SUMMARY_OPERATION_ID_OFFSET],
                &summary[P6C_SUMMARY_OPERATION_ID_OFFSET],
                P6C_OPERATION_ID_BYTES) < 0)) {
        ++position;
    }
    if ((position < registry->tombstone_count) &&
        (memcmp(
             &registry->tombstones[position]
                  .summary[P6C_SUMMARY_OPERATION_ID_OFFSET],
             &summary[P6C_SUMMARY_OPERATION_ID_OFFSET],
             P6C_OPERATION_ID_BYTES) == 0)) {
        return P6C_RESULT_CONFLICT;
    }
    if (registry->tombstone_count >= P6C_TOMBSTONE_CAPACITY) {
        return P6C_RESULT_LIMIT;
    }
    ++registry->tombstone_count;
    if (position < registry->tombstone_count - 1U) {
        memmove(
            &registry->tombstones[position + 1U],
            &registry->tombstones[position],
            (registry->tombstone_count - position - 1U) *
                sizeof(registry->tombstones[0]));
    }
    memset(&registry->tombstones[position], 0,
           sizeof(registry->tombstones[position]));
    registry->tombstones[position].occupied = true;
    registry->tombstones[position].opening_user = opening_user;
    memcpy(registry->tombstones[position].request_digest,
           request_digest, P6C_SHA256_BYTES);
    memcpy(registry->tombstones[position].summary, summary,
           P6C_OPERATION_SUMMARY_BYTES);
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_service_archive_acknowledged(
    struct p6c_service_registry *registry,
    struct p6c_service_entry *entry,
    uint8_t summary[static P6C_OPERATION_SUMMARY_BYTES])
{
    enum p6c_result result;

    if ((registry == NULL) || (entry == NULL) || (summary == NULL) ||
        !entry->occupied ||
        (entry->operation.state != P6C_OPERATION_ACKNOWLEDGED) ||
        entry->operation.authority_retained) {
        return P6C_RESULT_INVALID;
    }
    p6c_service_encode_summary(entry, summary);
    result = p6c_service_add_tombstone(
        registry, entry->opening_user, entry->request_digest, summary);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    p6c_service_entry_close(entry);
    p6c_service_entry_reset(entry);
    if (registry->count == 0U) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    --registry->count;
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_service_validate_root(
    const struct p6c_owned_fd *root, enum p6c_descriptor_type type)
{
    struct stat status;

    if ((root == NULL) || !p6c_owned_fd_is_live(root) ||
        (root->type != type) ||
        (fstat(root->descriptor, &status) != 0) ||
        !S_ISDIR(status.st_mode) || (status.st_uid != geteuid()) ||
        ((status.st_mode & (S_IWGRP | S_IWOTH)) != 0)) {
        return P6C_RESULT_UNSAFE;
    }
    return P6C_RESULT_OK;
}

static ssize_t p6c_service_write_no_sigpipe(
    int descriptor, const uint8_t *packet, size_t packet_size)
{
    sigset_t blocked;
    sigset_t previous;
    sigset_t pending;
    struct timespec no_wait = {0, 0};
    bool pipe_was_pending = false;
    ssize_t amount;
    int saved_errno;

    if ((sigemptyset(&blocked) != 0) ||
        (sigaddset(&blocked, SIGPIPE) != 0) ||
        (sigprocmask(SIG_BLOCK, &blocked, &previous) != 0)) {
        return -1;
    }
    if (sigpending(&pending) == 0) {
        pipe_was_pending = sigismember(&pending, SIGPIPE) == 1;
    }
    do {
        amount = write(descriptor, packet, packet_size);
    } while ((amount < 0) && (errno == EINTR));
    saved_errno = errno;
    if ((amount < 0) && (saved_errno == EPIPE) && !pipe_was_pending &&
        (sigpending(&pending) == 0) &&
        (sigismember(&pending, SIGPIPE) == 1)) {
        while ((sigtimedwait(&blocked, NULL, &no_wait) < 0) &&
               (errno == EINTR)) {
        }
    }
    if (sigprocmask(SIG_SETMASK, &previous, NULL) != 0) {
        return -1;
    }
    errno = saved_errno;
    return amount;
}

static enum p6c_result p6c_service_send(
    const struct p6c_owned_fd *socket_owner,
    uint16_t message_type,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    const uint8_t *payload,
    size_t payload_size)
{
    uint8_t *packet;
    size_t packet_size;
    ssize_t amount;

    if ((socket_owner == NULL) || (request_id == NULL) ||
        ((payload == NULL) && (payload_size != 0U)) ||
        (payload_size > (size_t)P6C_MAX_PAYLOAD_BYTES)) {
        return P6C_RESULT_INVALID;
    }
    packet_size = P6C_HEADER_SIZE + payload_size;
    packet = malloc(packet_size);
    if (packet == NULL) {
        return P6C_RESULT_SYSTEM;
    }
    p6c_encode_header_v1(
        packet, message_type, request_id, (uint32_t)payload_size,
        p6c_crc32(payload, payload_size));
    if (payload_size != 0U) {
        memcpy(&packet[P6C_HEADER_SIZE], payload, payload_size);
    }
#ifdef P6C_TESTING
    if (p6c_failpoint_active(P6C_FAIL_SERVICE_SEND)) {
        free(packet);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
#endif
#ifdef P6C_TESTING
    if (p6c_service_test_output != NULL) {
        if (p6c_service_test_output_count != NULL) {
            size_t output_index = *p6c_service_test_output_count;

            if ((p6c_service_test_output_sizes == NULL) ||
                (output_index >= p6c_service_test_input_count) ||
                (packet_size >
                 p6c_service_test_output_capacity -
                     p6c_service_test_output_offset)) {
                free(packet);
                return P6C_RESULT_LIMIT;
            }
            memcpy(
                &p6c_service_test_output[
                    p6c_service_test_output_offset],
                packet, packet_size);
            p6c_service_test_output_sizes[output_index] = packet_size;
            p6c_service_test_output_offset += packet_size;
            *p6c_service_test_output_count = output_index + 1U;
            free(packet);
            return P6C_RESULT_OK;
        }
        if ((p6c_service_test_output_size == NULL) ||
            (packet_size > p6c_service_test_output_capacity)) {
            free(packet);
            return P6C_RESULT_LIMIT;
        }
        memcpy(p6c_service_test_output, packet, packet_size);
        *p6c_service_test_output_size = packet_size;
        free(packet);
        return P6C_RESULT_OK;
    }
#endif
    amount = p6c_service_write_no_sigpipe(
        socket_owner->descriptor, packet, packet_size);
    free(packet);
    if (amount != (ssize_t)packet_size) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

static ssize_t p6c_service_receive(
    const struct p6c_owned_fd *socket_owner,
    uint8_t *packet,
    size_t capacity,
    int *message_flags,
    struct p6c_received_authority *authority)
{
    if (authority == NULL) {
        errno = EINVAL;
        return -1;
    }
    authority->credential_directory = P6C_INVALID_DESCRIPTOR;
    authority->invalid = false;
#ifdef P6C_TESTING
    if (p6c_failpoint_active(P6C_FAIL_SERVICE_RECEIVE)) {
        errno = EIO;
        return -1;
    }
    if (p6c_service_test_inputs != NULL) {
        size_t input_size;

        if (p6c_service_test_input_index >=
            p6c_service_test_input_count) {
            if (p6c_service_test_disconnect_after_input) {
                return 0;
            }
            return P6C_TEST_INPUT_COMPLETE;
        }
        input_size = p6c_service_test_input_sizes[
            p6c_service_test_input_index];
        if (input_size > capacity) {
            *message_flags = MSG_TRUNC;
            memcpy(
                packet,
                p6c_service_test_inputs[p6c_service_test_input_index],
                capacity);
            ++p6c_service_test_input_index;
            return (ssize_t)capacity;
        }
        memcpy(
            packet,
            p6c_service_test_inputs[p6c_service_test_input_index],
            input_size);
        ++p6c_service_test_input_index;
        *message_flags = 0;
        return (ssize_t)input_size;
    }
    if (p6c_service_test_input != NULL) {
        if (p6c_service_test_input_consumed) {
            if (p6c_service_test_disconnect_after_input) {
                return 0;
            }
            return P6C_TEST_INPUT_COMPLETE;
        }
        p6c_service_test_input_consumed = true;
        if (p6c_service_test_input_size > capacity) {
            *message_flags = MSG_TRUNC;
            memcpy(packet, p6c_service_test_input, capacity);
            return (ssize_t)capacity;
        }
        memcpy(packet, p6c_service_test_input, p6c_service_test_input_size);
        *message_flags = 0;
        return (ssize_t)p6c_service_test_input_size;
    }
#endif
    {
        struct iovec vector;
        struct msghdr message;
        union {
            struct cmsghdr alignment;
            unsigned char bytes[
                CMSG_SPACE(
                    sizeof(int) * P6C_MAX_RECEIVED_DESCRIPTORS)];
        } control;
        ssize_t amount;
        struct cmsghdr *header;
        size_t record_count = 0U;
        size_t descriptor_count = 0U;

        memset(&message, 0, sizeof(message));
        memset(&control, 0, sizeof(control));
        vector.iov_base = packet;
        vector.iov_len = capacity;
        message.msg_iov = &vector;
        message.msg_iovlen = 1U;
        message.msg_control = control.bytes;
        message.msg_controllen = sizeof(control.bytes);
        do {
            amount = recvmsg(
                socket_owner->descriptor, &message, MSG_CMSG_CLOEXEC);
        } while ((amount < 0) && (errno == EINTR));
        *message_flags = message.msg_flags;
        if (amount < 0) {
            return amount;
        }
        for (header = CMSG_FIRSTHDR(&message);
             header != NULL;
             header = CMSG_NXTHDR(&message, header)) {
            size_t payload_size;
            size_t descriptor_index;
            int *descriptors;

            ++record_count;
            if ((header->cmsg_len < CMSG_LEN(0U)) ||
                (header->cmsg_len >
                 (size_t)((unsigned char *)control.bytes +
                              message.msg_controllen -
                          (unsigned char *)header))) {
                authority->invalid = true;
                break;
            }
            payload_size = header->cmsg_len - CMSG_LEN(0U);
            if ((header->cmsg_level != SOL_SOCKET) ||
                (header->cmsg_type != SCM_RIGHTS) ||
                (payload_size == 0U) ||
                ((payload_size % sizeof(int)) != 0U)) {
                authority->invalid = true;
                continue;
            }
            descriptors = (int *)(void *)CMSG_DATA(header);
            for (descriptor_index = 0U;
                 descriptor_index < payload_size / sizeof(int);
                 ++descriptor_index) {
                int descriptor = descriptors[descriptor_index];

                ++descriptor_count;
                if ((descriptor < 0) ||
                    (fcntl(descriptor, F_GETFD) != FD_CLOEXEC)) {
                    authority->invalid = true;
                    if (descriptor >= 0) {
                        (void)close(descriptor);
                    }
                    continue;
                }
                if ((record_count == 1U) &&
                    (descriptor_count == 1U) &&
                    !authority->invalid) {
                    authority->credential_directory = descriptor;
                } else {
                    authority->invalid = true;
                    (void)close(descriptor);
                }
            }
        }
        if ((record_count > 1U) || (descriptor_count > 1U) ||
            ((message.msg_flags & MSG_CTRUNC) != 0)) {
            authority->invalid = true;
        }
        if (authority->invalid &&
            (authority->credential_directory >= 0)) {
            (void)close(authority->credential_directory);
            authority->credential_directory = P6C_INVALID_DESCRIPTOR;
        }
        return amount;
    }
}

static ssize_t p6c_service_receive_next(
    struct p6c_service_registry *registry, uint8_t *packet,
    size_t capacity, int *message_flags,
    struct p6c_received_authority *authority)
{
#ifdef P6C_TESTING
    if ((p6c_service_test_inputs != NULL) ||
        (p6c_service_test_input != NULL)) {
        return p6c_service_receive(
            &registry->configuration->socket, packet, capacity,
            message_flags, authority);
    }
#endif
    for (;;) {
        struct pollfd descriptors[
            1U + (P6C_MAX_OPERATIONS * 2U)];
        struct p6c_service_entry *owners[
            P6C_MAX_OPERATIONS * 2U];
        size_t descriptor_count = 1U;
        size_t index;
        int poll_result;

        memset(descriptors, 0, sizeof(descriptors));
        memset(owners, 0, sizeof(owners));
        descriptors[0].fd =
            registry->configuration->socket.descriptor;
        descriptors[0].events =
            (short)(POLLIN | POLLHUP | POLLERR);
        for (index = 0U; index < P6C_MAX_OPERATIONS; ++index) {
            struct p6c_service_entry *entry =
                &registry->entries[index];

            if (!entry->occupied) {
                continue;
            }
            if (p6c_owned_fd_is_live(
                    &entry->stdout_channel.first)) {
                descriptors[descriptor_count].fd =
                    entry->stdout_channel.first.descriptor;
                descriptors[descriptor_count].events =
                    (short)(POLLIN | POLLHUP | POLLERR);
                owners[descriptor_count - 1U] = entry;
                ++descriptor_count;
            }
            if (p6c_owned_fd_is_live(
                    &entry->stderr_channel.first)) {
                descriptors[descriptor_count].fd =
                    entry->stderr_channel.first.descriptor;
                descriptors[descriptor_count].events =
                    (short)(POLLIN | POLLHUP | POLLERR);
                owners[descriptor_count - 1U] = entry;
                ++descriptor_count;
            }
        }
        do {
            poll_result = poll(
                descriptors, (nfds_t)descriptor_count, -1);
        } while ((poll_result < 0) && (errno == EINTR));
        if (poll_result < 0) {
            return -1;
        }
        for (index = 1U; index < descriptor_count; ++index) {
            if ((descriptors[index].revents != 0) &&
                (owners[index - 1U] != NULL) &&
                (p6c_service_drain_entry(
                     owners[index - 1U]) != P6C_RESULT_OK)) {
                (void)p6c_service_mark_recovery(
                    owners[index - 1U],
                    owners[index - 1U]->operation.resume_state);
                errno = EIO;
                return -1;
            }
        }
        if (descriptors[0].revents != 0) {
            return p6c_service_receive(
                &registry->configuration->socket, packet, capacity,
                message_flags, authority);
        }
    }
}

static enum p6c_result p6c_service_send_error(
    const struct p6c_owned_fd *socket_owner,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    enum p6c_public_status status,
    const char *code,
    bool retryable,
    enum p6c_operation_state operation_state,
    const uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES])
{
    uint8_t payload[2U + 1U + 1U + P6C_RECOVERY_TOKEN_BYTES + 1U +
                    P6C_MAX_PUBLIC_CODE_BYTES];
    struct p6c_public_error error;
    size_t code_length;

    if (p6c_public_error_set(
            &error, status, code, retryable, operation_state,
            recovery_token) != P6C_RESULT_OK) {
        return P6C_RESULT_INVALID;
    }
    memset(payload, 0, sizeof(payload));
    p6c_store_u16_be(payload, (uint16_t)error.status);
    payload[2] = error.retryable ? UINT8_C(1) : UINT8_C(0);
    payload[3] = (uint8_t)error.operation_state;
    memcpy(&payload[4], error.recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    code_length = strlen(error.public_code);
    payload[4U + P6C_RECOVERY_TOKEN_BYTES] = (uint8_t)code_length;
    memcpy(&payload[5U + P6C_RECOVERY_TOKEN_BYTES], error.public_code,
           code_length);
    return p6c_service_send(
        socket_owner, P6C_ERROR_MESSAGE_TYPE, request_id, payload,
        sizeof(payload));
}

static const struct p6c_field_view *p6c_service_find_field(
    const struct p6c_frame_view *frame, uint16_t field_id)
{
    size_t index;

    for (index = 0U; index < frame->field_count; ++index) {
        if (frame->fields[index].field_id == field_id) {
            return &frame->fields[index];
        }
    }
    return NULL;
}

static enum p6c_result p6c_service_send_summary(
    const struct p6c_owned_fd *socket_owner,
    uint16_t request_type,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    const struct p6c_service_entry *entry)
{
    uint8_t summary[P6C_OPERATION_SUMMARY_BYTES];

    p6c_service_encode_summary(entry, summary);
    return p6c_service_send(
        socket_owner, (uint16_t)(P6C_RESPONSE_BIT | request_type),
        request_id, summary, sizeof(summary));
}

static enum p6c_result p6c_service_send_result_error(
    const struct p6c_owned_fd *socket_owner,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    enum p6c_result operation_result,
    const struct p6c_service_entry *entry)
{
    uint8_t zero_token[P6C_RECOVERY_TOKEN_BYTES];
    const uint8_t *token = zero_token;
    enum p6c_operation_state state = P6C_OPERATION_ABSENT;
    enum p6c_public_status status;
    const char *code;
    bool retryable = false;

    memset(zero_token, 0, sizeof(zero_token));
    if (entry != NULL) {
        token = entry->operation.recovery_token;
        state = entry->operation.state;
    }
    switch (operation_result) {
    case P6C_RESULT_UNAUTHORIZED:
        status = P6C_STATUS_UNAUTHORIZED;
        code = "UNAUTHORIZED";
        break;
    case P6C_RESULT_CONFLICT:
        status = P6C_STATUS_CONFLICT;
        code = "CONFLICT";
        break;
    case P6C_RESULT_LIMIT:
        status = P6C_STATUS_LIMIT_EXCEEDED;
        code = "LIMIT_EXCEEDED";
        break;
    case P6C_RESULT_TIMEOUT:
        status = P6C_STATUS_TIMEOUT;
        code = "TIMEOUT";
        retryable = true;
        break;
    case P6C_RESULT_RECOVERY_REQUIRED:
        status = P6C_STATUS_RECOVERY_REQUIRED;
        code = "RECOVERY_REQUIRED";
        retryable = true;
        state = P6C_OPERATION_RECOVERY_REQUIRED;
        break;
    case P6C_RESULT_UNSUPPORTED:
        status = P6C_STATUS_INTERNAL;
        code = "UNSUPPORTED_KERNEL";
        break;
    case P6C_RESULT_INVALID:
    case P6C_RESULT_MALFORMED:
    case P6C_RESULT_UNSAFE:
        status = P6C_STATUS_INVALID_REQUEST;
        code = "INVALID_REQUEST";
        break;
    default:
        status = P6C_STATUS_INTERNAL;
        code = "INTERNAL";
        break;
    }
    return p6c_service_send_error(
        socket_owner, request_id, status, code, retryable, state, token);
}

static enum p6c_result p6c_service_reconcile_ack_transcript(
    struct p6c_service_registry *registry,
    struct p6c_service_entry *entry, const char *name,
    enum p6c_stream_identity stream, uint64_t observed_size,
    uint64_t retained_size, bool truncated,
    const uint8_t digest[static P6C_SHA256_BYTES],
    const uint8_t retained_digest[static P6C_SHA256_BYTES],
    struct p6c_transcript *transcript)
{
    struct stat status;
    enum p6c_result result;

    if (fstatat(
            registry->configuration->journal_root.descriptor,
            name, &status, AT_SYMLINK_NOFOLLOW) != 0) {
        return (errno == ENOENT) ? P6C_RESULT_OK :
                                  P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_transcript_recover(
        &registry->configuration->journal_root, name, stream,
        observed_size, retained_size, truncated, digest,
        retained_digest, transcript);
    if (result != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_transcript_unlink(
        &registry->configuration->journal_root, name, transcript);
    if ((result != P6C_RESULT_OK) ||
        (p6c_transcript_close(transcript) != P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    (void)entry;
    return P6C_RESULT_OK;
}

static int p6c_service_token_nonzero(
    const uint8_t token[static P6C_RECOVERY_TOKEN_BYTES])
{
    size_t index;

    for (index = 0U; index < P6C_RECOVERY_TOKEN_BYTES; ++index) {
        if (token[index] != UINT8_C(0)) {
            return 1;
        }
    }
    return 0;
}

static enum p6c_result p6c_service_reconcile_removal_intent(
    struct p6c_service_registry *registry,
    struct p6c_service_entry *entry)
{
    const uint8_t *transcript_payload;
    const uint8_t *result_payload;
    uint8_t flags;
    struct stat status;
    enum p6c_result stdout_result;
    enum p6c_result stderr_result;

    if (!entry->journal.cgroup_removal_intent) {
        return P6C_RESULT_OK;
    }
    if ((entry->journal.durable_state ==
         P6C_OPERATION_RESULT_RETAINED) ||
        (entry->journal.durable_state ==
         P6C_OPERATION_ACKNOWLEDGED)) {
        if ((entry->journal.state_payload_lengths[
                 P6C_OPERATION_RESULT_RETAINED] !=
             (uint8_t)P6C_RESULT_PAYLOAD_BYTES) ||
            (memcmp(
                 entry->journal.retained_result_payload,
                 entry->journal.state_payloads[
                     P6C_OPERATION_RESULT_RETAINED],
                 P6C_RESULT_PAYLOAD_BYTES) != 0)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        return P6C_RESULT_OK;
    }
    if ((entry->journal.durable_state !=
         P6C_OPERATION_TRANSCRIPTS_FINAL) ||
        !entry->journal.transcript_digests_committed ||
        (entry->journal.state_payload_lengths[
             P6C_OPERATION_TRANSCRIPTS_FINAL] !=
         (uint8_t)P6C_TRANSCRIPTS_PAYLOAD_BYTES)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    transcript_payload = entry->journal.state_payloads[
        P6C_OPERATION_TRANSCRIPTS_FINAL];
    result_payload = entry->journal.retained_result_payload;
    flags = result_payload[P6C_RESULT_FLAGS_OFFSET];
    stdout_result = p6c_transcript_recover(
        &registry->configuration->journal_root,
        entry->stdout_name, P6C_STREAM_STDOUT,
        p6c_service_load_u64(
            &result_payload[P6C_RESULT_STDOUT_OBSERVED_OFFSET]),
        p6c_service_load_u64(
            &result_payload[P6C_RESULT_STDOUT_RETAINED_OFFSET]),
        (flags & P6C_RESULT_FLAG_STDOUT_TRUNCATED) != 0U,
        transcript_payload, entry->journal.stdout_retained_digest,
        &entry->stdout_transcript);
    stderr_result = p6c_transcript_recover(
        &registry->configuration->journal_root,
        entry->stderr_name, P6C_STREAM_STDERR,
        p6c_service_load_u64(
            &result_payload[P6C_RESULT_STDERR_OBSERVED_OFFSET]),
        p6c_service_load_u64(
            &result_payload[P6C_RESULT_STDERR_RETAINED_OFFSET]),
        (flags & P6C_RESULT_FLAG_STDERR_TRUNCATED) != 0U,
        &transcript_payload[P6C_SHA256_BYTES],
        entry->journal.stderr_retained_digest,
        &entry->stderr_transcript);
    if ((stdout_result != P6C_RESULT_OK) ||
        (stderr_result != P6C_RESULT_OK)) {
        if (stdout_result == P6C_RESULT_OK) {
            (void)p6c_transcript_close(&entry->stdout_transcript);
        }
        if (stderr_result == P6C_RESULT_OK) {
            (void)p6c_transcript_close(&entry->stderr_transcript);
        }
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((p6c_transcript_close(&entry->stdout_transcript) !=
         P6C_RESULT_OK) ||
        (p6c_transcript_close(&entry->stderr_transcript) !=
         P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    {
        char quarantine[P6C_CGROUP_QUARANTINE_NAME_BYTES];
        const char *physical_name = entry->cgroup_name;
        int named_result = fstatat(
            registry->configuration->cgroup_root.descriptor,
            physical_name, &status, AT_SYMLINK_NOFOLLOW);

        if ((named_result != 0) && (errno == ENOENT) &&
            (p6c_cgroup_quarantine_name(
                 entry->cgroup_name, quarantine) == P6C_RESULT_OK)) {
            named_result = fstatat(
                registry->configuration->cgroup_root.descriptor,
                quarantine, &status, AT_SYMLINK_NOFOLLOW);
            if (named_result == 0) {
                physical_name = quarantine;
            }
        }
        if (named_result == 0) {
            /*
             * Device and inode are not generation identities: both can be
             * reused after the original cgroup is removed. A restarted
             * process has no descriptor custody that can distinguish an
             * empty original from a same-name replacement, so any surviving
             * physical object requires operator recovery.
             */
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (errno != ENOENT) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
    }
    if (p6c_journal_append(
            &entry->journal, P6C_OPERATION_RESULT_RETAINED,
            result_payload, P6C_RESULT_PAYLOAD_BYTES) !=
        P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_service_load_one_journal(
    struct p6c_service_registry *registry, const char *name,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES])
{
    struct p6c_service_entry *entry;
    enum p6c_journal_recovery recovery;
    enum p6c_result recover_result;
    const uint8_t *reserved;
    enum p6c_operation_state durable;

    if (p6c_service_find_entry(registry, operation_id) != NULL) {
        registry->start_blocked = true;
        return P6C_RESULT_CONFLICT;
    }
    entry = p6c_service_reserve_entry(registry);
    if (entry == NULL) {
        registry->start_blocked = true;
        return P6C_RESULT_LIMIT;
    }
    memcpy(entry->operation.operation_id, operation_id,
           P6C_OPERATION_ID_BYTES);
    memcpy(entry->journal_name, name, strlen(name) + 1U);
    recover_result = p6c_journal_recover(
        &registry->configuration->journal_root, name, operation_id,
        geteuid(), &entry->journal, &recovery);
    if ((recover_result != P6C_RESULT_OK) &&
        (recover_result != P6C_RESULT_RECOVERY_REQUIRED)) {
        registry->start_blocked = true;
        entry->opening_user =
            registry->configuration->controller_user;
        entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
        entry->operation.resume_state = P6C_OPERATION_ABSENT;
        entry->operation.authority_retained = true;
        return recover_result;
    }
    if ((recover_result == P6C_RESULT_RECOVERY_REQUIRED) &&
        (recovery == P6C_JOURNAL_INVALID)) {
        registry->start_blocked = true;
        return P6C_RESULT_UNSAFE;
    }
    if ((entry->journal.state_payload_lengths[
             P6C_OPERATION_RESERVED] !=
         (uint8_t)P6C_RESERVED_PAYLOAD_BYTES) ||
        (entry->journal.state_payloads[
             P6C_OPERATION_RESERVED][0] != UINT8_C(1))) {
        registry->start_blocked = true;
        entry->opening_user =
            registry->configuration->controller_user;
        entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
        entry->operation.resume_state =
            entry->journal.durable_state;
        entry->operation.authority_retained = true;
        return P6C_RESULT_OK;
    }
    reserved = entry->journal.state_payloads[P6C_OPERATION_RESERVED];
    entry->opening_user = (uid_t)p6c_service_load_u32(&reserved[1]);
    memcpy(entry->operation.recovery_token, &reserved[5],
           P6C_RECOVERY_TOKEN_BYTES);
    memcpy(entry->request_digest, &reserved[21], P6C_SHA256_BYTES);
    if ((entry->opening_user !=
         registry->configuration->controller_user) ||
        !p6c_service_token_nonzero(
            entry->operation.recovery_token)) {
        registry->start_blocked = true;
        entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
        entry->operation.resume_state =
            entry->journal.durable_state;
        entry->operation.authority_retained = true;
        return P6C_RESULT_OK;
    }
    if (entry->journal.state_payload_lengths[
            P6C_OPERATION_EXECUTABLE_PINNED] ==
        (uint8_t)P6C_SHA256_BYTES) {
        memcpy(
            entry->executable_digest,
            entry->journal.state_payloads[
                P6C_OPERATION_EXECUTABLE_PINNED],
            P6C_SHA256_BYTES);
    }
    if (entry->journal.bundle_committed) {
        memcpy(entry->publication_identity,
               entry->journal.publication_identity, P6C_SHA256_BYTES);
        memcpy(entry->publication_digest,
               entry->journal.manifest_digest, P6C_SHA256_BYTES);
    }
    {
        char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];

        p6c_service_hex(
            operation_id, P6C_OPERATION_ID_BYTES, operation_hex);
        memcpy(entry->stdout_name, operation_hex,
               P6C_OPERATION_ID_BYTES * 2U);
        memcpy(
            &entry->stdout_name[P6C_OPERATION_ID_BYTES * 2U],
            ".stdout", sizeof(".stdout"));
        memcpy(entry->stderr_name, operation_hex,
               P6C_OPERATION_ID_BYTES * 2U);
        memcpy(
            &entry->stderr_name[P6C_OPERATION_ID_BYTES * 2U],
            ".stderr", sizeof(".stderr"));
    }
    durable = entry->journal.durable_state;
    if (entry->journal.cgroup_allocation_intent) {
        memcpy(
            entry->cgroup_name,
            entry->journal.cgroup_allocation_name,
            P6C_CGROUP_NAME_BYTES);
        if (!p6c_service_cgroup_name_valid(entry->cgroup_name)) {
            registry->start_blocked = true;
            entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
            entry->operation.resume_state = durable;
            entry->operation.authority_retained = true;
            return P6C_RESULT_OK;
        }
    }
    if (entry->journal.state_payload_lengths[
            P6C_OPERATION_CGROUP_CREATED] != 0U) {
        if (entry->journal.state_payload_lengths[
                P6C_OPERATION_CGROUP_CREATED] !=
            (uint8_t)P6C_CGROUP_CREATED_PAYLOAD_BYTES) {
            registry->start_blocked = true;
            entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
            entry->operation.resume_state = durable;
            entry->operation.authority_retained = true;
            return P6C_RESULT_OK;
        }
        memcpy(
            entry->cgroup_name,
            entry->journal.state_payloads[
                P6C_OPERATION_CGROUP_CREATED],
            P6C_CGROUP_NAME_BYTES - 1U);
        entry->cgroup_name[P6C_CGROUP_NAME_BYTES - 1U] = '\0';
        if (!entry->journal.cgroup_allocation_intent ||
            !entry->journal.cgroup_created_identity ||
            !p6c_service_cgroup_name_valid(entry->cgroup_name) ||
            (memcmp(
                 entry->cgroup_name,
                 entry->journal.cgroup_allocation_name,
                 P6C_CGROUP_NAME_BYTES) != 0)) {
            registry->start_blocked = true;
            entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
            entry->operation.resume_state = durable;
            entry->operation.authority_retained = true;
            return P6C_RESULT_OK;
        }
    } else if (durable >= P6C_OPERATION_CGROUP_CREATED) {
        registry->start_blocked = true;
        entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
        entry->operation.resume_state = durable;
        entry->operation.authority_retained = true;
        return P6C_RESULT_OK;
    }
    if (p6c_service_reconcile_removal_intent(
            registry, entry) != P6C_RESULT_OK) {
        registry->start_blocked = true;
        entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
        entry->operation.resume_state = durable;
        entry->operation.authority_retained = true;
        return P6C_RESULT_OK;
    }
    durable = entry->journal.durable_state;
    entry->operation.journal = &entry->journal;
    entry->operation.state = durable;
    entry->operation.resume_state = durable;
    entry->operation.authority_retained =
        durable != P6C_OPERATION_ACKNOWLEDGED;
    if (durable == P6C_OPERATION_ACKNOWLEDGED) {
        const uint8_t *transcript_payload =
            entry->journal.state_payloads[
                P6C_OPERATION_TRANSCRIPTS_FINAL];
        const uint8_t *result_payload =
            entry->journal.state_payloads[
                P6C_OPERATION_RESULT_RETAINED];
        const uint8_t *stdout_retained_digest =
            entry->journal.transcript_digests_committed ?
                entry->journal.stdout_retained_digest :
                transcript_payload;
        const uint8_t *stderr_retained_digest =
            entry->journal.transcript_digests_committed ?
                entry->journal.stderr_retained_digest :
                &transcript_payload[P6C_SHA256_BYTES];
        uint8_t flags = result_payload[P6C_RESULT_FLAGS_OFFSET];

        if ((entry->journal.state_payload_lengths[
                 P6C_OPERATION_TRANSCRIPTS_FINAL] !=
             (uint8_t)P6C_TRANSCRIPTS_PAYLOAD_BYTES) ||
            (entry->journal.state_payload_lengths[
                 P6C_OPERATION_RESULT_RETAINED] !=
             (uint8_t)P6C_RESULT_PAYLOAD_BYTES) ||
            (((flags & (P6C_RESULT_FLAG_STDOUT_TRUNCATED |
                        P6C_RESULT_FLAG_STDERR_TRUNCATED)) != 0U) &&
             !entry->journal.transcript_digests_committed) ||
            (p6c_service_reconcile_ack_transcript(
                 registry, entry, entry->stdout_name,
                 P6C_STREAM_STDOUT,
                 p6c_service_load_u64(
                     &result_payload[
                         P6C_RESULT_STDOUT_OBSERVED_OFFSET]),
                 p6c_service_load_u64(
                     &result_payload[
                         P6C_RESULT_STDOUT_RETAINED_OFFSET]),
                 (flags & P6C_RESULT_FLAG_STDOUT_TRUNCATED) != 0U,
                 transcript_payload,
                 stdout_retained_digest,
                 &entry->stdout_transcript) != P6C_RESULT_OK) ||
            (p6c_service_reconcile_ack_transcript(
                 registry, entry, entry->stderr_name,
                 P6C_STREAM_STDERR,
                 p6c_service_load_u64(
                     &result_payload[
                         P6C_RESULT_STDERR_OBSERVED_OFFSET]),
                 p6c_service_load_u64(
                     &result_payload[
                         P6C_RESULT_STDERR_RETAINED_OFFSET]),
                 (flags & P6C_RESULT_FLAG_STDERR_TRUNCATED) != 0U,
                 &transcript_payload[P6C_SHA256_BYTES],
                 stderr_retained_digest,
                 &entry->stderr_transcript) != P6C_RESULT_OK)) {
            registry->start_blocked = true;
            entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
            entry->operation.resume_state =
                P6C_OPERATION_ACKNOWLEDGED;
            entry->operation.authority_retained = true;
        }
    }
    if ((durable == P6C_OPERATION_RESULT_RETAINED) &&
        (entry->journal.state_payload_lengths[
             P6C_OPERATION_TRANSCRIPTS_FINAL] ==
         (uint8_t)P6C_TRANSCRIPTS_PAYLOAD_BYTES) &&
        (entry->journal.state_payload_lengths[
             P6C_OPERATION_RESULT_RETAINED] ==
         (uint8_t)P6C_RESULT_PAYLOAD_BYTES)) {
        const uint8_t *transcript_payload =
            entry->journal.state_payloads[
                P6C_OPERATION_TRANSCRIPTS_FINAL];
        const uint8_t *result_payload =
            entry->journal.state_payloads[
                P6C_OPERATION_RESULT_RETAINED];
        const uint8_t *stdout_retained_digest =
            entry->journal.transcript_digests_committed ?
                entry->journal.stdout_retained_digest :
                transcript_payload;
        const uint8_t *stderr_retained_digest =
            entry->journal.transcript_digests_committed ?
                entry->journal.stderr_retained_digest :
                &transcript_payload[P6C_SHA256_BYTES];
        uint8_t flags = result_payload[P6C_RESULT_FLAGS_OFFSET];
        enum p6c_result stdout_result;
        enum p6c_result stderr_result;

        if (((flags & (P6C_RESULT_FLAG_STDOUT_TRUNCATED |
                       P6C_RESULT_FLAG_STDERR_TRUNCATED)) != 0U) &&
            !entry->journal.transcript_digests_committed) {
            stdout_result = P6C_RESULT_RECOVERY_REQUIRED;
            stderr_result = P6C_RESULT_RECOVERY_REQUIRED;
        } else {
            stdout_result = p6c_transcript_recover(
            &registry->configuration->journal_root,
            entry->stdout_name, P6C_STREAM_STDOUT,
            p6c_service_load_u64(
                &result_payload[
                    P6C_RESULT_STDOUT_OBSERVED_OFFSET]),
            p6c_service_load_u64(
                &result_payload[
                    P6C_RESULT_STDOUT_RETAINED_OFFSET]),
            (flags & P6C_RESULT_FLAG_STDOUT_TRUNCATED) != 0U,
            transcript_payload, stdout_retained_digest,
            &entry->stdout_transcript);
            stderr_result = p6c_transcript_recover(
            &registry->configuration->journal_root,
            entry->stderr_name, P6C_STREAM_STDERR,
            p6c_service_load_u64(
                &result_payload[
                    P6C_RESULT_STDERR_OBSERVED_OFFSET]),
            p6c_service_load_u64(
                &result_payload[
                    P6C_RESULT_STDERR_RETAINED_OFFSET]),
            (flags & P6C_RESULT_FLAG_STDERR_TRUNCATED) != 0U,
            &transcript_payload[P6C_SHA256_BYTES],
            stderr_retained_digest,
            &entry->stderr_transcript);
        }
        if ((stdout_result != P6C_RESULT_OK) ||
            (stderr_result != P6C_RESULT_OK)) {
            registry->start_blocked = true;
            entry->operation.state =
                P6C_OPERATION_RECOVERY_REQUIRED;
            entry->operation.resume_state =
                P6C_OPERATION_RESULT_RETAINED;
        } else {
            entry->operation.stdout_transcript =
                &entry->stdout_transcript;
            entry->operation.stderr_transcript =
                &entry->stderr_transcript;
            entry->operation.exit_status =
                (int32_t)p6c_service_load_u32(
                    &result_payload[
                        P6C_RESULT_EXIT_STATUS_OFFSET]);
        }
    } else if (durable == P6C_OPERATION_RESULT_RETAINED) {
        registry->start_blocked = true;
        entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
        entry->operation.resume_state =
            P6C_OPERATION_RESULT_RETAINED;
    }
    if (entry->journal.bundle_committed &&
        (p6c_publication_recover(
             &registry->configuration->evidence_root,
             entry->operation.operation_id,
             entry->publication_identity,
             entry->publication_digest,
             &entry->publication) != P6C_RESULT_OK)) {
        registry->start_blocked = true;
        entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
        entry->operation.resume_state = durable;
        entry->operation.authority_retained = true;
    }
    if ((durable == P6C_OPERATION_CHILD_EXIT_OBSERVED) &&
        (entry->journal.state_payload_lengths[
             P6C_OPERATION_CHILD_EXIT_OBSERVED] == 4U)) {
        entry->operation.exit_status = (int32_t)p6c_service_load_u32(
            entry->journal.state_payloads[
                P6C_OPERATION_CHILD_EXIT_OBSERVED]);
    }
    if ((durable == P6C_OPERATION_RECOVERY_REQUIRED) &&
        (entry->journal.state_payload_lengths[
             P6C_OPERATION_RECOVERY_REQUIRED] == 1U)) {
        entry->operation.resume_state =
            (enum p6c_operation_state)
                entry->journal.state_payloads[
                    P6C_OPERATION_RECOVERY_REQUIRED][0];
    } else if ((durable != P6C_OPERATION_RESULT_RETAINED) &&
               (durable != P6C_OPERATION_ACKNOWLEDGED)) {
        registry->start_blocked = true;
        if ((recover_result == P6C_RESULT_OK) &&
            !entry->journal.recovery_required) {
            (void)p6c_service_mark_recovery(entry, durable);
        } else {
            entry->operation.state =
                P6C_OPERATION_RECOVERY_REQUIRED;
            entry->operation.resume_state = durable;
        }
    }
    if (entry->journal.cgroup_allocation_intent &&
        (durable < P6C_OPERATION_RESULT_RETAINED) &&
        (durable != P6C_OPERATION_ACKNOWLEDGED)) {
        bool populated = false;
        struct p6c_production_context production;
        struct p6c_process_adapter adapter;
        struct stat named_status;
        enum p6c_result cgroup_result;

        if (fstatat(
                registry->configuration->cgroup_root.descriptor,
                entry->cgroup_name, &named_status,
                AT_SYMLINK_NOFOLLOW) != 0) {
            if ((errno != ENOENT) ||
                (durable >= P6C_OPERATION_CHILD_CLONED)) {
                registry->start_blocked = true;
            }
            goto cgroup_recovery_complete;
        }
        cgroup_result = p6c_openat2_owned(
            &registry->configuration->cgroup_root,
            entry->cgroup_name,
            O_RDONLY | O_DIRECTORY | O_NOFOLLOW, (mode_t)0,
            P6C_DESCRIPTOR_CGROUP, &entry->cgroup);
        entry->cgroup.type = P6C_DESCRIPTOR_CGROUP;
        entry->cgroup_allocated = cgroup_result == P6C_RESULT_OK;
        entry->operation.cgroup = &entry->cgroup;
        production.registry = registry;
        production.entry = entry;
        production.terminal_observed = false;
        adapter = p6c_service_process_adapter(&production);
        if ((cgroup_result != P6C_RESULT_OK) ||
            (entry->journal.cgroup_created_identity &&
             (((uint64_t)entry->cgroup.device !=
               entry->journal.cgroup_created_device) ||
              ((uint64_t)entry->cgroup.inode !=
               entry->journal.cgroup_created_inode))) ||
            (p6c_cgroup_is_populated(
                 &entry->cgroup, &populated) != P6C_RESULT_OK)) {
            registry->start_blocked = true;
        } else if (populated &&
                   ((adapter.freeze_cgroup == NULL) ||
                    (adapter.kill_cgroup == NULL) ||
                    (adapter.wait_cgroup_empty == NULL) ||
                    (adapter.freeze_cgroup(
                         adapter.context,
                         &entry->operation) != P6C_RESULT_OK) ||
                    (adapter.kill_cgroup(
                         adapter.context,
                         &entry->operation) != P6C_RESULT_OK) ||
                    (adapter.wait_cgroup_empty(
                         adapter.context,
                         &entry->operation) != P6C_RESULT_OK))) {
            registry->start_blocked = true;
        }
cgroup_recovery_complete:
        ;
    }
    if ((recover_result != P6C_RESULT_OK) ||
        (recovery != P6C_JOURNAL_COMPLETE)) {
        registry->start_blocked = true;
        entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
        if (entry->operation.resume_state ==
            P6C_OPERATION_RECOVERY_REQUIRED) {
            entry->operation.resume_state = durable;
        }
    }
    if ((durable == P6C_OPERATION_ACKNOWLEDGED) &&
        (entry->operation.state == P6C_OPERATION_ACKNOWLEDGED) &&
        !entry->operation.authority_retained &&
        (recover_result == P6C_RESULT_OK) &&
        (recovery == P6C_JOURNAL_COMPLETE)) {
        uint8_t summary[P6C_OPERATION_SUMMARY_BYTES];
        enum p6c_result archive_result =
            p6c_service_archive_acknowledged(
                registry, entry, summary);

        if (archive_result != P6C_RESULT_OK) {
            registry->start_blocked = true;
            return archive_result;
        }
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_service_load_registry(
    struct p6c_service_registry *registry)
{
    DIR *directory;
    int duplicate;
    struct dirent *record;
    enum p6c_result result = P6C_RESULT_OK;

    duplicate = openat(
        registry->configuration->journal_root.descriptor, ".",
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (duplicate < 0) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    directory = fdopendir(duplicate);
    if (directory == NULL) {
        (void)close(duplicate);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    for (;;) {
        uint8_t operation_id[P6C_OPERATION_ID_BYTES];

        errno = 0;
        record = readdir(directory);
        if (record == NULL) {
            if (errno != 0) {
                result = P6C_RESULT_RECOVERY_REQUIRED;
            }
            break;
        }
        if ((strcmp(record->d_name, ".") == 0) ||
            (strcmp(record->d_name, "..") == 0)) {
            continue;
        }
        if (!p6c_service_parse_journal_name(
                record->d_name, operation_id)) {
            size_t name_length = strlen(record->d_name);

            if ((name_length >= sizeof(".journal") - 1U) &&
                (strcmp(
                     &record->d_name[
                         name_length - (sizeof(".journal") - 1U)],
                     ".journal") == 0)) {
                result = P6C_RESULT_UNSAFE;
                break;
            }
            continue;
        }
        result = p6c_service_load_one_journal(
            registry, record->d_name, operation_id);
        if (result != P6C_RESULT_OK) {
            registry->start_blocked = true;
            break;
        }
    }
    if (closedir(directory) != 0) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    return result;
}

static enum p6c_result p6c_service_find_or_load_tombstone(
    struct p6c_service_registry *registry,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    struct p6c_service_tombstone **tombstone)
{
    if ((registry == NULL) || (operation_id == NULL) ||
        (tombstone == NULL)) {
        return P6C_RESULT_INVALID;
    }
    *tombstone = p6c_service_find_tombstone(
        registry, operation_id);
    if (*tombstone != NULL) {
        return P6C_RESULT_OK;
    }
    return P6C_RESULT_OK;
}

static void p6c_service_insert_recovery_summary(
    uint8_t summaries[static P6C_MAX_OPERATIONS]
                     [P6C_OPERATION_SUMMARY_BYTES],
    size_t *count,
    const uint8_t summary[static P6C_OPERATION_SUMMARY_BYTES])
{
    size_t position = 0U;

    while ((*count > position) &&
           (memcmp(
                &summaries[position][P6C_SUMMARY_OPERATION_ID_OFFSET],
                &summary[P6C_SUMMARY_OPERATION_ID_OFFSET],
                P6C_OPERATION_ID_BYTES) < 0)) {
        ++position;
    }
    if (position >= P6C_MAX_OPERATIONS) {
        return;
    }
    if (*count < P6C_MAX_OPERATIONS) {
        ++*count;
    }
    if (position < *count - 1U) {
        memmove(
            &summaries[position + 1U], &summaries[position],
            (*count - position - 1U) * P6C_OPERATION_SUMMARY_BYTES);
    }
    memcpy(summaries[position], summary, P6C_OPERATION_SUMMARY_BYTES);
}

static enum p6c_result p6c_service_handle_recover(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame)
{
    uint8_t summaries[P6C_MAX_OPERATIONS]
                     [P6C_OPERATION_SUMMARY_BYTES];
    uint8_t payload[
        4U + (P6C_MAX_OPERATIONS * P6C_OPERATION_SUMMARY_BYTES)];
    size_t count = 0U;
    size_t index;

    for (index = 0U; index < P6C_MAX_OPERATIONS; ++index) {
        struct p6c_service_entry *entry = &registry->entries[index];

        if (entry->occupied && p6c_service_peer_matches(entry, peer)) {
            uint8_t summary[P6C_OPERATION_SUMMARY_BYTES];

            p6c_service_encode_summary(entry, summary);
            p6c_service_insert_recovery_summary(
                summaries, &count, summary);
        }
    }
    for (index = 0U; index < registry->tombstone_count; ++index) {
        const struct p6c_service_tombstone *tombstone =
            &registry->tombstones[index];

        if (tombstone->occupied &&
            (tombstone->opening_user == peer->user_id)) {
            p6c_service_insert_recovery_summary(
                summaries, &count, tombstone->summary);
        }
    }
    p6c_store_u32_be(payload, (uint32_t)count);
    for (index = 0U; index < count; ++index) {
        memcpy(
            &payload[4U + (index * P6C_OPERATION_SUMMARY_BYTES)],
            summaries[index], P6C_OPERATION_SUMMARY_BYTES);
    }
    return p6c_service_send(
        &registry->configuration->socket,
        (uint16_t)(P6C_RESPONSE_BIT | P6C_REQUEST_RECOVER),
        frame->request_id, payload,
        4U + (count * P6C_OPERATION_SUMMARY_BYTES));
}

static void p6c_service_release_uncommitted(
    struct p6c_service_registry *registry,
    struct p6c_service_entry *entry)
{
    free(entry->authority_storage);
    entry->authority_storage = NULL;
    p6c_service_entry_reset(entry);
    if (registry->count != 0U) {
        --registry->count;
    }
}

static enum p6c_result p6c_service_cleanup_prechild_cgroup(
    struct p6c_service_registry *registry,
    struct p6c_service_entry *entry,
    enum p6c_operation_state resume_state)
{
    long backoff_ns = P6C_DEGRADED_BACKOFF_INITIAL_NS;

    if ((registry == NULL) || (entry == NULL) ||
        !entry->journal.cgroup_allocation_intent ||
        !p6c_service_cgroup_name_valid(entry->cgroup_name)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    (void)p6c_service_mark_recovery(entry, resume_state);
    entry->operation.journal = &entry->journal;
    entry->operation.cgroup = &entry->cgroup;
    for (;;) {
        struct p6c_production_context production;
        struct p6c_process_adapter adapter;
        struct stat named_status;
        enum p6c_result result = P6C_RESULT_OK;

        if (!entry->cgroup_allocated) {
            if (fstatat(
                    registry->configuration->cgroup_root.descriptor,
                    entry->cgroup_name, &named_status,
                    AT_SYMLINK_NOFOLLOW) != 0) {
                if (errno == ENOENT) {
                    return P6C_RESULT_RECOVERY_REQUIRED;
                }
                result = P6C_RESULT_RECOVERY_REQUIRED;
            } else if (!S_ISDIR(named_status.st_mode) ||
                       (named_status.st_uid != geteuid()) ||
                       ((named_status.st_mode & (mode_t)0777) !=
                        (mode_t)0700)) {
                result = P6C_RESULT_UNSAFE;
            } else {
                entry->cgroup_allocated = true;
            }
        }
        if ((result == P6C_RESULT_OK) &&
            !p6c_owned_fd_is_live(&entry->cgroup)) {
            result = p6c_openat2_owned(
                &registry->configuration->cgroup_root,
                entry->cgroup_name,
                O_RDONLY | O_DIRECTORY | O_NOFOLLOW, (mode_t)0,
                P6C_DESCRIPTOR_CGROUP, &entry->cgroup);
            entry->cgroup.type = P6C_DESCRIPTOR_CGROUP;
        }
        if ((result == P6C_RESULT_OK) &&
            entry->journal.cgroup_created_identity &&
            (((uint64_t)entry->cgroup.device !=
              entry->journal.cgroup_created_device) ||
             ((uint64_t)entry->cgroup.inode !=
              entry->journal.cgroup_created_inode))) {
            result = P6C_RESULT_UNSAFE;
        }
        production.registry = registry;
        production.entry = entry;
        production.terminal_observed = true;
        adapter = p6c_service_process_adapter(&production);
        if ((result == P6C_RESULT_OK) &&
            ((adapter.freeze_cgroup == NULL) ||
             (adapter.freeze_cgroup(
                  adapter.context, &entry->operation) !=
              P6C_RESULT_OK) ||
             (adapter.kill_cgroup == NULL) ||
             (adapter.kill_cgroup(
                  adapter.context, &entry->operation) !=
              P6C_RESULT_OK) ||
             (adapter.wait_cgroup_empty == NULL) ||
             (adapter.wait_cgroup_empty(
                  adapter.context, &entry->operation) !=
              P6C_RESULT_OK) ||
             (adapter.remove_cgroup == NULL) ||
             (adapter.remove_cgroup(
                  adapter.context, &entry->operation) !=
              P6C_RESULT_OK))) {
            result = P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (result == P6C_RESULT_OK) {
            entry->cgroup_allocated = false;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        (void)p6c_service_mark_recovery(entry, resume_state);
        p6c_service_degraded_backoff(&backoff_ns);
    }
}

static enum p6c_result p6c_service_create_operation(
    struct p6c_service_registry *registry,
    struct p6c_service_entry *entry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame,
    const uint8_t request_digest[static P6C_SHA256_BYTES],
    struct p6c_received_authority *received)
{
    const struct p6c_field_view *operation_field;
    const struct p6c_field_view *digest_field;
    const struct p6c_field_view *executable_field;
    const struct p6c_field_view *argv_field;
    const struct p6c_field_view *environment_field;
    const struct p6c_field_view *credential_manifest_field;
    uint8_t reserved_payload[P6C_RESERVED_PAYLOAD_BYTES];
    uint8_t cgroup_created_payload[P6C_CGROUP_CREATED_PAYLOAD_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
    enum p6c_result result;
    struct p6c_production_context production;
    struct p6c_process_adapter adapter;

    operation_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_OPERATION_ID);
    digest_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_OPERATION_DIGEST);
    executable_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_EXECUTABLE);
    argv_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_ARGV);
    environment_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_ENVIRONMENT);
    credential_manifest_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_CREDENTIAL_MANIFEST);
    if ((operation_field == NULL) || (digest_field == NULL) ||
        (executable_field == NULL) || (argv_field == NULL) ||
        (environment_field == NULL) || (received == NULL) ||
        ((credential_manifest_field != NULL) !=
         (received->credential_directory >= 0))) {
        return P6C_RESULT_INVALID;
    }
    if (credential_manifest_field != NULL) {
        result = p6c_owned_fd_acquire(
            &entry->credential_directory,
            received->credential_directory,
            P6C_DESCRIPTOR_DIRECTORY);
        received->credential_directory = P6C_INVALID_DESCRIPTOR;
        if ((result != P6C_RESULT_OK) ||
            (entry->credential_directory.type !=
             P6C_DESCRIPTOR_DIRECTORY)) {
            return P6C_RESULT_UNSAFE;
        }
        memcpy(
            entry->credential_manifest,
            credential_manifest_field->value,
            (size_t)credential_manifest_field->value_length);
        entry->credential_manifest_size =
            (size_t)credential_manifest_field->value_length;
    }
    memcpy(entry->operation.operation_id, operation_field->value,
           P6C_OPERATION_ID_BYTES);
    memcpy(entry->request_digest, request_digest, P6C_SHA256_BYTES);
    memcpy(entry->executable_digest, digest_field->value,
           P6C_SHA256_BYTES);
    entry->opening_user = peer->user_id;
    result = p6c_service_prepare_authority(
        entry, executable_field, argv_field, environment_field);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    result = p6c_service_random_token(
        entry->operation.recovery_token);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    p6c_service_hex(
        entry->operation.operation_id, P6C_OPERATION_ID_BYTES,
        operation_hex);
    memcpy(entry->journal_name, operation_hex,
           P6C_OPERATION_ID_BYTES * 2U);
    memcpy(&entry->journal_name[P6C_OPERATION_ID_BYTES * 2U],
           ".journal", sizeof(".journal"));
    memcpy(entry->stdout_name, operation_hex,
           P6C_OPERATION_ID_BYTES * 2U);
    memcpy(&entry->stdout_name[P6C_OPERATION_ID_BYTES * 2U],
           ".stdout", sizeof(".stdout"));
    memcpy(entry->stderr_name, operation_hex,
           P6C_OPERATION_ID_BYTES * 2U);
    memcpy(&entry->stderr_name[P6C_OPERATION_ID_BYTES * 2U],
           ".stderr", sizeof(".stderr"));
    memset(reserved_payload, 0, sizeof(reserved_payload));
    reserved_payload[0] = UINT8_C(1);
    p6c_store_u32_be(&reserved_payload[1], (uint32_t)peer->user_id);
    memcpy(&reserved_payload[5], entry->operation.recovery_token,
           P6C_RECOVERY_TOKEN_BYTES);
    memcpy(&reserved_payload[21], request_digest, P6C_SHA256_BYTES);
    result = p6c_journal_create(
        &registry->configuration->journal_root, entry->journal_name,
        entry->operation.operation_id, geteuid(), &entry->journal);
    if (result != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_journal_append(
        &entry->journal, P6C_OPERATION_RESERVED, reserved_payload,
        sizeof(reserved_payload));
    if (result != P6C_RESULT_OK) {
        return p6c_service_mark_recovery(
            entry, P6C_OPERATION_ABSENT);
    }
    entry->operation.state = P6C_OPERATION_RESERVED;
    entry->operation.resume_state = P6C_OPERATION_RESERVED;
    entry->operation.authority_retained = true;
    result = p6c_pin_executable(
        &registry->configuration->source_root, entry->executable_path,
        geteuid(), entry->executable_digest, &entry->executable);
    if (result != P6C_RESULT_OK) {
        return p6c_service_mark_recovery(
            entry, P6C_OPERATION_RESERVED);
    }
    result = p6c_journal_append(
        &entry->journal, P6C_OPERATION_EXECUTABLE_PINNED,
        entry->executable_digest, P6C_SHA256_BYTES);
    if (result != P6C_RESULT_OK) {
        return p6c_service_mark_recovery(
            entry, P6C_OPERATION_EXECUTABLE_PINNED);
    }
    entry->operation.state = P6C_OPERATION_EXECUTABLE_PINNED;
    entry->operation.resume_state =
        P6C_OPERATION_EXECUTABLE_PINNED;
    if ((p6c_owned_pipe_create(&entry->status_channel) !=
         P6C_RESULT_OK) ||
        (p6c_owned_pipe_create(&entry->stdout_channel) !=
         P6C_RESULT_OK) ||
        (p6c_owned_pipe_create(&entry->stderr_channel) !=
         P6C_RESULT_OK) ||
        (p6c_transcript_create(
             &registry->configuration->journal_root,
             P6C_STREAM_STDOUT, P6C_MAX_TRANSCRIPT_RETAINED, false,
             &entry->stdout_transcript) != P6C_RESULT_OK) ||
        (p6c_transcript_create(
             &registry->configuration->journal_root,
             P6C_STREAM_STDERR, P6C_MAX_TRANSCRIPT_RETAINED, false,
             &entry->stderr_transcript) != P6C_RESULT_OK)) {
        return p6c_service_mark_recovery(
            entry, P6C_OPERATION_EXECUTABLE_PINNED);
    }
    {
        uint32_t attempt;
        struct stat existing;

        result = P6C_RESULT_CONFLICT;
        for (attempt = UINT32_C(0);
             attempt < P6C_CGROUP_CREATE_ATTEMPTS; ++attempt) {
            result = p6c_service_random_cgroup_name(
                entry->cgroup_name);
            if (result != P6C_RESULT_OK) {
                break;
            }
            if (fstatat(
                    registry->configuration->cgroup_root.descriptor,
                    entry->cgroup_name, &existing,
                    AT_SYMLINK_NOFOLLOW) != 0) {
                result = (errno == ENOENT) ? P6C_RESULT_OK :
                                             P6C_RESULT_SYSTEM;
                break;
            }
        }
    }
    if (result != P6C_RESULT_OK) {
        return p6c_service_mark_recovery(
            entry, P6C_OPERATION_EXECUTABLE_PINNED);
    }
    result = p6c_journal_append_cgroup_allocation_intent(
        &entry->journal, entry->cgroup_name);
    if (result != P6C_RESULT_OK) {
        return p6c_service_mark_recovery(
            entry, P6C_OPERATION_EXECUTABLE_PINNED);
    }
    result = p6c_cgroup_create(
        &registry->configuration->cgroup_root,
        entry->cgroup_name, geteuid(), &entry->cgroup);
    if (result != P6C_RESULT_OK) {
        struct stat allocated_status;

        if (fstatat(
                registry->configuration->cgroup_root.descriptor,
                entry->cgroup_name, &allocated_status,
                AT_SYMLINK_NOFOLLOW) == 0) {
            entry->cgroup_allocated = true;
        }
        return p6c_service_cleanup_prechild_cgroup(
            registry, entry, P6C_OPERATION_EXECUTABLE_PINNED);
    }
    entry->cgroup_allocated = true;
    memset(cgroup_created_payload, 0, sizeof(cgroup_created_payload));
    memcpy(
        &cgroup_created_payload[P6C_CGROUP_CREATED_NAME_OFFSET],
        entry->cgroup_name, P6C_CGROUP_NAME_BYTES - 1U);
    p6c_service_store_u64(
        &cgroup_created_payload[P6C_CGROUP_CREATED_DEVICE_OFFSET],
        (uint64_t)entry->cgroup.device);
    p6c_service_store_u64(
        &cgroup_created_payload[P6C_CGROUP_CREATED_INODE_OFFSET],
        (uint64_t)entry->cgroup.inode);
    result = p6c_journal_append(
        &entry->journal, P6C_OPERATION_CGROUP_CREATED,
        cgroup_created_payload, sizeof(cgroup_created_payload));
    if (result != P6C_RESULT_OK) {
        return p6c_service_cleanup_prechild_cgroup(
            registry, entry, P6C_OPERATION_CGROUP_CREATED);
    }
    memcpy(recovery_token, entry->operation.recovery_token,
           sizeof(recovery_token));
    result = p6c_operation_init(
        &entry->operation, operation_field->value,
        recovery_token, &entry->journal,
        &entry->executable, &entry->cgroup, &entry->status_channel,
        &entry->stdout_channel, &entry->stderr_channel,
        &entry->stdout_transcript, &entry->stderr_transcript);
    if (result != P6C_RESULT_OK) {
        return p6c_service_cleanup_prechild_cgroup(
            registry, entry, P6C_OPERATION_CGROUP_CREATED);
    }
    production.registry = registry;
    production.entry = entry;
    production.terminal_observed = false;
    adapter = p6c_service_process_adapter(&production);
    result = p6c_operation_start(&entry->operation, &adapter);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_service_handle_start(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame,
    struct p6c_received_authority *received)
{
    const struct p6c_field_view *operation_field =
        p6c_service_find_field(
            frame, (uint16_t)P6C_FIELD_OPERATION_ID);
    uint8_t request_digest[P6C_SHA256_BYTES];
    struct p6c_service_entry *entry;
    struct p6c_service_tombstone *tombstone;
    enum p6c_result result;

    if ((operation_field == NULL) ||
        (p6c_service_request_digest(
             frame, request_digest) != P6C_RESULT_OK)) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_INVALID, NULL);
    }
    entry = p6c_service_find_entry(
        registry, operation_field->value);
    if (entry != NULL) {
        if (!p6c_service_peer_matches(entry, peer)) {
            result = P6C_RESULT_UNAUTHORIZED;
        } else if (memcmp(
                       entry->request_digest, request_digest,
                       P6C_SHA256_BYTES) != 0) {
            result = P6C_RESULT_CONFLICT;
        } else {
            return p6c_service_send_summary(
                &registry->configuration->socket,
                (uint16_t)P6C_REQUEST_START, frame->request_id, entry);
        }
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            result,
            (result == P6C_RESULT_UNAUTHORIZED) ? NULL : entry);
    }
    tombstone = NULL;
    result = p6c_service_find_or_load_tombstone(
        registry, operation_field->value, &tombstone);
    if (result != P6C_RESULT_OK) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_RECOVERY_REQUIRED, NULL);
    }
    if (tombstone != NULL) {
        if (tombstone->opening_user != peer->user_id) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_UNAUTHORIZED, NULL);
        }
        if (memcmp(tombstone->request_digest, request_digest,
                   P6C_SHA256_BYTES) != 0) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_CONFLICT, NULL);
        }
        return p6c_service_send(
            &registry->configuration->socket,
            (uint16_t)(P6C_RESPONSE_BIT | P6C_REQUEST_START),
            frame->request_id, tombstone->summary,
            P6C_OPERATION_SUMMARY_BYTES);
    }
    if (registry->start_blocked) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_RECOVERY_REQUIRED, NULL);
    }
    if ((registry->count + registry->tombstone_count) >=
        P6C_TOMBSTONE_CAPACITY) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_LIMIT, NULL);
    }
    entry = p6c_service_reserve_entry(registry);
    if (entry == NULL) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_LIMIT, NULL);
    }
    result = p6c_service_create_operation(
        registry, entry, peer, frame, request_digest, received);
    if (result != P6C_RESULT_OK) {
        if (!p6c_owned_fd_is_live(&entry->journal.file)) {
            p6c_service_release_uncommitted(registry, entry);
            entry = NULL;
        }
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            result, entry);
    }
    return p6c_service_send_summary(
        &registry->configuration->socket,
        (uint16_t)P6C_REQUEST_START, frame->request_id, entry);
}

static enum p6c_result p6c_service_authorize_entry(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame,
    struct p6c_service_entry **authorized)
{
    const struct p6c_field_view *operation_field =
        p6c_service_find_field(
            frame, (uint16_t)P6C_FIELD_OPERATION_ID);
    const struct p6c_field_view *token_field =
        p6c_service_find_field(
            frame, (uint16_t)P6C_FIELD_RECOVERY_TOKEN);
    struct p6c_service_entry *entry;

    if ((operation_field == NULL) || (token_field == NULL) ||
        (authorized == NULL)) {
        return P6C_RESULT_INVALID;
    }
    entry = p6c_service_find_entry(
        registry, operation_field->value);
    if (entry == NULL) {
        return P6C_RESULT_STALE;
    }
    if (!p6c_service_peer_matches(entry, peer) ||
        (memcmp(entry->operation.recovery_token, token_field->value,
                P6C_RECOVERY_TOKEN_BYTES) != 0)) {
        return P6C_RESULT_UNAUTHORIZED;
    }
    *authorized = entry;
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_service_send_authorization_error(
    struct p6c_service_registry *registry,
    const struct p6c_frame_view *frame,
    enum p6c_result result,
    const struct p6c_service_entry *entry)
{
    if (result == P6C_RESULT_STALE) {
        uint8_t zero_token[P6C_RECOVERY_TOKEN_BYTES];

        memset(zero_token, 0, sizeof(zero_token));
        return p6c_service_send_error(
            &registry->configuration->socket, frame->request_id,
            P6C_STATUS_NOT_FOUND, "NOT_FOUND", false,
            P6C_OPERATION_ABSENT, zero_token);
    }
    if (result == P6C_RESULT_UNAUTHORIZED) {
        entry = NULL;
    }
    return p6c_service_send_result_error(
        &registry->configuration->socket, frame->request_id,
        result, entry);
}

static enum p6c_result p6c_service_handle_status(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame)
{
    struct p6c_service_entry *entry = NULL;
    enum p6c_result result = p6c_service_authorize_entry(
        registry, peer, frame, &entry);

    if (result != P6C_RESULT_OK) {
        return p6c_service_send_authorization_error(
            registry, frame, result, entry);
    }
    return p6c_service_send_summary(
        &registry->configuration->socket,
        (uint16_t)P6C_REQUEST_STATUS, frame->request_id, entry);
}

static enum p6c_result p6c_service_link_transcripts(
    struct p6c_service_registry *registry,
    struct p6c_service_entry *entry)
{
    if ((p6c_transcript_link(
             &registry->configuration->journal_root,
             entry->stdout_name,
             &entry->stdout_transcript) != P6C_RESULT_OK) ||
        (p6c_transcript_link(
             &registry->configuration->journal_root,
             entry->stderr_name,
             &entry->stderr_transcript) != P6C_RESULT_OK)) {
        return p6c_service_mark_recovery(
            entry, P6C_OPERATION_RESULT_RETAINED);
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_service_handle_stop(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame)
{
    struct p6c_service_entry *entry = NULL;
    enum p6c_result result = p6c_service_authorize_entry(
        registry, peer, frame, &entry);
    struct p6c_production_context production;
    struct p6c_process_adapter adapter;

    if (result != P6C_RESULT_OK) {
        return p6c_service_send_authorization_error(
            registry, frame, result, entry);
    }
    production.registry = registry;
    production.entry = entry;
    production.terminal_observed = false;
    adapter = p6c_service_process_adapter(&production);
    result = p6c_operation_stop(&entry->operation, &adapter);
    if (result != P6C_RESULT_OK) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            result, entry);
    }
    result = p6c_service_link_transcripts(registry, entry);
    if (result != P6C_RESULT_OK) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            result, entry);
    }
    return p6c_service_send_summary(
        &registry->configuration->socket,
        (uint16_t)P6C_REQUEST_STOP, frame->request_id, entry);
}

static enum p6c_result p6c_service_handle_run_once(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame,
    struct p6c_received_authority *received)
{
    const struct p6c_field_view *operation_field =
        p6c_service_find_field(
            frame, (uint16_t)P6C_FIELD_OPERATION_ID);
    uint8_t request_digest[P6C_SHA256_BYTES];
    struct p6c_service_entry *entry;
    enum p6c_result result;
    struct p6c_production_context production;
    struct p6c_process_adapter adapter;

    if ((operation_field == NULL) ||
        (p6c_service_request_digest(
             frame, request_digest) != P6C_RESULT_OK)) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_INVALID, NULL);
    }
    entry = p6c_service_find_entry(
        registry, operation_field->value);
    if (entry != NULL) {
        if (!p6c_service_peer_matches(entry, peer)) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_UNAUTHORIZED, NULL);
        }
        if (memcmp(entry->request_digest, request_digest,
                   P6C_SHA256_BYTES) != 0) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_CONFLICT, entry);
        }
    } else {
        if (registry->start_blocked) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_RECOVERY_REQUIRED, NULL);
        }
        if ((registry->count + registry->tombstone_count) >=
            P6C_TOMBSTONE_CAPACITY) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_LIMIT, NULL);
        }
        entry = p6c_service_reserve_entry(registry);
        if (entry == NULL) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_LIMIT, NULL);
        }
        result = p6c_service_create_operation(
            registry, entry, peer, frame, request_digest, received);
        if (result != P6C_RESULT_OK) {
            if (!p6c_owned_fd_is_live(&entry->journal.file)) {
                p6c_service_release_uncommitted(registry, entry);
                entry = NULL;
            }
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                result, entry);
        }
    }
    if (entry->operation.state != P6C_OPERATION_RESULT_RETAINED) {
        production.registry = registry;
        production.entry = entry;
        production.terminal_observed = false;
        adapter = p6c_service_process_adapter(&production);
        if ((adapter.wait_terminal == NULL) ||
            ((result = adapter.wait_terminal(
                  adapter.context, &entry->operation)) !=
             P6C_RESULT_OK &&
             result != P6C_RESULT_TIMEOUT)) {
            (void)p6c_service_mark_recovery(
                entry, entry->operation.resume_state);
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_RECOVERY_REQUIRED, entry);
        }
        result = p6c_operation_stop(&entry->operation, &adapter);
        if (result != P6C_RESULT_OK) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                result, entry);
        }
    }
    result = p6c_service_link_transcripts(registry, entry);
    if (result != P6C_RESULT_OK) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            result, entry);
    }
    return p6c_service_send_summary(
        &registry->configuration->socket,
        (uint16_t)P6C_REQUEST_RUN_ONCE, frame->request_id, entry);
}

static enum p6c_result p6c_service_handle_read_transcript(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame)
{
    struct p6c_service_entry *entry = NULL;
    const struct p6c_field_view *stream_field;
    const struct p6c_field_view *offset_field;
    const struct p6c_field_view *length_field;
    struct p6c_transcript *transcript;
    uint8_t *payload;
    uint64_t offset;
    uint32_t requested;
    size_t read_size = 0U;
    size_t payload_capacity;
    enum p6c_result result = p6c_service_authorize_entry(
        registry, peer, frame, &entry);

    if (result != P6C_RESULT_OK) {
        return p6c_service_send_authorization_error(
            registry, frame, result, entry);
    }
    stream_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_STREAM);
    offset_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_OFFSET);
    length_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_LENGTH);
    if ((stream_field == NULL) || (offset_field == NULL) ||
        (length_field == NULL) ||
        (entry->operation.state != P6C_OPERATION_RESULT_RETAINED)) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_INVALID, entry);
    }
    requested = p6c_service_load_u32(length_field->value);
    offset = p6c_service_load_u64(offset_field->value);
    if ((size_t)requested >
        (size_t)P6C_MAX_PAYLOAD_BYTES -
            P6C_TRANSCRIPT_METADATA_BYTES) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_LIMIT, entry);
    }
    transcript =
        (stream_field->value[0] == UINT8_C(P6C_STREAM_STDOUT)) ?
            &entry->stdout_transcript :
            &entry->stderr_transcript;
    if (offset > transcript->retained_size) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_INVALID, entry);
    }
    payload_capacity =
        P6C_TRANSCRIPT_METADATA_BYTES + (size_t)requested;
    payload = malloc(payload_capacity);
    if (payload == NULL) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_SYSTEM, entry);
    }
    result = p6c_transcript_read(
        transcript, offset,
        &payload[P6C_TRANSCRIPT_METADATA_BYTES],
        (size_t)requested, &read_size);
    if (result != P6C_RESULT_OK) {
        free(payload);
        (void)p6c_service_mark_recovery(
            entry, P6C_OPERATION_RESULT_RETAINED);
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_RECOVERY_REQUIRED, entry);
    }
    memset(payload, 0, P6C_TRANSCRIPT_METADATA_BYTES);
    memcpy(&payload[P6C_TRANSCRIPT_OPERATION_ID_OFFSET],
           entry->operation.operation_id, P6C_OPERATION_ID_BYTES);
    payload[P6C_TRANSCRIPT_STREAM_OFFSET] =
        (uint8_t)transcript->stream;
    if (transcript->eof_observed &&
        (offset + (uint64_t)read_size ==
         transcript->retained_size)) {
        payload[P6C_TRANSCRIPT_FLAGS_OFFSET] |=
            P6C_TRANSCRIPT_FLAG_EOF;
    }
    if (transcript->truncated) {
        payload[P6C_TRANSCRIPT_FLAGS_OFFSET] |=
            P6C_TRANSCRIPT_FLAG_TRUNCATED;
    }
    p6c_service_store_u64(
        &payload[P6C_TRANSCRIPT_OFFSET_OFFSET], offset);
    p6c_store_u32_be(
        &payload[P6C_TRANSCRIPT_COUNT_OFFSET],
        (uint32_t)read_size);
    p6c_service_store_u64(
        &payload[P6C_TRANSCRIPT_OBSERVED_SIZE_OFFSET],
        transcript->observed_size);
    p6c_service_store_u64(
        &payload[P6C_TRANSCRIPT_RETAINED_SIZE_OFFSET],
        transcript->retained_size);
    memcpy(&payload[P6C_TRANSCRIPT_DIGEST_OFFSET],
           transcript->digest, P6C_SHA256_BYTES);
    result = p6c_service_send(
        &registry->configuration->socket,
        (uint16_t)(
            P6C_RESPONSE_BIT | P6C_REQUEST_READ_TRANSCRIPT),
        frame->request_id, payload,
        P6C_TRANSCRIPT_METADATA_BYTES + read_size);
    free(payload);
    return result;
}

static int p6c_service_bytes_nonzero(
    const uint8_t *bytes, size_t size)
{
    size_t index;

    for (index = 0U; index < size; ++index) {
        if (bytes[index] != UINT8_C(0)) {
            return 1;
        }
    }
    return 0;
}

static enum p6c_result p6c_service_read_all_transcript(
    struct p6c_transcript *transcript, uint8_t **content,
    size_t *content_size)
{
    size_t size;
    size_t read_size = 0U;
    uint8_t *buffer;
    enum p6c_result result;

    if ((transcript == NULL) || (content == NULL) ||
        (content_size == NULL) ||
        (transcript->retained_size > (uint64_t)SIZE_MAX)) {
        return P6C_RESULT_INVALID;
    }
    size = (size_t)transcript->retained_size;
    buffer = malloc((size == 0U) ? 1U : size);
    if (buffer == NULL) {
        return P6C_RESULT_SYSTEM;
    }
    result = p6c_transcript_read(
        transcript, UINT64_C(0), buffer, size, &read_size);
    if ((result != P6C_RESULT_OK) || (read_size != size)) {
        free(buffer);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    *content = buffer;
    *content_size = size;
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_service_send_publication(
    struct p6c_service_registry *registry,
    const struct p6c_frame_view *frame,
    const struct p6c_service_entry *entry)
{
    uint8_t payload[
        P6C_OPERATION_SUMMARY_BYTES + P6C_SHA256_BYTES];

    p6c_service_encode_summary(entry, payload);
    memcpy(&payload[P6C_OPERATION_SUMMARY_BYTES],
           entry->publication_digest, P6C_SHA256_BYTES);
    return p6c_service_send(
        &registry->configuration->socket,
        (uint16_t)(
            P6C_RESPONSE_BIT | P6C_REQUEST_PUBLISH_BUNDLE),
        frame->request_id, payload, sizeof(payload));
}

static enum p6c_result p6c_service_handle_publish(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame)
{
    struct p6c_service_entry *entry = NULL;
    const struct p6c_field_view *publication_field;
    uint8_t *stdout_content = NULL;
    uint8_t *stderr_content = NULL;
    size_t stdout_size = 0U;
    size_t stderr_size = 0U;
    char authority[2048];
    char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
    char executable_hex[(P6C_SHA256_BYTES * 2U) + 1U];
    char stdout_hex[(P6C_SHA256_BYTES * 2U) + 1U];
    char stderr_hex[(P6C_SHA256_BYTES * 2U) + 1U];
    char publication_hex[(P6C_SHA256_BYTES * 2U) + 1U];
    int authority_size;
    struct p6c_publication_item items[3];
    enum p6c_result result = p6c_service_authorize_entry(
        registry, peer, frame, &entry);

    if (result != P6C_RESULT_OK) {
        return p6c_service_send_authorization_error(
            registry, frame, result, entry);
    }
    publication_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_PUBLICATION_ID);
    if ((publication_field == NULL) ||
        !p6c_service_bytes_nonzero(
            publication_field->value, P6C_SHA256_BYTES) ||
        (entry->operation.state != P6C_OPERATION_RESULT_RETAINED)) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_INVALID, entry);
    }
    if (entry->journal.bundle_committed) {
        if (memcmp(
                entry->publication_identity,
                publication_field->value,
                P6C_SHA256_BYTES) != 0) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_CONFLICT, entry);
        }
        return p6c_service_send_publication(registry, frame, entry);
    }
    result = p6c_service_read_all_transcript(
        &entry->stdout_transcript, &stdout_content, &stdout_size);
    if (result == P6C_RESULT_OK) {
        result = p6c_service_read_all_transcript(
            &entry->stderr_transcript, &stderr_content, &stderr_size);
    }
    if (result != P6C_RESULT_OK) {
        free(stdout_content);
        free(stderr_content);
        (void)p6c_service_mark_recovery(
            entry, P6C_OPERATION_RESULT_RETAINED);
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_RECOVERY_REQUIRED, entry);
    }
    p6c_service_hex(
        entry->operation.operation_id, P6C_OPERATION_ID_BYTES,
        operation_hex);
    p6c_service_hex(
        entry->executable_digest, P6C_SHA256_BYTES, executable_hex);
    p6c_service_hex(
        entry->stdout_transcript.digest, P6C_SHA256_BYTES,
        stdout_hex);
    p6c_service_hex(
        entry->stderr_transcript.digest, P6C_SHA256_BYTES,
        stderr_hex);
    p6c_service_hex(
        publication_field->value, P6C_SHA256_BYTES, publication_hex);
    authority_size = snprintf(
        authority, sizeof(authority),
        "{\"format\":\"p6c-authority-v1\","
        "\"operation_id\":\"%s\","
        "\"state\":%d,"
        "\"executable_sha256\":\"%s\","
        "\"stdout\":{\"observed\":%llu,\"retained\":%llu,"
        "\"truncated\":%s,\"sha256\":\"%s\"},"
        "\"stderr\":{\"observed\":%llu,\"retained\":%llu,"
        "\"truncated\":%s,\"sha256\":\"%s\"},"
        "\"publication_identity\":\"%s\","
        "\"live_execution\":false,\"live_trading\":false}\n",
        operation_hex, (int)P6C_OPERATION_RESULT_RETAINED,
        executable_hex,
        (unsigned long long)entry->stdout_transcript.observed_size,
        (unsigned long long)entry->stdout_transcript.retained_size,
        entry->stdout_transcript.truncated ? "true" : "false",
        stdout_hex,
        (unsigned long long)entry->stderr_transcript.observed_size,
        (unsigned long long)entry->stderr_transcript.retained_size,
        entry->stderr_transcript.truncated ? "true" : "false",
        stderr_hex, publication_hex);
    if ((authority_size < 0) ||
        ((size_t)authority_size >= sizeof(authority))) {
        free(stdout_content);
        free(stderr_content);
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_LIMIT, entry);
    }
    items[0].name = "authority.json";
    items[0].content = (const uint8_t *)authority;
    items[0].content_length = (size_t)authority_size;
    items[0].candidate_identity = publication_hex;
    items[1].name = "stderr.bin";
    items[1].content = stderr_content;
    items[1].content_length = stderr_size;
    items[1].candidate_identity = publication_hex;
    items[2].name = "stdout.bin";
    items[2].content = stdout_content;
    items[2].content_length = stdout_size;
    items[2].candidate_identity = publication_hex;
    memcpy(entry->publication_identity, publication_field->value,
           P6C_SHA256_BYTES);
    memcpy(entry->journal.publication_identity,
           publication_field->value, P6C_SHA256_BYTES);
    result = p6c_publish_bundle(
        &registry->configuration->evidence_root,
        entry->operation.operation_id,
        P6C_OPERATION_RESULT_RETAINED, items, 3U,
        &entry->journal, &entry->publication);
    free(stdout_content);
    free(stderr_content);
    if (result != P6C_RESULT_OK) {
        (void)p6c_service_mark_recovery(
            entry, P6C_OPERATION_RESULT_RETAINED);
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_RECOVERY_REQUIRED, entry);
    }
    memcpy(entry->publication_digest,
           entry->publication.manifest_digest, P6C_SHA256_BYTES);
    return p6c_service_send_publication(registry, frame, entry);
}

static enum p6c_result p6c_service_handle_ack(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame)
{
    struct p6c_service_entry *entry = NULL;
    struct p6c_service_tombstone *tombstone;
    const struct p6c_field_view *operation_field;
    const struct p6c_field_view *token_field;
    const struct p6c_field_view *publication_field;
    uint8_t zero_publication[P6C_SHA256_BYTES];
    const uint8_t *expected;
    uint8_t acknowledged_summary[P6C_OPERATION_SUMMARY_BYTES];
    enum p6c_result result;
    operation_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_OPERATION_ID);
    token_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_RECOVERY_TOKEN);
    publication_field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_PUBLICATION_ID);
    if ((operation_field == NULL) || (token_field == NULL) ||
        (publication_field == NULL)) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_INVALID, NULL);
    }
    entry = p6c_service_find_entry(
        registry, operation_field->value);
    tombstone = NULL;
    if (entry == NULL) {
        result = p6c_service_find_or_load_tombstone(
            registry, operation_field->value, &tombstone);
        if (result != P6C_RESULT_OK) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_RECOVERY_REQUIRED, NULL);
        }
    }
    if (tombstone != NULL) {
        const uint8_t *summary_token =
            &tombstone->summary[P6C_SUMMARY_RECOVERY_TOKEN_OFFSET];
        const uint8_t *summary_publication =
            &tombstone->summary[P6C_SUMMARY_PUBLICATION_DIGEST_OFFSET];

        if ((tombstone->opening_user != peer->user_id) ||
            (memcmp(summary_token, token_field->value,
                    P6C_RECOVERY_TOKEN_BYTES) != 0)) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_UNAUTHORIZED, NULL);
        }
        if (memcmp(summary_publication, publication_field->value,
                   P6C_SHA256_BYTES) != 0) {
            return p6c_service_send_result_error(
                &registry->configuration->socket, frame->request_id,
                P6C_RESULT_CONFLICT, NULL);
        }
        return p6c_service_send(
            &registry->configuration->socket,
            (uint16_t)(P6C_RESPONSE_BIT | P6C_REQUEST_ACK),
            frame->request_id, tombstone->summary,
            P6C_OPERATION_SUMMARY_BYTES);
    }
    {
        enum p6c_result authorize_result =
            p6c_service_authorize_entry(
                registry, peer, frame, &entry);

        if (authorize_result != P6C_RESULT_OK) {
            return p6c_service_send_authorization_error(
                registry, frame, authorize_result, entry);
        }
    }
    memset(zero_publication, 0, sizeof(zero_publication));
    expected = entry->journal.bundle_committed ?
                   entry->publication_digest :
                   zero_publication;
    if (memcmp(expected, publication_field->value,
               P6C_SHA256_BYTES) != 0) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_CONFLICT, entry);
    }
    result = p6c_operation_ack(
        &entry->operation, operation_field->value,
        token_field->value);
    if (result != P6C_RESULT_OK) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            result, entry);
    }
    if ((p6c_transcript_unlink(
             &registry->configuration->journal_root,
             entry->stdout_name, &entry->stdout_transcript) !=
         P6C_RESULT_OK) ||
        (p6c_transcript_unlink(
             &registry->configuration->journal_root,
             entry->stderr_name, &entry->stderr_transcript) !=
         P6C_RESULT_OK) ||
        (p6c_publication_close(&entry->publication) !=
         P6C_RESULT_OK)) {
        entry->operation.state = P6C_OPERATION_RECOVERY_REQUIRED;
        entry->operation.resume_state =
            P6C_OPERATION_ACKNOWLEDGED;
        entry->operation.authority_retained = true;
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_RECOVERY_REQUIRED, entry);
    }
    result = p6c_service_archive_acknowledged(
        registry, entry, acknowledged_summary);
    if (result != P6C_RESULT_OK) {
        return p6c_service_send_result_error(
            &registry->configuration->socket, frame->request_id,
            P6C_RESULT_RECOVERY_REQUIRED, NULL);
    }
    return p6c_service_send(
        &registry->configuration->socket,
        (uint16_t)(P6C_RESPONSE_BIT | P6C_REQUEST_ACK),
        frame->request_id, acknowledged_summary,
        sizeof(acknowledged_summary));
}

static enum p6c_result p6c_service_cleanup_after_disconnect(
    struct p6c_service_registry *registry,
    enum p6c_disconnect_reason reason)
{
    long backoff_ns = P6C_DEGRADED_BACKOFF_INITIAL_NS;

    if (registry == NULL) {
        return P6C_RESULT_INVALID;
    }
    for (;;) {
        bool custody_pending = false;
        size_t index;

        for (index = 0U; index < P6C_MAX_OPERATIONS; ++index) {
            struct p6c_service_entry *entry =
                &registry->entries[index];
            struct p6c_production_context production;
            struct p6c_process_adapter adapter;
            enum p6c_result result = P6C_RESULT_OK;
            bool physical_pending;
            bool transcript_pending;

            if (!entry->occupied ||
                !entry->operation.authority_retained) {
                continue;
            }
            production.registry = registry;
            production.entry = entry;
            production.terminal_observed =
                entry->operation.physical_custody >=
                P6C_CHILD_EXIT_OBSERVED;
            adapter = p6c_service_process_adapter(&production);
            physical_pending =
                (entry->operation.physical_custody != P6C_CHILD_NONE) &&
                (entry->operation.physical_custody < P6C_CHILD_REAPED);
            transcript_pending =
                (entry->operation.physical_custody >=
                 P6C_CHILD_REAPED) &&
                (!entry->stdout_transcript.finalized ||
                 !entry->stderr_transcript.finalized);
            if (physical_pending || transcript_pending ||
                ((entry->operation.physical_custody !=
                  P6C_CHILD_NONE) &&
                 entry->cgroup_allocated)) {
                result = p6c_operation_stop(
                    &entry->operation, &adapter);
                if (result == P6C_RESULT_OK) {
                    entry->cgroup_allocated = false;
                    result = p6c_service_link_transcripts(
                        registry, entry);
                } else if (entry->stdout_transcript.finalized &&
                           entry->stderr_transcript.finalized) {
                    (void)p6c_service_link_transcripts(
                        registry, entry);
                }
            } else if (entry->operation.state ==
                       P6C_OPERATION_RESULT_RETAINED) {
                result = p6c_service_link_transcripts(
                    registry, entry);
            }
            if (entry->cgroup_allocated) {
                bool removal_authorized =
                    entry->journal.cgroup_removal_intent ||
                    ((entry->operation.physical_custody ==
                      P6C_CHILD_NONE) &&
                     (entry->operation.resume_state <
                      P6C_OPERATION_CHILD_CLONED) &&
                     entry->journal.cgroup_allocation_intent);

                if (!p6c_owned_fd_is_live(&entry->cgroup)) {
                    struct stat named_status;
                    char quarantine[
                        P6C_CGROUP_QUARANTINE_NAME_BYTES];
                    const char *physical_name =
                        entry->cgroup_name;
                    int named_result = fstatat(
                        registry->configuration
                            ->cgroup_root.descriptor,
                        physical_name, &named_status,
                        AT_SYMLINK_NOFOLLOW);

                    if ((named_result != 0) &&
                        (errno == ENOENT) &&
                        (p6c_cgroup_quarantine_name(
                             entry->cgroup_name,
                             quarantine) ==
                         P6C_RESULT_OK)) {
                        named_result = fstatat(
                            registry->configuration
                                ->cgroup_root.descriptor,
                            quarantine, &named_status,
                            AT_SYMLINK_NOFOLLOW);
                        if (named_result == 0) {
                            physical_name = quarantine;
                        }
                    }
                    if (named_result != 0) {
                        if (errno == ENOENT) {
                            entry->cgroup_allocated = false;
                        } else {
                            result =
                                P6C_RESULT_RECOVERY_REQUIRED;
                        }
                    } else if (!S_ISDIR(named_status.st_mode) ||
                               (named_status.st_uid != geteuid()) ||
                               ((named_status.st_mode &
                                 (mode_t)0777) != (mode_t)0700) ||
                               (p6c_openat2_owned(
                                    &registry->configuration
                                         ->cgroup_root,
                                    physical_name,
                                    O_RDONLY | O_DIRECTORY |
                                        O_NOFOLLOW,
                                    (mode_t)0,
                                    P6C_DESCRIPTOR_CGROUP,
                                    &entry->cgroup) !=
                                P6C_RESULT_OK)) {
                        result = P6C_RESULT_RECOVERY_REQUIRED;
                    }
                    entry->cgroup.type = P6C_DESCRIPTOR_CGROUP;
                    entry->operation.cgroup = &entry->cgroup;
                }
                if (entry->cgroup_allocated &&
                    p6c_owned_fd_is_live(&entry->cgroup) &&
                    entry->journal.cgroup_created_identity &&
                    (((uint64_t)entry->cgroup.device !=
                      entry->journal.cgroup_created_device) ||
                     ((uint64_t)entry->cgroup.inode !=
                      entry->journal.cgroup_created_inode))) {
                    result = P6C_RESULT_RECOVERY_REQUIRED;
                } else if (entry->cgroup_allocated &&
                           p6c_owned_fd_is_live(&entry->cgroup) &&
                           ((adapter.freeze_cgroup == NULL) ||
                            (adapter.freeze_cgroup(
                                 adapter.context,
                                 &entry->operation) !=
                             P6C_RESULT_OK) ||
                            (adapter.kill_cgroup == NULL) ||
                            (adapter.kill_cgroup(
                                 adapter.context,
                                 &entry->operation) !=
                             P6C_RESULT_OK) ||
                            (adapter.wait_cgroup_empty == NULL) ||
                            (adapter.wait_cgroup_empty(
                                 adapter.context,
                                 &entry->operation) !=
                             P6C_RESULT_OK))) {
                    result = P6C_RESULT_RECOVERY_REQUIRED;
                } else if (entry->cgroup_allocated &&
                           p6c_owned_fd_is_live(&entry->cgroup) &&
                           removal_authorized) {
                    if ((adapter.remove_cgroup == NULL) ||
                        (adapter.remove_cgroup(
                             adapter.context,
                             &entry->operation) !=
                         P6C_RESULT_OK)) {
                        result = P6C_RESULT_RECOVERY_REQUIRED;
                    } else {
                        entry->cgroup_allocated = false;
                    }
                } else if (entry->cgroup_allocated) {
                    result = P6C_RESULT_RECOVERY_REQUIRED;
                }
            }
            physical_pending =
                (entry->operation.physical_custody != P6C_CHILD_NONE) &&
                (entry->operation.physical_custody < P6C_CHILD_REAPED);
            transcript_pending =
                (entry->operation.physical_custody >=
                 P6C_CHILD_REAPED) &&
                (!entry->stdout_transcript.finalized ||
                 !entry->stderr_transcript.finalized);
            if (physical_pending || transcript_pending ||
                entry->cgroup_allocated) {
                custody_pending = true;
                if (result != P6C_RESULT_OK) {
                    (void)p6c_service_mark_recovery(
                        entry, entry->operation.resume_state);
                }
            }
        }
        if (!custody_pending) {
            (void)reason;
            return P6C_RESULT_OK;
        }
        p6c_service_degraded_backoff(&backoff_ns);
    }
}

static enum p6c_result p6c_service_check_replay(
    struct p6c_service_registry *registry,
    const struct p6c_peer_identity *peer,
    const struct p6c_frame_view *frame,
    const uint8_t *packet, size_t packet_size)
{
    struct p6c_sha256 hash;
    uint8_t digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t command_identity[P6C_SHA256_BYTES];
    const struct p6c_field_view *field;
    size_t index;

    memset(operation_id, 0, sizeof(operation_id));
    memset(command_identity, 0, sizeof(command_identity));
    p6c_sha256_init(&hash);
    if ((p6c_sha256_update(&hash, packet, packet_size) !=
         P6C_RESULT_OK) ||
        (p6c_sha256_final(&hash, digest) != P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    for (index = 0U; index < registry->replay.count; ++index) {
        const struct p6c_durable_replay_entry *entry =
            &registry->replay.entries[index];

        if (memcmp(
                entry->request_id, frame->request_id,
                P6C_REQUEST_ID_BYTES) != 0) {
            continue;
        }
        return (entry->controller_user != peer->user_id) ?
                   P6C_RESULT_UNAUTHORIZED :
                   P6C_RESULT_CONFLICT;
    }
    if (registry->replay.count >= P6C_REPLAY_CAPACITY) {
        return P6C_RESULT_LIMIT;
    }
    field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_OPERATION_ID);
    if ((field != NULL) &&
        (field->value_length == (uint32_t)P6C_OPERATION_ID_BYTES)) {
        memcpy(operation_id, field->value, P6C_OPERATION_ID_BYTES);
    }
    if (((frame->message_type == (uint16_t)P6C_REQUEST_START) ||
         (frame->message_type == (uint16_t)P6C_REQUEST_RUN_ONCE)) &&
        ((registry->count + registry->tombstone_count) >=
         P6C_TOMBSTONE_CAPACITY) &&
        (p6c_service_find_entry(registry, operation_id) == NULL) &&
        (p6c_service_find_tombstone(registry, operation_id) == NULL)) {
        return P6C_RESULT_LIMIT;
    }
    field = p6c_service_find_field(
        frame, (uint16_t)P6C_FIELD_OPERATION_DIGEST);
    if ((field != NULL) &&
        (field->value_length == (uint32_t)P6C_SHA256_BYTES)) {
        memcpy(command_identity, field->value, P6C_SHA256_BYTES);
    } else {
        field = p6c_service_find_field(
            frame, (uint16_t)P6C_FIELD_PUBLICATION_ID);
        if ((field != NULL) &&
            (field->value_length == (uint32_t)P6C_SHA256_BYTES)) {
            memcpy(
                command_identity, field->value,
                P6C_SHA256_BYTES);
        }
    }
    return p6c_replay_ledger_reserve(
        registry, peer, frame->message_type, frame->request_id,
        digest, operation_id, command_identity);
}

enum p6c_result p6c_service_run(struct p6c_service_config *configuration)
{
    struct p6c_peer_identity peer;
    struct p6c_service_registry registry;
    uint8_t *packet;
    uint8_t zero_request[P6C_REQUEST_ID_BYTES];
    uint8_t zero_token[P6C_RECOVERY_TOKEN_BYTES];
    enum p6c_result result;

    if ((configuration == NULL) ||
        (p6c_service_validate_root(
             &configuration->journal_root,
             P6C_DESCRIPTOR_DIRECTORY) != P6C_RESULT_OK) ||
        (p6c_service_validate_root(
             &configuration->source_root,
             P6C_DESCRIPTOR_DIRECTORY) != P6C_RESULT_OK) ||
        (p6c_service_validate_root(
             &configuration->cgroup_root,
             P6C_DESCRIPTOR_CGROUP) != P6C_RESULT_OK) ||
        (p6c_service_validate_root(
             &configuration->evidence_root,
             P6C_DESCRIPTOR_DIRECTORY) != P6C_RESULT_OK)) {
        return P6C_RESULT_UNSAFE;
    }
    result = p6c_authenticate_peer(
        &configuration->socket, configuration->controller_user, &peer);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    p6c_service_registry_init(&registry, configuration);
    result = p6c_service_load_registry(&registry);
    if (result != P6C_RESULT_OK) {
        (void)p6c_service_cleanup_after_disconnect(
            &registry, P6C_DISCONNECT_RECEIVE_FAILURE);
        p6c_service_registry_close(&registry);
        return result;
    }
    result = p6c_replay_ledger_open(&registry);
    if (result != P6C_RESULT_OK) {
        (void)p6c_service_cleanup_after_disconnect(
            &registry, P6C_DISCONNECT_RECEIVE_FAILURE);
        p6c_service_registry_close(&registry);
        return result;
    }
    packet = malloc((size_t)P6C_MAX_FRAME_BYTES + 1U);
    if (packet == NULL) {
        (void)p6c_service_cleanup_after_disconnect(
            &registry, P6C_DISCONNECT_RECEIVE_FAILURE);
        p6c_service_registry_close(&registry);
        return P6C_RESULT_SYSTEM;
    }
    memset(zero_request, 0, sizeof(zero_request));
    memset(zero_token, 0, sizeof(zero_token));
    for (;;) {
        ssize_t amount;
        int message_flags = 0;
        struct p6c_received_authority authority;
        struct p6c_frame_view frame;
        enum p6c_parse_result parse_result;
        const uint8_t *response_request = zero_request;

        amount = p6c_service_receive_next(
            &registry, packet,
            (size_t)P6C_MAX_FRAME_BYTES + 1U, &message_flags,
            &authority);
        if (amount == 0) {
            result = p6c_service_cleanup_after_disconnect(
                &registry, P6C_DISCONNECT_RECEIVE_EOF);
            free(packet);
            p6c_service_registry_close(&registry);
            return result;
        }
#ifdef P6C_TESTING
        if (amount == P6C_TEST_INPUT_COMPLETE) {
            result = p6c_service_cleanup_after_disconnect(
                &registry, P6C_DISCONNECT_RECEIVE_EOF);
            free(packet);
            p6c_service_registry_close(&registry);
            return result;
        }
#endif
        if (amount < 0) {
            (void)p6c_service_cleanup_after_disconnect(
                &registry, P6C_DISCONNECT_RECEIVE_FAILURE);
            free(packet);
            p6c_service_registry_close(&registry);
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if ((size_t)amount >=
            P6C_HEADER_REQUEST_ID_OFFSET + P6C_REQUEST_ID_BYTES) {
            response_request = &packet[P6C_HEADER_REQUEST_ID_OFFSET];
        }
        if (authority.invalid ||
            ((message_flags & (MSG_TRUNC | MSG_CTRUNC)) != 0) ||
            ((message_flags &
              ~(MSG_CMSG_CLOEXEC | MSG_EOR | MSG_TRUNC | MSG_CTRUNC)) !=
             0) ||
            ((size_t)amount > (size_t)P6C_MAX_FRAME_BYTES)) {
            if (authority.credential_directory >= 0) {
                (void)close(authority.credential_directory);
                authority.credential_directory = P6C_INVALID_DESCRIPTOR;
            }
            result = p6c_service_send_error(
                &configuration->socket, response_request,
                P6C_STATUS_INVALID_FRAME, "INVALID_FRAME", false,
                P6C_OPERATION_ABSENT, zero_token);
            if (result != P6C_RESULT_OK) {
                (void)p6c_service_cleanup_after_disconnect(
                    &registry, P6C_DISCONNECT_SEND_FAILURE);
                free(packet);
                p6c_service_registry_close(&registry);
                return result;
            }
            continue;
        }
        parse_result = p6c_decode_request(
            packet, (size_t)amount, &frame);
        if (parse_result != P6C_PARSE_OK) {
            if (authority.credential_directory >= 0) {
                (void)close(authority.credential_directory);
                authority.credential_directory = P6C_INVALID_DESCRIPTOR;
            }
            result = p6c_service_send_error(
                &configuration->socket, response_request,
                (parse_result == P6C_PARSE_UNSUPPORTED_VERSION) ?
                    P6C_STATUS_UNSUPPORTED_VERSION :
                    P6C_STATUS_INVALID_FRAME,
                (parse_result == P6C_PARSE_UNSUPPORTED_VERSION) ?
                    "UNSUPPORTED_VERSION" :
                    "INVALID_FRAME",
                false, P6C_OPERATION_ABSENT, zero_token);
            if (result != P6C_RESULT_OK) {
                (void)p6c_service_cleanup_after_disconnect(
                    &registry, P6C_DISCONNECT_SEND_FAILURE);
                free(packet);
                p6c_service_registry_close(&registry);
                return result;
            }
            continue;
        }
        {
            const struct p6c_field_view *credential_manifest =
                p6c_service_find_field(
                    &frame,
                    (uint16_t)P6C_FIELD_CREDENTIAL_MANIFEST);
            bool credential_request =
                (frame.message_type == (uint16_t)P6C_REQUEST_START) ||
                (frame.message_type == (uint16_t)P6C_REQUEST_RUN_ONCE);
            bool descriptor_present =
                authority.credential_directory >= 0;

            if ((!credential_request && descriptor_present) ||
                (credential_request &&
                 ((credential_manifest != NULL) !=
                  descriptor_present))) {
                if (authority.credential_directory >= 0) {
                    (void)close(authority.credential_directory);
                    authority.credential_directory =
                        P6C_INVALID_DESCRIPTOR;
                }
                result = p6c_service_send_error(
                    &configuration->socket, frame.request_id,
                    P6C_STATUS_INVALID_FRAME, "INVALID_FRAME", false,
                    P6C_OPERATION_ABSENT, zero_token);
                if (result != P6C_RESULT_OK) {
                    (void)p6c_service_cleanup_after_disconnect(
                        &registry, P6C_DISCONNECT_SEND_FAILURE);
                    free(packet);
                    p6c_service_registry_close(&registry);
                    return result;
                }
                continue;
            }
        }
        result = p6c_service_check_replay(
            &registry, &peer, &frame, packet, (size_t)amount);
        if (result != P6C_RESULT_OK) {
            if (authority.credential_directory >= 0) {
                (void)close(authority.credential_directory);
                authority.credential_directory = P6C_INVALID_DESCRIPTOR;
            }
            result = p6c_service_send_result_error(
                &configuration->socket, frame.request_id,
                result, NULL);
            if (result != P6C_RESULT_OK) {
                (void)p6c_service_cleanup_after_disconnect(
                    &registry, P6C_DISCONNECT_SEND_FAILURE);
                free(packet);
                p6c_service_registry_close(&registry);
                return result;
            }
            continue;
        }
        if (frame.message_type == (uint16_t)P6C_REQUEST_HELLO) {
            uint8_t hello[8] = {
                UINT8_C(0), UINT8_C(1), UINT8_C(0), UINT8_C(0),
                UINT8_C(0), UINT8_C(0), UINT8_C(0), UINT8_C(0)
            };

            result = p6c_service_send(
                &configuration->socket,
                (uint16_t)(P6C_RESPONSE_BIT | P6C_REQUEST_HELLO),
                frame.request_id, hello, sizeof(hello));
        } else if (frame.message_type ==
                   (uint16_t)P6C_REQUEST_START) {
            result = p6c_service_handle_start(
                &registry, &peer, &frame, &authority);
        } else if (frame.message_type ==
                   (uint16_t)P6C_REQUEST_STATUS) {
            result = p6c_service_handle_status(
                &registry, &peer, &frame);
        } else if (frame.message_type ==
                   (uint16_t)P6C_REQUEST_STOP) {
            result = p6c_service_handle_stop(
                &registry, &peer, &frame);
        } else if (frame.message_type ==
                   (uint16_t)P6C_REQUEST_RUN_ONCE) {
            result = p6c_service_handle_run_once(
                &registry, &peer, &frame, &authority);
        } else if (frame.message_type ==
                   (uint16_t)P6C_REQUEST_READ_TRANSCRIPT) {
            result = p6c_service_handle_read_transcript(
                &registry, &peer, &frame);
        } else if (frame.message_type ==
                   (uint16_t)P6C_REQUEST_PUBLISH_BUNDLE) {
            result = p6c_service_handle_publish(
                &registry, &peer, &frame);
        } else if (frame.message_type ==
                   (uint16_t)P6C_REQUEST_ACK) {
            result = p6c_service_handle_ack(
                &registry, &peer, &frame);
        } else if (frame.message_type ==
                   (uint16_t)P6C_REQUEST_RECOVER) {
            result = p6c_service_handle_recover(
                &registry, &peer, &frame);
        } else {
            result = p6c_service_send_error(
                &configuration->socket, frame.request_id,
                P6C_STATUS_INVALID_REQUEST, "INVALID_REQUEST",
                false, P6C_OPERATION_ABSENT, zero_token);
        }
        if (authority.credential_directory >= 0) {
            (void)close(authority.credential_directory);
            authority.credential_directory = P6C_INVALID_DESCRIPTOR;
        }
        if (result != P6C_RESULT_OK) {
            (void)p6c_service_cleanup_after_disconnect(
                &registry, P6C_DISCONNECT_SEND_FAILURE);
            free(packet);
            p6c_service_registry_close(&registry);
            return result;
        }
    }
}

enum p6c_result p6c_service_config_close(
    struct p6c_service_config *configuration)
{
    struct p6c_owned_fd *owners[5];
    enum p6c_result result = P6C_RESULT_OK;
    size_t index;

    if (configuration == NULL) {
        return P6C_RESULT_INVALID;
    }
    owners[0] = &configuration->socket;
    owners[1] = &configuration->journal_root;
    owners[2] = &configuration->source_root;
    owners[3] = &configuration->cgroup_root;
    owners[4] = &configuration->evidence_root;
    for (index = 0U; index < sizeof(owners) / sizeof(owners[0]); ++index) {
        if (p6c_owned_fd_is_live(owners[index]) &&
            (p6c_owned_fd_close(owners[index]) != P6C_RESULT_OK)) {
            result = P6C_RESULT_RECOVERY_REQUIRED;
        }
    }
    return result;
}
