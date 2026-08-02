#include "p6c_types.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <unistd.h>


static int p6c_cgroup_name_safe(const char *name)
{
    size_t length;
    size_t index;

    if (name == NULL) {
        return 0;
    }
    length = strnlen(name, 81U);
    if ((length == 0U) || (length > 80U) ||
        (name[0] == '.') || (name[0] == '-')) {
        return 0;
    }
    for (index = 0U; index < length; ++index) {
        unsigned char character = (unsigned char)name[index];

        if (!(((character >= (unsigned char)'a') &&
               (character <= (unsigned char)'z')) ||
              ((character >= (unsigned char)'0') &&
               (character <= (unsigned char)'9')) ||
              (character == (unsigned char)'-'))) {
            return 0;
        }
    }
    return 1;
}

enum p6c_result p6c_cgroup_quarantine_name(
    const char *name,
    char output[static P6C_CGROUP_QUARANTINE_NAME_BYTES])
{
    int amount;

    if (!p6c_cgroup_name_safe(name) || (output == NULL)) {
        return P6C_RESULT_INVALID;
    }
    amount = snprintf(
        output, P6C_CGROUP_QUARANTINE_NAME_BYTES, "p6q-%s", name);
    return ((amount > 0) &&
            ((size_t)amount <
             P6C_CGROUP_QUARANTINE_NAME_BYTES)) ?
               P6C_RESULT_OK :
               P6C_RESULT_LIMIT;
}

#ifdef P6C_TESTING
static int p6c_test_remove_root = P6C_INVALID_DESCRIPTOR;
static char p6c_test_remove_replacement[P6C_CGROUP_NAME_BYTES];
static char p6c_test_remove_displaced[
    P6C_CGROUP_QUARANTINE_NAME_BYTES];

void p6c_test_cgroup_remove_substitution_set(
    int root_descriptor, const char *replacement_name,
    const char *displaced_name)
{
    p6c_test_remove_root = P6C_INVALID_DESCRIPTOR;
    memset(
        p6c_test_remove_replacement, 0,
        sizeof(p6c_test_remove_replacement));
    memset(
        p6c_test_remove_displaced, 0,
        sizeof(p6c_test_remove_displaced));
    if ((root_descriptor < 0) || (replacement_name == NULL) ||
        (displaced_name == NULL) ||
        !p6c_cgroup_name_safe(replacement_name) ||
        (strnlen(
             displaced_name,
             sizeof(p6c_test_remove_displaced)) >=
         sizeof(p6c_test_remove_displaced))) {
        return;
    }
    p6c_test_remove_root = root_descriptor;
    (void)strcpy(
        p6c_test_remove_replacement, replacement_name);
    (void)strcpy(p6c_test_remove_displaced, displaced_name);
}

static void p6c_test_cgroup_remove_substitute(const char *name)
{
    int root = p6c_test_remove_root;

    if (root < 0) {
        return;
    }
    p6c_test_remove_root = P6C_INVALID_DESCRIPTOR;
    (void)renameat(
        root, name, root, p6c_test_remove_displaced);
    (void)renameat(
        root, p6c_test_remove_replacement, root, name);
}
#endif

enum p6c_result p6c_cgroup_create(
    const struct p6c_owned_fd *root, const char *name,
    uid_t approved_owner, struct p6c_owned_fd *cgroup)
{
    struct stat created_status;
    struct stat status;
    struct stat current_status;
    enum p6c_result result;

    if ((root == NULL) || (cgroup == NULL) ||
        !p6c_owned_fd_is_live(root) ||
        (root->type != P6C_DESCRIPTOR_CGROUP) ||
        !p6c_cgroup_name_safe(name)) {
        return P6C_RESULT_INVALID;
    }
    p6c_owned_fd_reset(cgroup);
    if (mkdirat(root->descriptor, name, (mode_t)0700) != 0) {
        return (errno == EEXIST) ? P6C_RESULT_CONFLICT :
                                  P6C_RESULT_SYSTEM;
    }
    if ((fstatat(
             root->descriptor, name, &created_status,
             AT_SYMLINK_NOFOLLOW) != 0) ||
        !S_ISDIR(created_status.st_mode) ||
        (created_status.st_uid != approved_owner) ||
        ((created_status.st_mode & (mode_t)0777) != (mode_t)0700)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_openat2_owned(
        root, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_CGROUP, cgroup);
    if (result != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    cgroup->type = P6C_DESCRIPTOR_CGROUP;
    if ((fstat(cgroup->descriptor, &status) != 0) ||
        (fstatat(
             root->descriptor, name, &current_status,
             AT_SYMLINK_NOFOLLOW) != 0) ||
        !S_ISDIR(status.st_mode) || (status.st_uid != approved_owner) ||
        ((status.st_mode & (mode_t)0777) != (mode_t)0700) ||
        !S_ISDIR(current_status.st_mode) ||
        (created_status.st_dev != status.st_dev) ||
        (created_status.st_ino != status.st_ino) ||
        (current_status.st_dev != status.st_dev) ||
        (current_status.st_ino != status.st_ino) ||
        (status.st_dev != cgroup->device) ||
        (status.st_ino != cgroup->inode)) {
        cgroup->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

enum p6c_result p6c_cgroup_remove(
    const struct p6c_owned_fd *root, const char *name,
    struct p6c_owned_fd *cgroup)
{
    struct stat descriptor_status;
    struct stat name_status;
    struct stat descriptor_after;
    bool populated;
    enum p6c_result result = P6C_RESULT_RECOVERY_REQUIRED;
    int locked = 0;

    if ((root == NULL) || (cgroup == NULL) ||
        !p6c_owned_fd_is_live(root) ||
        (root->type != P6C_DESCRIPTOR_CGROUP) ||
        !p6c_owned_fd_is_live(cgroup) ||
        (cgroup->type != P6C_DESCRIPTOR_CGROUP) ||
        !p6c_cgroup_name_safe(name)) {
        return P6C_RESULT_INVALID;
    }
    if (flock(root->descriptor, LOCK_EX | LOCK_NB) != 0) {
        cgroup->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    locked = 1;
    if ((p6c_cgroup_is_populated(cgroup, &populated) != P6C_RESULT_OK) ||
        populated ||
        (fstat(cgroup->descriptor, &descriptor_status) != 0)) {
        cgroup->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        goto cleanup;
    }
    if (fstatat(
            root->descriptor, name, &name_status,
            AT_SYMLINK_NOFOLLOW) != 0) {
        cgroup->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        goto cleanup;
    }
    if (
        !S_ISDIR(name_status.st_mode) ||
        (descriptor_status.st_dev != name_status.st_dev) ||
        (descriptor_status.st_ino != name_status.st_ino) ||
        ((descriptor_status.st_mode & S_IFMT) !=
         (name_status.st_mode & S_IFMT))) {
        cgroup->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        goto cleanup;
    }
#ifdef P6C_TESTING
    p6c_test_cgroup_remove_substitute(name);
#endif
    if ((fstatat(
             root->descriptor, name, &name_status,
             AT_SYMLINK_NOFOLLOW) != 0) ||
        !S_ISDIR(name_status.st_mode) ||
        (descriptor_status.st_dev != name_status.st_dev) ||
        (descriptor_status.st_ino != name_status.st_ino) ||
        ((descriptor_status.st_mode & S_IFMT) !=
         (name_status.st_mode & S_IFMT))) {
        cgroup->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        goto cleanup;
    }
    if (unlinkat(
            root->descriptor, name, AT_REMOVEDIR) != 0) {
        cgroup->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        goto cleanup;
    }
    errno = 0;
    if ((fstatat(
             root->descriptor, name, &name_status,
             AT_SYMLINK_NOFOLLOW) == 0) ||
        (errno != ENOENT) ||
        (fstat(cgroup->descriptor, &descriptor_after) != 0) ||
        (descriptor_status.st_dev != descriptor_after.st_dev) ||
        (descriptor_status.st_ino != descriptor_after.st_ino) ||
        ((descriptor_status.st_mode & S_IFMT) !=
         (descriptor_after.st_mode & S_IFMT))) {
        cgroup->lifecycle = P6C_DESCRIPTOR_RECOVERY;
        goto cleanup;
    }
    if (p6c_owned_fd_close(cgroup) != P6C_RESULT_OK) {
        goto cleanup;
    }
    result = P6C_RESULT_OK;

cleanup:
    if (locked != 0) {
        (void)flock(root->descriptor, LOCK_UN);
    }
    return result;
}


static enum p6c_result p6c_cgroup_write_one(
    const struct p6c_owned_fd *cgroup, const char *control)
{
    struct p6c_owned_fd file;
    enum p6c_result result;
    ssize_t amount;

    if ((cgroup == NULL) || (control == NULL) ||
        !p6c_owned_fd_is_live(cgroup) ||
        (cgroup->type != P6C_DESCRIPTOR_CGROUP)) {
        return P6C_RESULT_INVALID;
    }
    result = p6c_openat2_owned(
        cgroup, control, O_WRONLY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_REGULAR, &file);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    do {
        amount = write(file.descriptor, "1", 1U);
    } while ((amount < 0) && (errno == EINTR));
    if (amount != 1) {
        file.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        (void)p6c_owned_fd_close(&file);
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    result = p6c_owned_fd_close(&file);
    return result;
}

enum p6c_result p6c_cgroup_freeze(
    const struct p6c_owned_fd *cgroup)
{
    return p6c_cgroup_write_one(cgroup, "cgroup.freeze");
}

enum p6c_result p6c_cgroup_kill(
    const struct p6c_owned_fd *cgroup)
{
    return p6c_cgroup_write_one(cgroup, "cgroup.kill");
}

enum p6c_result p6c_cgroup_is_populated(
    const struct p6c_owned_fd *cgroup, bool *populated)
{
    struct p6c_owned_fd events;
    uint8_t buffer[4096];
    size_t total = 0U;
    enum p6c_result result;
    unsigned int matches = 0U;
    bool value = false;

    if ((cgroup == NULL) || (populated == NULL) ||
        !p6c_owned_fd_is_live(cgroup) ||
        (cgroup->type != P6C_DESCRIPTOR_CGROUP)) {
        return P6C_RESULT_INVALID;
    }
    result = p6c_openat2_owned(
        cgroup, "cgroup.events", O_RDONLY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_REGULAR, &events);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    while (total < sizeof(buffer)) {
        ssize_t amount = read(events.descriptor, &buffer[total],
                              sizeof(buffer) - total);

        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            events.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            (void)p6c_owned_fd_close(&events);
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (amount == 0) {
            break;
        }
        total += (size_t)amount;
    }
    if (total == sizeof(buffer)) {
        events.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        (void)p6c_owned_fd_close(&events);
        return P6C_RESULT_LIMIT;
    }
    {
        size_t offset = 0U;

        while (offset < total) {
            size_t line_start = offset;
            size_t line_length;

            while ((offset < total) && (buffer[offset] != UINT8_C('\n'))) {
                ++offset;
            }
            line_length = offset - line_start;
            if ((line_length == 11U) &&
                (memcmp(&buffer[line_start], "populated 0", 11U) == 0)) {
                ++matches;
                value = false;
            } else if ((line_length == 11U) &&
                       (memcmp(&buffer[line_start],
                               "populated 1", 11U) == 0)) {
                ++matches;
                value = true;
            }
            if (offset < total) {
                ++offset;
            }
        }
    }
    result = p6c_owned_fd_close(&events);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    if (matches != 1U) {
        return P6C_RESULT_MALFORMED;
    }
    *populated = value;
    return P6C_RESULT_OK;
}

enum p6c_result p6c_cgroup_is_frozen(
    const struct p6c_owned_fd *cgroup, bool *frozen)
{
    struct p6c_owned_fd events;
    uint8_t buffer[4096];
    size_t total = 0U;
    enum p6c_result result;
    unsigned int matches = 0U;
    bool value = false;

    if ((cgroup == NULL) || (frozen == NULL) ||
        !p6c_owned_fd_is_live(cgroup) ||
        (cgroup->type != P6C_DESCRIPTOR_CGROUP)) {
        return P6C_RESULT_INVALID;
    }
    result = p6c_openat2_owned(
        cgroup, "cgroup.events", O_RDONLY | O_NOFOLLOW, (mode_t)0,
        P6C_DESCRIPTOR_REGULAR, &events);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    while (total < sizeof(buffer)) {
        ssize_t amount = read(
            events.descriptor, &buffer[total], sizeof(buffer) - total);

        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            events.lifecycle = P6C_DESCRIPTOR_RECOVERY;
            (void)p6c_owned_fd_close(&events);
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        if (amount == 0) {
            break;
        }
        total += (size_t)amount;
    }
    if (total == sizeof(buffer)) {
        events.lifecycle = P6C_DESCRIPTOR_RECOVERY;
        (void)p6c_owned_fd_close(&events);
        return P6C_RESULT_LIMIT;
    }
    {
        size_t offset = 0U;

        while (offset < total) {
            size_t line_start = offset;
            size_t line_length;

            while ((offset < total) && (buffer[offset] != UINT8_C('\n'))) {
                ++offset;
            }
            line_length = offset - line_start;
            if ((line_length == 8U) &&
                (memcmp(&buffer[line_start], "frozen 0", 8U) == 0)) {
                ++matches;
                value = false;
            } else if ((line_length == 8U) &&
                       (memcmp(&buffer[line_start], "frozen 1", 8U) == 0)) {
                ++matches;
                value = true;
            }
            if (offset < total) {
                ++offset;
            }
        }
    }
    result = p6c_owned_fd_close(&events);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    if (matches != 1U) {
        return P6C_RESULT_MALFORMED;
    }
    *frozen = value;
    return P6C_RESULT_OK;
}
