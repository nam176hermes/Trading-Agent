#include "p6c_types.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/fs.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/syscall.h>
#include <unistd.h>


static void p6c_hex(const uint8_t *input, size_t size, char *output)
{
    static const char HEX[] = "0123456789abcdef";
    size_t index;

    for (index = 0U; index < size; ++index) {
        output[index * 2U] = HEX[input[index] >> 4];
        output[(index * 2U) + 1U] =
            HEX[input[index] & UINT8_C(0x0f)];
    }
    output[size * 2U] = '\0';
}

static int p6c_public_name_valid(const char *name, size_t maximum)
{
    size_t length;
    size_t index;

    if (name == NULL) {
        return 0;
    }
    length = strnlen(name, maximum + 1U);
    if ((length == 0U) || (length > maximum) ||
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

static enum p6c_result p6c_publication_recovery(
    struct p6c_publication_result *result, enum p6c_result failure)
{
    if (result != NULL) {
        result->recovery_required = true;
    }
    return failure;
}

static enum p6c_result p6c_write_all(
    struct p6c_owned_fd *owner, const uint8_t *content, size_t size,
    enum p6c_failpoint failpoint)
{
    size_t offset = 0U;

    while (offset < size) {
        ssize_t amount;

        if (p6c_failpoint_active(failpoint)) {
            if (offset == 0U) {
                do {
                    amount = write(owner->descriptor, content, 1U);
                } while ((amount < 0) && (errno == EINTR));
                if (amount == 1) {
                    offset = 1U;
                }
            }
            owner->lifecycle = P6C_DESCRIPTOR_RECOVERY;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        do {
            amount = write(owner->descriptor, &content[offset],
                           size - offset);
        } while ((amount < 0) && (errno == EINTR));
        if (amount <= 0) {
            owner->lifecycle = P6C_DESCRIPTOR_RECOVERY;
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        offset += (size_t)amount;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_create_file(
    const struct p6c_owned_fd *directory, const char *name,
    const uint8_t *content, size_t size, enum p6c_failpoint write_failpoint,
    uint8_t digest[static P6C_SHA256_BYTES])
{
    struct p6c_owned_fd file;
    struct stat status;
    struct p6c_sha256 hash;
    int descriptor;
    enum p6c_result result;
    enum p6c_result close_result;

    p6c_owned_fd_reset(&file);
    descriptor = openat(directory->descriptor, name,
                        O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC |
                            O_NOFOLLOW,
                        (mode_t)0600);
    if (descriptor < 0) {
        return (errno == EEXIST) ? P6C_RESULT_CONFLICT :
                                  P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_owned_fd_acquire(
        &file, descriptor, P6C_DESCRIPTOR_REGULAR);
    if (result != P6C_RESULT_OK) {
        if (p6c_owned_fd_is_live(&file)) {
            (void)p6c_owned_fd_close(&file);
        }
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_write_all(
        &file, content, size, write_failpoint);
    if (result == P6C_RESULT_OK) {
        if (p6c_failpoint_active(P6C_FAIL_PUBLICATION_FILE_FSYNC) ||
            (fsync(file.descriptor) != 0)) {
            file.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            result = P6C_RESULT_RECOVERY_REQUIRED;
        }
    }
    if ((fstat(file.descriptor, &status) != 0) ||
        !S_ISREG(status.st_mode) || (status.st_nlink != 1) ||
        (status.st_uid != getuid()) ||
        ((status.st_mode & (mode_t)0777) != (mode_t)0600)) {
        file.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    p6c_sha256_init(&hash);
    if ((p6c_sha256_update(&hash, content, size) != P6C_RESULT_OK) ||
        (p6c_sha256_final(&hash, digest) != P6C_RESULT_OK)) {
        file.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    close_result = p6c_owned_fd_close(&file);
    if (close_result != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return result;
}

static enum p6c_result p6c_manifest_append(
    char *manifest, size_t capacity, size_t *used,
    const char *format, ...)
{
    va_list arguments;
    int written;

    if ((manifest == NULL) || (used == NULL) || (*used >= capacity)) {
        return P6C_RESULT_LIMIT;
    }
    va_start(arguments, format);
    written = vsnprintf(&manifest[*used], capacity - *used,
                        format, arguments);
    va_end(arguments);
    if ((written < 0) || ((size_t)written >= capacity - *used)) {
        return P6C_RESULT_LIMIT;
    }
    *used += (size_t)written;
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_build_manifest(
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    enum p6c_operation_state cleanup_state,
    const struct p6c_publication_item *items,
    size_t item_count,
    const uint8_t item_digests[P6C_MAX_PUBLICATION_FILES]
                              [P6C_SHA256_BYTES],
    char manifest[static P6C_MAX_MANIFEST_BYTES],
    size_t *manifest_size)
{
    char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
    size_t used = 0U;
    size_t index;

    p6c_hex(operation_id, P6C_OPERATION_ID_BYTES, operation_hex);
    if (p6c_manifest_append(
            manifest, P6C_MAX_MANIFEST_BYTES, &used,
            "{\"format\":\"p6c-bundle-v1\","
            "\"operation_id\":\"%s\",\"cleanup_state\":%d,"
            "\"live_execution\":false,\"live_trading\":false,"
            "\"files\":[",
            operation_hex, (int)cleanup_state) != P6C_RESULT_OK) {
        return P6C_RESULT_LIMIT;
    }
    for (index = 0U; index < item_count; ++index) {
        char digest_hex[(P6C_SHA256_BYTES * 2U) + 1U];

        p6c_hex(item_digests[index], P6C_SHA256_BYTES, digest_hex);
        if (p6c_manifest_append(
                manifest, P6C_MAX_MANIFEST_BYTES, &used,
                "%s{\"name\":\"%s\",\"size\":%zu,"
                "\"sha256\":\"%s\",\"candidate\":\"%s\"}",
                (index == 0U) ? "" : ",", items[index].name,
                items[index].content_length, digest_hex,
                items[index].candidate_identity) != P6C_RESULT_OK) {
            return P6C_RESULT_LIMIT;
        }
    }
    if (p6c_manifest_append(
            manifest, P6C_MAX_MANIFEST_BYTES, &used, "]}\n") !=
        P6C_RESULT_OK) {
        return P6C_RESULT_LIMIT;
    }
    *manifest_size = used;
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_verify_file(
    const struct p6c_owned_fd *directory, const char *name,
    size_t expected_size,
    const uint8_t expected_digest[static P6C_SHA256_BYTES])
{
    struct p6c_owned_fd file;
    struct stat status;
    uint8_t digest[P6C_SHA256_BYTES];
    enum p6c_result result;

    result = p6c_openat2_owned(
        directory, name, O_RDONLY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_REGULAR, &file);
    if (result != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((fstat(file.descriptor, &status) != 0) ||
        !S_ISREG(status.st_mode) || (status.st_nlink != 1) ||
        (status.st_uid != getuid()) ||
        ((status.st_mode & (mode_t)0777) != (mode_t)0600) ||
        (status.st_size < 0) ||
        ((uintmax_t)status.st_size != (uintmax_t)expected_size) ||
        (p6c_sha256_fd(&file, digest) != P6C_RESULT_OK) ||
        (memcmp(digest, expected_digest, P6C_SHA256_BYTES) != 0)) {
        file.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        (void)p6c_owned_fd_close(&file);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return p6c_owned_fd_close(&file);
}

static enum p6c_result p6c_recover_file(
    const struct p6c_owned_fd *directory, const char *name,
    size_t maximum_size, uint8_t **content, size_t *content_size,
    uint8_t digest[static P6C_SHA256_BYTES])
{
    struct p6c_owned_fd file;
    struct stat before;
    struct stat after;
    uint8_t *buffer;
    size_t size;
    size_t offset = 0U;
    enum p6c_result result;

    if ((content == NULL) || (content_size == NULL) || (digest == NULL)) {
        return P6C_RESULT_INVALID;
    }
    *content = NULL;
    *content_size = 0U;
    result = p6c_openat2_owned(
        directory, name, O_RDONLY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_REGULAR, &file);
    if (result != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if ((fstat(file.descriptor, &before) != 0) ||
        !S_ISREG(before.st_mode) || (before.st_nlink != 1) ||
        (before.st_uid != getuid()) ||
        ((before.st_mode & (mode_t)0777) != (mode_t)0600) ||
        (before.st_size < 0) ||
        ((uintmax_t)before.st_size > (uintmax_t)maximum_size) ||
        ((uintmax_t)before.st_size > (uintmax_t)SIZE_MAX)) {
        file.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        (void)p6c_owned_fd_close(&file);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    size = (size_t)before.st_size;
    buffer = malloc((size == 0U) ? 1U : size);
    if (buffer == NULL) {
        (void)p6c_owned_fd_close(&file);
        return P6C_RESULT_SYSTEM;
    }
    while (offset < size) {
        ssize_t amount = pread(
            file.descriptor, &buffer[offset], size - offset,
            (off_t)offset);

        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        if (amount == 0) {
            break;
        }
        offset += (size_t)amount;
    }
    if ((offset != size) ||
        (fstat(file.descriptor, &after) != 0) ||
        (before.st_dev != after.st_dev) ||
        (before.st_ino != after.st_ino) ||
        (before.st_size != after.st_size) ||
        (before.st_mtim.tv_sec != after.st_mtim.tv_sec) ||
        (before.st_mtim.tv_nsec != after.st_mtim.tv_nsec) ||
        (before.st_ctim.tv_sec != after.st_ctim.tv_sec) ||
        (before.st_ctim.tv_nsec != after.st_ctim.tv_nsec) ||
        (p6c_sha256_fd(&file, digest) != P6C_RESULT_OK) ||
        (p6c_owned_fd_close(&file) != P6C_RESULT_OK)) {
        if (p6c_owned_fd_is_live(&file)) {
            file.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            (void)p6c_owned_fd_close(&file);
        }
        free(buffer);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    *content = buffer;
    *content_size = size;
    return P6C_RESULT_OK;
}

static enum p6c_result p6c_publication_exact_files(
    const struct p6c_owned_fd *directory)
{
    static const char *const EXPECTED[] = {
        "authority.json", "manifest.json", "stderr.bin", "stdout.bin"
    };
    bool seen[sizeof(EXPECTED) / sizeof(EXPECTED[0])] = {
        false, false, false, false
    };
    DIR *stream;
    struct dirent *entry;
    int duplicate;
    enum p6c_result result = P6C_RESULT_OK;

    duplicate = openat(
        directory->descriptor, ".",
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (duplicate < 0) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    stream = fdopendir(duplicate);
    if (stream == NULL) {
        (void)close(duplicate);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    errno = 0;
    while ((entry = readdir(stream)) != NULL) {
        size_t index;
        bool recognized = false;

        if ((strcmp(entry->d_name, ".") == 0) ||
            (strcmp(entry->d_name, "..") == 0)) {
            continue;
        }
        for (index = 0U;
             index < sizeof(EXPECTED) / sizeof(EXPECTED[0]);
             ++index) {
            if (strcmp(entry->d_name, EXPECTED[index]) == 0) {
                if (seen[index]) {
                    result = P6C_RESULT_RECOVERY_REQUIRED;
                }
                seen[index] = true;
                recognized = true;
                break;
            }
        }
        if (!recognized) {
            result = P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (result != P6C_RESULT_OK) {
            break;
        }
    }
    if (errno != 0) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (closedir(stream) != 0) {
        result = P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (result == P6C_RESULT_OK) {
        size_t index;

        for (index = 0U;
             index < sizeof(EXPECTED) / sizeof(EXPECTED[0]);
             ++index) {
            if (!seen[index]) {
                result = P6C_RESULT_RECOVERY_REQUIRED;
            }
        }
    }
    return result;
}

static enum p6c_result p6c_publication_validate(
    const struct p6c_owned_fd *evidence_root,
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    enum p6c_operation_state cleanup_state,
    const struct p6c_publication_item *items,
    size_t item_count,
    struct p6c_journal *journal,
    struct stat *root_status)
{
    size_t index;

    if ((evidence_root == NULL) || (operation_id == NULL) ||
        (items == NULL) || (journal == NULL) || (root_status == NULL) ||
        !p6c_owned_fd_is_live(evidence_root) ||
        (evidence_root->type != P6C_DESCRIPTOR_DIRECTORY) ||
        (item_count == 0U) ||
        (item_count > P6C_MAX_PUBLICATION_FILES) ||
        (cleanup_state != P6C_OPERATION_RESULT_RETAINED) ||
        (journal->durable_state != P6C_OPERATION_RESULT_RETAINED) ||
        journal->recovery_required || journal->bundle_committed ||
        (fstat(evidence_root->descriptor, root_status) != 0) ||
        !S_ISDIR(root_status->st_mode) ||
        (root_status->st_uid != getuid()) ||
        ((root_status->st_mode & (S_IWGRP | S_IWOTH)) != 0)) {
        return P6C_RESULT_INVALID;
    }
    for (index = 0U; index < item_count; ++index) {
        if (!p6c_public_name_valid(
                items[index].name, P6C_MAX_PUBLICATION_NAME) ||
            (strcmp(items[index].name, "manifest.json") == 0) ||
            !p6c_public_name_valid(
                items[index].candidate_identity,
                P6C_MAX_PUBLICATION_NAME) ||
            ((items[index].content == NULL) &&
             (items[index].content_length != 0U)) ||
            (items[index].content_length >
             (size_t)P6C_MAX_PAYLOAD_BYTES) ||
            ((index != 0U) &&
             (strcmp(items[index - 1U].name, items[index].name) >= 0))) {
            return P6C_RESULT_INVALID;
        }
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_publish_bundle(
    const struct p6c_owned_fd *evidence_root,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    enum p6c_operation_state cleanup_state,
    const struct p6c_publication_item *items,
    size_t item_count,
    struct p6c_journal *journal,
    struct p6c_publication_result *result)
{
    struct stat root_status;
    struct stat committed_status;
    uint8_t random_bytes[16];
    char random_hex[33];
    uint8_t item_digests[P6C_MAX_PUBLICATION_FILES][P6C_SHA256_BYTES];
    char manifest[P6C_MAX_MANIFEST_BYTES];
    size_t manifest_size;
    enum p6c_result operation_result;
    size_t index;

    if (result == NULL) {
        return P6C_RESULT_INVALID;
    }
    memset(result, 0, sizeof(*result));
    p6c_owned_fd_reset(&result->staging_directory);
    p6c_owned_fd_reset(&result->committed_directory);
    operation_result = p6c_publication_validate(
        evidence_root, operation_id, cleanup_state, items, item_count,
        journal, &root_status);
    if (operation_result != P6C_RESULT_OK) {
        return operation_result;
    }
    if (getrandom(random_bytes, sizeof(random_bytes), 0U) !=
        (ssize_t)sizeof(random_bytes)) {
        return P6C_RESULT_SYSTEM;
    }
    p6c_hex(random_bytes, sizeof(random_bytes), random_hex);
    if (snprintf(result->staging_name, sizeof(result->staging_name),
                 ".p6c-stage-%s", random_hex) != 43) {
        return P6C_RESULT_LIMIT;
    }
    p6c_hex(operation_id, P6C_OPERATION_ID_BYTES,
            result->generation_name);
    if (mkdirat(evidence_root->descriptor, result->staging_name,
                (mode_t)0700) != 0) {
        return (errno == EEXIST) ? P6C_RESULT_CONFLICT :
                                  P6C_RESULT_SYSTEM;
    }
    operation_result = p6c_openat2_owned(
        evidence_root, result->staging_name,
        O_RDONLY | O_DIRECTORY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_DIRECTORY, &result->staging_directory);
    if (operation_result != P6C_RESULT_OK) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    if (fsync(evidence_root->descriptor) != 0) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    for (index = 0U; index < item_count; ++index) {
        operation_result = p6c_create_file(
            &result->staging_directory, items[index].name,
            items[index].content, items[index].content_length,
            P6C_FAIL_PUBLICATION_WRITE, item_digests[index]);
        if (operation_result != P6C_RESULT_OK) {
            return p6c_publication_recovery(
                result, P6C_RESULT_RECOVERY_REQUIRED);
        }
    }
    operation_result = p6c_build_manifest(
        operation_id, cleanup_state, items, item_count, item_digests,
        manifest, &manifest_size);
    if ((operation_result != P6C_RESULT_OK) ||
        p6c_failpoint_active(P6C_FAIL_PUBLICATION_MANIFEST)) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    operation_result = p6c_create_file(
        &result->staging_directory, "manifest.json",
        (const uint8_t *)manifest, manifest_size, P6C_FAIL_NONE,
        result->manifest_digest);
    if (operation_result != P6C_RESULT_OK) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    if (fsync(result->staging_directory.descriptor) != 0) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    if (p6c_failpoint_active(P6C_FAIL_PUBLICATION_RENAME)) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    if (syscall(SYS_renameat2, evidence_root->descriptor,
                result->staging_name, evidence_root->descriptor,
                result->generation_name, RENAME_NOREPLACE) != 0) {
        return p6c_publication_recovery(
            result, (errno == EEXIST) ? P6C_RESULT_CONFLICT :
                                        P6C_RESULT_RECOVERY_REQUIRED);
    }
    result->renamed = true;
    if (p6c_failpoint_active(P6C_FAIL_PUBLICATION_ROOT_FSYNC) ||
        (fsync(evidence_root->descriptor) != 0)) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    operation_result = p6c_openat2_owned(
        evidence_root, result->generation_name,
        O_RDONLY | O_DIRECTORY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_DIRECTORY, &result->committed_directory);
    if (operation_result != P6C_RESULT_OK) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    if (p6c_failpoint_active(P6C_FAIL_PUBLICATION_VERIFY) ||
        (fstat(result->committed_directory.descriptor,
               &committed_status) != 0) ||
        !S_ISDIR(committed_status.st_mode) ||
        (committed_status.st_uid != root_status.st_uid) ||
        ((committed_status.st_mode & (mode_t)0777) != (mode_t)0700) ||
        (committed_status.st_dev != result->staging_directory.device) ||
        (committed_status.st_ino != result->staging_directory.inode)) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    for (index = 0U; index < item_count; ++index) {
        operation_result = p6c_verify_file(
            &result->committed_directory, items[index].name,
            items[index].content_length, item_digests[index]);
        if (operation_result != P6C_RESULT_OK) {
            return p6c_publication_recovery(
                result, P6C_RESULT_RECOVERY_REQUIRED);
        }
    }
    operation_result = p6c_verify_file(
        &result->committed_directory, "manifest.json", manifest_size,
        result->manifest_digest);
    if (operation_result != P6C_RESULT_OK) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    result->verified = true;
    if (p6c_journal_append_bundle_committed(
            journal, result->manifest_digest) != P6C_RESULT_OK) {
        return p6c_publication_recovery(
            result, P6C_RESULT_RECOVERY_REQUIRED);
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_publication_recover(
    const struct p6c_owned_fd *evidence_root,
    const uint8_t operation_id[static P6C_OPERATION_ID_BYTES],
    const uint8_t publication_identity[static P6C_SHA256_BYTES],
    const uint8_t manifest_digest[static P6C_SHA256_BYTES],
    struct p6c_publication_result *result)
{
    struct stat root_status;
    struct stat generation_status;
    struct p6c_publication_item items[3];
    uint8_t item_digests[
        P6C_MAX_PUBLICATION_FILES][P6C_SHA256_BYTES];
    uint8_t actual_manifest_digest[P6C_SHA256_BYTES];
    uint8_t *authority = NULL;
    uint8_t *stderr_content = NULL;
    uint8_t *stdout_content = NULL;
    uint8_t *manifest_content = NULL;
    size_t authority_size = 0U;
    size_t stderr_size = 0U;
    size_t stdout_size = 0U;
    size_t manifest_size = 0U;
    char expected_manifest[P6C_MAX_MANIFEST_BYTES];
    size_t expected_manifest_size = 0U;
    char publication_hex[(P6C_SHA256_BYTES * 2U) + 1U];
    enum p6c_result operation_result = P6C_RESULT_RECOVERY_REQUIRED;

    if ((evidence_root == NULL) || (operation_id == NULL) ||
        (publication_identity == NULL) || (manifest_digest == NULL) ||
        (result == NULL) || !p6c_owned_fd_is_live(evidence_root) ||
        (evidence_root->type != P6C_DESCRIPTOR_DIRECTORY) ||
        (fstat(evidence_root->descriptor, &root_status) != 0) ||
        !S_ISDIR(root_status.st_mode) ||
        (root_status.st_uid != getuid()) ||
        ((root_status.st_mode & (S_IWGRP | S_IWOTH)) != 0)) {
        return P6C_RESULT_INVALID;
    }
    memset(result, 0, sizeof(*result));
    p6c_owned_fd_reset(&result->staging_directory);
    p6c_owned_fd_reset(&result->committed_directory);
    p6c_hex(
        operation_id, P6C_OPERATION_ID_BYTES,
        result->generation_name);
    if ((p6c_openat2_owned(
             evidence_root, result->generation_name,
             O_RDONLY | O_DIRECTORY | O_NOFOLLOW, (mode_t)0,
             P6C_DESCRIPTOR_DIRECTORY,
             &result->committed_directory) != P6C_RESULT_OK) ||
        (fstat(result->committed_directory.descriptor,
               &generation_status) != 0) ||
        !S_ISDIR(generation_status.st_mode) ||
        (generation_status.st_uid != root_status.st_uid) ||
        ((generation_status.st_mode & (mode_t)0777) != (mode_t)0700) ||
        (p6c_publication_exact_files(
             &result->committed_directory) != P6C_RESULT_OK)) {
        goto cleanup;
    }
    if ((p6c_recover_file(
             &result->committed_directory, "authority.json",
             (size_t)P6C_MAX_PAYLOAD_BYTES, &authority,
             &authority_size, item_digests[0]) != P6C_RESULT_OK) ||
        (p6c_recover_file(
             &result->committed_directory, "stderr.bin",
             (size_t)P6C_MAX_PAYLOAD_BYTES, &stderr_content,
             &stderr_size, item_digests[1]) != P6C_RESULT_OK) ||
        (p6c_recover_file(
             &result->committed_directory, "stdout.bin",
             (size_t)P6C_MAX_PAYLOAD_BYTES, &stdout_content,
             &stdout_size, item_digests[2]) != P6C_RESULT_OK) ||
        (p6c_recover_file(
             &result->committed_directory, "manifest.json",
             P6C_MAX_MANIFEST_BYTES, &manifest_content,
             &manifest_size, actual_manifest_digest) != P6C_RESULT_OK) ||
        (memcmp(
             actual_manifest_digest, manifest_digest,
             P6C_SHA256_BYTES) != 0)) {
        goto cleanup;
    }
    p6c_hex(
        publication_identity, P6C_SHA256_BYTES,
        publication_hex);
    items[0].name = "authority.json";
    items[0].content = authority;
    items[0].content_length = authority_size;
    items[0].candidate_identity = publication_hex;
    items[1].name = "stderr.bin";
    items[1].content = stderr_content;
    items[1].content_length = stderr_size;
    items[1].candidate_identity = publication_hex;
    items[2].name = "stdout.bin";
    items[2].content = stdout_content;
    items[2].content_length = stdout_size;
    items[2].candidate_identity = publication_hex;
    if ((p6c_build_manifest(
             operation_id, P6C_OPERATION_RESULT_RETAINED,
             items, 3U, item_digests, expected_manifest,
             &expected_manifest_size) != P6C_RESULT_OK) ||
        (manifest_size != expected_manifest_size) ||
        (memcmp(
             manifest_content, expected_manifest,
             manifest_size) != 0)) {
        goto cleanup;
    }
    memcpy(
        result->manifest_digest, actual_manifest_digest,
        P6C_SHA256_BYTES);
    result->renamed = true;
    result->verified = true;
    operation_result = P6C_RESULT_OK;

cleanup:
    free(manifest_content);
    free(stdout_content);
    free(stderr_content);
    free(authority);
    if ((operation_result != P6C_RESULT_OK) &&
        p6c_owned_fd_is_live(&result->committed_directory)) {
        (void)p6c_owned_fd_close(&result->committed_directory);
    }
    if (operation_result != P6C_RESULT_OK) {
        result->recovery_required = true;
    }
    return operation_result;
}

enum p6c_result p6c_publication_close(
    struct p6c_publication_result *result)
{
    enum p6c_result first = P6C_RESULT_OK;
    enum p6c_result second = P6C_RESULT_OK;

    if (result == NULL) {
        return P6C_RESULT_INVALID;
    }
    if (p6c_owned_fd_is_live(&result->committed_directory)) {
        first = p6c_owned_fd_close(&result->committed_directory);
    }
    if (p6c_owned_fd_is_live(&result->staging_directory)) {
        second = p6c_owned_fd_close(&result->staging_directory);
    }
    return ((first == P6C_RESULT_OK) && (second == P6C_RESULT_OK)) ?
               P6C_RESULT_OK :
               P6C_RESULT_RECOVERY_REQUIRED;
}
