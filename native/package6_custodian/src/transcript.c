#include "p6c_types.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>


static int p6c_transcript_name_safe(const char *name)
{
    size_t length;
    size_t index;

    if (name == NULL) {
        return 0;
    }
    length = strnlen(name, 65U);
    if ((length == 0U) || (length > 64U) ||
        (name[0] == '.')) {
        return 0;
    }
    for (index = 0U; index < length; ++index) {
        unsigned char character = (unsigned char)name[index];

        if (!(((character >= (unsigned char)'a') &&
               (character <= (unsigned char)'z')) ||
              ((character >= (unsigned char)'0') &&
               (character <= (unsigned char)'9')) ||
              (character == (unsigned char)'.'))) {
            return 0;
        }
    }
    return 1;
}

enum p6c_result p6c_transcript_create(
    const struct p6c_owned_fd *fallback_directory,
    enum p6c_stream_identity stream,
    uint64_t retained_limit,
    bool allow_test_fallback,
    struct p6c_transcript *transcript)
{
    int descriptor;
    struct stat status;
    enum p6c_result result;

    (void)allow_test_fallback;
    if ((fallback_directory == NULL) || (transcript == NULL) ||
        !p6c_owned_fd_is_live(fallback_directory) ||
        (fallback_directory->type != P6C_DESCRIPTOR_DIRECTORY) ||
        ((stream != P6C_STREAM_STDOUT) &&
         (stream != P6C_STREAM_STDERR)) ||
        (retained_limit > P6C_MAX_TRANSCRIPT_RETAINED)) {
        return P6C_RESULT_INVALID;
    }
    memset(transcript, 0, sizeof(*transcript));
    p6c_owned_fd_reset(&transcript->sink);
    descriptor = openat(fallback_directory->descriptor, ".",
                        O_TMPFILE | O_RDWR | O_CLOEXEC,
                        (mode_t)0600);
    if (descriptor < 0) {
        return ((errno == EOPNOTSUPP) || (errno == EISDIR) ||
                (errno == ENOENT)) ?
                   P6C_RESULT_UNSUPPORTED :
                   P6C_RESULT_SYSTEM;
    }
    result = p6c_owned_fd_acquire(
        &transcript->sink, descriptor, P6C_DESCRIPTOR_REGULAR);
    if (result != P6C_RESULT_OK) {
        transcript->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((fstat(descriptor, &status) != 0) || !S_ISREG(status.st_mode) ||
        (status.st_nlink != 0) ||
        ((status.st_mode & (mode_t)0777) != (mode_t)0600)) {
        transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        transcript->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    transcript->stream = stream;
    transcript->retained_limit = retained_limit;
    p6c_sha256_init(&transcript->hash);
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_transcript_write_retained(
    struct p6c_transcript *transcript,
    const uint8_t *data,
    size_t size)
{
    size_t offset = 0U;

    while (offset < size) {
        ssize_t amount;

        if (p6c_failpoint_active(P6C_FAIL_TRANSCRIPT_WRITE)) {
            transcript->recovery_required = true;
            transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        amount = pwrite(
            transcript->sink.descriptor, &data[offset], size - offset,
            (off_t)(transcript->retained_size + (uint64_t)offset));
        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            transcript->recovery_required = true;
            transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (amount == 0) {
            transcript->recovery_required = true;
            transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        offset += (size_t)amount;
    }
    transcript->retained_size += (uint64_t)size;
    return P6C_RESULT_OK;
}

enum p6c_result p6c_transcript_ingest(
    struct p6c_transcript *transcript,
    const void *data,
    size_t size)
{
    uint64_t available;
    size_t retain;
    enum p6c_result result;

    if ((transcript == NULL) || ((data == NULL) && (size != 0U)) ||
        !p6c_owned_fd_is_live(&transcript->sink) ||
        transcript->finalized || transcript->recovery_required ||
        ((uint64_t)size > UINT64_MAX - transcript->observed_size)) {
        return P6C_RESULT_INVALID;
    }
    result = p6c_sha256_update(&transcript->hash, data, size);
    if (result != P6C_RESULT_OK) {
        transcript->recovery_required = true;
        transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    transcript->observed_size += (uint64_t)size;
    available = transcript->retained_limit - transcript->retained_size;
    retain = size;
    if ((uint64_t)retain > available) {
        retain = (size_t)available;
        transcript->truncated = true;
    }
    if (retain != 0U) {
        result = p6c_transcript_write_retained(
            transcript, (const uint8_t *)data, retain);
        if (result != P6C_RESULT_OK) {
            return result;
        }
    }
    if (retain < size) {
        transcript->truncated = true;
    }
    return P6C_RESULT_OK;
}

void p6c_transcript_observe_eof(struct p6c_transcript *transcript)
{
    if (transcript != NULL) {
        transcript->eof_observed = true;
    }
}

void p6c_transcript_prove_cleanup(struct p6c_transcript *transcript)
{
    if (transcript != NULL) {
        transcript->descendant_cleanup_proven = true;
    }
}

enum p6c_result p6c_transcript_finalize(
    struct p6c_transcript *transcript)
{
    if ((transcript == NULL) ||
        !p6c_owned_fd_is_live(&transcript->sink) ||
        transcript->finalized || transcript->recovery_required ||
        !transcript->eof_observed ||
        !transcript->descendant_cleanup_proven) {
        return P6C_RESULT_INVALID;
    }
    if (p6c_failpoint_active(P6C_FAIL_TRANSCRIPT_FSYNC) ||
        (fsync(transcript->sink.descriptor) != 0) ||
        p6c_failpoint_active(P6C_FAIL_TRANSCRIPT_DIGEST) ||
        (p6c_sha256_final(&transcript->hash, transcript->digest) !=
         P6C_RESULT_OK) ||
        (p6c_sha256_fd(
             &transcript->sink, transcript->retained_digest) !=
         P6C_RESULT_OK)) {
        transcript->recovery_required = true;
        transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    transcript->finalized = true;
    return P6C_RESULT_OK;
}

enum p6c_result p6c_transcript_read(
    struct p6c_transcript *transcript,
    uint64_t offset,
    void *output,
    size_t requested,
    size_t *read_size)
{
    uint64_t available;
    size_t amount_to_read;
    size_t total = 0U;

    if ((transcript == NULL) || (output == NULL) || (read_size == NULL) ||
        !p6c_owned_fd_is_live(&transcript->sink) ||
        !transcript->finalized || transcript->recovery_required ||
        (requested > (size_t)P6C_MAX_PAYLOAD_BYTES) ||
        (offset > transcript->retained_size)) {
        return P6C_RESULT_INVALID;
    }
    available = transcript->retained_size - offset;
    amount_to_read = requested;
    if ((uint64_t)amount_to_read > available) {
        amount_to_read = (size_t)available;
    }
    if (p6c_failpoint_active(P6C_FAIL_TRANSCRIPT_READ)) {
        transcript->recovery_required = true;
        transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    while (total < amount_to_read) {
        ssize_t amount = pread(
            transcript->sink.descriptor, (uint8_t *)output + total,
            amount_to_read - total, (off_t)(offset + (uint64_t)total));

        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            transcript->recovery_required = true;
            transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (amount == 0) {
            transcript->recovery_required = true;
            transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        total += (size_t)amount;
    }
    *read_size = total;
    return P6C_RESULT_OK;
}

enum p6c_result p6c_transcript_close(
    struct p6c_transcript *transcript)
{
    if (transcript == NULL) {
        return P6C_RESULT_INVALID;
    }
    if (!p6c_owned_fd_is_live(&transcript->sink)) {
        return P6C_RESULT_OK;
    }
    if (p6c_failpoint_active(P6C_FAIL_TRANSCRIPT_CLOSE)) {
        transcript->recovery_required = true;
        transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return p6c_owned_fd_close(&transcript->sink);
}

enum p6c_result p6c_transcript_link(
    const struct p6c_owned_fd *directory, const char *name,
    struct p6c_transcript *transcript)
{
    struct stat descriptor_status;
    struct stat name_status;

    if ((directory == NULL) || (transcript == NULL) ||
        !p6c_owned_fd_is_live(directory) ||
        (directory->type != P6C_DESCRIPTOR_DIRECTORY) ||
        !p6c_owned_fd_is_live(&transcript->sink) ||
        !transcript->finalized || transcript->recovery_required ||
        !p6c_transcript_name_safe(name) ||
        (fstat(transcript->sink.descriptor, &descriptor_status) != 0) ||
        !S_ISREG(descriptor_status.st_mode) ||
        (descriptor_status.st_uid != geteuid()) ||
        ((descriptor_status.st_mode & (mode_t)0777) != (mode_t)0600) ||
        (descriptor_status.st_size < 0) ||
        ((uint64_t)descriptor_status.st_size !=
         transcript->retained_size)) {
        return P6C_RESULT_INVALID;
    }
    if (fstatat(directory->descriptor, name, &name_status,
                AT_SYMLINK_NOFOLLOW) == 0) {
        if (!S_ISREG(name_status.st_mode) ||
            (name_status.st_nlink != 1) ||
            (name_status.st_uid != descriptor_status.st_uid) ||
            ((name_status.st_mode & (mode_t)0777) != (mode_t)0600) ||
            (name_status.st_dev != descriptor_status.st_dev) ||
            (name_status.st_ino != descriptor_status.st_ino)) {
            transcript->recovery_required = true;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        return P6C_RESULT_OK;
    }
    if (errno != ENOENT) {
        transcript->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((linkat(
             transcript->sink.descriptor, "", directory->descriptor,
             name, AT_EMPTY_PATH) != 0) ||
        (fsync(directory->descriptor) != 0) ||
        (fstatat(directory->descriptor, name, &name_status,
                 AT_SYMLINK_NOFOLLOW) != 0) ||
        !S_ISREG(name_status.st_mode) || (name_status.st_nlink != 1) ||
        (name_status.st_dev != descriptor_status.st_dev) ||
        (name_status.st_ino != descriptor_status.st_ino)) {
        transcript->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_transcript_recover(
    const struct p6c_owned_fd *directory, const char *name,
    enum p6c_stream_identity stream, uint64_t observed_size,
    uint64_t retained_size, bool truncated,
    const uint8_t digest[static P6C_SHA256_BYTES],
    const uint8_t expected_retained_digest[static P6C_SHA256_BYTES],
    struct p6c_transcript *transcript)
{
    struct stat status;
    uint8_t retained_digest[P6C_SHA256_BYTES];
    enum p6c_result result;

    if ((directory == NULL) || (digest == NULL) ||
        (expected_retained_digest == NULL) ||
        (transcript == NULL) || !p6c_owned_fd_is_live(directory) ||
        (directory->type != P6C_DESCRIPTOR_DIRECTORY) ||
        !p6c_transcript_name_safe(name) ||
        ((stream != P6C_STREAM_STDOUT) &&
         (stream != P6C_STREAM_STDERR)) ||
        (retained_size > observed_size) ||
        (retained_size > P6C_MAX_TRANSCRIPT_RETAINED) ||
        (truncated != (observed_size > retained_size))) {
        return P6C_RESULT_INVALID;
    }
    memset(transcript, 0, sizeof(*transcript));
    p6c_owned_fd_reset(&transcript->sink);
    if (!truncated &&
        (memcmp(digest, expected_retained_digest,
                P6C_SHA256_BYTES) != 0)) {
        transcript->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_openat2_owned(
        directory, name, O_RDWR | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_REGULAR, &transcript->sink);
    if (result != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((fstat(transcript->sink.descriptor, &status) != 0) ||
        !S_ISREG(status.st_mode) || (status.st_nlink != 1) ||
        (status.st_uid != geteuid()) ||
        ((status.st_mode & (mode_t)0777) != (mode_t)0600) ||
        (status.st_size < 0) ||
        ((uint64_t)status.st_size != retained_size) ||
        (p6c_sha256_fd(&transcript->sink, retained_digest) !=
         P6C_RESULT_OK) ||
        (memcmp(retained_digest, expected_retained_digest,
                P6C_SHA256_BYTES) != 0)) {
        transcript->sink.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        transcript->recovery_required = true;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    transcript->stream = stream;
    transcript->observed_size = observed_size;
    transcript->retained_size = retained_size;
    transcript->retained_limit = P6C_MAX_TRANSCRIPT_RETAINED;
    transcript->truncated = truncated;
    transcript->eof_observed = true;
    transcript->descendant_cleanup_proven = true;
    transcript->finalized = true;
    memcpy(transcript->digest, digest, P6C_SHA256_BYTES);
    memcpy(transcript->retained_digest, retained_digest,
           P6C_SHA256_BYTES);
    return P6C_RESULT_OK;
}

enum p6c_result p6c_transcript_unlink(
    const struct p6c_owned_fd *directory, const char *name,
    const struct p6c_transcript *transcript)
{
    struct stat status;

    if ((directory == NULL) || (transcript == NULL) ||
        !p6c_owned_fd_is_live(directory) ||
        (directory->type != P6C_DESCRIPTOR_DIRECTORY) ||
        !p6c_transcript_name_safe(name)) {
        return P6C_RESULT_INVALID;
    }
    if (fstatat(directory->descriptor, name, &status,
                AT_SYMLINK_NOFOLLOW) != 0) {
        return (errno == ENOENT) ? P6C_RESULT_OK :
                                  P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (!S_ISREG(status.st_mode) || (status.st_nlink != 1) ||
        (status.st_dev != transcript->sink.device) ||
        (status.st_ino != transcript->sink.inode) ||
        ((status.st_mode & S_IFMT) !=
         (transcript->sink.mode & S_IFMT))) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((unlinkat(directory->descriptor, name, 0) != 0) ||
        (fsync(directory->descriptor) != 0)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}
