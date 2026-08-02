#include "p6c_types.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/sched.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>


static void p6c_process_store_u64(
    uint8_t output[static 8], uint64_t value)
{
    size_t index;

    for (index = 0U; index < 8U; ++index) {
        output[7U - index] =
            (uint8_t)(value >> (index * 8U));
    }
}

static void p6c_operation_result_payload(
    const struct p6c_operation *operation,
    uint8_t result_payload[static P6C_RESULT_PAYLOAD_BYTES])
{
    memset(result_payload, 0, P6C_RESULT_PAYLOAD_BYTES);
    p6c_process_store_u64(
        &result_payload[P6C_RESULT_STDOUT_OBSERVED_OFFSET],
        operation->stdout_transcript->observed_size);
    p6c_process_store_u64(
        &result_payload[P6C_RESULT_STDOUT_RETAINED_OFFSET],
        operation->stdout_transcript->retained_size);
    p6c_process_store_u64(
        &result_payload[P6C_RESULT_STDERR_OBSERVED_OFFSET],
        operation->stderr_transcript->observed_size);
    p6c_process_store_u64(
        &result_payload[P6C_RESULT_STDERR_RETAINED_OFFSET],
        operation->stderr_transcript->retained_size);
    if (operation->stdout_transcript->truncated) {
        result_payload[P6C_RESULT_FLAGS_OFFSET] |=
            P6C_RESULT_FLAG_STDOUT_TRUNCATED;
    }
    if (operation->stderr_transcript->truncated) {
        result_payload[P6C_RESULT_FLAGS_OFFSET] |=
            P6C_RESULT_FLAG_STDERR_TRUNCATED;
    }
    result_payload[P6C_RESULT_EXIT_STATUS_OFFSET] =
        (uint8_t)((uint32_t)operation->exit_status >> 24);
    result_payload[P6C_RESULT_EXIT_STATUS_OFFSET + 1U] =
        (uint8_t)((uint32_t)operation->exit_status >> 16);
    result_payload[P6C_RESULT_EXIT_STATUS_OFFSET + 2U] =
        (uint8_t)((uint32_t)operation->exit_status >> 8);
    result_payload[P6C_RESULT_EXIT_STATUS_OFFSET + 3U] =
        (uint8_t)(uint32_t)operation->exit_status;
}

static enum p6c_result p6c_operation_mark_recovery(
    struct p6c_operation *operation,
    enum p6c_operation_state resume_state)
{
    uint8_t payload[1];

    if (operation == NULL) {
        return P6C_RESULT_INVALID;
    }
    operation->resume_state = resume_state;
    payload[0] = (uint8_t)resume_state;
    if ((operation->journal != NULL) &&
        !operation->journal->recovery_required &&
        (operation->journal->durable_state !=
         P6C_OPERATION_RECOVERY_REQUIRED)) {
        if (p6c_journal_append(
                operation->journal, P6C_OPERATION_RECOVERY_REQUIRED,
                payload, sizeof(payload)) != P6C_RESULT_OK) {
            operation->journal->recovery_required = true;
        }
    }
    operation->state = P6C_OPERATION_RECOVERY_REQUIRED;
    operation->authority_retained = true;
    return P6C_RESULT_RECOVERY_REQUIRED;
}

static enum p6c_result p6c_operation_transition(
    struct p6c_operation *operation,
    enum p6c_operation_state next_state,
    const void *payload,
    size_t payload_length)
{
    if ((operation == NULL) || (operation->journal == NULL) ||
        (p6c_journal_append(operation->journal, next_state, payload,
                            payload_length) != P6C_RESULT_OK)) {
        enum p6c_operation_state resume =
            (operation == NULL) ? P6C_OPERATION_ABSENT :
                                  operation->resume_state;
        return p6c_operation_mark_recovery(operation, resume);
    }
    operation->state = next_state;
    operation->resume_state = next_state;
    return P6C_RESULT_OK;
}

static int p6c_operation_prerequisites(
    const struct p6c_operation *operation)
{
    return (operation != NULL) &&
           (operation->journal != NULL) &&
           (operation->executable != NULL) &&
           p6c_owned_fd_is_live(&operation->executable->file) &&
           (operation->cgroup != NULL) &&
           p6c_owned_fd_is_live(operation->cgroup) &&
           (operation->status_channel != NULL) &&
           p6c_owned_fd_is_live(&operation->status_channel->first) &&
           p6c_owned_fd_is_live(&operation->status_channel->second) &&
           (operation->stdout_channel != NULL) &&
           p6c_owned_fd_is_live(&operation->stdout_channel->first) &&
           p6c_owned_fd_is_live(&operation->stdout_channel->second) &&
           (operation->stderr_channel != NULL) &&
           p6c_owned_fd_is_live(&operation->stderr_channel->first) &&
           p6c_owned_fd_is_live(&operation->stderr_channel->second) &&
           (operation->stdout_transcript != NULL) &&
           p6c_owned_fd_is_live(&operation->stdout_transcript->sink) &&
           (operation->stderr_transcript != NULL) &&
           p6c_owned_fd_is_live(&operation->stderr_transcript->sink);
}

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
    struct p6c_transcript *stderr_transcript)
{
    if ((operation == NULL) || (operation_id == NULL) ||
        (recovery_token == NULL) || (journal == NULL) ||
        (journal->durable_state != P6C_OPERATION_CGROUP_CREATED)) {
        return P6C_RESULT_INVALID;
    }
    memset(operation, 0, sizeof(*operation));
    memcpy(operation->operation_id, operation_id, P6C_OPERATION_ID_BYTES);
    memcpy(operation->recovery_token, recovery_token,
           P6C_RECOVERY_TOKEN_BYTES);
    operation->journal = journal;
    operation->executable = executable;
    operation->cgroup = cgroup;
    operation->status_channel = status_channel;
    operation->stdout_channel = stdout_channel;
    operation->stderr_channel = stderr_channel;
    operation->stdout_transcript = stdout_transcript;
    operation->stderr_transcript = stderr_transcript;
    p6c_owned_fd_reset(&operation->pidfd);
    operation->child_pid = (pid_t)-1;
    operation->state = P6C_OPERATION_CGROUP_CREATED;
    operation->resume_state = P6C_OPERATION_CGROUP_CREATED;
    operation->exit_status = P6C_UNKNOWN_EXIT_STATUS;
    operation->authority_retained = true;
    operation->physical_custody = P6C_CHILD_NONE;
    if (!p6c_operation_prerequisites(operation)) {
        memset(operation, 0, sizeof(*operation));
        p6c_owned_fd_reset(&operation->pidfd);
        operation->child_pid = (pid_t)-1;
        return P6C_RESULT_INVALID;
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_operation_start(
    struct p6c_operation *operation,
    const struct p6c_process_adapter *adapter)
{
    enum p6c_result result;
    enum p6c_exec_confirmation confirmation;

    if (!p6c_operation_prerequisites(operation) || (adapter == NULL) ||
        (adapter->clone_child == NULL) ||
        (adapter->confirm_exec == NULL) ||
        (operation->state != P6C_OPERATION_CGROUP_CREATED) ||
        p6c_owned_fd_is_live(&operation->pidfd) ||
        (operation->child_pid > (pid_t)0) ||
        (operation->physical_custody != P6C_CHILD_NONE)) {
        return P6C_RESULT_INVALID;
    }
    result = adapter->clone_child(adapter->context, operation);
    if ((operation->physical_custody == P6C_CHILD_NONE) &&
        (operation->child_pid > (pid_t)0)) {
        operation->physical_custody = P6C_CHILD_PID_WAITABLE;
    }
    if ((operation->physical_custody < P6C_CHILD_PIDFD_OWNED) &&
        p6c_owned_fd_is_live(&operation->pidfd)) {
        operation->physical_custody = P6C_CHILD_PIDFD_OWNED;
    }
    if (result != P6C_RESULT_OK) {
        return p6c_operation_mark_recovery(
            operation,
            (operation->physical_custody == P6C_CHILD_NONE) ?
                P6C_OPERATION_CGROUP_CREATED :
                P6C_OPERATION_CHILD_CLONED);
    }
    if ((operation->physical_custody != P6C_CHILD_PIDFD_OWNED) ||
        !p6c_owned_fd_is_live(&operation->pidfd)) {
        return p6c_operation_mark_recovery(
            operation, P6C_OPERATION_CHILD_CLONED);
    }
    operation->resume_state = P6C_OPERATION_CHILD_CLONED;
    result = p6c_operation_transition(
        operation, P6C_OPERATION_CHILD_CLONED, NULL, 0U);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    confirmation = adapter->confirm_exec(adapter->context, operation);
    if (confirmation != P6C_EXEC_CONFIRM_CLEAN_EOF) {
        return p6c_operation_mark_recovery(
            operation, P6C_OPERATION_CHILD_CLONED);
    }
    result = p6c_operation_transition(
        operation, P6C_OPERATION_EXEC_CONFIRMED, NULL, 0U);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    return p6c_operation_transition(
        operation, P6C_OPERATION_RUNNING, NULL, 0U);
}

static enum p6c_result p6c_stop_callback(
    struct p6c_operation *operation,
    enum p6c_result (*callback)(void *, struct p6c_operation *),
    void *context,
    enum p6c_operation_state resume_state)
{
    if ((callback == NULL) ||
        (callback(context, operation) != P6C_RESULT_OK)) {
        return p6c_operation_mark_recovery(operation, resume_state);
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_operation_stop(
    struct p6c_operation *operation,
    const struct p6c_process_adapter *adapter)
{
    enum p6c_operation_state progress;
    enum p6c_result result;
    int32_t exit_status;
    uint8_t exit_payload[4];
    bool durable_cleanup;

    if ((operation == NULL) || (adapter == NULL) ||
        !operation->authority_retained ||
        ((operation->state != P6C_OPERATION_RUNNING) &&
         (operation->state != P6C_OPERATION_RECOVERY_REQUIRED) &&
         (operation->state != P6C_OPERATION_RESULT_RETAINED))) {
        return P6C_RESULT_INVALID;
    }
    if (operation->state == P6C_OPERATION_RESULT_RETAINED) {
        return P6C_RESULT_OK;
    }
    progress = (operation->state == P6C_OPERATION_RECOVERY_REQUIRED) ?
                   operation->resume_state :
                   operation->state;
    durable_cleanup = (operation->journal != NULL) &&
                      !operation->journal->recovery_required;
    if ((progress < P6C_OPERATION_CHILD_CLONED) &&
        (operation->physical_custody != P6C_CHILD_NONE)) {
        progress = P6C_OPERATION_CHILD_CLONED;
    }
    if ((progress < P6C_OPERATION_CHILD_CLONED) ||
        (progress > P6C_OPERATION_RESULT_RETAINED)) {
        return P6C_RESULT_INVALID;
    }
    if (progress < P6C_OPERATION_STOP_REQUESTED) {
        if (durable_cleanup) {
            result = p6c_operation_transition(
                operation, P6C_OPERATION_STOP_REQUESTED, NULL, 0U);
            if (result != P6C_RESULT_OK) {
                durable_cleanup = false;
            }
        }
        progress = P6C_OPERATION_STOP_REQUESTED;
        operation->resume_state = progress;
    }
    if (progress == P6C_OPERATION_STOP_REQUESTED) {
        if ((operation->physical_custody >= P6C_CHILD_PID_WAITABLE) &&
            (operation->physical_custody < P6C_CHILD_EXIT_OBSERVED) &&
            (adapter->signal_term != NULL) &&
            (adapter->signal_term(adapter->context, operation) !=
             P6C_RESULT_OK)) {
            return p6c_operation_mark_recovery(operation, progress);
        }
        if ((operation->physical_custody >= P6C_CHILD_PID_WAITABLE) &&
            (operation->physical_custody < P6C_CHILD_EXIT_OBSERVED) &&
            (adapter->wait_grace != NULL)) {
            result = adapter->wait_grace(adapter->context, operation);
            if ((result != P6C_RESULT_OK) &&
                (result != P6C_RESULT_TIMEOUT)) {
                return p6c_operation_mark_recovery(operation, progress);
            }
        }
        result = p6c_stop_callback(
            operation, adapter->freeze_cgroup, adapter->context, progress);
        if (result != P6C_RESULT_OK) {
            return result;
        }
        result = p6c_stop_callback(
            operation, adapter->kill_cgroup, adapter->context, progress);
        if (result != P6C_RESULT_OK) {
            return result;
        }
        if (durable_cleanup) {
            result = p6c_operation_transition(
                operation, P6C_OPERATION_CGROUP_KILLED, NULL, 0U);
            if (result != P6C_RESULT_OK) {
                durable_cleanup = false;
            }
        }
        progress = P6C_OPERATION_CGROUP_KILLED;
        operation->resume_state = progress;
    }
    if (progress == P6C_OPERATION_CGROUP_KILLED) {
        result = p6c_stop_callback(
            operation, adapter->wait_cgroup_empty, adapter->context,
            progress);
        if (result != P6C_RESULT_OK) {
            return result;
        }
        if (durable_cleanup) {
            result = p6c_operation_transition(
                operation, P6C_OPERATION_CGROUP_EMPTY, NULL, 0U);
            if (result != P6C_RESULT_OK) {
                durable_cleanup = false;
            }
        }
        progress = P6C_OPERATION_CGROUP_EMPTY;
        operation->resume_state = progress;
    }
    if (progress == P6C_OPERATION_CGROUP_EMPTY) {
        exit_status = P6C_UNKNOWN_EXIT_STATUS;
        if ((operation->physical_custody < P6C_CHILD_PID_WAITABLE) ||
            (operation->physical_custody >= P6C_CHILD_EXIT_OBSERVED) ||
            (adapter->observe_child == NULL) ||
            (adapter->observe_child(adapter->context, operation,
                                    &exit_status) != P6C_RESULT_OK)) {
            return p6c_operation_mark_recovery(operation, progress);
        }
        operation->physical_custody = P6C_CHILD_EXIT_OBSERVED;
        exit_payload[0] = (uint8_t)((uint32_t)exit_status >> 24);
        exit_payload[1] = (uint8_t)((uint32_t)exit_status >> 16);
        exit_payload[2] = (uint8_t)((uint32_t)exit_status >> 8);
        exit_payload[3] = (uint8_t)(uint32_t)exit_status;
        if (durable_cleanup) {
            result = p6c_operation_transition(
                operation, P6C_OPERATION_CHILD_EXIT_OBSERVED,
                exit_payload, sizeof(exit_payload));
            if (result != P6C_RESULT_OK) {
                durable_cleanup = false;
            }
        }
        operation->exit_status = exit_status;
        progress = P6C_OPERATION_CHILD_EXIT_OBSERVED;
        operation->resume_state = progress;
    }
    if (progress == P6C_OPERATION_CHILD_EXIT_OBSERVED) {
        if (operation->physical_custody !=
            P6C_CHILD_EXIT_OBSERVED) {
            return p6c_operation_mark_recovery(operation, progress);
        }
        result = p6c_stop_callback(
            operation, adapter->reap_child, adapter->context, progress);
        if (result != P6C_RESULT_OK) {
            return result;
        }
        operation->physical_custody = P6C_CHILD_REAPED;
        if (durable_cleanup) {
            result = p6c_operation_transition(
                operation, P6C_OPERATION_CHILD_REAPED, NULL, 0U);
            if (result != P6C_RESULT_OK) {
                durable_cleanup = false;
            }
        }
        progress = P6C_OPERATION_CHILD_REAPED;
        operation->resume_state = progress;
    }
    if (progress == P6C_OPERATION_CHILD_REAPED) {
        uint8_t transcript_payload[P6C_TRANSCRIPTS_PAYLOAD_BYTES];

        result = p6c_stop_callback(
            operation, adapter->finalize_transcripts, adapter->context,
            progress);
        if (result != P6C_RESULT_OK) {
            return result;
        }
        memcpy(transcript_payload, operation->stdout_transcript->digest,
               P6C_SHA256_BYTES);
        memcpy(&transcript_payload[P6C_SHA256_BYTES],
               operation->stderr_transcript->digest,
               P6C_SHA256_BYTES);
        if (durable_cleanup) {
            result = p6c_operation_transition(
                operation, P6C_OPERATION_TRANSCRIPTS_FINAL,
                transcript_payload, sizeof(transcript_payload));
            if (result != P6C_RESULT_OK) {
                durable_cleanup = false;
            }
        }
        progress = P6C_OPERATION_TRANSCRIPTS_FINAL;
        operation->resume_state = progress;
    }
    if (progress == P6C_OPERATION_TRANSCRIPTS_FINAL) {
        uint8_t result_payload[P6C_RESULT_PAYLOAD_BYTES];

        p6c_operation_result_payload(operation, result_payload);
        if (!durable_cleanup) {
            operation->state = P6C_OPERATION_RECOVERY_REQUIRED;
            operation->authority_retained = true;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (!operation->journal->transcript_digests_committed) {
            result = p6c_journal_append_transcript_digests(
                operation->journal,
                operation->stdout_transcript->retained_digest,
                operation->stderr_transcript->retained_digest);
            if (result != P6C_RESULT_OK) {
                return p6c_operation_mark_recovery(operation, progress);
            }
        }
        if (!operation->journal->cgroup_removal_intent) {
            if (!p6c_owned_fd_is_live(operation->cgroup)) {
                return p6c_operation_mark_recovery(operation, progress);
            }
            result = p6c_journal_append_cgroup_removal_intent(
                operation->journal,
                (uint64_t)operation->cgroup->device,
                (uint64_t)operation->cgroup->inode,
                result_payload);
            if (result != P6C_RESULT_OK) {
                return p6c_operation_mark_recovery(operation, progress);
            }
        } else if ((operation->journal->cgroup_device !=
                    (uint64_t)operation->cgroup->device) ||
                   (operation->journal->cgroup_inode !=
                    (uint64_t)operation->cgroup->inode) ||
                   (memcmp(operation->journal->retained_result_payload,
                           result_payload,
                           P6C_RESULT_PAYLOAD_BYTES) != 0)) {
            return p6c_operation_mark_recovery(operation, progress);
        }
        result = p6c_stop_callback(
            operation, adapter->remove_cgroup, adapter->context, progress);
        if (result != P6C_RESULT_OK) {
            return result;
        }
        return p6c_operation_transition(
            operation, P6C_OPERATION_RESULT_RETAINED,
            result_payload, sizeof(result_payload));
    }
    return (progress == P6C_OPERATION_RESULT_RETAINED) ?
               P6C_RESULT_OK :
               P6C_RESULT_INVALID;
}

static enum p6c_result p6c_close_operation_owners(
    struct p6c_operation *operation)
{
    enum p6c_result result = P6C_RESULT_OK;

    if (p6c_owned_fd_is_live(&operation->pidfd) &&
        (p6c_owned_fd_close(&operation->pidfd) != P6C_RESULT_OK)) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((operation->status_channel != NULL) &&
        (p6c_owned_pair_close(operation->status_channel) !=
         P6C_RESULT_OK)) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((operation->stdout_channel != NULL) &&
        (p6c_owned_pair_close(operation->stdout_channel) !=
         P6C_RESULT_OK)) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((operation->stderr_channel != NULL) &&
        (p6c_owned_pair_close(operation->stderr_channel) !=
         P6C_RESULT_OK)) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((operation->stdout_transcript != NULL) &&
        (p6c_transcript_close(operation->stdout_transcript) !=
         P6C_RESULT_OK)) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((operation->stderr_transcript != NULL) &&
        (p6c_transcript_close(operation->stderr_transcript) !=
         P6C_RESULT_OK)) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((operation->executable != NULL) &&
        (p6c_executable_close(operation->executable) != P6C_RESULT_OK)) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((operation->cgroup != NULL) &&
        p6c_owned_fd_is_live(operation->cgroup) &&
        (p6c_owned_fd_close(operation->cgroup) != P6C_RESULT_OK)) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    return result;
}

enum p6c_result p6c_operation_ack(
    struct p6c_operation *operation,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[static P6C_RECOVERY_TOKEN_BYTES])
{
    enum p6c_result result;
    bool resume_acknowledged = false;
    bool acknowledge_allowed;

    if ((operation == NULL) || (operation_id == NULL) ||
        (recovery_token == NULL)) {
        return P6C_RESULT_INVALID;
    }
    if ((memcmp(operation->operation_id, operation_id,
                P6C_OPERATION_ID_BYTES) != 0) ||
        (memcmp(operation->recovery_token, recovery_token,
                P6C_RECOVERY_TOKEN_BYTES) != 0)) {
        return P6C_RESULT_UNAUTHORIZED;
    }
    if ((operation->state == P6C_OPERATION_ACKNOWLEDGED) &&
        !operation->authority_retained) {
        return P6C_RESULT_OK;
    }
    if ((operation->state == P6C_OPERATION_RECOVERY_REQUIRED) &&
        (operation->resume_state == P6C_OPERATION_ACKNOWLEDGED) &&
        operation->authority_retained) {
        resume_acknowledged = true;
    }
    acknowledge_allowed =
        (operation->state == P6C_OPERATION_RESULT_RETAINED) ||
        ((operation->state == P6C_OPERATION_RECOVERY_REQUIRED) &&
         (operation->resume_state == P6C_OPERATION_RESULT_RETAINED)) ||
        resume_acknowledged;
    if (!acknowledge_allowed || !operation->authority_retained) {
        return P6C_RESULT_INVALID;
    }
    if (!resume_acknowledged) {
        result = p6c_operation_transition(
            operation, P6C_OPERATION_ACKNOWLEDGED, NULL, 0U);
        if (result != P6C_RESULT_OK) {
            return result;
        }
    }
    result = p6c_close_operation_owners(operation);
    if (result != P6C_RESULT_OK) {
        operation->authority_retained = true;
        operation->state = P6C_OPERATION_RECOVERY_REQUIRED;
        operation->resume_state = P6C_OPERATION_ACKNOWLEDGED;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((operation->journal != NULL) &&
        (p6c_journal_close(operation->journal) != P6C_RESULT_OK)) {
        operation->authority_retained = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    operation->authority_retained = false;
    return P6C_RESULT_OK;
}

static int p6c_child_duplicate(int source, int target, bool close_on_exec)
{
    int descriptor_flags = close_on_exec ? FD_CLOEXEC : 0;
    int result;

    if (source == target) {
        return fcntl(target, F_SETFD, descriptor_flags);
    }
    result = dup3(source, target, close_on_exec ? O_CLOEXEC : 0);
    return (result < 0) ? -1 : 0;
}

static void p6c_child_failure(int status_descriptor, uint8_t reason)
{
    uint8_t marker[8] = {
        UINT8_C('P'), UINT8_C('6'), UINT8_C('C'), UINT8_C('E'),
        UINT8_C('X'), UINT8_C('E'), UINT8_C('1'), reason
    };
    size_t offset = 0U;

    while (offset < sizeof(marker)) {
        ssize_t amount = write(status_descriptor, &marker[offset],
                               sizeof(marker) - offset);

        if (amount <= 0) {
            break;
        }
        offset += (size_t)amount;
    }
    _exit(127);
}

enum p6c_result p6c_clone3_spawn(
    struct p6c_operation *operation,
    const struct p6c_spawn_spec *specification)
{
    struct clone_args arguments;
    int pidfd = P6C_INVALID_DESCRIPTOR;
    long clone_result;

    if (!p6c_operation_prerequisites(operation) ||
        (specification == NULL) || (specification->argv == NULL) ||
        (specification->environment == NULL) ||
        (specification->argv_count == 0U) ||
        (specification->argv_count > (size_t)P6C_MAX_ARGV_COUNT) ||
        (specification->environment_count >
         (size_t)P6C_MAX_ENVIRONMENT_COUNT) ||
        p6c_owned_fd_is_live(&operation->pidfd) ||
        ((specification->credential_directory != NULL) &&
         (!p6c_owned_fd_is_live(
              specification->credential_directory) ||
          (specification->credential_directory->type !=
           P6C_DESCRIPTOR_DIRECTORY)))) {
        return P6C_RESULT_INVALID;
    }
    memset(&arguments, 0, sizeof(arguments));
    arguments.flags = (uint64_t)(CLONE_PIDFD | CLONE_INTO_CGROUP);
    arguments.pidfd = (uint64_t)(uintptr_t)&pidfd;
    arguments.cgroup = (uint64_t)(unsigned int)operation->cgroup->descriptor;
    arguments.exit_signal = (uint64_t)SIGCHLD;
    do {
        clone_result = syscall(SYS_clone3, &arguments, sizeof(arguments));
    } while ((clone_result < 0) && (errno == EINTR));
    if (clone_result < 0) {
        return p6c_classify_clone3_errno(errno);
    }
    if (clone_result == 0) {
        int stdout_copy = fcntl(
            operation->stdout_channel->second.descriptor,
            F_DUPFD_CLOEXEC, 6);
        int stderr_copy = fcntl(
            operation->stderr_channel->second.descriptor,
            F_DUPFD_CLOEXEC, 6);
        int executable_copy = fcntl(
            operation->executable->file.descriptor,
            F_DUPFD_CLOEXEC, 6);
        int status_copy = fcntl(
            operation->status_channel->second.descriptor,
            F_DUPFD_CLOEXEC, 6);

        if ((stdout_copy < 0) || (stderr_copy < 0) ||
            (executable_copy < 0) || (status_copy < 0)) {
            _exit(127);
        }
        if ((p6c_child_duplicate(
                 stdout_copy, STDOUT_FILENO, false) != 0) ||
            (p6c_child_duplicate(
                 stderr_copy, STDERR_FILENO, false) != 0) ||
            (p6c_child_duplicate(executable_copy, 3, true) != 0) ||
            (p6c_child_duplicate(status_copy, 4, true) != 0) ||
            ((specification->credential_directory != NULL) &&
             (p6c_child_duplicate(
                  specification->credential_directory->descriptor,
                  5, false) != 0))) {
            p6c_child_failure(4, UINT8_C(1));
        }
        (void)close(STDIN_FILENO);
        if (syscall(
                SYS_close_range,
                (specification->credential_directory == NULL) ? 5U : 6U,
                UINT_MAX, 0U) != 0) {
            p6c_child_failure(4, UINT8_C(2));
        }
        operation->executable->file.descriptor = 3;
        (void)p6c_execve_pinned(
            operation->executable, specification->argv,
            specification->environment);
        p6c_child_failure(4, UINT8_C(3));
    }
    if (clone_result > (long)INT_MAX) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    operation->child_pid = (pid_t)clone_result;
    operation->physical_custody = P6C_CHILD_PID_WAITABLE;
    if (pidfd < 0) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (p6c_failpoint_active(P6C_FAIL_PIDFD_ACQUIRE)) {
        (void)close(pidfd);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (p6c_owned_fd_acquire(
            &operation->pidfd, pidfd, P6C_DESCRIPTOR_PIDFD) !=
        P6C_RESULT_OK) {
        /*
         * Acquisition did not establish a validated owner.  The raw
         * clone3 pidfd is still this function's responsibility and is
         * closed exactly once; cgroup custody remains authoritative.
         */
        (void)close(pidfd);
        p6c_owned_fd_reset(&operation->pidfd);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    operation->physical_custody = P6C_CHILD_PIDFD_OWNED;
    if (p6c_owned_fd_is_live(&operation->status_channel->second) &&
        (p6c_failpoint_active(P6C_FAIL_STATUS_WRITER_CLOSE) ||
         (p6c_owned_fd_close(&operation->status_channel->second) !=
          P6C_RESULT_OK))) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (p6c_owned_fd_is_live(&operation->stdout_channel->second) &&
        (p6c_failpoint_active(P6C_FAIL_STDOUT_WRITER_CLOSE) ||
         (p6c_owned_fd_close(&operation->stdout_channel->second) !=
          P6C_RESULT_OK))) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (p6c_owned_fd_is_live(&operation->stderr_channel->second) &&
        (p6c_failpoint_active(P6C_FAIL_STDERR_WRITER_CLOSE) ||
         (p6c_owned_fd_close(&operation->stderr_channel->second) !=
          P6C_RESULT_OK))) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_classify_clone3_errno(int error_number)
{
    if (error_number == EINTR) {
        return P6C_RESULT_OK;
    }
    if (error_number == ENOSYS) {
        return P6C_RESULT_UNSUPPORTED;
    }
    if (error_number == EINVAL) {
        return P6C_RESULT_INVALID;
    }
    if (error_number == EPERM) {
        return P6C_RESULT_UNAUTHORIZED;
    }
    return P6C_RESULT_SYSTEM;
}

enum p6c_exec_confirmation p6c_confirm_exec_status(
    struct p6c_operation *operation,
    uint32_t timeout_milliseconds)
{
    struct pollfd descriptors[2];
    uint8_t marker[8];
    int result;
    ssize_t amount;

    if ((operation == NULL) ||
        (operation->state != P6C_OPERATION_CHILD_CLONED) ||
        !p6c_owned_fd_is_live(&operation->pidfd) ||
        (operation->status_channel == NULL) ||
        !p6c_owned_fd_is_live(&operation->status_channel->first) ||
        (timeout_milliseconds > (uint32_t)INT32_MAX)) {
        return P6C_EXEC_CONFIRM_ERROR;
    }
    memset(descriptors, 0, sizeof(descriptors));
    descriptors[0].fd = operation->status_channel->first.descriptor;
    descriptors[0].events = (short)(POLLIN | POLLHUP | POLLERR);
    descriptors[1].fd = operation->pidfd.descriptor;
    descriptors[1].events = (short)(POLLIN | POLLHUP | POLLERR);
    do {
        result = poll(descriptors, 2U, (int)timeout_milliseconds);
    } while ((result < 0) && (errno == EINTR));
    if (result == 0) {
        return P6C_EXEC_CONFIRM_TIMEOUT;
    }
    if (result < 0) {
        return P6C_EXEC_CONFIRM_ERROR;
    }
    if (descriptors[1].revents != 0) {
        return P6C_EXEC_CONFIRM_QUICK_EXIT;
    }
    do {
        amount = read(operation->status_channel->first.descriptor,
                      marker, sizeof(marker));
    } while ((amount < 0) && (errno == EINTR));
    if (amount == 0) {
        struct pollfd pidfd_check;

        memset(&pidfd_check, 0, sizeof(pidfd_check));
        pidfd_check.fd = operation->pidfd.descriptor;
        pidfd_check.events = (short)(POLLIN | POLLHUP | POLLERR);
        do {
            result = poll(&pidfd_check, 1U, 0);
        } while ((result < 0) && (errno == EINTR));
        if ((result < 0) || (pidfd_check.revents != 0)) {
            return P6C_EXEC_CONFIRM_QUICK_EXIT;
        }
        return P6C_EXEC_CONFIRM_CLEAN_EOF;
    }
    if (amount < 0) {
        return P6C_EXEC_CONFIRM_ERROR;
    }
    if (amount < (ssize_t)sizeof(marker)) {
        return P6C_EXEC_CONFIRM_PARTIAL;
    }
    return P6C_EXEC_CONFIRM_BYTES;
}

enum p6c_result p6c_pidfd_signal(
    const struct p6c_owned_fd *pidfd, int signal_number)
{
    long result;

    if ((pidfd == NULL) || !p6c_owned_fd_is_live(pidfd) ||
        (pidfd->type != P6C_DESCRIPTOR_PIDFD) ||
        (signal_number <= 0)) {
        return P6C_RESULT_INVALID;
    }
    do {
        result = syscall(
            SYS_pidfd_send_signal, pidfd->descriptor,
            signal_number, NULL, 0U);
    } while ((result != 0) && (errno == EINTR));
    if (result != 0) {
        return P6C_RESULT_SYSTEM;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_pidfd_wait(
    const struct p6c_owned_fd *pidfd, int options,
    int32_t *exit_status)
{
    siginfo_t information;
    int result;

    if ((pidfd == NULL) || !p6c_owned_fd_is_live(pidfd) ||
        (pidfd->type != P6C_DESCRIPTOR_PIDFD)) {
        return P6C_RESULT_INVALID;
    }
    memset(&information, 0, sizeof(information));
    do {
        result = waitid(
            P_PIDFD, (id_t)(unsigned int)pidfd->descriptor,
            &information, options);
    } while ((result != 0) && (errno == EINTR));
    if (result != 0) {
        return P6C_RESULT_SYSTEM;
    }
    if (exit_status != NULL) {
        *exit_status = (int32_t)information.si_status;
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_pidfd_observe(
    const struct p6c_owned_fd *pidfd, int32_t *exit_status)
{
    if (exit_status == NULL) {
        return P6C_RESULT_INVALID;
    }
    return p6c_pidfd_wait(pidfd, WEXITED | WNOWAIT, exit_status);
}

enum p6c_result p6c_pidfd_reap(
    const struct p6c_owned_fd *pidfd)
{
    return p6c_pidfd_wait(pidfd, WEXITED, NULL);
}

static enum p6c_result p6c_child_pid_wait(
    pid_t child_pid, int options, int32_t *exit_status)
{
    siginfo_t information;
    int result;

    if (child_pid <= (pid_t)0) {
        return P6C_RESULT_INVALID;
    }
    memset(&information, 0, sizeof(information));
    do {
        result = waitid(
            P_PID, (id_t)(unsigned int)child_pid,
            &information, options);
    } while ((result != 0) && (errno == EINTR));
    if ((result != 0) || (information.si_pid != child_pid)) {
        return P6C_RESULT_SYSTEM;
    }
    if (exit_status != NULL) {
        *exit_status = (int32_t)information.si_status;
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_child_pid_observe(
    pid_t child_pid, int32_t *exit_status)
{
    if (exit_status == NULL) {
        return P6C_RESULT_INVALID;
    }
    return p6c_child_pid_wait(
        child_pid, WEXITED | WNOWAIT, exit_status);
}

enum p6c_result p6c_child_pid_reap(pid_t child_pid)
{
    return p6c_child_pid_wait(child_pid, WEXITED, NULL);
}
