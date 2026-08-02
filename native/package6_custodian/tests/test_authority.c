#include "p6c_types.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/magic.h>
#include <linux/sched.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>


#define CHECK(condition)                                                        \
    do {                                                                        \
        if (!(condition)) {                                                     \
            (void)fprintf(stderr,                                               \
                          "test_authority: check failed at line %d\n",          \
                          __LINE__);                                             \
            return EXIT_FAILURE;                                                \
        }                                                                       \
    } while (0)

struct test_directory {
    char path[128];
    struct p6c_owned_fd owner;
};

static int test_directory_create(struct test_directory *directory)
{
    char template[] = "/tmp/p6c-authority-XXXXXX";
    char *created;
    int descriptor;

    memset(directory, 0, sizeof(*directory));
    p6c_owned_fd_reset(&directory->owner);
    created = mkdtemp(template);
    if (created == NULL) {
        return EXIT_FAILURE;
    }
    if (strlen(created) >= sizeof(directory->path)) {
        (void)rmdir(created);
        return EXIT_FAILURE;
    }
    (void)strcpy(directory->path, created);
    descriptor = open(created, O_RDONLY | O_DIRECTORY | O_CLOEXEC |
                                   O_NOFOLLOW);
    if (descriptor < 0) {
        (void)rmdir(created);
        return EXIT_FAILURE;
    }
    if (p6c_owned_fd_acquire(&directory->owner, descriptor,
                             P6C_DESCRIPTOR_DIRECTORY) != P6C_RESULT_OK) {
        (void)close(descriptor);
        (void)rmdir(created);
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static int test_directory_close(struct test_directory *directory,
                                const char *file_name)
{
    int result = EXIT_SUCCESS;

    if ((unlinkat(
             directory->owner.descriptor,
             ".p6c-replay-ledger", 0) != 0) &&
        (errno != ENOENT)) {
        result = EXIT_FAILURE;
    }
    if ((file_name != NULL) &&
        (unlinkat(directory->owner.descriptor, file_name, 0) != 0) &&
        (errno != ENOENT)) {
        result = EXIT_FAILURE;
    }
    if (p6c_owned_fd_is_live(&directory->owner) &&
        (p6c_owned_fd_close(&directory->owner) != P6C_RESULT_OK)) {
        result = EXIT_FAILURE;
    }
    if ((rmdir(directory->path) != 0) && (errno != ENOENT)) {
        result = EXIT_FAILURE;
    }
    return result;
}

static int test_remove_empty_service_cgroups(
    const struct test_directory *directory)
{
    DIR *stream;
    struct dirent *entry;
    int descriptor;
    int result = EXIT_SUCCESS;

    descriptor = openat(
        directory->owner.descriptor, ".",
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) {
        return EXIT_FAILURE;
    }
    stream = fdopendir(descriptor);
    if (stream == NULL) {
        (void)close(descriptor);
        return EXIT_FAILURE;
    }
    errno = 0;
    while ((entry = readdir(stream)) != NULL) {
        if ((strncmp(entry->d_name, "p6c-", 4U) == 0) &&
            (strlen(entry->d_name) == 36U) &&
            (unlinkat(
                 directory->owner.descriptor, entry->d_name,
                 AT_REMOVEDIR) != 0)) {
            result = EXIT_FAILURE;
        }
    }
    if ((errno != 0) || (closedir(stream) != 0)) {
        result = EXIT_FAILURE;
    }
    return result;
}

static int test_remove_service_transcripts(
    const struct test_directory *directory)
{
    DIR *stream;
    struct dirent *entry;
    int descriptor;
    int result = EXIT_SUCCESS;

    descriptor = openat(
        directory->owner.descriptor, ".",
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) {
        return EXIT_FAILURE;
    }
    stream = fdopendir(descriptor);
    if (stream == NULL) {
        (void)close(descriptor);
        return EXIT_FAILURE;
    }
    errno = 0;
    while ((entry = readdir(stream)) != NULL) {
        size_t length = strlen(entry->d_name);
        bool transcript =
            ((length > sizeof(".stdout") - 1U) &&
             (strcmp(
                  &entry->d_name[
                      length - (sizeof(".stdout") - 1U)],
                  ".stdout") == 0)) ||
            ((length > sizeof(".stderr") - 1U) &&
             (strcmp(
                  &entry->d_name[
                      length - (sizeof(".stderr") - 1U)],
                  ".stderr") == 0));

        if (transcript &&
            (unlinkat(
                 directory->owner.descriptor,
                 entry->d_name, 0) != 0)) {
            result = EXIT_FAILURE;
        }
    }
    if ((errno != 0) || (closedir(stream) != 0)) {
        result = EXIT_FAILURE;
    }
    return result;
}

static void test_digest_hex(const uint8_t digest[P6C_SHA256_BYTES],
                            char output[(P6C_SHA256_BYTES * 2U) + 1U])
{
    static const char HEX[] = "0123456789abcdef";
    size_t index;

    for (index = 0U; index < P6C_SHA256_BYTES; ++index) {
        output[index * 2U] = HEX[digest[index] >> 4];
        output[(index * 2U) + 1U] = HEX[digest[index] & UINT8_C(0x0f)];
    }
    output[P6C_SHA256_BYTES * 2U] = '\0';
}

static void test_operation_hex(
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    char output[(P6C_OPERATION_ID_BYTES * 2U) + 1U])
{
    static const char HEX[] = "0123456789abcdef";
    size_t index;

    for (index = 0U; index < P6C_OPERATION_ID_BYTES; ++index) {
        output[index * 2U] = HEX[operation_id[index] >> 4];
        output[(index * 2U) + 1U] =
            HEX[operation_id[index] & UINT8_C(0x0f)];
    }
    output[P6C_OPERATION_ID_BYTES * 2U] = '\0';
}

static int test_sha_vector(const uint8_t *input, size_t size,
                           const char *expected, size_t fragment)
{
    struct p6c_sha256 context;
    uint8_t digest[P6C_SHA256_BYTES];
    char actual[(P6C_SHA256_BYTES * 2U) + 1U];
    size_t offset = 0U;

    p6c_sha256_init(&context);
    while (offset < size) {
        size_t amount = fragment;

        if (amount > size - offset) {
            amount = size - offset;
        }
        if (p6c_sha256_update(&context, &input[offset], amount) !=
            P6C_RESULT_OK) {
            return EXIT_FAILURE;
        }
        offset += amount;
    }
    if (p6c_sha256_final(&context, digest) != P6C_RESULT_OK) {
        return EXIT_FAILURE;
    }
    test_digest_hex(digest, actual);
    return (strcmp(actual, expected) == 0) ? EXIT_SUCCESS : EXIT_FAILURE;
}

static int case_sha256_vectors(void)
{
    static const uint8_t ABC[] = {'a', 'b', 'c'};
    static const uint8_t LONG_VECTOR[] =
        "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    static const struct {
        size_t size;
        const char *digest;
    } BOUNDARIES[] = {
        {55U, "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"},
        {56U, "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"},
        {63U, "7d3e74a05d7db15bce4ad9ec0658ea98e3f06eeecf16b4c6fff2da457ddc2f34"},
        {64U, "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"},
        {65U, "635361c48bb9eab14198e76ea8ab7f1a41685d6ad62aa9146d301d4f17eb0ae0"},
    };
    uint8_t million[1000];
    uint8_t boundary[65];
    struct p6c_sha256 context;
    uint8_t digest[P6C_SHA256_BYTES];
    char actual[(P6C_SHA256_BYTES * 2U) + 1U];
    size_t index;

    CHECK(test_sha_vector(NULL, 0U,
                          "e3b0c44298fc1c149afbf4c8996fb924"
                          "27ae41e4649b934ca495991b7852b855",
                          1U) == EXIT_SUCCESS);
    CHECK(test_sha_vector(ABC, sizeof(ABC),
                          "ba7816bf8f01cfea414140de5dae2223"
                          "b00361a396177a9cb410ff61f20015ad",
                          1U) == EXIT_SUCCESS);
    CHECK(test_sha_vector(LONG_VECTOR, sizeof(LONG_VECTOR) - 1U,
                          "248d6a61d20638b8e5c026930c3e6039"
                          "a33ce45964ff2167f6ecedd419db06c1",
                          7U) == EXIT_SUCCESS);
    memset(million, 'a', sizeof(million));
    p6c_sha256_init(&context);
    for (index = 0U; index < 1000U; ++index) {
        CHECK(p6c_sha256_update(&context, million, sizeof(million)) ==
              P6C_RESULT_OK);
    }
    CHECK(p6c_sha256_final(&context, digest) == P6C_RESULT_OK);
    test_digest_hex(digest, actual);
    CHECK(strcmp(actual,
                 "cdc76e5c9914fb9281a1c7e284d73e67"
                 "f1809a48a497200e046d39ccc7112cd0") == 0);
    memset(boundary, 'a', sizeof(boundary));
    for (index = 0U;
         index < sizeof(BOUNDARIES) / sizeof(BOUNDARIES[0]);
         ++index) {
        CHECK(test_sha_vector(boundary, BOUNDARIES[index].size,
                              BOUNDARIES[index].digest, 3U) == EXIT_SUCCESS);
    }
    return EXIT_SUCCESS;
}

static int case_owned_close_once(void)
{
    struct p6c_owned_fd owner;
    int descriptor = open("/dev/null", O_RDONLY | O_CLOEXEC);

    CHECK(descriptor >= 0);
    p6c_owned_fd_reset(&owner);
    CHECK(p6c_owned_fd_acquire(&owner, descriptor,
                               P6C_DESCRIPTOR_REGULAR) == P6C_RESULT_OK);
    CHECK(p6c_owned_fd_close(&owner) == P6C_RESULT_OK);
    CHECK(owner.lifecycle == P6C_DESCRIPTOR_CLOSED);
    CHECK(owner.closure_proven);
    CHECK(p6c_owned_fd_close(&owner) == P6C_RESULT_INVALID);
    CHECK(fcntl(descriptor, F_GETFD) == -1);
    CHECK(errno == EBADF);
    return EXIT_SUCCESS;
}

static int case_descriptor_reuse(void)
{
    struct p6c_owned_fd owner;
    int original = open("/dev/null", O_RDONLY | O_CLOEXEC);
    int replacement;

    CHECK(original >= 0);
    p6c_owned_fd_reset(&owner);
    CHECK(p6c_owned_fd_acquire(&owner, original,
                               P6C_DESCRIPTOR_REGULAR) == P6C_RESULT_OK);
    CHECK(close(original) == 0);
    replacement = open("/dev/zero", O_RDONLY | O_CLOEXEC);
    CHECK(replacement == original);
    CHECK(p6c_owned_fd_close(&owner) == P6C_RESULT_STALE);
    CHECK(fcntl(replacement, F_GETFD) >= 0);
    CHECK(close(replacement) == 0);
    return EXIT_SUCCESS;
}

static size_t test_open_descriptor_count(void)
{
    DIR *directory = opendir("/proc/self/fd");
    struct dirent *entry;
    size_t count = 0U;

    if (directory == NULL) {
        return SIZE_MAX;
    }
    while ((entry = readdir(directory)) != NULL) {
        if ((strcmp(entry->d_name, ".") != 0) &&
            (strcmp(entry->d_name, "..") != 0)) {
            ++count;
        }
    }
    if (closedir(directory) != 0) {
        return SIZE_MAX;
    }
    return count;
}

static int case_partial_pair(void)
{
    struct p6c_owned_pair pair;
    size_t before;
    size_t after;

    p6c_owned_fd_reset(&pair.first);
    p6c_owned_fd_reset(&pair.second);
    before = test_open_descriptor_count();
    CHECK(before != SIZE_MAX);
    p6c_test_failpoint_set(P6C_FAIL_PAIR_SECOND_ACQUIRE);
    CHECK(p6c_owned_pipe_create(&pair) == P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(!p6c_owned_fd_is_live(&pair.first));
    CHECK(!p6c_owned_fd_is_live(&pair.second));
    CHECK(pair.first.descriptor == P6C_INVALID_DESCRIPTOR);
    CHECK(pair.second.descriptor == P6C_INVALID_DESCRIPTOR);
    after = test_open_descriptor_count();
    CHECK(after == before);
    CHECK(p6c_owned_pair_close(&pair) == P6C_RESULT_OK);
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    return EXIT_SUCCESS;
}

static int case_pipe_acquisition_failure_matrix(void)
{
    static const enum p6c_failpoint FAILURES[] = {
        P6C_FAIL_PAIR_FIRST_ACQUIRE,
        P6C_FAIL_PAIR_SECOND_ACQUIRE,
        P6C_FAIL_PAIR_FIRST_FSTAT,
        P6C_FAIL_PAIR_SECOND_FSTAT,
        P6C_FAIL_PAIR_GETFL,
        P6C_FAIL_PAIR_SETFL
    };
    size_t index;

    for (index = 0U;
         index < sizeof(FAILURES) / sizeof(FAILURES[0]); ++index) {
        struct p6c_owned_pair pair;
        size_t before = test_open_descriptor_count();

        CHECK(before != SIZE_MAX);
        memset(&pair, 0xa5, sizeof(pair));
        p6c_test_failpoint_set(FAILURES[index]);
        CHECK(p6c_owned_pipe_create(&pair) ==
              P6C_RESULT_RECOVERY_REQUIRED);
        CHECK(pair.first.descriptor == P6C_INVALID_DESCRIPTOR);
        CHECK(pair.second.descriptor == P6C_INVALID_DESCRIPTOR);
        CHECK(pair.first.lifecycle == P6C_DESCRIPTOR_EMPTY);
        CHECK(pair.second.lifecycle == P6C_DESCRIPTOR_EMPTY);
        CHECK(pair.first.device == (dev_t)0);
        CHECK(pair.first.inode == (ino_t)0);
        CHECK(pair.second.device == (dev_t)0);
        CHECK(pair.second.inode == (ino_t)0);
        CHECK(test_open_descriptor_count() == before);
    }
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    return EXIT_SUCCESS;
}

static int case_pipe_end_blocking_flags(void)
{
    struct p6c_owned_pair pipe_owner;
    int read_flags;
    int write_flags;

    CHECK(p6c_owned_pipe_create(&pipe_owner) == P6C_RESULT_OK);
    read_flags = fcntl(pipe_owner.first.descriptor, F_GETFL);
    write_flags = fcntl(pipe_owner.second.descriptor, F_GETFL);
    CHECK(read_flags >= 0);
    CHECK(write_flags >= 0);
    CHECK((read_flags & O_NONBLOCK) != 0);
    CHECK((write_flags & O_NONBLOCK) == 0);
    CHECK((fcntl(pipe_owner.first.descriptor, F_GETFD) & FD_CLOEXEC) != 0);
    CHECK((fcntl(pipe_owner.second.descriptor, F_GETFD) & FD_CLOEXEC) != 0);
    CHECK(p6c_owned_pair_close(&pipe_owner) == P6C_RESULT_OK);
    return EXIT_SUCCESS;
}

static void test_fill_identity(uint8_t identity[P6C_OPERATION_ID_BYTES],
                               uint8_t value)
{
    memset(identity, (int)value, P6C_OPERATION_ID_BYTES);
}

static int case_journal_chain(void)
{
    struct test_directory directory;
    struct p6c_journal journal;
    struct p6c_journal recovered;
    enum p6c_journal_recovery recovery;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x31));
    CHECK(p6c_journal_create(&directory.owner, "operation.journal",
                             operation_id, getuid(), &journal) ==
          P6C_RESULT_OK);
    CHECK(p6c_journal_append(&journal, P6C_OPERATION_RESERVED,
                             NULL, 0U) == P6C_RESULT_OK);
    CHECK(p6c_journal_append(&journal, P6C_OPERATION_EXECUTABLE_PINNED,
                             "elf", 3U) == P6C_RESULT_OK);
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_recover(&directory.owner, "operation.journal",
                              operation_id, getuid(), &recovered,
                              &recovery) == P6C_RESULT_OK);
    CHECK(recovery == P6C_JOURNAL_COMPLETE);
    CHECK(recovered.durable_state == P6C_OPERATION_EXECUTABLE_PINNED);
    CHECK(recovered.next_sequence == UINT64_C(3));
    CHECK(p6c_journal_close(&recovered) == P6C_RESULT_OK);
    CHECK(test_directory_close(&directory, "operation.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_journal_torn_tail(void)
{
    struct test_directory directory;
    struct p6c_journal journal;
    struct p6c_journal recovered;
    enum p6c_journal_recovery recovery;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    static const uint8_t TAIL[] = {UINT8_C(1), UINT8_C(2), UINT8_C(3)};

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x32));
    CHECK(p6c_journal_create(&directory.owner, "torn.journal",
                             operation_id, getuid(), &journal) ==
          P6C_RESULT_OK);
    CHECK(p6c_journal_append(&journal, P6C_OPERATION_RESERVED,
                             NULL, 0U) == P6C_RESULT_OK);
    CHECK(write(journal.file.descriptor, TAIL, sizeof(TAIL)) ==
          (ssize_t)sizeof(TAIL));
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_recover(&directory.owner, "torn.journal",
                              operation_id, getuid(), &recovered,
                              &recovery) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(recovery == P6C_JOURNAL_TORN_TAIL);
    CHECK(recovered.durable_state == P6C_OPERATION_RESERVED);
    CHECK(recovered.recovery_required);
    CHECK(p6c_journal_close(&recovered) == P6C_RESULT_OK);
    CHECK(test_directory_close(&directory, "torn.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_journal_impossible_transition(void)
{
    CHECK(!p6c_transition_allowed(P6C_OPERATION_ABSENT,
                                  P6C_OPERATION_RUNNING));
    CHECK(p6c_transition_allowed(P6C_OPERATION_ABSENT,
                                 P6C_OPERATION_RESERVED));
    CHECK(p6c_transition_allowed(P6C_OPERATION_RUNNING,
                                 P6C_OPERATION_STOP_REQUESTED));
    CHECK(p6c_transition_allowed(P6C_OPERATION_RUNNING,
                                 P6C_OPERATION_RECOVERY_REQUIRED));
    CHECK(!p6c_transition_allowed(P6C_OPERATION_ACKNOWLEDGED,
                                  P6C_OPERATION_RESERVED));
    return EXIT_SUCCESS;
}

static int case_journal_fsync_failure(void)
{
    struct test_directory directory;
    struct p6c_journal journal;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x33));
    CHECK(p6c_journal_create(&directory.owner, "fsync.journal",
                             operation_id, getuid(), &journal) ==
          P6C_RESULT_OK);
    p6c_test_failpoint_set(P6C_FAIL_JOURNAL_FSYNC);
    CHECK(p6c_journal_append(&journal, P6C_OPERATION_RESERVED,
                             NULL, 0U) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(journal.recovery_required);
    CHECK(p6c_owned_fd_is_live(&journal.file));
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    CHECK(test_directory_close(&directory, "fsync.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

enum journal_corruption {
    JOURNAL_CORRUPT_SEQUENCE_DUPLICATE = 0,
    JOURNAL_CORRUPT_SEQUENCE_GAP,
    JOURNAL_CORRUPT_PRIOR_DIGEST,
    JOURNAL_CORRUPT_PAYLOAD_DIGEST,
    JOURNAL_CORRUPT_IMPOSSIBLE_TRANSITION,
    JOURNAL_CORRUPT_UNKNOWN_RECORD
};

static void test_store_u64_be(uint8_t output[8], uint64_t value)
{
    size_t index;

    for (index = 0U; index < 8U; ++index) {
        output[7U - index] = (uint8_t)(value >> (index * 8U));
    }
}

static void test_rehash_journal_record(
    uint8_t record[P6C_JOURNAL_RECORD_BYTES])
{
    struct p6c_sha256 hash;

    p6c_sha256_init(&hash);
    if ((p6c_sha256_update(&hash, record, 168U) != P6C_RESULT_OK) ||
        (p6c_sha256_final(&hash, &record[168]) != P6C_RESULT_OK)) {
        abort();
    }
}

static void test_rehash_journal_payload_and_record(
    uint8_t record[P6C_JOURNAL_RECORD_BYTES])
{
    struct p6c_sha256 hash;
    uint32_t payload_length =
        ((uint32_t)record[68] << 24) |
        ((uint32_t)record[69] << 16) |
        ((uint32_t)record[70] << 8) |
        (uint32_t)record[71];

    if (payload_length > (uint32_t)P6C_JOURNAL_PAYLOAD_BYTES) {
        abort();
    }
    p6c_sha256_init(&hash);
    if ((p6c_sha256_update(
             &hash, &record[72], (size_t)payload_length) !=
         P6C_RESULT_OK) ||
        (p6c_sha256_final(&hash, &record[136]) != P6C_RESULT_OK)) {
        abort();
    }
    test_rehash_journal_record(record);
}

static int test_rebuild_journal_chain(
    int descriptor, uint64_t record_count)
{
    uint8_t prior[P6C_SHA256_BYTES];
    uint64_t index;

    memset(prior, 0, sizeof(prior));
    for (index = UINT64_C(0); index < record_count; ++index) {
        uint8_t record[P6C_JOURNAL_RECORD_BYTES];
        off_t offset = (off_t)(
            index * (uint64_t)P6C_JOURNAL_RECORD_BYTES);

        if (pread(
                descriptor, record, sizeof(record), offset) !=
            (ssize_t)sizeof(record)) {
            return EXIT_FAILURE;
        }
        memcpy(&record[36], prior, P6C_SHA256_BYTES);
        test_rehash_journal_payload_and_record(record);
        if (pwrite(
                descriptor, record, sizeof(record), offset) !=
            (ssize_t)sizeof(record)) {
            return EXIT_FAILURE;
        }
        memcpy(prior, &record[168], P6C_SHA256_BYTES);
    }
    return (fsync(descriptor) == 0) ? EXIT_SUCCESS : EXIT_FAILURE;
}

static int case_journal_v1_rejected(void)
{
    struct test_directory directory;
    struct p6c_journal journal;
    struct p6c_journal recovered;
    enum p6c_journal_recovery recovery;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    int descriptor;
    uint64_t index;
    static const uint8_t V1_MAGIC[8] = {
        UINT8_C('P'), UINT8_C('6'), UINT8_C('C'), UINT8_C('J'),
        UINT8_C('N'), UINT8_C('L'), UINT8_C('1'), UINT8_C(0)
    };

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x38));
    CHECK(p6c_journal_create(
              &directory.owner, "version-one.journal",
              operation_id, getuid(), &journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_RESERVED,
              NULL, 0U) == P6C_RESULT_OK);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_EXECUTABLE_PINNED,
              "elf", 3U) == P6C_RESULT_OK);
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    descriptor = openat(
        directory.owner.descriptor, "version-one.journal",
        O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    CHECK(descriptor >= 0);
    for (index = UINT64_C(0); index < UINT64_C(2); ++index) {
        uint8_t record[P6C_JOURNAL_RECORD_BYTES];
        off_t offset = (off_t)(
            index * (uint64_t)P6C_JOURNAL_RECORD_BYTES);

        CHECK(pread(
                  descriptor, record, sizeof(record), offset) ==
              (ssize_t)sizeof(record));
        memcpy(record, V1_MAGIC, sizeof(V1_MAGIC));
        p6c_store_u16_be(&record[8], UINT16_C(1));
        CHECK(pwrite(
                  descriptor, record, sizeof(record), offset) ==
              (ssize_t)sizeof(record));
    }
    CHECK(test_rebuild_journal_chain(
              descriptor, UINT64_C(2)) == EXIT_SUCCESS);
    CHECK(close(descriptor) == 0);
    CHECK(p6c_journal_recover(
              &directory.owner, "version-one.journal",
              operation_id, getuid(), &recovered,
              &recovery) == P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(recovery == P6C_JOURNAL_INVALID);
    CHECK(recovered.recovery_required);
    CHECK(p6c_journal_close(&recovered) == P6C_RESULT_OK);
    CHECK(test_directory_close(
              &directory, "version-one.journal") == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int journal_corruption_case(enum journal_corruption corruption)
{
    struct test_directory directory;
    struct p6c_journal journal;
    struct p6c_journal recovered;
    enum p6c_journal_recovery recovery;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t record[P6C_JOURNAL_RECORD_BYTES];
    int descriptor;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x39));
    CHECK(p6c_journal_create(&directory.owner, "corrupt.journal",
                             operation_id, getuid(), &journal) ==
          P6C_RESULT_OK);
    CHECK(p6c_journal_append(&journal, P6C_OPERATION_RESERVED,
                             NULL, 0U) == P6C_RESULT_OK);
    CHECK(p6c_journal_append(&journal, P6C_OPERATION_EXECUTABLE_PINNED,
                             "elf", 3U) == P6C_RESULT_OK);
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    descriptor = openat(directory.owner.descriptor, "corrupt.journal",
                        O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    CHECK(descriptor >= 0);
    CHECK(pread(descriptor, record, sizeof(record),
                (off_t)P6C_JOURNAL_RECORD_BYTES) ==
          (ssize_t)sizeof(record));
    switch (corruption) {
    case JOURNAL_CORRUPT_SEQUENCE_DUPLICATE:
        test_store_u64_be(&record[28], UINT64_C(1));
        break;
    case JOURNAL_CORRUPT_SEQUENCE_GAP:
        test_store_u64_be(&record[28], UINT64_C(3));
        break;
    case JOURNAL_CORRUPT_PRIOR_DIGEST:
        record[36] ^= UINT8_C(1);
        break;
    case JOURNAL_CORRUPT_PAYLOAD_DIGEST:
        record[136] ^= UINT8_C(1);
        break;
    case JOURNAL_CORRUPT_IMPOSSIBLE_TRANSITION:
        p6c_store_u16_be(&record[10], (uint16_t)P6C_OPERATION_RUNNING);
        break;
    case JOURNAL_CORRUPT_UNKNOWN_RECORD:
        p6c_store_u16_be(&record[10], UINT16_C(0x7fff));
        break;
    }
    test_rehash_journal_record(record);
    CHECK(pwrite(descriptor, record, sizeof(record),
                 (off_t)P6C_JOURNAL_RECORD_BYTES) ==
          (ssize_t)sizeof(record));
    CHECK(fsync(descriptor) == 0);
    CHECK(close(descriptor) == 0);
    CHECK(p6c_journal_recover(&directory.owner, "corrupt.journal",
                              operation_id, getuid(), &recovered,
                              &recovery) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(recovery == P6C_JOURNAL_INVALID);
    CHECK(recovered.recovery_required);
    CHECK(p6c_owned_fd_is_live(&recovered.file));
    CHECK(p6c_journal_close(&recovered) == P6C_RESULT_OK);
    CHECK(test_directory_close(&directory, "corrupt.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_journal_sequence_duplicate(void)
{
    return journal_corruption_case(JOURNAL_CORRUPT_SEQUENCE_DUPLICATE);
}

static int case_journal_sequence_gap(void)
{
    return journal_corruption_case(JOURNAL_CORRUPT_SEQUENCE_GAP);
}

static int case_journal_prior_digest(void)
{
    return journal_corruption_case(JOURNAL_CORRUPT_PRIOR_DIGEST);
}

static int case_journal_payload_digest(void)
{
    return journal_corruption_case(JOURNAL_CORRUPT_PAYLOAD_DIGEST);
}

static int case_journal_corrupt_transition(void)
{
    return journal_corruption_case(JOURNAL_CORRUPT_IMPOSSIBLE_TRANSITION);
}

static int case_journal_unknown_record(void)
{
    return journal_corruption_case(JOURNAL_CORRUPT_UNKNOWN_RECORD);
}

static int case_journal_unsafe_objects(void)
{
    struct test_directory directory;
    struct p6c_journal journal;
    struct p6c_journal recovered;
    enum p6c_journal_recovery recovery;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x3a));
    CHECK(p6c_journal_create(&directory.owner, "unsafe.journal",
                             operation_id, getuid(), &journal) ==
          P6C_RESULT_OK);
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    CHECK(fchmodat(directory.owner.descriptor, "unsafe.journal",
                   (mode_t)0644, 0) == 0);
    CHECK(p6c_journal_recover(&directory.owner, "unsafe.journal",
                              operation_id, getuid(), &recovered,
                              &recovery) == P6C_RESULT_UNSAFE);
    CHECK(p6c_journal_close(&recovered) == P6C_RESULT_OK);
    CHECK(fchmodat(directory.owner.descriptor, "unsafe.journal",
                   (mode_t)0600, 0) == 0);
    CHECK(linkat(directory.owner.descriptor, "unsafe.journal",
                 directory.owner.descriptor, "hardlink.journal", 0) == 0);
    CHECK(p6c_journal_recover(&directory.owner, "unsafe.journal",
                              operation_id, getuid(), &recovered,
                              &recovery) == P6C_RESULT_UNSAFE);
    CHECK(p6c_journal_close(&recovered) == P6C_RESULT_OK);
    CHECK(unlinkat(directory.owner.descriptor, "hardlink.journal", 0) == 0);
    CHECK(symlinkat("unsafe.journal", directory.owner.descriptor,
                    "symlink.journal") == 0);
    CHECK(p6c_journal_recover(&directory.owner, "symlink.journal",
                              operation_id, getuid(), &recovered,
                              &recovery) == P6C_RESULT_UNSAFE);
    CHECK(unlinkat(directory.owner.descriptor, "symlink.journal", 0) == 0);
    CHECK(test_directory_close(&directory, "unsafe.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int test_write_file(const struct test_directory *directory,
                           const char *name, const uint8_t *content,
                           size_t size, mode_t mode)
{
    int descriptor = openat(directory->owner.descriptor, name,
                            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC |
                                O_NOFOLLOW,
                            mode);
    size_t offset = 0U;

    if (descriptor < 0) {
        return EXIT_FAILURE;
    }
    while (offset < size) {
        ssize_t amount = write(descriptor, &content[offset], size - offset);

        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            (void)close(descriptor);
            return EXIT_FAILURE;
        }
        if (amount == 0) {
            (void)close(descriptor);
            return EXIT_FAILURE;
        }
        offset += (size_t)amount;
    }
    if ((fsync(descriptor) != 0) || (close(descriptor) != 0)) {
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static void test_hash_bytes(const uint8_t *content, size_t size,
                            uint8_t digest[P6C_SHA256_BYTES])
{
    struct p6c_sha256 context;

    p6c_sha256_init(&context);
    if ((p6c_sha256_update(&context, content, size) != P6C_RESULT_OK) ||
        (p6c_sha256_final(&context, digest) != P6C_RESULT_OK)) {
        abort();
    }
}

static int case_credential_authority_revalidated_before_clone(void)
{
    struct test_directory credentials;
    struct stat directory_info;
    struct stat leaf_info;
    uint8_t manifest[256];
    uint8_t digest[P6C_SHA256_BYTES];
    size_t offset = 0U;
    char displaced_directory[160];
    static const char LEAF[] = "database-password";
    static const char DISPLACED_LEAF[] = "database-password-attested";
    static const uint8_t VALUE[] = "test-only-credential";
    static const uint8_t REPLACEMENT[] = "replacement-credential";

    CHECK(test_directory_create(&credentials) == EXIT_SUCCESS);
    CHECK(chmod(credentials.path, (mode_t)0700) == 0);
    CHECK(test_write_file(
              &credentials, LEAF, VALUE, sizeof(VALUE) - 1U,
              (mode_t)0600) == EXIT_SUCCESS);
    CHECK(fstat(credentials.owner.descriptor, &directory_info) == 0);
    CHECK(fstatat(
              credentials.owner.descriptor, LEAF, &leaf_info,
              AT_SYMLINK_NOFOLLOW) == 0);
    test_hash_bytes(VALUE, sizeof(VALUE) - 1U, digest);

    memcpy(&manifest[offset], "P6CM1", 5U);
    offset += 5U;
    p6c_store_u32_be(&manifest[offset], UINT32_C(1));
    offset += 4U;
    test_store_u64_be(
        &manifest[offset], (uint64_t)directory_info.st_dev);
    offset += 8U;
    test_store_u64_be(
        &manifest[offset], (uint64_t)directory_info.st_ino);
    offset += 8U;
    p6c_store_u32_be(
        &manifest[offset], (uint32_t)directory_info.st_uid);
    offset += 4U;
    p6c_store_u32_be(
        &manifest[offset], (uint32_t)directory_info.st_gid);
    offset += 4U;
    p6c_store_u32_be(
        &manifest[offset], (uint32_t)directory_info.st_mode);
    offset += 4U;
    p6c_store_u32_be(&manifest[offset], (uint32_t)(sizeof(LEAF) - 1U));
    offset += 4U;
    memcpy(&manifest[offset], LEAF, sizeof(LEAF) - 1U);
    offset += sizeof(LEAF) - 1U;
    test_store_u64_be(&manifest[offset], (uint64_t)leaf_info.st_dev);
    offset += 8U;
    test_store_u64_be(&manifest[offset], (uint64_t)leaf_info.st_ino);
    offset += 8U;
    p6c_store_u32_be(&manifest[offset], (uint32_t)leaf_info.st_uid);
    offset += 4U;
    p6c_store_u32_be(&manifest[offset], (uint32_t)leaf_info.st_gid);
    offset += 4U;
    p6c_store_u32_be(&manifest[offset], (uint32_t)leaf_info.st_mode);
    offset += 4U;
    test_store_u64_be(&manifest[offset], (uint64_t)leaf_info.st_nlink);
    offset += 8U;
    test_store_u64_be(&manifest[offset], (uint64_t)leaf_info.st_size);
    offset += 8U;
    test_store_u64_be(
        &manifest[offset],
        (uint64_t)(
            ((int64_t)leaf_info.st_mtim.tv_sec *
             INT64_C(1000000000)) +
            (int64_t)leaf_info.st_mtim.tv_nsec));
    offset += 8U;
    test_store_u64_be(
        &manifest[offset],
        (uint64_t)(
            ((int64_t)leaf_info.st_ctim.tv_sec *
             INT64_C(1000000000)) +
            (int64_t)leaf_info.st_ctim.tv_nsec));
    offset += 8U;
    memcpy(&manifest[offset], digest, sizeof(digest));
    offset += sizeof(digest);

    CHECK(p6c_test_verify_credential_authority(
              credentials.owner.descriptor, manifest, offset) ==
          P6C_RESULT_OK);
    CHECK(snprintf(
              displaced_directory, sizeof(displaced_directory),
              "%s-attested", credentials.path) > 0);
    CHECK(rename(credentials.path, displaced_directory) == 0);
    CHECK(mkdir(credentials.path, (mode_t)0700) == 0);
    CHECK(p6c_test_verify_credential_authority(
              credentials.owner.descriptor, manifest, offset) ==
          P6C_RESULT_OK);
    CHECK(rmdir(credentials.path) == 0);
    CHECK(rename(displaced_directory, credentials.path) == 0);

    CHECK(renameat(
              credentials.owner.descriptor, LEAF,
              credentials.owner.descriptor, DISPLACED_LEAF) == 0);
    CHECK(test_write_file(
              &credentials, LEAF, REPLACEMENT,
              sizeof(REPLACEMENT) - 1U, (mode_t)0600) == EXIT_SUCCESS);
    CHECK(p6c_test_verify_credential_authority(
              credentials.owner.descriptor, manifest, offset) ==
          P6C_RESULT_UNSAFE);
    CHECK(unlinkat(credentials.owner.descriptor, LEAF, 0) == 0);
    CHECK(renameat(
              credentials.owner.descriptor, DISPLACED_LEAF,
              credentials.owner.descriptor, LEAF) == 0);
    CHECK(test_directory_close(&credentials, LEAF) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_executable_authority(void)
{
    struct test_directory directory;
    struct p6c_executable executable;
    uint8_t elf[128];
    uint8_t digest[P6C_SHA256_BYTES];
    uint8_t wrong_digest[P6C_SHA256_BYTES];
    static const uint8_t SCRIPT[] = "#!/bin/sh\nexit 0\n";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), digest);
    memset(wrong_digest, UINT8_C(0), sizeof(wrong_digest));

    CHECK(test_write_file(&directory, "approved.elf", elf, sizeof(elf),
                          (mode_t)0700) == EXIT_SUCCESS);
    memset(&executable, 0, sizeof(executable));
    CHECK(p6c_pin_executable(&directory.owner, "approved.elf", getuid(),
                             digest, &executable) == P6C_RESULT_OK);
    CHECK(p6c_owned_fd_is_live(&executable.file));
    CHECK(memcmp(executable.digest, digest, sizeof(digest)) == 0);
    CHECK(p6c_executable_close(&executable) == P6C_RESULT_OK);

    memset(&executable, 0, sizeof(executable));
    CHECK(p6c_pin_executable(&directory.owner, "approved.elf", getuid(),
                             wrong_digest, &executable) ==
          P6C_RESULT_UNSAFE);
    CHECK(!p6c_owned_fd_is_live(&executable.file));

    CHECK(test_write_file(&directory, "script", SCRIPT,
                          sizeof(SCRIPT) - 1U,
                          (mode_t)0700) == EXIT_SUCCESS);
    test_hash_bytes(SCRIPT, sizeof(SCRIPT) - 1U, digest);
    memset(&executable, 0, sizeof(executable));
    CHECK(p6c_pin_executable(&directory.owner, "script", getuid(),
                             digest, &executable) == P6C_RESULT_UNSAFE);

    CHECK(test_write_file(&directory, "unsafe.elf", elf, sizeof(elf),
                          (mode_t)0770) == EXIT_SUCCESS);
    CHECK(fchmodat(directory.owner.descriptor, "unsafe.elf",
                   (mode_t)0770, 0) == 0);
    test_hash_bytes(elf, sizeof(elf), digest);
    memset(&executable, 0, sizeof(executable));
    CHECK(p6c_pin_executable(&directory.owner, "unsafe.elf", getuid(),
                             digest, &executable) == P6C_RESULT_UNSAFE);

    CHECK(symlinkat("approved.elf", directory.owner.descriptor,
                    "linked.elf") == 0);
    memset(&executable, 0, sizeof(executable));
    CHECK(p6c_pin_executable(&directory.owner, "linked.elf", getuid(),
                             digest, &executable) == P6C_RESULT_UNSAFE);

    memset(&executable, 0, sizeof(executable));
    CHECK(p6c_pin_executable(&directory.owner, "approved.elf",
                             getuid() + (uid_t)1, digest, &executable) ==
          P6C_RESULT_UNSAFE);

    p6c_test_failpoint_set(P6C_FAIL_EXEC_HASH_READ);
    memset(&executable, 0, sizeof(executable));
    CHECK(p6c_pin_executable(&directory.owner, "approved.elf", getuid(),
                             digest, &executable) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(p6c_owned_fd_is_live(&executable.file));
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(p6c_executable_close(&executable) == P6C_RESULT_OK);

    CHECK(unlinkat(directory.owner.descriptor, "linked.elf", 0) == 0);
    CHECK(unlinkat(directory.owner.descriptor, "unsafe.elf", 0) == 0);
    CHECK(unlinkat(directory.owner.descriptor, "script", 0) == 0);
    CHECK(test_directory_close(&directory, "approved.elf") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_executable_replacement_during_hash(void)
{
    struct test_directory directory;
    struct p6c_executable executable;
    uint8_t approved[32768];
    uint8_t replacement[32768];
    uint8_t digest[P6C_SHA256_BYTES];

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(approved, UINT8_C(0x5a), sizeof(approved));
    approved[0] = UINT8_C(0x7f);
    approved[1] = UINT8_C('E');
    approved[2] = UINT8_C('L');
    approved[3] = UINT8_C('F');
    memcpy(replacement, approved, sizeof(replacement));
    replacement[sizeof(replacement) - 1U] ^= UINT8_C(0xff);
    test_hash_bytes(approved, sizeof(approved), digest);
    CHECK(test_write_file(
              &directory, "approved-race.elf", approved,
              sizeof(approved), (mode_t)0700) == EXIT_SUCCESS);
    CHECK(test_write_file(
              &directory, "replacement-race.elf", replacement,
              sizeof(replacement), (mode_t)0700) == EXIT_SUCCESS);
    p6c_test_exec_replacement_set(
        directory.owner.descriptor, "approved-race.elf",
        "replacement-race.elf", "displaced-race.elf");
    memset(&executable, 0, sizeof(executable));
    CHECK(p6c_pin_executable(
              &directory.owner, "approved-race.elf", getuid(),
              digest, &executable) == P6C_RESULT_UNSAFE);
    CHECK(!p6c_owned_fd_is_live(&executable.file));
    p6c_test_exec_replacement_set(
        P6C_INVALID_DESCRIPTOR, NULL, NULL, NULL);
    CHECK(unlinkat(
              directory.owner.descriptor, "displaced-race.elf", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor, "approved-race.elf", 0) == 0);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_peer_and_replay(void)
{
    int sockets[2];
    struct p6c_owned_fd socket_owner;
    struct p6c_peer_identity peer;
    struct p6c_peer_identity other_peer;
    struct p6c_replay_table table;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t digest[P6C_SHA256_BYTES];
    uint8_t different_digest[P6C_SHA256_BYTES];
    uint8_t token[P6C_RECOVERY_TOKEN_BYTES];
    struct p6c_public_error public_error;

    CHECK(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) ==
          0);
    p6c_owned_fd_reset(&socket_owner);
    CHECK(p6c_owned_fd_acquire(&socket_owner, sockets[0],
                               P6C_DESCRIPTOR_SOCKET) == P6C_RESULT_OK);
    CHECK(socket_owner.type == P6C_DESCRIPTOR_SOCKET);
    peer.process_id = getpid();
    peer.user_id = getuid();
    peer.group_id = getgid();
    p6c_test_peer_override_set(true, &peer);
    CHECK(p6c_authenticate_peer(&socket_owner, getuid(), &peer) ==
          P6C_RESULT_OK);
    CHECK(peer.process_id == getpid());
    CHECK(peer.user_id == getuid());
    CHECK(peer.group_id == getgid());
    CHECK(p6c_authenticate_peer(&socket_owner, getuid() + (uid_t)1,
                                &peer) == P6C_RESULT_UNAUTHORIZED);

    test_fill_identity(operation_id, UINT8_C(0x45));
    memset(digest, UINT8_C(0x64), sizeof(digest));
    memset(different_digest, UINT8_C(0x65), sizeof(different_digest));
    p6c_replay_table_init(&table);
    CHECK(p6c_replay_check(&table, operation_id, digest, &peer) ==
          P6C_REPLAY_NEW);
    CHECK(p6c_replay_check(&table, operation_id, digest, &peer) ==
          P6C_REPLAY_IDENTICAL);
    CHECK(p6c_replay_check(&table, operation_id, different_digest, &peer) ==
          P6C_REPLAY_DIGEST_MISMATCH);
    other_peer = peer;
    other_peer.process_id += (pid_t)1;
    CHECK(p6c_replay_check(&table, operation_id, digest, &other_peer) ==
          P6C_REPLAY_DIFFERENT_PEER);

    memset(token, UINT8_C(0x72), sizeof(token));
    CHECK(p6c_public_error_set(
              &public_error, P6C_STATUS_RECOVERY_REQUIRED,
              "RECOVERY_REQUIRED", true, P6C_OPERATION_RECOVERY_REQUIRED,
              token) == P6C_RESULT_OK);
    CHECK(strcmp(public_error.public_code, "RECOVERY_REQUIRED") == 0);
    CHECK(p6c_public_error_set(
              &public_error, P6C_STATUS_INTERNAL, "errno: private/path",
              false, P6C_OPERATION_ABSENT, token) == P6C_RESULT_INVALID);

    p6c_test_peer_override_set(false, NULL);
    CHECK(p6c_owned_fd_close(&socket_owner) == P6C_RESULT_OK);
    CHECK(close(sockets[1]) == 0);
    return EXIT_SUCCESS;
}

static int case_production_peer_credentials(void)
{
    int sockets[2];
    struct p6c_owned_fd socket_owner;
    struct p6c_peer_identity peer;
    int socket_type = 0;
    socklen_t socket_type_size = (socklen_t)sizeof(socket_type);

    CHECK(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) ==
          0);
    p6c_owned_fd_reset(&socket_owner);
    CHECK(p6c_owned_fd_acquire(
              &socket_owner, sockets[0], P6C_DESCRIPTOR_SOCKET) ==
          P6C_RESULT_OK);
    p6c_test_peer_override_set(false, NULL);
    if ((getsockopt(
             sockets[0], SOL_SOCKET, SO_TYPE,
             &socket_type, &socket_type_size) != 0) &&
        (errno == EPERM)) {
        (void)puts(
            "production_peer_credentials: BLOCKED_EPERM_GETSOCKOPT");
        CHECK(p6c_owned_fd_close(&socket_owner) == P6C_RESULT_OK);
        CHECK(close(sockets[1]) == 0);
        return EXIT_SUCCESS;
    }
    CHECK(socket_type == SOCK_SEQPACKET);
    CHECK(p6c_authenticate_peer(
              &socket_owner, getuid(), &peer) == P6C_RESULT_OK);
    CHECK(peer.process_id == getpid());
    CHECK(peer.user_id == getuid());
    CHECK(peer.group_id == getgid());
    CHECK(p6c_authenticate_peer(
              &socket_owner, getuid() + (uid_t)1, &peer) ==
          P6C_RESULT_UNAUTHORIZED);
    CHECK(p6c_owned_fd_close(&socket_owner) == P6C_RESULT_OK);
    CHECK(close(sockets[1]) == 0);
    return EXIT_SUCCESS;
}

static int case_production_pidfd_observe_reap(void)
{
    struct p6c_owned_fd pidfd_owner;
    pid_t child;
    int descriptor;
    int32_t exit_status = INT32_C(-1);
    int status;

    child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        _exit(23);
    }
    descriptor = (int)syscall(SYS_pidfd_open, child, 0U);
    CHECK(descriptor >= 0);
    p6c_owned_fd_reset(&pidfd_owner);
    CHECK(p6c_owned_fd_acquire(
              &pidfd_owner, descriptor, P6C_DESCRIPTOR_PIDFD) ==
          P6C_RESULT_OK);
    pidfd_owner.type = P6C_DESCRIPTOR_PIDFD;
    CHECK(p6c_pidfd_observe(&pidfd_owner, &exit_status) ==
          P6C_RESULT_OK);
    CHECK(exit_status == INT32_C(23));
    exit_status = INT32_C(-1);
    CHECK(p6c_pidfd_observe(&pidfd_owner, &exit_status) ==
          P6C_RESULT_OK);
    CHECK(exit_status == INT32_C(23));
    CHECK(p6c_pidfd_reap(&pidfd_owner) == P6C_RESULT_OK);
    errno = 0;
    CHECK(waitpid(child, &status, WNOHANG) == -1);
    CHECK(errno == ECHILD);
    CHECK(p6c_owned_fd_close(&pidfd_owner) == P6C_RESULT_OK);
    return EXIT_SUCCESS;
}

static int case_transcript_truncation(void)
{
    struct test_directory directory;
    struct p6c_transcript transcript;
    uint8_t expected[P6C_SHA256_BYTES];
    uint8_t output[8];
    size_t read_size = 0U;
    static const uint8_t CONTENT[] = {
        UINT8_C('a'), UINT8_C('b'), UINT8_C('c'),
        UINT8_C('d'), UINT8_C('e'), UINT8_C('f')
    };

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(p6c_transcript_create(&directory.owner, P6C_STREAM_STDOUT,
                                UINT64_C(4), true, &transcript) ==
          P6C_RESULT_OK);
    CHECK(p6c_transcript_ingest(&transcript, CONTENT, sizeof(CONTENT)) ==
          P6C_RESULT_OK);
    CHECK(transcript.observed_size == UINT64_C(6));
    CHECK(transcript.retained_size == UINT64_C(4));
    CHECK(transcript.truncated);
    p6c_transcript_observe_eof(&transcript);
    CHECK(transcript.eof_observed);
    CHECK(!transcript.descendant_cleanup_proven);
    CHECK(p6c_transcript_finalize(&transcript) == P6C_RESULT_INVALID);
    p6c_transcript_prove_cleanup(&transcript);
    CHECK(p6c_transcript_finalize(&transcript) == P6C_RESULT_OK);
    test_hash_bytes(CONTENT, sizeof(CONTENT), expected);
    CHECK(memcmp(transcript.digest, expected, sizeof(expected)) == 0);
    memset(output, UINT8_C(0xa5), sizeof(output));
    CHECK(p6c_transcript_read(&transcript, UINT64_C(1), output, 3U,
                              &read_size) == P6C_RESULT_OK);
    CHECK(read_size == 3U);
    CHECK(memcmp(output, "bcd", 3U) == 0);
    CHECK(output[3] == UINT8_C(0xa5));
    CHECK(p6c_transcript_read(&transcript, UINT64_C(5), output, 1U,
                              &read_size) == P6C_RESULT_INVALID);
    CHECK(p6c_transcript_close(&transcript) == P6C_RESULT_OK);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_transcript_faults(void)
{
    struct test_directory directory;
    struct p6c_transcript transcript;
    uint8_t output[4];
    size_t read_size = 0U;
    static const uint8_t CONTENT[] = {
        UINT8_C('d'), UINT8_C('a'), UINT8_C('t'), UINT8_C('a')
    };

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(p6c_transcript_create(&directory.owner, P6C_STREAM_STDERR,
                                UINT64_C(16), true, &transcript) ==
          P6C_RESULT_OK);
    p6c_test_failpoint_set(P6C_FAIL_TRANSCRIPT_WRITE);
    CHECK(p6c_transcript_ingest(&transcript, CONTENT, sizeof(CONTENT)) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(transcript.recovery_required);
    CHECK(p6c_owned_fd_is_live(&transcript.sink));
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(p6c_transcript_close(&transcript) == P6C_RESULT_OK);

    CHECK(p6c_transcript_create(&directory.owner, P6C_STREAM_STDERR,
                                UINT64_C(16), true, &transcript) ==
          P6C_RESULT_OK);
    CHECK(p6c_transcript_ingest(&transcript, CONTENT, sizeof(CONTENT)) ==
          P6C_RESULT_OK);
    p6c_transcript_observe_eof(&transcript);
    p6c_transcript_prove_cleanup(&transcript);
    p6c_test_failpoint_set(P6C_FAIL_TRANSCRIPT_DIGEST);
    CHECK(p6c_transcript_finalize(&transcript) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(p6c_owned_fd_is_live(&transcript.sink));
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(p6c_transcript_close(&transcript) == P6C_RESULT_OK);

    CHECK(p6c_transcript_create(&directory.owner, P6C_STREAM_STDERR,
                                UINT64_C(16), true, &transcript) ==
          P6C_RESULT_OK);
    CHECK(p6c_transcript_ingest(&transcript, CONTENT, sizeof(CONTENT)) ==
          P6C_RESULT_OK);
    p6c_transcript_observe_eof(&transcript);
    p6c_transcript_prove_cleanup(&transcript);
    p6c_test_failpoint_set(P6C_FAIL_TRANSCRIPT_FSYNC);
    CHECK(p6c_transcript_finalize(&transcript) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(p6c_transcript_close(&transcript) == P6C_RESULT_OK);

    CHECK(p6c_transcript_create(&directory.owner, P6C_STREAM_STDERR,
                                UINT64_C(16), true, &transcript) ==
          P6C_RESULT_OK);
    CHECK(p6c_transcript_ingest(&transcript, CONTENT, sizeof(CONTENT)) ==
          P6C_RESULT_OK);
    p6c_transcript_observe_eof(&transcript);
    p6c_transcript_prove_cleanup(&transcript);
    CHECK(p6c_transcript_finalize(&transcript) == P6C_RESULT_OK);
    p6c_test_failpoint_set(P6C_FAIL_TRANSCRIPT_READ);
    CHECK(p6c_transcript_read(&transcript, UINT64_C(0), output,
                              sizeof(output), &read_size) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    p6c_test_failpoint_set(P6C_FAIL_TRANSCRIPT_CLOSE);
    CHECK(p6c_transcript_close(&transcript) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(p6c_owned_fd_is_live(&transcript.sink));
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(p6c_transcript_close(&transcript) == P6C_RESULT_OK);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_production_blocking_pipe_drain(void)
{
    struct test_directory directory;
    struct p6c_owned_pair pipe_owner;
    struct p6c_transcript transcript;
    struct p6c_sha256 full_hash;
    struct p6c_sha256 retained_hash;
    uint8_t full_digest[P6C_SHA256_BYTES];
    uint8_t retained_digest[P6C_SHA256_BYTES];
    uint8_t chunk[4096];
    uint8_t observed[4096];
    size_t read_size = 0U;
    size_t index;
    size_t chunks = 64U;
    pid_t child;
    int status;
    bool eof = false;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(p6c_owned_pipe_create(&pipe_owner) == P6C_RESULT_OK);
    CHECK((fcntl(pipe_owner.first.descriptor, F_GETFL) & O_NONBLOCK) != 0);
    CHECK((fcntl(pipe_owner.second.descriptor, F_GETFL) & O_NONBLOCK) == 0);
    for (index = 0U; index < sizeof(chunk); ++index) {
        chunk[index] = (uint8_t)(index & 0xffU);
    }
    CHECK(p6c_transcript_create(
              &directory.owner, P6C_STREAM_STDOUT, UINT64_C(8192),
              true, &transcript) == P6C_RESULT_OK);
    child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        size_t chunk_index;

        (void)close(pipe_owner.first.descriptor);
        for (chunk_index = 0U; chunk_index < chunks; ++chunk_index) {
            size_t offset = 0U;

            while (offset < sizeof(chunk)) {
                ssize_t amount = write(
                    pipe_owner.second.descriptor, &chunk[offset],
                    sizeof(chunk) - offset);

                if (amount < 0) {
                    if (errno == EINTR) {
                        continue;
                    }
                    _exit((errno == EAGAIN) ? 91 : 92);
                }
                if (amount == 0) {
                    _exit(93);
                }
                offset += (size_t)amount;
            }
        }
        _exit(0);
    }
    CHECK(p6c_owned_fd_close(&pipe_owner.second) == P6C_RESULT_OK);
    while (!eof) {
        uint8_t input[8192];
        ssize_t amount = read(
            pipe_owner.first.descriptor, input, sizeof(input));

        if (amount > 0) {
            CHECK(p6c_transcript_ingest(
                      &transcript, input, (size_t)amount) ==
                  P6C_RESULT_OK);
        } else if (amount == 0) {
            eof = true;
        } else if ((errno == EAGAIN) || (errno == EWOULDBLOCK)) {
            struct pollfd descriptor;

            memset(&descriptor, 0, sizeof(descriptor));
            descriptor.fd = pipe_owner.first.descriptor;
            descriptor.events = (short)(POLLIN | POLLHUP | POLLERR);
            CHECK(poll(&descriptor, 1U, 1000) > 0);
        } else if (errno != EINTR) {
            CHECK(false);
        }
    }
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status));
    CHECK(WEXITSTATUS(status) == 0);
    p6c_transcript_observe_eof(&transcript);
    p6c_transcript_prove_cleanup(&transcript);
    CHECK(p6c_transcript_finalize(&transcript) == P6C_RESULT_OK);
    CHECK(transcript.observed_size ==
          (uint64_t)(chunks * sizeof(chunk)));
    CHECK(transcript.retained_size == UINT64_C(8192));
    CHECK(transcript.truncated);
    p6c_sha256_init(&full_hash);
    for (index = 0U; index < chunks; ++index) {
        CHECK(p6c_sha256_update(
                  &full_hash, chunk, sizeof(chunk)) == P6C_RESULT_OK);
    }
    CHECK(p6c_sha256_final(&full_hash, full_digest) == P6C_RESULT_OK);
    p6c_sha256_init(&retained_hash);
    CHECK(p6c_sha256_update(
              &retained_hash, chunk, sizeof(chunk)) == P6C_RESULT_OK);
    CHECK(p6c_sha256_update(
              &retained_hash, chunk, sizeof(chunk)) == P6C_RESULT_OK);
    CHECK(p6c_sha256_final(
              &retained_hash, retained_digest) == P6C_RESULT_OK);
    CHECK(memcmp(transcript.digest, full_digest, P6C_SHA256_BYTES) == 0);
    CHECK(memcmp(
              transcript.retained_digest, retained_digest,
              P6C_SHA256_BYTES) == 0);
    CHECK(p6c_transcript_read(
              &transcript, UINT64_C(0), observed, sizeof(observed),
              &read_size) == P6C_RESULT_OK);
    CHECK(read_size == sizeof(observed));
    CHECK(memcmp(observed, chunk, sizeof(chunk)) == 0);
    CHECK(p6c_owned_fd_close(&pipe_owner.first) == P6C_RESULT_OK);
    CHECK(p6c_transcript_close(&transcript) == P6C_RESULT_OK);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_truncated_retained_prefix_tamper(void)
{
    struct test_directory directory;
    struct p6c_transcript transcript;
    uint8_t full_digest[P6C_SHA256_BYTES];
    uint8_t retained_digest[P6C_SHA256_BYTES];
    int descriptor;
    static const uint8_t FULL[] = "abcdef";
    static const uint8_t RETAINED[] = "abcd";
    static const uint8_t REPLACEMENT[] = "zbcd";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_hash_bytes(FULL, sizeof(FULL) - 1U, full_digest);
    test_hash_bytes(RETAINED, sizeof(RETAINED) - 1U, retained_digest);
    CHECK(test_write_file(
              &directory, "truncated.stdout", RETAINED,
              sizeof(RETAINED) - 1U, (mode_t)0600) == EXIT_SUCCESS);
    CHECK(p6c_transcript_recover(
              &directory.owner, "truncated.stdout",
              P6C_STREAM_STDOUT, UINT64_C(6), UINT64_C(4), true,
              full_digest, retained_digest, &transcript) ==
          P6C_RESULT_OK);
    CHECK(p6c_transcript_close(&transcript) == P6C_RESULT_OK);
    descriptor = openat(
        directory.owner.descriptor, "truncated.stdout",
        O_WRONLY | O_TRUNC | O_CLOEXEC | O_NOFOLLOW);
    CHECK(descriptor >= 0);
    CHECK(write(
              descriptor, REPLACEMENT,
              sizeof(REPLACEMENT) - 1U) ==
          (ssize_t)(sizeof(REPLACEMENT) - 1U));
    CHECK(fsync(descriptor) == 0);
    CHECK(close(descriptor) == 0);
    CHECK(p6c_transcript_recover(
              &directory.owner, "truncated.stdout",
              P6C_STREAM_STDOUT, UINT64_C(6), UINT64_C(4), true,
              full_digest, retained_digest, &transcript) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(transcript.recovery_required);
    CHECK(p6c_transcript_close(&transcript) == P6C_RESULT_OK);
    CHECK(test_directory_close(
              &directory, "truncated.stdout") == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_untruncated_digest_contradiction(void)
{
    struct test_directory directory;
    struct p6c_transcript transcript;
    uint8_t retained_digest[P6C_SHA256_BYTES];
    uint8_t contradictory_digest[P6C_SHA256_BYTES];
    static const uint8_t RETAINED[] = "authenticated-retained-stream";
    static const uint8_t OTHER_STREAM[] = "cross-stream-substitution";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_hash_bytes(
        RETAINED, sizeof(RETAINED) - 1U, retained_digest);
    test_hash_bytes(
        OTHER_STREAM, sizeof(OTHER_STREAM) - 1U,
        contradictory_digest);
    CHECK(test_write_file(
              &directory, "contradictory.stdout", RETAINED,
              sizeof(RETAINED) - 1U, (mode_t)0600) == EXIT_SUCCESS);
    CHECK(p6c_transcript_recover(
              &directory.owner, "contradictory.stdout",
              P6C_STREAM_STDOUT,
              (uint64_t)(sizeof(RETAINED) - 1U),
              (uint64_t)(sizeof(RETAINED) - 1U), false,
              contradictory_digest, retained_digest, &transcript) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(transcript.recovery_required);
    CHECK(p6c_transcript_close(&transcript) == P6C_RESULT_OK);
    CHECK(test_directory_close(
              &directory, "contradictory.stdout") == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_transcript_recovery_both_zero_streams(void)
{
    struct test_directory directory;
    struct p6c_transcript stdout_transcript;
    struct p6c_transcript stderr_transcript;
    uint8_t empty_digest[P6C_SHA256_BYTES];

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_hash_bytes(NULL, 0U, empty_digest);
    CHECK(test_write_file(
              &directory, "zero.stdout", NULL, 0U,
              (mode_t)0600) == EXIT_SUCCESS);
    CHECK(test_write_file(
              &directory, "zero.stderr", NULL, 0U,
              (mode_t)0600) == EXIT_SUCCESS);
    CHECK(p6c_transcript_recover(
              &directory.owner, "zero.stdout", P6C_STREAM_STDOUT,
              UINT64_C(0), UINT64_C(0), false,
              empty_digest, empty_digest, &stdout_transcript) ==
          P6C_RESULT_OK);
    CHECK(p6c_transcript_recover(
              &directory.owner, "zero.stderr", P6C_STREAM_STDERR,
              UINT64_C(0), UINT64_C(0), false,
              empty_digest, empty_digest, &stderr_transcript) ==
          P6C_RESULT_OK);
    CHECK(p6c_transcript_close(&stdout_transcript) == P6C_RESULT_OK);
    CHECK(p6c_transcript_close(&stderr_transcript) == P6C_RESULT_OK);
    CHECK(unlinkat(
              directory.owner.descriptor, "zero.stderr", 0) == 0);
    CHECK(test_directory_close(
              &directory, "zero.stdout") == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

enum fake_stage {
    FAKE_STAGE_NONE = 0,
    FAKE_STAGE_CLONE,
    FAKE_STAGE_CLONE_PIDFD_ACQUIRE,
    FAKE_STAGE_CLONE_STATUS_CLOSE,
    FAKE_STAGE_CLONE_STDOUT_CLOSE,
    FAKE_STAGE_CLONE_STDERR_CLOSE,
    FAKE_STAGE_WAIT_TERMINAL,
    FAKE_STAGE_SIGNAL,
    FAKE_STAGE_GRACE,
    FAKE_STAGE_FREEZE,
    FAKE_STAGE_KILL,
    FAKE_STAGE_EMPTY,
    FAKE_STAGE_OBSERVE,
    FAKE_STAGE_REAP,
    FAKE_STAGE_TRANSCRIPTS,
    FAKE_STAGE_REMOVE
};

struct fake_process {
    enum fake_stage fail_stage;
    enum p6c_result fail_result;
    enum p6c_exec_confirmation confirmation;
    unsigned int calls[FAKE_STAGE_REMOVE + 1];
    const uint8_t *stdout_content;
    size_t stdout_content_size;
    const uint8_t *stderr_content;
    size_t stderr_content_size;
    bool fail_result_journal_after_remove;
    bool fail_once;
    unsigned int failures_remaining;
    const struct p6c_owned_fd *removal_root;
    const char *removal_name;
};

struct operation_fixture {
    struct test_directory directory;
    struct p6c_journal journal;
    struct p6c_executable executable;
    struct p6c_owned_fd cgroup;
    struct p6c_owned_pair status_channel;
    struct p6c_owned_pair stdout_channel;
    struct p6c_owned_pair stderr_channel;
    struct p6c_transcript stdout_transcript;
    struct p6c_transcript stderr_transcript;
    struct p6c_operation operation;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
};

static enum fake_stage test_hold_stage = FAKE_STAGE_NONE;
static int test_hold_ready_descriptor = P6C_INVALID_DESCRIPTOR;
static int test_hold_release_descriptor = P6C_INVALID_DESCRIPTOR;
static bool test_hold_released = false;

static enum p6c_result fake_result(struct fake_process *fake,
                                   enum fake_stage stage)
{
    if ((test_hold_stage == stage) && !test_hold_released) {
        uint8_t marker = UINT8_C(1);
        ssize_t amount;

        do {
            amount = write(
                test_hold_ready_descriptor, &marker, sizeof(marker));
        } while ((amount < 0) && (errno == EINTR));
        if (amount != (ssize_t)sizeof(marker)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        do {
            amount = read(
                test_hold_release_descriptor, &marker, sizeof(marker));
        } while ((amount < 0) && (errno == EINTR));
        if (amount != (ssize_t)sizeof(marker)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        test_hold_released = true;
    }
    ++fake->calls[stage];
    if (fake->fail_stage == stage) {
        enum p6c_result result = fake->fail_result;

        if (fake->failures_remaining != 0U) {
            --fake->failures_remaining;
            if (fake->failures_remaining == 0U) {
                fake->fail_stage = FAKE_STAGE_NONE;
            }
            return result;
        }
        if (fake->fail_once) {
            fake->fail_stage = FAKE_STAGE_NONE;
        }
        return result;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result fake_clone(
    void *context, struct p6c_operation *operation)
{
    struct fake_process *fake = context;
    enum p6c_result result = fake_result(fake, FAKE_STAGE_CLONE);
    int descriptor;

    if (result != P6C_RESULT_OK) {
        return result;
    }
    if (((fake->stdout_content_size != 0U) &&
         (p6c_transcript_ingest(
              operation->stdout_transcript, fake->stdout_content,
              fake->stdout_content_size) != P6C_RESULT_OK)) ||
        ((fake->stderr_content_size != 0U) &&
         (p6c_transcript_ingest(
              operation->stderr_transcript, fake->stderr_content,
              fake->stderr_content_size) != P6C_RESULT_OK))) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    operation->child_pid = getpid();
    operation->physical_custody = P6C_CHILD_PID_WAITABLE;
    if (fake->fail_stage == FAKE_STAGE_CLONE_PIDFD_ACQUIRE) {
        return fake_result(fake, FAKE_STAGE_CLONE_PIDFD_ACQUIRE);
    }
    descriptor = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (descriptor < 0) {
        return P6C_RESULT_SYSTEM;
    }
    result = p6c_owned_fd_acquire(
        &operation->pidfd, descriptor, P6C_DESCRIPTOR_PIDFD);
    if (result != P6C_RESULT_OK) {
        return result;
    }
    operation->physical_custody = P6C_CHILD_PIDFD_OWNED;
    if (fake->fail_stage == FAKE_STAGE_CLONE_STATUS_CLOSE) {
        return fake_result(fake, FAKE_STAGE_CLONE_STATUS_CLOSE);
    }
    if (fake->fail_stage == FAKE_STAGE_CLONE_STDOUT_CLOSE) {
        return fake_result(fake, FAKE_STAGE_CLONE_STDOUT_CLOSE);
    }
    if (fake->fail_stage == FAKE_STAGE_CLONE_STDERR_CLOSE) {
        return fake_result(fake, FAKE_STAGE_CLONE_STDERR_CLOSE);
    }
    return P6C_RESULT_OK;
}

static enum p6c_exec_confirmation fake_confirm(
    void *context, struct p6c_operation *operation)
{
    struct fake_process *fake = context;

    (void)operation;
    return fake->confirmation;
}

static enum p6c_result fake_signal(
    void *context, struct p6c_operation *operation)
{
    (void)operation;
    return fake_result(context, FAKE_STAGE_SIGNAL);
}

static enum p6c_result fake_wait_terminal(
    void *context, struct p6c_operation *operation)
{
    (void)operation;
    return fake_result(context, FAKE_STAGE_WAIT_TERMINAL);
}

static enum p6c_result fake_grace(
    void *context, struct p6c_operation *operation)
{
    (void)operation;
    return fake_result(context, FAKE_STAGE_GRACE);
}

static enum p6c_result fake_freeze(
    void *context, struct p6c_operation *operation)
{
    (void)operation;
    return fake_result(context, FAKE_STAGE_FREEZE);
}

static enum p6c_result fake_kill(
    void *context, struct p6c_operation *operation)
{
    (void)operation;
    return fake_result(context, FAKE_STAGE_KILL);
}

static enum p6c_result fake_empty(
    void *context, struct p6c_operation *operation)
{
    (void)operation;
    return fake_result(context, FAKE_STAGE_EMPTY);
}

static enum p6c_result fake_observe(
    void *context, struct p6c_operation *operation,
    int32_t *exit_status)
{
    enum p6c_result result;

    (void)operation;
    result = fake_result(context, FAKE_STAGE_OBSERVE);
    if (result == P6C_RESULT_OK) {
        *exit_status = INT32_C(17);
    }
    return result;
}

static enum p6c_result fake_reap(
    void *context, struct p6c_operation *operation)
{
    (void)operation;
    return fake_result(context, FAKE_STAGE_REAP);
}

static enum p6c_result fake_transcripts(
    void *context, struct p6c_operation *operation)
{
    enum p6c_result result =
        fake_result(context, FAKE_STAGE_TRANSCRIPTS);

    if (result != P6C_RESULT_OK) {
        return result;
    }
    p6c_transcript_observe_eof(operation->stdout_transcript);
    p6c_transcript_prove_cleanup(operation->stdout_transcript);
    p6c_transcript_observe_eof(operation->stderr_transcript);
    p6c_transcript_prove_cleanup(operation->stderr_transcript);
    if ((p6c_transcript_finalize(operation->stdout_transcript) !=
         P6C_RESULT_OK) ||
        (p6c_transcript_finalize(operation->stderr_transcript) !=
         P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result fake_remove(
    void *context, struct p6c_operation *operation)
{
    struct fake_process *fake = context;
    enum p6c_result result;
    char discovered_name[NAME_MAX + 1U];
    const char *removal_name = fake->removal_name;

    result = fake_result(fake, FAKE_STAGE_REMOVE);
    memset(discovered_name, 0, sizeof(discovered_name));
    if ((result == P6C_RESULT_OK) &&
        (fake->removal_root != NULL) &&
        (removal_name == NULL) &&
        (operation != NULL) && (operation->cgroup != NULL)) {
        int duplicate = dup(fake->removal_root->descriptor);
        DIR *directory = (duplicate < 0) ? NULL : fdopendir(duplicate);
        struct dirent *entry;

        if (directory == NULL) {
            if (duplicate >= 0) {
                (void)close(duplicate);
            }
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        while ((entry = readdir(directory)) != NULL) {
            struct stat status;

            if ((strncmp(entry->d_name, "p6c-", 4U) != 0) ||
                (fstatat(
                     fake->removal_root->descriptor,
                     entry->d_name, &status,
                     AT_SYMLINK_NOFOLLOW) != 0) ||
                (status.st_dev != operation->cgroup->device) ||
                (status.st_ino != operation->cgroup->inode)) {
                continue;
            }
            if (strlen(entry->d_name) >= sizeof(discovered_name)) {
                (void)closedir(directory);
                return P6C_RESULT_RECOVERY_REQUIRED;
            }
            (void)strcpy(discovered_name, entry->d_name);
            removal_name = discovered_name;
            break;
        }
        if ((closedir(directory) != 0) || (removal_name == NULL)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
    }
    if ((result == P6C_RESULT_OK) &&
        (fake->removal_root != NULL) &&
        (removal_name != NULL)) {
        char events_path[64];
        int unlink_result;

        if ((operation == NULL) || (operation->cgroup == NULL) ||
            (snprintf(
                 events_path, sizeof(events_path), "%s/cgroup.events",
                 removal_name) < 0)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
        unlink_result = unlinkat(
            fake->removal_root->descriptor, events_path, 0);
        if (((unlink_result != 0) && (errno != ENOENT)) ||
            (p6c_owned_fd_close(operation->cgroup) != P6C_RESULT_OK) ||
            (unlinkat(
                 fake->removal_root->descriptor,
                 removal_name, AT_REMOVEDIR) != 0) ||
            (fsync(fake->removal_root->descriptor) != 0)) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
    }
    if ((result == P6C_RESULT_OK) &&
        fake->fail_result_journal_after_remove) {
        p6c_test_failpoint_set(P6C_FAIL_JOURNAL_WRITE);
    }
    return result;
}

static struct p6c_process_adapter fake_adapter(
    struct fake_process *fake)
{
    struct p6c_process_adapter adapter;

    memset(&adapter, 0, sizeof(adapter));
    adapter.context = fake;
    adapter.clone_child = fake_clone;
    adapter.confirm_exec = fake_confirm;
    adapter.wait_terminal = fake_wait_terminal;
    adapter.signal_term = fake_signal;
    adapter.wait_grace = fake_grace;
    adapter.freeze_cgroup = fake_freeze;
    adapter.kill_cgroup = fake_kill;
    adapter.wait_cgroup_empty = fake_empty;
    adapter.observe_child = fake_observe;
    adapter.reap_child = fake_reap;
    adapter.finalize_transcripts = fake_transcripts;
    adapter.remove_cgroup = fake_remove;
    return adapter;
}

struct real_child_process {
    pid_t child;
    int service_socket;
    unsigned int kill_calls;
    unsigned int empty_calls;
    unsigned int observe_calls;
    unsigned int reap_calls;
    unsigned int transcript_calls;
};

static enum p6c_result real_child_clone(
    void *context, struct p6c_operation *operation)
{
    struct real_child_process *real = context;
    pid_t child = fork();
    int descriptor;

    if (child < 0) {
        return P6C_RESULT_SYSTEM;
    }
    if (child == 0) {
        static const uint8_t OUTPUT[] =
            "real-disconnect-child-output\n";

        (void)close(real->service_socket);
        (void)close(operation->status_channel->first.descriptor);
        (void)close(operation->status_channel->second.descriptor);
        (void)close(operation->stdout_channel->first.descriptor);
        (void)close(operation->stderr_channel->first.descriptor);
        if (write(
                operation->stdout_channel->second.descriptor,
                OUTPUT, sizeof(OUTPUT) - 1U) !=
            (ssize_t)(sizeof(OUTPUT) - 1U)) {
            _exit(111);
        }
        (void)close(operation->stdout_channel->second.descriptor);
        (void)close(operation->stderr_channel->second.descriptor);
        for (;;) {
            (void)pause();
        }
    }
    real->child = child;
    operation->child_pid = child;
    operation->physical_custody = P6C_CHILD_PID_WAITABLE;
    descriptor = (int)syscall(SYS_pidfd_open, child, 0U);
    if (descriptor < 0) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    if (p6c_owned_fd_acquire(
            &operation->pidfd, descriptor,
            P6C_DESCRIPTOR_PIDFD) != P6C_RESULT_OK) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    operation->pidfd.type = P6C_DESCRIPTOR_PIDFD;
    operation->physical_custody = P6C_CHILD_PIDFD_OWNED;
    if ((p6c_owned_fd_close(&operation->status_channel->second) !=
         P6C_RESULT_OK) ||
        (p6c_owned_fd_close(&operation->stdout_channel->second) !=
         P6C_RESULT_OK) ||
        (p6c_owned_fd_close(&operation->stderr_channel->second) !=
         P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

static enum p6c_exec_confirmation real_child_confirm(
    void *context, struct p6c_operation *operation)
{
    (void)context;
    return p6c_confirm_exec_status(operation, UINT32_C(1000));
}

static enum p6c_result real_child_signal(
    void *context, struct p6c_operation *operation)
{
    (void)context;
    (void)operation;
    return P6C_RESULT_OK;
}

static enum p6c_result real_child_grace(
    void *context, struct p6c_operation *operation)
{
    (void)context;
    (void)operation;
    return P6C_RESULT_TIMEOUT;
}

static enum p6c_result real_child_freeze(
    void *context, struct p6c_operation *operation)
{
    (void)context;
    (void)operation;
    return P6C_RESULT_OK;
}

static enum p6c_result real_child_kill(
    void *context, struct p6c_operation *operation)
{
    struct real_child_process *real = context;

    ++real->kill_calls;
    return p6c_pidfd_signal(&operation->pidfd, SIGKILL);
}

static enum p6c_result real_child_empty(
    void *context, struct p6c_operation *operation)
{
    struct real_child_process *real = context;
    struct pollfd descriptor;
    int result;

    ++real->empty_calls;
    memset(&descriptor, 0, sizeof(descriptor));
    descriptor.fd = operation->pidfd.descriptor;
    descriptor.events = (short)(POLLIN | POLLHUP | POLLERR);
    do {
        result = poll(&descriptor, 1U, 1000);
    } while ((result < 0) && (errno == EINTR));
    return (result > 0) ? P6C_RESULT_OK : P6C_RESULT_TIMEOUT;
}

static enum p6c_result real_child_observe(
    void *context, struct p6c_operation *operation,
    int32_t *exit_status)
{
    struct real_child_process *real = context;

    ++real->observe_calls;
    return p6c_pidfd_observe(&operation->pidfd, exit_status);
}

static enum p6c_result real_child_reap(
    void *context, struct p6c_operation *operation)
{
    struct real_child_process *real = context;

    ++real->reap_calls;
    return p6c_pidfd_reap(&operation->pidfd);
}

static enum p6c_result real_child_drain(
    struct p6c_owned_fd *source,
    struct p6c_transcript *transcript)
{
    bool eof = false;

    if (!p6c_owned_fd_is_live(source)) {
        return transcript->eof_observed ?
                   P6C_RESULT_OK :
                   P6C_RESULT_RECOVERY_REQUIRED;
    }
    while (!eof) {
        uint8_t input[4096];
        ssize_t amount = read(source->descriptor, input, sizeof(input));

        if (amount > 0) {
            if (p6c_transcript_ingest(
                    transcript, input, (size_t)amount) !=
                P6C_RESULT_OK) {
                return P6C_RESULT_RECOVERY_REQUIRED;
            }
        } else if (amount == 0) {
            eof = true;
        } else if ((errno == EAGAIN) || (errno == EWOULDBLOCK)) {
            struct pollfd descriptor;

            memset(&descriptor, 0, sizeof(descriptor));
            descriptor.fd = source->descriptor;
            descriptor.events = (short)(POLLIN | POLLHUP | POLLERR);
            if (poll(&descriptor, 1U, 1000) <= 0) {
                return P6C_RESULT_TIMEOUT;
            }
        } else if (errno != EINTR) {
            return P6C_RESULT_RECOVERY_REQUIRED;
        }
    }
    p6c_transcript_observe_eof(transcript);
    return P6C_RESULT_OK;
}

static enum p6c_result real_child_transcripts(
    void *context, struct p6c_operation *operation)
{
    struct real_child_process *real = context;

    ++real->transcript_calls;
    if ((real_child_drain(
             &operation->stdout_channel->first,
             operation->stdout_transcript) != P6C_RESULT_OK) ||
        (real_child_drain(
             &operation->stderr_channel->first,
             operation->stderr_transcript) != P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    p6c_transcript_prove_cleanup(operation->stdout_transcript);
    p6c_transcript_prove_cleanup(operation->stderr_transcript);
    if ((p6c_transcript_finalize(operation->stdout_transcript) !=
         P6C_RESULT_OK) ||
        (p6c_transcript_finalize(operation->stderr_transcript) !=
         P6C_RESULT_OK)) {
        return P6C_RESULT_RECOVERY_REQUIRED;
    }
    return P6C_RESULT_OK;
}

static enum p6c_result real_child_remove(
    void *context, struct p6c_operation *operation)
{
    (void)context;
    return p6c_owned_fd_close(operation->cgroup);
}

static struct p6c_process_adapter real_child_adapter(
    struct real_child_process *real)
{
    struct p6c_process_adapter adapter;

    memset(&adapter, 0, sizeof(adapter));
    adapter.context = real;
    adapter.clone_child = real_child_clone;
    adapter.confirm_exec = real_child_confirm;
    adapter.signal_term = real_child_signal;
    adapter.wait_grace = real_child_grace;
    adapter.freeze_cgroup = real_child_freeze;
    adapter.kill_cgroup = real_child_kill;
    adapter.wait_cgroup_empty = real_child_empty;
    adapter.observe_child = real_child_observe;
    adapter.reap_child = real_child_reap;
    adapter.finalize_transcripts = real_child_transcripts;
    adapter.remove_cgroup = real_child_remove;
    return adapter;
}

static int operation_fixture_create(struct operation_fixture *fixture)
{
    uint8_t cgroup_created[P6C_CGROUP_CREATED_PAYLOAD_BYTES];
    int descriptor;
    static const char CGROUP_NAME[] =
        "p6c-81818181818181818181818181818181";

    memset(fixture, 0, sizeof(*fixture));
    p6c_owned_fd_reset(&fixture->executable.file);
    p6c_owned_fd_reset(&fixture->cgroup);
    p6c_owned_fd_reset(&fixture->status_channel.first);
    p6c_owned_fd_reset(&fixture->status_channel.second);
    p6c_owned_fd_reset(&fixture->stdout_channel.first);
    p6c_owned_fd_reset(&fixture->stdout_channel.second);
    p6c_owned_fd_reset(&fixture->stderr_channel.first);
    p6c_owned_fd_reset(&fixture->stderr_channel.second);
    if (test_directory_create(&fixture->directory) != EXIT_SUCCESS) {
        return EXIT_FAILURE;
    }
    test_fill_identity(fixture->operation_id, UINT8_C(0x81));
    test_fill_identity(fixture->recovery_token, UINT8_C(0x82));
    if (p6c_journal_create(&fixture->directory.owner, "process.journal",
                           fixture->operation_id, getuid(),
                           &fixture->journal) != P6C_RESULT_OK) {
        return EXIT_FAILURE;
    }
    if ((p6c_journal_append(&fixture->journal, P6C_OPERATION_RESERVED,
                            NULL, 0U) != P6C_RESULT_OK) ||
        (p6c_journal_append(&fixture->journal,
                            P6C_OPERATION_EXECUTABLE_PINNED,
                            NULL, 0U) != P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    descriptor = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if ((descriptor < 0) ||
        (p6c_owned_fd_acquire(&fixture->executable.file, descriptor,
                              P6C_DESCRIPTOR_REGULAR) != P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    descriptor = dup(fixture->directory.owner.descriptor);
    if ((descriptor < 0) ||
        (p6c_owned_fd_acquire(&fixture->cgroup, descriptor,
                              P6C_DESCRIPTOR_CGROUP) != P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    fixture->cgroup.type = P6C_DESCRIPTOR_CGROUP;
    memset(cgroup_created, 0, sizeof(cgroup_created));
    memcpy(
        &cgroup_created[P6C_CGROUP_CREATED_NAME_OFFSET],
        CGROUP_NAME, P6C_CGROUP_NAME_BYTES - 1U);
    test_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_DEVICE_OFFSET],
        (uint64_t)fixture->cgroup.device);
    test_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_INODE_OFFSET],
        (uint64_t)fixture->cgroup.inode);
    if ((p6c_journal_append_cgroup_allocation_intent(
             &fixture->journal, CGROUP_NAME) != P6C_RESULT_OK) ||
        (p6c_journal_append(
             &fixture->journal, P6C_OPERATION_CGROUP_CREATED,
             cgroup_created, sizeof(cgroup_created)) != P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    if ((p6c_owned_pipe_create(&fixture->status_channel) !=
         P6C_RESULT_OK) ||
        (p6c_owned_pipe_create(&fixture->stdout_channel) !=
         P6C_RESULT_OK) ||
        (p6c_owned_pipe_create(&fixture->stderr_channel) !=
         P6C_RESULT_OK) ||
        (p6c_transcript_create(&fixture->directory.owner,
                               P6C_STREAM_STDOUT, UINT64_C(1024), true,
                               &fixture->stdout_transcript) !=
         P6C_RESULT_OK) ||
        (p6c_transcript_create(&fixture->directory.owner,
                               P6C_STREAM_STDERR, UINT64_C(1024), true,
                               &fixture->stderr_transcript) !=
         P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    if (p6c_operation_init(
            &fixture->operation, fixture->operation_id,
            fixture->recovery_token, &fixture->journal,
            &fixture->executable, &fixture->cgroup,
            &fixture->status_channel, &fixture->stdout_channel,
            &fixture->stderr_channel, &fixture->stdout_transcript,
            &fixture->stderr_transcript) != P6C_RESULT_OK) {
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static int operation_fixture_close(struct operation_fixture *fixture)
{
    int result = EXIT_SUCCESS;

    if (p6c_owned_fd_is_live(&fixture->operation.pidfd) &&
        (p6c_owned_fd_close(&fixture->operation.pidfd) != P6C_RESULT_OK)) {
        result = EXIT_FAILURE;
    }
    if (p6c_owned_fd_is_live(&fixture->executable.file) &&
        (p6c_executable_close(&fixture->executable) != P6C_RESULT_OK)) {
        result = EXIT_FAILURE;
    }
    if (p6c_owned_fd_is_live(&fixture->cgroup) &&
        (p6c_owned_fd_close(&fixture->cgroup) != P6C_RESULT_OK)) {
        result = EXIT_FAILURE;
    }
    if (p6c_owned_pair_close(&fixture->status_channel) != P6C_RESULT_OK) {
        result = EXIT_FAILURE;
    }
    if (p6c_owned_pair_close(&fixture->stdout_channel) != P6C_RESULT_OK) {
        result = EXIT_FAILURE;
    }
    if (p6c_owned_pair_close(&fixture->stderr_channel) != P6C_RESULT_OK) {
        result = EXIT_FAILURE;
    }
    if (p6c_transcript_close(&fixture->stdout_transcript) !=
        P6C_RESULT_OK) {
        result = EXIT_FAILURE;
    }
    if (p6c_transcript_close(&fixture->stderr_transcript) !=
        P6C_RESULT_OK) {
        result = EXIT_FAILURE;
    }
    if (p6c_journal_close(&fixture->journal) != P6C_RESULT_OK) {
        result = EXIT_FAILURE;
    }
    if (test_directory_close(&fixture->directory, "process.journal") !=
        EXIT_SUCCESS) {
        result = EXIT_FAILURE;
    }
    return result;
}

static int case_process_success_stop_ack(void)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    uint8_t wrong[P6C_OPERATION_ID_BYTES];

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    CHECK(fixture.operation.state == P6C_OPERATION_RUNNING);
    CHECK(fixture.operation.authority_retained);
    CHECK(p6c_operation_stop(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    CHECK(fixture.operation.state == P6C_OPERATION_RESULT_RETAINED);
    CHECK(fixture.operation.exit_status == INT32_C(17));
    memset(wrong, UINT8_C(0), sizeof(wrong));
    CHECK(p6c_operation_ack(&fixture.operation, wrong,
                            fixture.recovery_token) ==
          P6C_RESULT_UNAUTHORIZED);
    CHECK(fixture.operation.authority_retained);
    CHECK(p6c_operation_ack(&fixture.operation, fixture.operation_id,
                            fixture.recovery_token) ==
          P6C_RESULT_OK);
    CHECK(fixture.operation.state == P6C_OPERATION_ACKNOWLEDGED);
    CHECK(!fixture.operation.authority_retained);
    CHECK(p6c_operation_ack(&fixture.operation, fixture.operation_id,
                            fixture.recovery_token) ==
          P6C_RESULT_OK);
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_process_repeated_stop(void)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    fake.fail_stage = FAKE_STAGE_KILL;
    fake.fail_result = P6C_RESULT_SYSTEM;
    CHECK(p6c_operation_stop(&fixture.operation, &adapter) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(fixture.operation.state == P6C_OPERATION_RECOVERY_REQUIRED);
    CHECK(fixture.operation.authority_retained);
    fake.fail_stage = FAKE_STAGE_NONE;
    CHECK(p6c_operation_stop(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    CHECK(fixture.operation.state == P6C_OPERATION_RESULT_RETAINED);
    CHECK(fake.calls[FAKE_STAGE_KILL] == 2U);
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int process_journal_boundary_case(
    bool during_start, unsigned int successful_fsyncs,
    enum p6c_operation_state expected_state)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    enum p6c_journal_recovery recovery;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    if (!during_start) {
        CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
              P6C_RESULT_OK);
    }
    p6c_test_failpoint_set_after(
        P6C_FAIL_JOURNAL_FSYNC, successful_fsyncs);
    CHECK((during_start ?
               p6c_operation_start(&fixture.operation, &adapter) :
               p6c_operation_stop(&fixture.operation, &adapter)) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(fixture.operation.authority_retained);
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(p6c_journal_close(&fixture.journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_recover(
              &fixture.directory.owner, "process.journal",
              fixture.operation_id, getuid(), &fixture.journal,
              &recovery) == P6C_RESULT_OK);
    CHECK(recovery == P6C_JOURNAL_COMPLETE);
    CHECK(fixture.journal.durable_state == expected_state);
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_boundary_child_cloned(void)
{
    return process_journal_boundary_case(
        true, 0U, P6C_OPERATION_CHILD_CLONED);
}

static int case_boundary_exec_confirmed(void)
{
    return process_journal_boundary_case(
        true, 1U, P6C_OPERATION_EXEC_CONFIRMED);
}

static int case_boundary_running(void)
{
    return process_journal_boundary_case(
        true, 2U, P6C_OPERATION_RUNNING);
}

static int case_boundary_stop_requested(void)
{
    return process_journal_boundary_case(
        false, 0U, P6C_OPERATION_STOP_REQUESTED);
}

static int case_boundary_cgroup_killed(void)
{
    return process_journal_boundary_case(
        false, 1U, P6C_OPERATION_CGROUP_KILLED);
}

static int case_boundary_cgroup_empty(void)
{
    return process_journal_boundary_case(
        false, 2U, P6C_OPERATION_CGROUP_EMPTY);
}

static int case_boundary_child_exit_observed(void)
{
    return process_journal_boundary_case(
        false, 3U, P6C_OPERATION_CHILD_EXIT_OBSERVED);
}

static int case_boundary_child_reaped(void)
{
    return process_journal_boundary_case(
        false, 4U, P6C_OPERATION_CHILD_REAPED);
}

static int case_boundary_transcripts_final(void)
{
    return process_journal_boundary_case(
        false, 5U, P6C_OPERATION_TRANSCRIPTS_FINAL);
}

static int case_boundary_result_retained(void)
{
    return process_journal_boundary_case(
        false, 6U, P6C_OPERATION_RESULT_RETAINED);
}

static int case_operation_acquisition_failures(void)
{
    struct operation_fixture fixture;
    struct p6c_operation operation;
    struct p6c_owned_fd *owners[10];
    size_t index;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    owners[0] = &fixture.executable.file;
    owners[1] = &fixture.cgroup;
    owners[2] = &fixture.status_channel.first;
    owners[3] = &fixture.status_channel.second;
    owners[4] = &fixture.stdout_channel.first;
    owners[5] = &fixture.stdout_channel.second;
    owners[6] = &fixture.stderr_channel.first;
    owners[7] = &fixture.stderr_channel.second;
    owners[8] = &fixture.stdout_transcript.sink;
    owners[9] = &fixture.stderr_transcript.sink;
    for (index = 0U; index < sizeof(owners) / sizeof(owners[0]); ++index) {
        enum p6c_descriptor_lifecycle lifecycle = owners[index]->lifecycle;

        owners[index]->lifecycle = P6C_DESCRIPTOR_EMPTY;
        CHECK(p6c_operation_init(
                  &operation, fixture.operation_id, fixture.recovery_token,
                  &fixture.journal, &fixture.executable, &fixture.cgroup,
                  &fixture.status_channel, &fixture.stdout_channel,
                  &fixture.stderr_channel, &fixture.stdout_transcript,
                  &fixture.stderr_transcript) == P6C_RESULT_INVALID);
        owners[index]->lifecycle = lifecycle;
    }
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_clone3_errno_classification(void)
{
    CHECK(p6c_classify_clone3_errno(ENOSYS) == P6C_RESULT_UNSUPPORTED);
    CHECK(p6c_classify_clone3_errno(EINVAL) == P6C_RESULT_INVALID);
    CHECK(p6c_classify_clone3_errno(EPERM) == P6C_RESULT_UNAUTHORIZED);
    CHECK(p6c_classify_clone3_errno(EINTR) == P6C_RESULT_OK);
    CHECK(p6c_classify_clone3_errno(EIO) == P6C_RESULT_SYSTEM);
    return EXIT_SUCCESS;
}

static int case_cgroup_fake_files(void)
{
    struct test_directory directory;
    bool populated;
    int descriptor;
    char value[2];
    static const uint8_t ZERO[] = "0";
    static const uint8_t EVENTS[] = "populated 0\nfrozen 1\n";
    static const uint8_t DUPLICATE[] = "populated 0\npopulated 1\n";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(test_write_file(&directory, "cgroup.freeze", ZERO,
                          sizeof(ZERO) - 1U, (mode_t)0600) ==
          EXIT_SUCCESS);
    CHECK(test_write_file(&directory, "cgroup.kill", ZERO,
                          sizeof(ZERO) - 1U, (mode_t)0600) ==
          EXIT_SUCCESS);
    CHECK(test_write_file(&directory, "cgroup.events", EVENTS,
                          sizeof(EVENTS) - 1U, (mode_t)0600) ==
          EXIT_SUCCESS);
    directory.owner.type = P6C_DESCRIPTOR_CGROUP;
    CHECK(p6c_cgroup_freeze(&directory.owner) == P6C_RESULT_OK);
    CHECK(p6c_cgroup_kill(&directory.owner) == P6C_RESULT_OK);
    descriptor = openat(directory.owner.descriptor, "cgroup.freeze",
                        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(descriptor >= 0);
    CHECK(read(descriptor, value, 1U) == 1);
    CHECK(value[0] == '1');
    CHECK(close(descriptor) == 0);
    CHECK(p6c_cgroup_is_populated(&directory.owner, &populated) ==
          P6C_RESULT_OK);
    CHECK(!populated);
    descriptor = openat(directory.owner.descriptor, "cgroup.events",
                        O_WRONLY | O_TRUNC | O_CLOEXEC | O_NOFOLLOW);
    CHECK(descriptor >= 0);
    CHECK(write(descriptor, DUPLICATE, sizeof(DUPLICATE) - 1U) ==
          (ssize_t)(sizeof(DUPLICATE) - 1U));
    CHECK(close(descriptor) == 0);
    CHECK(p6c_cgroup_is_populated(&directory.owner, &populated) ==
          P6C_RESULT_MALFORMED);
    CHECK(unlinkat(directory.owner.descriptor, "cgroup.events", 0) == 0);
    CHECK(unlinkat(directory.owner.descriptor, "cgroup.kill", 0) == 0);
    CHECK(unlinkat(directory.owner.descriptor, "cgroup.freeze", 0) == 0);
    directory.owner.type = P6C_DESCRIPTOR_DIRECTORY;
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_cgroup_remove_substitution_window(void)
{
    struct test_directory directory;
    struct p6c_owned_fd cgroup;
    struct stat original_status;
    struct stat replacement_status;
    struct stat observed;
    int descriptor;
    static const char ORIGINAL[] =
        "p6c-11111111111111111111111111111111";
    static const char REPLACEMENT[] =
        "p6c-22222222222222222222222222222222";
    static const char DISPLACED[] = "p6q-displaced-generation";
    static const uint8_t EVENTS[] = "populated 0\n";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    directory.owner.type = P6C_DESCRIPTOR_CGROUP;
    CHECK(mkdirat(
              directory.owner.descriptor, ORIGINAL,
              (mode_t)0700) == 0);
    CHECK(mkdirat(
              directory.owner.descriptor, REPLACEMENT,
              (mode_t)0700) == 0);
    CHECK(p6c_openat2_owned(
              &directory.owner, ORIGINAL,
              O_RDONLY | O_DIRECTORY | O_NOFOLLOW, (mode_t)0,
              P6C_DESCRIPTOR_CGROUP, &cgroup) == P6C_RESULT_OK);
    cgroup.type = P6C_DESCRIPTOR_CGROUP;
    descriptor = openat(
        cgroup.descriptor, "cgroup.events",
        O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
        (mode_t)0600);
    CHECK(descriptor >= 0);
    CHECK(write(descriptor, EVENTS, sizeof(EVENTS) - 1U) ==
          (ssize_t)(sizeof(EVENTS) - 1U));
    CHECK(close(descriptor) == 0);
    CHECK(fstat(cgroup.descriptor, &original_status) == 0);
    CHECK(fstatat(
              directory.owner.descriptor, REPLACEMENT,
              &replacement_status, AT_SYMLINK_NOFOLLOW) == 0);
    p6c_test_cgroup_remove_substitution_set(
        directory.owner.descriptor, REPLACEMENT, DISPLACED);

    CHECK(p6c_cgroup_remove(
              &directory.owner, ORIGINAL, &cgroup) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(p6c_owned_fd_is_live(&cgroup));
    CHECK(cgroup.lifecycle == P6C_DESCRIPTOR_RECOVERY);
    CHECK(fstat(cgroup.descriptor, &observed) == 0);
    CHECK(observed.st_dev == original_status.st_dev);
    CHECK(observed.st_ino == original_status.st_ino);
    CHECK(fstatat(
              directory.owner.descriptor, ORIGINAL, &observed,
              AT_SYMLINK_NOFOLLOW) == 0);
    CHECK(observed.st_dev == replacement_status.st_dev);
    CHECK(observed.st_ino == replacement_status.st_ino);
    CHECK(fstatat(
              directory.owner.descriptor, DISPLACED, &observed,
              AT_SYMLINK_NOFOLLOW) == 0);
    CHECK(observed.st_dev == original_status.st_dev);
    CHECK(observed.st_ino == original_status.st_ino);

    CHECK(p6c_owned_fd_close(&cgroup) == P6C_RESULT_OK);
    CHECK(unlinkat(
              directory.owner.descriptor, DISPLACED,
              AT_REMOVEDIR) != 0);
    CHECK(errno == ENOTEMPTY);
    descriptor = openat(
        directory.owner.descriptor, DISPLACED,
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(descriptor >= 0);
    CHECK(unlinkat(descriptor, "cgroup.events", 0) == 0);
    CHECK(close(descriptor) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor, DISPLACED,
              AT_REMOVEDIR) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor, ORIGINAL,
              AT_REMOVEDIR) == 0);
    directory.owner.type = P6C_DESCRIPTOR_DIRECTORY;
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int process_confirmation_case(enum p6c_exec_confirmation confirmation)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = confirmation;
    adapter = fake_adapter(&fake);
    CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(fixture.operation.state == P6C_OPERATION_RECOVERY_REQUIRED);
    CHECK(fixture.operation.resume_state == P6C_OPERATION_CHILD_CLONED);
    CHECK(fixture.operation.authority_retained);
    CHECK(p6c_owned_fd_is_live(&fixture.operation.pidfd));
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_exec_marker_bytes(void)
{
    return process_confirmation_case(P6C_EXEC_CONFIRM_BYTES);
}

static int case_exec_marker_partial(void)
{
    return process_confirmation_case(P6C_EXEC_CONFIRM_PARTIAL);
}

static int case_exec_marker_timeout(void)
{
    return process_confirmation_case(P6C_EXEC_CONFIRM_TIMEOUT);
}

static int case_exec_marker_quick_exit(void)
{
    return process_confirmation_case(P6C_EXEC_CONFIRM_QUICK_EXIT);
}

static int case_exec_marker_error(void)
{
    return process_confirmation_case(P6C_EXEC_CONFIRM_ERROR);
}

static int post_clone_cleanup_case(
    enum fake_stage clone_failure, bool pidfd_expected)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    fake.fail_stage = clone_failure;
    fake.fail_result = P6C_RESULT_RECOVERY_REQUIRED;
    adapter = fake_adapter(&fake);
    CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(fixture.operation.resume_state ==
          P6C_OPERATION_CHILD_CLONED);
    CHECK(fixture.operation.physical_custody ==
          (pidfd_expected ? P6C_CHILD_PIDFD_OWNED :
                            P6C_CHILD_PID_WAITABLE));
    fake.fail_stage = FAKE_STAGE_NONE;
    CHECK(p6c_operation_stop(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    CHECK(fake.calls[FAKE_STAGE_KILL] == 1U);
    CHECK(fake.calls[FAKE_STAGE_EMPTY] == 1U);
    CHECK(fake.calls[FAKE_STAGE_OBSERVE] == 1U);
    CHECK(fake.calls[FAKE_STAGE_REAP] == 1U);
    CHECK(fake.calls[FAKE_STAGE_TRANSCRIPTS] == 1U);
    CHECK(fake.calls[FAKE_STAGE_REMOVE] == 1U);
    CHECK(p6c_operation_ack(
              &fixture.operation, fixture.operation_id,
              fixture.recovery_token) == P6C_RESULT_OK);
    CHECK(!p6c_owned_fd_is_live(&fixture.operation.pidfd));
    CHECK(!p6c_owned_fd_is_live(&fixture.cgroup));
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_post_clone_pidfd_acquire_failure(void)
{
    return post_clone_cleanup_case(
        FAKE_STAGE_CLONE_PIDFD_ACQUIRE, false);
}

static int case_post_clone_status_writer_close_failure(void)
{
    return post_clone_cleanup_case(
        FAKE_STAGE_CLONE_STATUS_CLOSE, true);
}

static int case_post_clone_stdout_writer_close_failure(void)
{
    return post_clone_cleanup_case(
        FAKE_STAGE_CLONE_STDOUT_CLOSE, true);
}

static int case_post_clone_stderr_writer_close_failure(void)
{
    return post_clone_cleanup_case(
        FAKE_STAGE_CLONE_STDERR_CLOSE, true);
}

static int case_post_clone_child_journal_failure_cleanup(void)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_failpoint_set(P6C_FAIL_JOURNAL_FSYNC);
    CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(fixture.operation.physical_custody ==
          P6C_CHILD_PIDFD_OWNED);
    CHECK(p6c_operation_stop(&fixture.operation, &adapter) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(fake.calls[FAKE_STAGE_KILL] == 1U);
    CHECK(fake.calls[FAKE_STAGE_EMPTY] == 1U);
    CHECK(fake.calls[FAKE_STAGE_OBSERVE] == 1U);
    CHECK(fake.calls[FAKE_STAGE_REAP] == 1U);
    CHECK(fake.calls[FAKE_STAGE_TRANSCRIPTS] == 1U);
    CHECK(fake.calls[FAKE_STAGE_REMOVE] == 0U);
    CHECK(fixture.stdout_transcript.finalized);
    CHECK(fixture.stderr_transcript.finalized);
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int process_stop_failure_case(enum fake_stage stage,
                                     enum p6c_result failure)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    fake.fail_stage = stage;
    fake.fail_result = failure;
    CHECK(p6c_operation_stop(&fixture.operation, &adapter) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(fixture.operation.state == P6C_OPERATION_RECOVERY_REQUIRED);
    CHECK(fixture.operation.authority_retained);
    CHECK(p6c_owned_fd_is_live(&fixture.operation.pidfd));
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_stop_freeze_error(void)
{
    return process_stop_failure_case(FAKE_STAGE_FREEZE, P6C_RESULT_SYSTEM);
}

static int case_stop_signal_error(void)
{
    return process_stop_failure_case(FAKE_STAGE_SIGNAL, P6C_RESULT_SYSTEM);
}

static int case_stop_grace_error(void)
{
    return process_stop_failure_case(FAKE_STAGE_GRACE, P6C_RESULT_SYSTEM);
}

static int case_stop_kill_error(void)
{
    return process_stop_failure_case(FAKE_STAGE_KILL, P6C_RESULT_SYSTEM);
}

static int case_stop_populated_timeout(void)
{
    return process_stop_failure_case(FAKE_STAGE_EMPTY, P6C_RESULT_TIMEOUT);
}

static int case_stop_observe_error(void)
{
    return process_stop_failure_case(FAKE_STAGE_OBSERVE, P6C_RESULT_SYSTEM);
}

static int case_stop_reap_error(void)
{
    return process_stop_failure_case(FAKE_STAGE_REAP, P6C_RESULT_SYSTEM);
}

static int case_stop_remove_error(void)
{
    return process_stop_failure_case(FAKE_STAGE_REMOVE, P6C_RESULT_SYSTEM);
}

static int case_stop_transcript_error(void)
{
    return process_stop_failure_case(
        FAKE_STAGE_TRANSCRIPTS, P6C_RESULT_RECOVERY_REQUIRED);
}

static int case_removal_intent_failure_prevents_remove(void)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    p6c_test_failpoint_set(P6C_FAIL_REMOVAL_INTENT_JOURNAL);
    CHECK(p6c_operation_stop(&fixture.operation, &adapter) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(fake.calls[FAKE_STAGE_REMOVE] == 0U);
    CHECK(!fixture.journal.cgroup_removal_intent);
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_result_append_failure_after_remove(void)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    enum p6c_journal_recovery recovery;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    fake.fail_result_journal_after_remove = true;
    adapter = fake_adapter(&fake);
    CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    CHECK(p6c_operation_stop(&fixture.operation, &adapter) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(fake.calls[FAKE_STAGE_REMOVE] == 1U);
    CHECK(fixture.journal.cgroup_removal_intent);
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(p6c_journal_close(&fixture.journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_recover(
              &fixture.directory.owner, "process.journal",
              fixture.operation_id, getuid(), &fixture.journal,
              &recovery) == P6C_RESULT_OK);
    CHECK(recovery == P6C_JOURNAL_COMPLETE);
    CHECK(fixture.journal.durable_state ==
          P6C_OPERATION_TRANSCRIPTS_FINAL);
    CHECK(fixture.journal.cgroup_removal_intent);
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int journal_digest_rebind_case(uint16_t target_type)
{
    struct operation_fixture fixture;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    enum p6c_journal_recovery recovery;
    uint64_t record_count;
    uint64_t index;
    bool changed = false;

    CHECK(operation_fixture_create(&fixture) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    CHECK(p6c_operation_start(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    CHECK(p6c_operation_stop(&fixture.operation, &adapter) ==
          P6C_RESULT_OK);
    record_count = fixture.journal.next_sequence - UINT64_C(1);
    for (index = UINT64_C(0); index < record_count; ++index) {
        uint8_t record[P6C_JOURNAL_RECORD_BYTES];
        uint16_t record_type;
        off_t offset = (off_t)(
            index * (uint64_t)P6C_JOURNAL_RECORD_BYTES);

        CHECK(pread(
                  fixture.journal.file.descriptor, record,
                  sizeof(record), offset) ==
              (ssize_t)sizeof(record));
        record_type = (uint16_t)(
            ((uint16_t)record[10] << 8) | (uint16_t)record[11]);
        if (record_type != target_type) {
            continue;
        }
        record[72] ^= UINT8_C(0x01);
        test_rehash_journal_payload_and_record(record);
        CHECK(pwrite(
                  fixture.journal.file.descriptor, record,
                  sizeof(record), offset) ==
              (ssize_t)sizeof(record));
        CHECK(fsync(fixture.journal.file.descriptor) == 0);
        changed = true;
        break;
    }
    CHECK(changed);
    CHECK(p6c_journal_close(&fixture.journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_recover(
              &fixture.directory.owner, "process.journal",
              fixture.operation_id, getuid(), &fixture.journal,
              &recovery) == P6C_RESULT_RECOVERY_REQUIRED);
    CHECK(recovery == P6C_JOURNAL_INVALID);
    CHECK(operation_fixture_close(&fixture) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_retained_digest_record_rebind(void)
{
    return journal_digest_rebind_case(
        P6C_JOURNAL_TRANSCRIPT_DIGESTS);
}

static int case_full_stream_digest_independent_tamper(void)
{
    return journal_digest_rebind_case(
        (uint16_t)P6C_OPERATION_TRANSCRIPTS_FINAL);
}

static int case_disconnect_cleanup_failure_matrix(void)
{
    static const enum fake_stage STAGES[] = {
        FAKE_STAGE_SIGNAL,
        FAKE_STAGE_GRACE,
        FAKE_STAGE_FREEZE,
        FAKE_STAGE_KILL,
        FAKE_STAGE_EMPTY,
        FAKE_STAGE_OBSERVE,
        FAKE_STAGE_REAP,
        FAKE_STAGE_TRANSCRIPTS,
        FAKE_STAGE_REMOVE
    };
    size_t index;

    for (index = 0U; index < sizeof(STAGES) / sizeof(STAGES[0]); ++index) {
        CHECK(process_stop_failure_case(
                  STAGES[index], P6C_RESULT_RECOVERY_REQUIRED) ==
              EXIT_SUCCESS);
    }
    return EXIT_SUCCESS;
}

static int service_owned_duplicate(
    const struct p6c_owned_fd *source,
    enum p6c_descriptor_type type,
    struct p6c_owned_fd *destination)
{
    int descriptor = dup(source->descriptor);

    if (descriptor < 0) {
        return EXIT_FAILURE;
    }
    p6c_owned_fd_reset(destination);
    if (p6c_owned_fd_acquire(destination, descriptor, type) !=
        P6C_RESULT_OK) {
        (void)close(descriptor);
        return EXIT_FAILURE;
    }
    destination->type = type;
    return EXIT_SUCCESS;
}

static size_t service_store_field(
    uint8_t *output, uint16_t field_id, const uint8_t *value,
    size_t value_length)
{
    p6c_store_u16_be(output, field_id);
    p6c_store_u16_be(&output[2], UINT16_C(0));
    p6c_store_u32_be(&output[4], (uint32_t)value_length);
    if (value_length != 0U) {
        memcpy(&output[P6C_FIELD_HEADER_SIZE], value, value_length);
    }
    return P6C_FIELD_HEADER_SIZE + value_length;
}

static size_t service_build_request(
    uint8_t *packet, size_t packet_capacity, uint16_t message_type,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    const uint8_t *payload, size_t payload_size)
{
    size_t packet_size = P6C_HEADER_SIZE + payload_size;

    if ((packet == NULL) || (request_id == NULL) ||
        ((payload == NULL) && (payload_size != 0U)) ||
        (packet_size > packet_capacity) ||
        (payload_size > (size_t)UINT32_MAX)) {
        return 0U;
    }
    p6c_encode_header_v1(
        packet, message_type, request_id, (uint32_t)payload_size,
        p6c_crc32(payload, payload_size));
    if (payload_size != 0U) {
        memcpy(&packet[P6C_HEADER_SIZE], payload, payload_size);
    }
    return packet_size;
}

static size_t service_build_operation_request(
    uint8_t *packet, size_t packet_capacity, uint16_t message_type,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES])
{
    uint8_t payload[
        (2U * P6C_FIELD_HEADER_SIZE) + P6C_OPERATION_ID_BYTES +
        P6C_RECOVERY_TOKEN_BYTES];
    size_t payload_size = 0U;

    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_OPERATION_ID,
        operation_id, P6C_OPERATION_ID_BYTES);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_RECOVERY_TOKEN,
        recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    return service_build_request(
        packet, packet_capacity, message_type, request_id,
        payload, payload_size);
}

static size_t service_build_publication_request(
    uint8_t *packet, size_t packet_capacity, uint16_t message_type,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES],
    const uint8_t publication_id[P6C_SHA256_BYTES])
{
    uint8_t payload[
        (3U * P6C_FIELD_HEADER_SIZE) + P6C_OPERATION_ID_BYTES +
        P6C_RECOVERY_TOKEN_BYTES + P6C_SHA256_BYTES];
    size_t payload_size = 0U;

    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_OPERATION_ID,
        operation_id, P6C_OPERATION_ID_BYTES);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_RECOVERY_TOKEN,
        recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_PUBLICATION_ID,
        publication_id, P6C_SHA256_BYTES);
    return service_build_request(
        packet, packet_capacity, message_type, request_id,
        payload, payload_size);
}

static size_t service_build_transcript_request(
    uint8_t *packet, size_t packet_capacity,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES],
    enum p6c_stream_identity stream, uint64_t offset, uint32_t length)
{
    uint8_t payload[
        (5U * P6C_FIELD_HEADER_SIZE) + P6C_OPERATION_ID_BYTES +
        P6C_RECOVERY_TOKEN_BYTES + 1U + 8U + 4U];
    uint8_t encoded_offset[8];
    uint8_t encoded_length[4];
    uint8_t encoded_stream = (uint8_t)stream;
    size_t payload_size = 0U;
    size_t offset_index;

    for (offset_index = 0U; offset_index < 8U; ++offset_index) {
        encoded_offset[7U - offset_index] =
            (uint8_t)(offset >> (offset_index * 8U));
    }
    p6c_store_u32_be(encoded_length, length);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_OPERATION_ID,
        operation_id, P6C_OPERATION_ID_BYTES);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_RECOVERY_TOKEN,
        recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_STREAM,
        &encoded_stream, 1U);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_OFFSET,
        encoded_offset, sizeof(encoded_offset));
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_LENGTH,
        encoded_length, sizeof(encoded_length));
    return service_build_request(
        packet, packet_capacity, (uint16_t)P6C_REQUEST_READ_TRANSCRIPT,
        request_id, payload, payload_size);
}

static size_t service_build_start_request(
    uint8_t *packet, size_t packet_capacity,
    uint16_t message_type,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    const uint8_t executable_digest[P6C_SHA256_BYTES],
    const char *executable)
{
    uint8_t argv_field[4U + 4U + 8U];
    uint8_t environment_field[1024];
    uint8_t payload[1536];
    size_t payload_size = 0U;
    size_t environment_size = 4U;
    size_t executable_length = strlen(executable);
    static const uint8_t ARGUMENT[] = "approved";
    static const char *const ENVIRONMENT[] = {
        "HOME=/tmp",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "LIVE_EXECUTION_ENABLED=false",
        "LIVE_TRADING_APPROVED=false",
        "LIVE_TRADING_ENABLED=false",
        "PATH=/usr/bin:/bin",
        "TRADING_MODE=paper",
        "TRADING_PACKAGE6_APPROVAL_SHA256="
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH=/tmp/activation.json",
        "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH=/tmp/authority.json",
        "TRADING_PACKAGE6_STAGING_SCOPE=PACKAGE6_STAGING_V2",
        "TZ=UTC"
    };
    size_t environment_index;

    p6c_store_u32_be(argv_field, UINT32_C(1));
    p6c_store_u32_be(&argv_field[4], (uint32_t)(sizeof(ARGUMENT) - 1U));
    memcpy(&argv_field[8], ARGUMENT, sizeof(ARGUMENT) - 1U);
    p6c_store_u32_be(
        environment_field,
        (uint32_t)(sizeof(ENVIRONMENT) / sizeof(ENVIRONMENT[0])));
    for (environment_index = 0U;
         environment_index <
             sizeof(ENVIRONMENT) / sizeof(ENVIRONMENT[0]);
         ++environment_index) {
        size_t length = strlen(ENVIRONMENT[environment_index]);

        if (environment_size + 4U + length >
            sizeof(environment_field)) {
            return 0U;
        }
        p6c_store_u32_be(
            &environment_field[environment_size], (uint32_t)length);
        environment_size += 4U;
        memcpy(
            &environment_field[environment_size],
            ENVIRONMENT[environment_index], length);
        environment_size += length;
    }
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_OPERATION_ID,
        operation_id, P6C_OPERATION_ID_BYTES);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_OPERATION_DIGEST,
        executable_digest, P6C_SHA256_BYTES);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_EXECUTABLE,
        (const uint8_t *)executable, executable_length);
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_ARGV,
        argv_field, sizeof(argv_field));
    payload_size += service_store_field(
        &payload[payload_size], (uint16_t)P6C_FIELD_ENVIRONMENT,
        environment_field, environment_size);
    return service_build_request(
        packet, packet_capacity, message_type,
        request_id, payload, payload_size);
}

static int service_config_create(
    const struct test_directory *directory, int sockets[2],
    struct p6c_service_config *configuration,
    struct p6c_peer_identity *peer)
{
    if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) !=
        0) {
        return EXIT_FAILURE;
    }
    memset(configuration, 0, sizeof(*configuration));
    p6c_owned_fd_reset(&configuration->socket);
    p6c_owned_fd_reset(&configuration->journal_root);
    p6c_owned_fd_reset(&configuration->source_root);
    p6c_owned_fd_reset(&configuration->cgroup_root);
    p6c_owned_fd_reset(&configuration->evidence_root);
    if ((p6c_owned_fd_acquire(
             &configuration->socket, sockets[0],
             P6C_DESCRIPTOR_SOCKET) != P6C_RESULT_OK) ||
        (service_owned_duplicate(
             &directory->owner, P6C_DESCRIPTOR_DIRECTORY,
             &configuration->journal_root) != EXIT_SUCCESS) ||
        (service_owned_duplicate(
             &directory->owner, P6C_DESCRIPTOR_DIRECTORY,
             &configuration->source_root) != EXIT_SUCCESS) ||
        (service_owned_duplicate(
             &directory->owner, P6C_DESCRIPTOR_CGROUP,
             &configuration->cgroup_root) != EXIT_SUCCESS) ||
        (service_owned_duplicate(
             &directory->owner, P6C_DESCRIPTOR_DIRECTORY,
             &configuration->evidence_root) != EXIT_SUCCESS)) {
        return EXIT_FAILURE;
    }
    configuration->controller_user = getuid();
    peer->process_id = getpid();
    peer->user_id = getuid();
    peer->group_id = getgid();
    p6c_test_peer_override_set(true, peer);
    return EXIT_SUCCESS;
}

static int service_config_destroy(
    struct p6c_service_config *configuration, int peer_socket)
{
    int result = EXIT_SUCCESS;

    p6c_test_service_process_adapter_set(NULL);
    p6c_test_service_io_set(NULL, 0U, NULL, 0U, NULL);
    p6c_test_service_disconnect_after_input(false);
    p6c_test_peer_override_set(false, NULL);
    if (p6c_service_config_close(configuration) != P6C_RESULT_OK) {
        result = EXIT_FAILURE;
    }
    if ((close(peer_socket) != 0) && (errno != EBADF)) {
        result = EXIT_FAILURE;
    }
    return result;
}

static int service_disconnect_case(
    enum fake_stage failure_stage, bool transcript_output,
    bool send_failure, bool receive_failure,
    unsigned int failures_before_success)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t packet[1024];
    uint8_t response[512];
    size_t packet_size;
    size_t response_size = 0U;
    enum p6c_result service_result;
    static const char EXECUTABLE[] = "disconnect.elf";
    static const char JOURNAL[] =
        "bcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbc.journal";
    static const char STDOUT_NAME[] =
        "bcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbc.stdout";
    static const char STDERR_NAME[] =
        "bcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbc.stderr";
    static const uint8_t OUTPUT[] =
        "disconnect-transcript-output";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    fake.fail_stage = failure_stage;
    fake.fail_result = P6C_RESULT_RECOVERY_REQUIRED;
    fake.failures_remaining = failures_before_success;
    if (transcript_output) {
        fake.stdout_content = OUTPUT;
        fake.stdout_content_size = sizeof(OUTPUT) - 1U;
    }
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0xbc));
    test_fill_identity(request_id, UINT8_C(0xbd));
    test_fill_identity(recovery_token, UINT8_C(0xbe));
    p6c_test_recovery_token_set(recovery_token);
    packet_size = service_build_start_request(
        packet, sizeof(packet), (uint16_t)P6C_REQUEST_START,
        request_id, operation_id, executable_digest, EXECUTABLE);
    CHECK(packet_size != 0U);
    p6c_test_service_io_set(
        packet, packet_size, response, sizeof(response),
        &response_size);
    p6c_test_service_disconnect_after_input(true);
    if (receive_failure) {
        p6c_test_failpoint_set_after(P6C_FAIL_SERVICE_RECEIVE, 1U);
    }
    if (send_failure) {
        p6c_test_failpoint_set(P6C_FAIL_SERVICE_SEND);
    }
    service_result = p6c_service_run(&configuration);
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    p6c_test_service_disconnect_after_input(false);
    if (send_failure || receive_failure) {
        CHECK(service_result == P6C_RESULT_RECOVERY_REQUIRED);
    } else {
        CHECK(service_result == P6C_RESULT_OK);
    }
    CHECK(fake.calls[FAKE_STAGE_KILL] >=
          ((failure_stage == FAKE_STAGE_KILL) ?
               failures_before_success + 1U : 1U));
    CHECK(fake.calls[FAKE_STAGE_EMPTY] >=
          ((failure_stage == FAKE_STAGE_EMPTY) ?
               failures_before_success + 1U : 1U));
    CHECK(fake.calls[FAKE_STAGE_TRANSCRIPTS] >=
          ((failure_stage == FAKE_STAGE_TRANSCRIPTS) ?
               failures_before_success + 1U : 1U));
    CHECK(fake.calls[FAKE_STAGE_REMOVE] >=
          ((failure_stage == FAKE_STAGE_REMOVE) ?
               failures_before_success + 1U : 1U));
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK((unlinkat(
               directory.owner.descriptor, STDOUT_NAME, 0) == 0) ||
          (errno == ENOENT));
    CHECK((unlinkat(
               directory.owner.descriptor, STDERR_NAME, 0) == 0) ||
          (errno == ENOENT));
    CHECK(unlinkat(
              directory.owner.descriptor, JOURNAL, 0) == 0);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_disconnect_immediately_after_child_custody(void)
{
    return service_disconnect_case(
        FAKE_STAGE_CLONE_STATUS_CLOSE, false, false, false, 0U);
}

static int case_disconnect_after_running(void)
{
    return service_disconnect_case(
        FAKE_STAGE_NONE, false, false, false, 0U);
}

static int case_disconnect_during_transcript_output(void)
{
    return service_disconnect_case(
        FAKE_STAGE_NONE, true, false, false, 0U);
}

static int case_disconnect_send_failure_after_start(void)
{
    return service_disconnect_case(
        FAKE_STAGE_NONE, false, true, false, 0U);
}

static int case_disconnect_receive_failure_active(void)
{
    return service_disconnect_case(
        FAKE_STAGE_NONE, false, false, true, 0U);
}

static int case_disconnect_cleanup_retry_all_stages(void)
{
    static const enum fake_stage STAGES[] = {
        FAKE_STAGE_SIGNAL,
        FAKE_STAGE_FREEZE,
        FAKE_STAGE_KILL,
        FAKE_STAGE_EMPTY,
        FAKE_STAGE_OBSERVE,
        FAKE_STAGE_REAP,
        FAKE_STAGE_TRANSCRIPTS,
        FAKE_STAGE_REMOVE
    };
    size_t index;

    for (index = 0U; index < sizeof(STAGES) / sizeof(STAGES[0]);
         ++index) {
        if (service_disconnect_case(
                STAGES[index], true, false, false, 1U) !=
            EXIT_SUCCESS) {
            return EXIT_FAILURE;
        }
    }
    return EXIT_SUCCESS;
}

static int case_disconnect_cleanup_retries_past_legacy_cap(void)
{
    return service_disconnect_case(
        FAKE_STAGE_KILL, true, false, false, 7U);
}

static int case_disconnect_cleanup_held_authority(void)
{
    int ready_pipe[2];
    int release_pipe[2];
    pid_t worker;
    struct pollfd ready;
    uint8_t marker = UINT8_C(1);
    int status;

    CHECK(pipe2(ready_pipe, O_CLOEXEC) == 0);
    CHECK(pipe2(release_pipe, O_CLOEXEC) == 0);
    test_hold_stage = FAKE_STAGE_KILL;
    test_hold_ready_descriptor = ready_pipe[1];
    test_hold_release_descriptor = release_pipe[0];
    test_hold_released = false;
    worker = fork();
    CHECK(worker >= (pid_t)0);
    if (worker == (pid_t)0) {
        int outcome;

        (void)close(ready_pipe[0]);
        (void)close(release_pipe[1]);
        outcome = service_disconnect_case(
            FAKE_STAGE_NONE, true, false, false, 0U);
        _exit(outcome);
    }
    CHECK(close(ready_pipe[1]) == 0);
    CHECK(close(release_pipe[0]) == 0);
    memset(&ready, 0, sizeof(ready));
    ready.fd = ready_pipe[0];
    ready.events = POLLIN;
    CHECK(poll(&ready, 1U, 2000) == 1);
    CHECK(read(ready_pipe[0], &marker, sizeof(marker)) ==
          (ssize_t)sizeof(marker));
    CHECK(waitpid(worker, &status, WNOHANG) == (pid_t)0);
    CHECK(write(release_pipe[1], &marker, sizeof(marker)) ==
          (ssize_t)sizeof(marker));
    CHECK(waitpid(worker, &status, 0) == worker);
    CHECK(WIFEXITED(status));
    CHECK(WEXITSTATUS(status) == EXIT_SUCCESS);
    CHECK(close(ready_pipe[0]) == 0);
    CHECK(close(release_pipe[1]) == 0);
    test_hold_stage = FAKE_STAGE_NONE;
    test_hold_ready_descriptor = P6C_INVALID_DESCRIPTOR;
    test_hold_release_descriptor = P6C_INVALID_DESCRIPTOR;
    test_hold_released = false;
    return EXIT_SUCCESS;
}

static int case_service_replay_identical_request(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    const uint8_t *packets[2] = {packet, packet};
    size_t packet_sizes[2] = {sizeof(packet), sizeof(packet)};
    uint8_t responses[512];
    size_t response_sizes[2];
    size_t response_count = 0U;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    test_fill_identity(request_id, UINT8_C(0x31));
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_HELLO,
              request_id, NULL, 0U) == sizeof(packet));
    p6c_test_service_io_set_packets(
        packets, packet_sizes, 2U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 2U);
    CHECK(response_sizes[0] == P6C_HEADER_SIZE + 8U);
    CHECK(response_sizes[1] != P6C_HEADER_SIZE + 8U);
    CHECK(responses[
              response_sizes[0] +
              P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0xff));
    CHECK(responses[
              response_sizes[0] + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_CONFLICT));
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_replay_restart_duplicate(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[256];
    size_t response_size = 0U;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(request_id, UINT8_C(0x34));
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_HELLO,
              request_id, NULL, 0U) == sizeof(packet));
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response),
        &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_size == P6C_HEADER_SIZE + 8U);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);

    response_size = 0U;
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response),
        &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_size != P6C_HEADER_SIZE + 8U);
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0xff));
    CHECK(response[P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_CONFLICT));
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int service_hello_once(
    const struct test_directory *directory,
    const uint8_t request_id[P6C_REQUEST_ID_BYTES],
    enum p6c_result *service_result,
    uint8_t *response, size_t response_capacity,
    size_t *response_size)
{
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t packet[P6C_HEADER_SIZE];

    if ((directory == NULL) || (request_id == NULL) ||
        (service_result == NULL) || (response == NULL) ||
        (response_size == NULL) ||
        (service_build_request(
             packet, sizeof(packet),
             (uint16_t)P6C_REQUEST_HELLO, request_id,
             NULL, 0U) != sizeof(packet)) ||
        (service_config_create(
             directory, sockets, &configuration, &peer) !=
         EXIT_SUCCESS)) {
        return EXIT_FAILURE;
    }
    p6c_test_service_io_set(
        packet, sizeof(packet), response, response_capacity,
        response_size);
    *service_result = p6c_service_run(&configuration);
    return service_config_destroy(
        &configuration, sockets[1]);
}

static int case_service_replay_restart_uid_mismatch(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    enum p6c_result service_result;
    int sockets[2];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[256];
    size_t response_size = 0U;
    uid_t other_user = (uid_t)((uint64_t)getuid() + UINT64_C(1));

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(request_id, UINT8_C(0x35));
    CHECK(service_hello_once(
              &directory, request_id, &service_result,
              response, sizeof(response), &response_size) ==
          EXIT_SUCCESS);
    CHECK(service_result == P6C_RESULT_OK);
    CHECK(response_size == P6C_HEADER_SIZE + 8U);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) ==
          EXIT_SUCCESS);
    configuration.controller_user = other_user;
    peer.user_id = other_user;
    p6c_test_peer_override_set(true, &peer);
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_HELLO,
              request_id, NULL, 0U) == sizeof(packet));
    response_size = 0U;
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response),
        &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0xff));
    CHECK(response[P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_UNAUTHORIZED));
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_replay_ledger_hardening(void)
{
    enum replay_tamper {
        REPLAY_TAMPER_SYMLINK = 0,
        REPLAY_TAMPER_HARDLINK,
        REPLAY_TAMPER_MODE,
        REPLAY_TAMPER_TRUNCATE,
        REPLAY_TAMPER_TORN,
        REPLAY_TAMPER_CHAIN,
        REPLAY_TAMPER_CAPACITY
    };
    size_t tamper;

    for (tamper = (size_t)REPLAY_TAMPER_SYMLINK;
         tamper <= (size_t)REPLAY_TAMPER_CAPACITY; ++tamper) {
        struct test_directory directory;
        enum p6c_result service_result;
        uint8_t request_id[P6C_REQUEST_ID_BYTES];
        uint8_t response[256];
        size_t response_size = 0U;
        int descriptor = P6C_INVALID_DESCRIPTOR;
        uint8_t marker = UINT8_C(1);

        CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
        memset(request_id, 0, sizeof(request_id));
        request_id[0] = UINT8_C(0x50);
        request_id[P6C_REQUEST_ID_BYTES - 1U] =
            (uint8_t)(tamper + 1U);
        CHECK(service_hello_once(
                  &directory, request_id, &service_result,
                  response, sizeof(response), &response_size) ==
              EXIT_SUCCESS);
        CHECK(service_result == P6C_RESULT_OK);
        if (tamper == (size_t)REPLAY_TAMPER_SYMLINK) {
            CHECK(unlinkat(
                      directory.owner.descriptor,
                      ".p6c-replay-ledger", 0) == 0);
            CHECK(symlinkat(
                      "/dev/null", directory.owner.descriptor,
                      ".p6c-replay-ledger") == 0);
        } else if (tamper == (size_t)REPLAY_TAMPER_HARDLINK) {
            CHECK(linkat(
                      directory.owner.descriptor,
                      ".p6c-replay-ledger",
                      directory.owner.descriptor,
                      "ledger.alias", 0) == 0);
        } else if (tamper == (size_t)REPLAY_TAMPER_MODE) {
            CHECK(fchmodat(
                      directory.owner.descriptor,
                      ".p6c-replay-ledger", (mode_t)0644, 0) == 0);
        } else {
            descriptor = openat(
                directory.owner.descriptor,
                ".p6c-replay-ledger",
                O_RDWR | O_CLOEXEC | O_NOFOLLOW);
            CHECK(descriptor >= 0);
            if (tamper == (size_t)REPLAY_TAMPER_TRUNCATE) {
                CHECK(ftruncate(descriptor, (off_t)0) == 0);
            } else if (tamper == (size_t)REPLAY_TAMPER_TORN) {
                CHECK(pwrite(
                          descriptor, &marker, sizeof(marker),
                          (off_t)(64U + 184U)) ==
                      (ssize_t)sizeof(marker));
            } else if (tamper == (size_t)REPLAY_TAMPER_CHAIN) {
                CHECK(pwrite(
                          descriptor, &marker, sizeof(marker),
                          (off_t)(64U + 120U)) ==
                      (ssize_t)sizeof(marker));
            } else {
                CHECK(ftruncate(
                          descriptor,
                          (off_t)(64U + (P6C_REPLAY_CAPACITY * 184U) +
                                  1U)) == 0);
            }
            CHECK(fsync(descriptor) == 0);
            CHECK(close(descriptor) == 0);
        }
        request_id[0] = UINT8_C(0x60);
        response_size = 0U;
        CHECK(service_hello_once(
                  &directory, request_id, &service_result,
                  response, sizeof(response), &response_size) ==
              EXIT_SUCCESS);
        CHECK(service_result != P6C_RESULT_OK);
        CHECK(response_size == 0U);
        if (tamper == (size_t)REPLAY_TAMPER_HARDLINK) {
            CHECK(unlinkat(
                      directory.owner.descriptor,
                      "ledger.alias", 0) == 0);
        }
        CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    }
    return EXIT_SUCCESS;
}

static int case_service_replay_changed_payload_collision(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packets_storage[2][P6C_HEADER_SIZE];
    const uint8_t *packets[2] = {
        packets_storage[0], packets_storage[1]
    };
    size_t packet_sizes[2] = {
        P6C_HEADER_SIZE, P6C_HEADER_SIZE
    };
    uint8_t responses[512];
    size_t response_sizes[2];
    size_t response_count = 0U;
    size_t second_offset;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    test_fill_identity(request_id, UINT8_C(0x32));
    CHECK(service_build_request(
              packets_storage[0], sizeof(packets_storage[0]),
              (uint16_t)P6C_REQUEST_HELLO, request_id,
              NULL, 0U) == P6C_HEADER_SIZE);
    CHECK(service_build_request(
              packets_storage[1], sizeof(packets_storage[1]),
              (uint16_t)P6C_REQUEST_RECOVER, request_id,
              NULL, 0U) == P6C_HEADER_SIZE);
    p6c_test_service_io_set_packets(
        packets, packet_sizes, 2U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 2U);
    second_offset = response_sizes[0];
    CHECK(responses[
              second_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
          UINT8_C(0xff));
    CHECK(responses[
              second_offset + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_CONFLICT));
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_replay_capacity_before_dispatch(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t request_ids[P6C_REPLAY_CAPACITY + 1U]
                       [P6C_REQUEST_ID_BYTES];
    uint8_t packet_storage[P6C_REPLAY_CAPACITY + 1U]
                          [2048];
    const uint8_t *packets[P6C_REPLAY_CAPACITY + 1U];
    size_t packet_sizes[P6C_REPLAY_CAPACITY + 1U];
    uint8_t responses[
        (P6C_REPLAY_CAPACITY + 1U) *
        (P6C_HEADER_SIZE + 96U)];
    size_t response_sizes[P6C_REPLAY_CAPACITY + 1U];
    size_t response_count = 0U;
    size_t index;
    static const char EXECUTABLE[] = "replay-limit.elf";
    static const char JOURNAL[] =
        "91919191919191919191919191919191.journal";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x91));
    for (index = 0U; index < P6C_REPLAY_CAPACITY + 1U; ++index) {
        memset(request_ids[index], 0, P6C_REQUEST_ID_BYTES);
        request_ids[index][0] = UINT8_C(0x40);
        request_ids[index][P6C_REQUEST_ID_BYTES - 1U] =
            (uint8_t)(index + 1U);
        packets[index] = packet_storage[index];
        if (index < P6C_REPLAY_CAPACITY) {
            packet_sizes[index] = service_build_request(
                packet_storage[index], sizeof(packet_storage[index]),
                (uint16_t)P6C_REQUEST_HELLO, request_ids[index],
                NULL, 0U);
            CHECK(packet_sizes[index] == P6C_HEADER_SIZE);
        } else {
            packet_sizes[index] = service_build_start_request(
                packet_storage[index], sizeof(packet_storage[index]),
                (uint16_t)P6C_REQUEST_START, request_ids[index],
                operation_id, executable_digest, EXECUTABLE);
            CHECK(packet_sizes[index] != 0U);
        }
    }
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    p6c_test_service_io_set_packets(
        packets, packet_sizes, P6C_REPLAY_CAPACITY,
        responses, sizeof(responses), response_sizes,
        &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == P6C_REPLAY_CAPACITY);
    for (index = 0U; index < P6C_REPLAY_CAPACITY; ++index) {
        CHECK(response_sizes[index] == P6C_HEADER_SIZE + 8U);
    }
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);

    response_count = 0U;
    memset(response_sizes, 0, sizeof(response_sizes));
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    p6c_test_service_io_set_packets(
        &packets[P6C_REPLAY_CAPACITY],
        &packet_sizes[P6C_REPLAY_CAPACITY], 1U,
        responses, sizeof(responses), response_sizes,
        &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 1U);
    CHECK(responses[P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
          UINT8_C(0xff));
    CHECK(responses[P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_LIMIT_EXCEEDED));
    CHECK(fake.calls[FAKE_STAGE_CLONE] == 0U);
    CHECK(faccessat(
              directory.owner.descriptor, JOURNAL,
              F_OK, AT_SYMLINK_NOFOLLOW) != 0);
    CHECK(errno == ENOENT);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_replay_malformed_does_not_consume(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet_storage[2][P6C_HEADER_SIZE];
    const uint8_t *packets[2] = {
        packet_storage[0], packet_storage[1]
    };
    size_t packet_sizes[2] = {
        P6C_HEADER_SIZE, P6C_HEADER_SIZE
    };
    uint8_t responses[512];
    size_t response_sizes[2];
    size_t response_count = 0U;
    size_t second_offset;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    test_fill_identity(request_id, UINT8_C(0x33));
    CHECK(service_build_request(
              packet_storage[0], sizeof(packet_storage[0]),
              (uint16_t)P6C_REQUEST_HELLO, request_id,
              NULL, 0U) == P6C_HEADER_SIZE);
    memcpy(packet_storage[1], packet_storage[0], P6C_HEADER_SIZE);
    packet_storage[0][P6C_HEADER_MAGIC_OFFSET] ^= UINT8_C(0xff);
    p6c_test_service_io_set_packets(
        packets, packet_sizes, 2U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 2U);
    second_offset = response_sizes[0];
    CHECK(responses[
              second_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
          UINT8_C(0x80));
    CHECK(responses[
              second_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_HELLO));
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_start_dispatches(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct p6c_journal recovered_journal;
    enum p6c_journal_recovery recovery;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[2048];
    uint8_t response[512];
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    size_t packet_size;
    size_t response_size = 0U;
    size_t index;
    static const uint8_t EXECUTABLE[] = "approved.elf";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, (const char *)EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0x91));
    test_fill_identity(request_id, UINT8_C(0x92));
    packet_size = service_build_start_request(
        packet, sizeof(packet), (uint16_t)P6C_REQUEST_START,
        request_id, operation_id, executable_digest,
        (const char *)EXECUTABLE);
    CHECK(packet_size != 0U);
    p6c_test_service_io_set(
        packet, packet_size, response, sizeof(response), &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_size >= P6C_HEADER_SIZE);
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0x80));
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_START));
    for (index = 0U; index < sizeof(request_id); ++index) {
        CHECK(response[P6C_HEADER_REQUEST_ID_OFFSET + index] ==
              request_id[index]);
    }
    CHECK(p6c_journal_recover(
              &directory.owner,
              "91919191919191919191919191919191.journal",
              operation_id, getuid(), &recovered_journal,
              &recovery) == P6C_RESULT_OK);
    CHECK(recovery == P6C_JOURNAL_COMPLETE);
    CHECK(recovered_journal.state_payload_lengths[
              P6C_OPERATION_CGROUP_CREATED] ==
          P6C_CGROUP_CREATED_PAYLOAD_BYTES);
    CHECK(memcmp(
              recovered_journal.state_payloads[
                  P6C_OPERATION_CGROUP_CREATED],
              "p6c-", 4U) == 0);
    CHECK(memcmp(
              recovered_journal.state_payloads[
                  P6C_OPERATION_CGROUP_CREATED],
              "p6c-91919191919191919191919191919191", 36U) != 0);
    CHECK(p6c_journal_close(&recovered_journal) == P6C_RESULT_OK);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "91919191919191919191919191919191.journal", 0) == 0);
    CHECK(test_remove_service_transcripts(&directory) == EXIT_SUCCESS);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, (const char *)EXECUTABLE) ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_prechild_created_append_failure_cleanup(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[1024];
    uint8_t response[512];
    size_t packet_size;
    size_t response_size = 0U;
    static const char EXECUTABLE[] = "prechild-failure.elf";
    static const char JOURNAL[] =
        "92929292929292929292929292929292.journal";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) ==
          EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    fake.removal_root = &directory.owner;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0x92));
    test_fill_identity(request_id, UINT8_C(0x93));
    packet_size = service_build_start_request(
        packet, sizeof(packet), (uint16_t)P6C_REQUEST_START,
        request_id, operation_id, executable_digest, EXECUTABLE);
    CHECK(packet_size != 0U);
    p6c_test_failpoint_set_after(P6C_FAIL_JOURNAL_WRITE, 3U);
    p6c_test_service_io_set(
        packet, packet_size, response, sizeof(response),
        &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0xff));
    CHECK(response[P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_RECOVERY_REQUIRED));
    CHECK(fake.calls[FAKE_STAGE_CLONE] == 0U);
    CHECK(fake.calls[FAKE_STAGE_EMPTY] >= 1U);
    CHECK(fake.calls[FAKE_STAGE_REMOVE] >= 1U);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor, JOURNAL, 0) == 0);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_startup_recover_enumerates(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct p6c_journal journal;
    int sockets[2];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[512];
    uint8_t reserved_payload[53];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t request_digest[P6C_SHA256_BYTES];
    size_t response_size = 0U;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0xa1));
    test_fill_identity(request_id, UINT8_C(0xa2));
    test_fill_identity(recovery_token, UINT8_C(0xa3));
    memset(request_digest, UINT8_C(0xa4), sizeof(request_digest));
    memset(reserved_payload, 0, sizeof(reserved_payload));
    reserved_payload[0] = UINT8_C(1);
    p6c_store_u32_be(&reserved_payload[1], (uint32_t)getuid());
    memcpy(&reserved_payload[5], recovery_token, sizeof(recovery_token));
    memcpy(&reserved_payload[21], request_digest, sizeof(request_digest));
    CHECK(p6c_journal_create(
              &directory.owner,
              "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1.journal",
              operation_id, getuid(), &journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_RESERVED, reserved_payload,
              sizeof(reserved_payload)) ==
          P6C_RESULT_OK);
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_RECOVER,
              request_id, NULL, 0U) == sizeof(packet));
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response), &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_size >= P6C_HEADER_SIZE + 4U);
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0x80));
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_RECOVER));
    CHECK(response[P6C_HEADER_SIZE] == UINT8_C(0));
    CHECK(response[P6C_HEADER_SIZE + 1U] == UINT8_C(0));
    CHECK(response[P6C_HEADER_SIZE + 2U] == UINT8_C(0));
    CHECK(response[P6C_HEADER_SIZE + 3U] == UINT8_C(1));
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(
              &directory,
              "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_start_status_stop(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t request_ids[4][P6C_REQUEST_ID_BYTES];
    uint8_t packets[4][1024];
    const uint8_t *packet_pointers[4];
    size_t packet_sizes[4];
    uint8_t responses[4U * (P6C_HEADER_SIZE +
                            P6C_OPERATION_SUMMARY_BYTES)];
    size_t response_sizes[4];
    size_t response_count = 0U;
    size_t response_offset = 0U;
    size_t index;
    static const char EXECUTABLE[] = "approved.elf";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0xb1));
    test_fill_identity(recovery_token, UINT8_C(0xb2));
    p6c_test_recovery_token_set(recovery_token);
    for (index = 0U; index < 4U; ++index) {
        test_fill_identity(request_ids[index], (uint8_t)(0xc1U + index));
        packet_pointers[index] = packets[index];
    }
    packet_sizes[0] = service_build_start_request(
        packets[0], sizeof(packets[0]), (uint16_t)P6C_REQUEST_START,
        request_ids[0], operation_id,
        executable_digest, EXECUTABLE);
    packet_sizes[1] = service_build_operation_request(
        packets[1], sizeof(packets[1]), (uint16_t)P6C_REQUEST_STATUS,
        request_ids[1], operation_id, recovery_token);
    packet_sizes[2] = service_build_operation_request(
        packets[2], sizeof(packets[2]), (uint16_t)P6C_REQUEST_STOP,
        request_ids[2], operation_id, recovery_token);
    packet_sizes[3] = service_build_operation_request(
        packets[3], sizeof(packets[3]), (uint16_t)P6C_REQUEST_STOP,
        request_ids[3], operation_id, recovery_token);
    for (index = 0U; index < 4U; ++index) {
        CHECK(packet_sizes[index] != 0U);
    }
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, 4U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 4U);
    for (index = 0U; index < response_count; ++index) {
        CHECK(response_sizes[index] ==
              P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES);
        CHECK(responses[
                  response_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
              UINT8_C(0x80));
        CHECK(responses[
                  response_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
              (uint8_t)((index == 0U) ? P6C_REQUEST_START :
                        (index == 1U) ? P6C_REQUEST_STATUS :
                                       P6C_REQUEST_STOP));
        CHECK(responses[
                  response_offset + P6C_HEADER_SIZE +
                  P6C_SUMMARY_STATE_OFFSET] ==
              (uint8_t)((index < 2U) ? P6C_OPERATION_RUNNING :
                                       P6C_OPERATION_RESULT_RETAINED));
        response_offset += response_sizes[index];
    }
    CHECK(fake.calls[FAKE_STAGE_CLONE] == 1U);
    CHECK(fake.calls[FAKE_STAGE_KILL] == 1U);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1.stderr", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1.stdout", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1.journal", 0) == 0);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_start_replay(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t conflicting_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t request_ids[3][P6C_REQUEST_ID_BYTES];
    uint8_t packets[3][1024];
    const uint8_t *packet_pointers[3];
    size_t packet_sizes[3];
    uint8_t responses[3U * (P6C_HEADER_SIZE +
                            P6C_OPERATION_SUMMARY_BYTES)];
    size_t response_sizes[3];
    size_t response_count = 0U;
    size_t third_offset;
    size_t index;
    static const char EXECUTABLE[] = "approved.elf";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    memcpy(conflicting_digest, executable_digest, sizeof(conflicting_digest));
    conflicting_digest[0] ^= UINT8_C(0xff);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0xd1));
    test_fill_identity(recovery_token, UINT8_C(0xd2));
    p6c_test_recovery_token_set(recovery_token);
    for (index = 0U; index < 3U; ++index) {
        test_fill_identity(request_ids[index], (uint8_t)(0xe1U + index));
        packet_pointers[index] = packets[index];
    }
    packet_sizes[0] = service_build_start_request(
        packets[0], sizeof(packets[0]), (uint16_t)P6C_REQUEST_START,
        request_ids[0], operation_id,
        executable_digest, EXECUTABLE);
    packet_sizes[1] = service_build_start_request(
        packets[1], sizeof(packets[1]), (uint16_t)P6C_REQUEST_START,
        request_ids[1], operation_id,
        executable_digest, EXECUTABLE);
    packet_sizes[2] = service_build_start_request(
        packets[2], sizeof(packets[2]), (uint16_t)P6C_REQUEST_START,
        request_ids[2], operation_id,
        conflicting_digest, EXECUTABLE);
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, 3U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 3U);
    CHECK(response_sizes[0] ==
          P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES);
    CHECK(response_sizes[1] ==
          P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES);
    third_offset = response_sizes[0] + response_sizes[1];
    CHECK(responses[
              third_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
          UINT8_C(0xff));
    CHECK(responses[
              third_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(0xff));
    CHECK(responses[third_offset + P6C_HEADER_SIZE] == UINT8_C(0));
    CHECK(responses[third_offset + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_CONFLICT));
    CHECK(fake.calls[FAKE_STAGE_CLONE] == 1U);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1.journal", 0) == 0);
    CHECK(test_remove_service_transcripts(&directory) == EXIT_SUCCESS);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_run_once_read_ack(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t transcript_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t zero_publication[P6C_SHA256_BYTES];
    uint8_t request_ids[3][P6C_REQUEST_ID_BYTES];
    uint8_t packets[3][1024];
    const uint8_t *packet_pointers[3];
    size_t packet_sizes[3];
    uint8_t responses[
        (2U * (P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES)) +
        P6C_HEADER_SIZE + P6C_TRANSCRIPT_METADATA_BYTES + 3U];
    size_t response_sizes[3];
    size_t response_count = 0U;
    size_t read_offset;
    size_t ack_offset;
    size_t index;
    static const char EXECUTABLE[] = "approved.elf";
    static const uint8_t STDOUT_CONTENT[] = "abcdef";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    test_hash_bytes(
        STDOUT_CONTENT, sizeof(STDOUT_CONTENT) - 1U,
        transcript_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    fake.stdout_content = STDOUT_CONTENT;
    fake.stdout_content_size = sizeof(STDOUT_CONTENT) - 1U;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0xf1));
    test_fill_identity(recovery_token, UINT8_C(0xf2));
    memset(zero_publication, 0, sizeof(zero_publication));
    p6c_test_recovery_token_set(recovery_token);
    for (index = 0U; index < 3U; ++index) {
        test_fill_identity(request_ids[index], (uint8_t)(0x31U + index));
        packet_pointers[index] = packets[index];
    }
    packet_sizes[0] = service_build_start_request(
        packets[0], sizeof(packets[0]), (uint16_t)P6C_REQUEST_RUN_ONCE,
        request_ids[0], operation_id, executable_digest, EXECUTABLE);
    packet_sizes[1] = service_build_transcript_request(
        packets[1], sizeof(packets[1]), request_ids[1], operation_id,
        recovery_token, P6C_STREAM_STDOUT, UINT64_C(1), UINT32_C(3));
    packet_sizes[2] = service_build_publication_request(
        packets[2], sizeof(packets[2]), (uint16_t)P6C_REQUEST_ACK,
        request_ids[2], operation_id, recovery_token, zero_publication);
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, 3U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 3U);
    CHECK(response_sizes[0] ==
          P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES);
    CHECK(responses[P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0x80));
    CHECK(responses[P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_RUN_ONCE));
    CHECK(responses[P6C_HEADER_SIZE + P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_RESULT_RETAINED));
    read_offset = response_sizes[0];
    CHECK(response_sizes[1] ==
          P6C_HEADER_SIZE + P6C_TRANSCRIPT_METADATA_BYTES + 3U);
    CHECK(responses[
              read_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_READ_TRANSCRIPT));
    CHECK(responses[
              read_offset + P6C_HEADER_SIZE +
              P6C_TRANSCRIPT_STREAM_OFFSET] ==
          UINT8_C(P6C_STREAM_STDOUT));
    CHECK(responses[
              read_offset + P6C_HEADER_SIZE +
              P6C_TRANSCRIPT_FLAGS_OFFSET] ==
          UINT8_C(0));
    CHECK(memcmp(
              &responses[
                  read_offset + P6C_HEADER_SIZE +
                  P6C_TRANSCRIPT_DIGEST_OFFSET],
              transcript_digest, P6C_SHA256_BYTES) == 0);
    CHECK(memcmp(
              &responses[
                  read_offset + P6C_HEADER_SIZE +
                  P6C_TRANSCRIPT_METADATA_BYTES],
              "bcd", 3U) == 0);
    ack_offset = read_offset + response_sizes[1];
    CHECK(response_sizes[2] ==
          P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES);
    CHECK(responses[
              ack_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_ACK));
    CHECK(responses[
              ack_offset + P6C_HEADER_SIZE +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_ACKNOWLEDGED));
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "f1f1f1f1f1f1f1f1f1f1f1f1f1f1f1f1.journal", 0) == 0);
    CHECK(test_remove_service_transcripts(&directory) == EXIT_SUCCESS);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_ack_rejection_matrix(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t wrong_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t zero_publication[P6C_SHA256_BYTES];
    uint8_t wrong_publication[P6C_SHA256_BYTES];
    uint8_t request_ids[7][P6C_REQUEST_ID_BYTES];
    uint8_t packets[7][1024];
    const uint8_t *packet_pointers[7];
    size_t packet_sizes[7];
    uint8_t responses[1204];
    size_t response_sizes[7];
    size_t response_count = 0U;
    size_t offsets[7];
    size_t offset = 0U;
    size_t index;
    static const char EXECUTABLE[] = "ack-matrix.elf";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0xab));
    test_fill_identity(recovery_token, UINT8_C(0xac));
    test_fill_identity(wrong_token, UINT8_C(0xad));
    memset(zero_publication, 0, sizeof(zero_publication));
    memset(wrong_publication, UINT8_C(0xae), sizeof(wrong_publication));
    p6c_test_recovery_token_set(recovery_token);
    for (index = 0U; index < 7U; ++index) {
        test_fill_identity(request_ids[index], (uint8_t)(0xb0U + index));
        packet_pointers[index] = packets[index];
    }
    packet_sizes[0] = service_build_start_request(
        packets[0], sizeof(packets[0]), (uint16_t)P6C_REQUEST_START,
        request_ids[0], operation_id, executable_digest, EXECUTABLE);
    packet_sizes[1] = service_build_publication_request(
        packets[1], sizeof(packets[1]), (uint16_t)P6C_REQUEST_ACK,
        request_ids[1], operation_id, recovery_token, zero_publication);
    packet_sizes[2] = service_build_publication_request(
        packets[2], sizeof(packets[2]), (uint16_t)P6C_REQUEST_ACK,
        request_ids[2], operation_id, wrong_token, zero_publication);
    packet_sizes[3] = service_build_operation_request(
        packets[3], sizeof(packets[3]), (uint16_t)P6C_REQUEST_STOP,
        request_ids[3], operation_id, recovery_token);
    packet_sizes[4] = service_build_publication_request(
        packets[4], sizeof(packets[4]), (uint16_t)P6C_REQUEST_ACK,
        request_ids[4], operation_id, recovery_token, wrong_publication);
    packet_sizes[5] = service_build_publication_request(
        packets[5], sizeof(packets[5]), (uint16_t)P6C_REQUEST_ACK,
        request_ids[5], operation_id, recovery_token, zero_publication);
    packet_sizes[6] = service_build_publication_request(
        packets[6], sizeof(packets[6]), (uint16_t)P6C_REQUEST_ACK,
        request_ids[6], operation_id, recovery_token, zero_publication);
    for (index = 0U; index < 7U; ++index) {
        CHECK(packet_sizes[index] != 0U);
    }
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, 7U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 7U);
    for (index = 0U; index < 7U; ++index) {
        offsets[index] = offset;
        offset += response_sizes[index];
    }
    CHECK(responses[
              offsets[1] + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_INVALID_REQUEST));
    CHECK(responses[
              offsets[2] + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_UNAUTHORIZED));
    CHECK(responses[
              offsets[4] + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_CONFLICT));
    CHECK(response_sizes[5] ==
          P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES);
    CHECK(response_sizes[6] == response_sizes[5]);
    CHECK(responses[
              offsets[5] + P6C_HEADER_SIZE +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_ACKNOWLEDGED));
    CHECK(responses[
              offsets[6] + P6C_HEADER_SIZE +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_ACKNOWLEDGED));
    CHECK(fake.calls[FAKE_STAGE_CLONE] == 1U);
    CHECK(fake.calls[FAKE_STAGE_KILL] == 1U);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "abababababababababababababababab.journal", 0) == 0);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_read_rejection_matrix(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t transcript_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t wrong_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t request_ids[6][P6C_REQUEST_ID_BYTES];
    uint8_t packets[6][1024];
    const uint8_t *packet_pointers[6];
    size_t packet_sizes[6];
    uint8_t responses[900];
    size_t response_sizes[6];
    size_t response_count = 0U;
    size_t offsets[6];
    size_t offset = 0U;
    size_t index;
    static const char EXECUTABLE[] = "read-matrix.elf";
    static const uint8_t STDOUT_CONTENT[] = "abc";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    test_hash_bytes(
        STDOUT_CONTENT, sizeof(STDOUT_CONTENT) - 1U,
        transcript_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    fake.stdout_content = STDOUT_CONTENT;
    fake.stdout_content_size = sizeof(STDOUT_CONTENT) - 1U;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0xba));
    test_fill_identity(recovery_token, UINT8_C(0xbb));
    test_fill_identity(wrong_token, UINT8_C(0xbc));
    p6c_test_recovery_token_set(recovery_token);
    for (index = 0U; index < 6U; ++index) {
        test_fill_identity(request_ids[index], (uint8_t)(0xc1U + index));
        packet_pointers[index] = packets[index];
    }
    packet_sizes[0] = service_build_start_request(
        packets[0], sizeof(packets[0]), (uint16_t)P6C_REQUEST_RUN_ONCE,
        request_ids[0], operation_id, executable_digest, EXECUTABLE);
    packet_sizes[1] = service_build_transcript_request(
        packets[1], sizeof(packets[1]), request_ids[1], operation_id,
        wrong_token, P6C_STREAM_STDOUT, UINT64_C(0), UINT32_C(1));
    packet_sizes[2] = service_build_transcript_request(
        packets[2], sizeof(packets[2]), request_ids[2], operation_id,
        recovery_token, (enum p6c_stream_identity)3,
        UINT64_C(0), UINT32_C(1));
    packet_sizes[3] = service_build_transcript_request(
        packets[3], sizeof(packets[3]), request_ids[3], operation_id,
        recovery_token, P6C_STREAM_STDOUT, UINT64_C(4), UINT32_C(1));
    packet_sizes[4] = service_build_transcript_request(
        packets[4], sizeof(packets[4]), request_ids[4], operation_id,
        recovery_token, P6C_STREAM_STDOUT, UINT64_C(0),
        P6C_MAX_PAYLOAD_BYTES);
    packet_sizes[5] = service_build_transcript_request(
        packets[5], sizeof(packets[5]), request_ids[5], operation_id,
        recovery_token, P6C_STREAM_STDOUT, UINT64_C(0), UINT32_C(3));
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, 6U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 6U);
    for (index = 0U; index < 6U; ++index) {
        offsets[index] = offset;
        offset += response_sizes[index];
    }
    CHECK(responses[
              offsets[1] + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_UNAUTHORIZED));
    CHECK(responses[
              offsets[2] + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_INVALID_FRAME));
    CHECK(responses[
              offsets[3] + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_INVALID_REQUEST));
    CHECK(responses[
              offsets[4] + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_LIMIT_EXCEEDED));
    CHECK(response_sizes[5] ==
          P6C_HEADER_SIZE + P6C_TRANSCRIPT_METADATA_BYTES + 3U);
    CHECK(responses[
              offsets[5] + P6C_HEADER_SIZE +
              P6C_TRANSCRIPT_FLAGS_OFFSET] ==
          UINT8_C(P6C_TRANSCRIPT_FLAG_EOF));
    CHECK(memcmp(
              &responses[
                  offsets[5] + P6C_HEADER_SIZE +
                  P6C_TRANSCRIPT_DIGEST_OFFSET],
              transcript_digest, P6C_SHA256_BYTES) == 0);
    CHECK(memcmp(
              &responses[
                  offsets[5] + P6C_HEADER_SIZE +
                  P6C_TRANSCRIPT_METADATA_BYTES],
              STDOUT_CONTENT, sizeof(STDOUT_CONTENT) - 1U) == 0);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "babababababababababababababababa.stderr", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "babababababababababababababababa.stdout", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "babababababababababababababababa.journal", 0) == 0);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_publish_repeat_conflict(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    int generation_descriptor;
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t publication_id[P6C_SHA256_BYTES];
    uint8_t conflicting_id[P6C_SHA256_BYTES];
    uint8_t request_ids[5][P6C_REQUEST_ID_BYTES];
    uint8_t packets[5][1024];
    const uint8_t *packet_pointers[5];
    size_t packet_sizes[5];
    uint8_t responses[5U * (
        P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES +
        P6C_SHA256_BYTES)];
    size_t response_sizes[5];
    size_t response_count = 0U;
    size_t third_offset;
    size_t fifth_offset;
    size_t index;
    static const char EXECUTABLE[] = "approved.elf";
    static const uint8_t STDOUT_CONTENT[] = "publication-output";
    static const char GENERATION[] =
        "41414141414141414141414141414141";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    fake.stdout_content = STDOUT_CONTENT;
    fake.stdout_content_size = sizeof(STDOUT_CONTENT) - 1U;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0x41));
    test_fill_identity(recovery_token, UINT8_C(0x42));
    memset(publication_id, UINT8_C(0x43), sizeof(publication_id));
    memcpy(conflicting_id, publication_id, sizeof(conflicting_id));
    conflicting_id[0] ^= UINT8_C(0xff);
    p6c_test_recovery_token_set(recovery_token);
    for (index = 0U; index < 5U; ++index) {
        test_fill_identity(request_ids[index], (uint8_t)(0x51U + index));
        packet_pointers[index] = packets[index];
    }
    packet_sizes[0] = service_build_start_request(
        packets[0], sizeof(packets[0]), (uint16_t)P6C_REQUEST_START,
        request_ids[0], operation_id, executable_digest, EXECUTABLE);
    packet_sizes[1] = service_build_operation_request(
        packets[1], sizeof(packets[1]), (uint16_t)P6C_REQUEST_STOP,
        request_ids[1], operation_id, recovery_token);
    packet_sizes[2] = service_build_publication_request(
        packets[2], sizeof(packets[2]),
        (uint16_t)P6C_REQUEST_PUBLISH_BUNDLE, request_ids[2],
        operation_id, recovery_token, publication_id);
    packet_sizes[3] = service_build_publication_request(
        packets[3], sizeof(packets[3]),
        (uint16_t)P6C_REQUEST_PUBLISH_BUNDLE, request_ids[3],
        operation_id, recovery_token, publication_id);
    packet_sizes[4] = service_build_publication_request(
        packets[4], sizeof(packets[4]),
        (uint16_t)P6C_REQUEST_PUBLISH_BUNDLE, request_ids[4],
        operation_id, recovery_token, conflicting_id);
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, 5U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 5U);
    third_offset = response_sizes[0] + response_sizes[1];
    CHECK(response_sizes[2] ==
          P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES +
              P6C_SHA256_BYTES);
    CHECK(responses[
              third_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_PUBLISH_BUNDLE));
    CHECK(response_sizes[3] == response_sizes[2]);
    fifth_offset = third_offset + response_sizes[2] + response_sizes[3];
    CHECK(responses[
              fifth_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
          UINT8_C(0xff));
    CHECK(responses[
              fifth_offset + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_CONFLICT));
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    generation_descriptor = openat(
        directory.owner.descriptor, GENERATION,
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(generation_descriptor >= 0);
    CHECK(unlinkat(generation_descriptor, "stdout.bin", 0) == 0);
    CHECK(unlinkat(generation_descriptor, "stderr.bin", 0) == 0);
    CHECK(unlinkat(generation_descriptor, "authority.json", 0) == 0);
    CHECK(unlinkat(generation_descriptor, "manifest.json", 0) == 0);
    CHECK(close(generation_descriptor) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor, GENERATION,
              AT_REMOVEDIR) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "41414141414141414141414141414141.journal", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "41414141414141414141414141414141.stderr", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "41414141414141414141414141414141.stdout", 0) == 0);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_response_failures_recover(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    int generation_descriptor;
    int manifest_descriptor;
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t publication_id[P6C_SHA256_BYTES];
    uint8_t request_ids[5][P6C_REQUEST_ID_BYTES];
    uint8_t run_packet[1024];
    uint8_t publish_packet[256];
    uint8_t recover_packet[P6C_HEADER_SIZE];
    uint8_t failed_response[1];
    uint8_t responses[
        P6C_HEADER_SIZE + 4U + P6C_OPERATION_SUMMARY_BYTES +
        P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES +
        P6C_SHA256_BYTES];
    const uint8_t *final_packets[2];
    size_t final_packet_sizes[2];
    size_t response_sizes[2];
    size_t response_count = 0U;
    size_t failed_response_size = 0U;
    size_t run_packet_size;
    size_t publish_packet_size;
    size_t publish_offset;
    size_t recovery_response_size = 0U;
    size_t index;
    static const char EXECUTABLE[] = "response-failure.elf";
    static const char GENERATION[] =
        "55555555555555555555555555555555";
    static const uint8_t STDOUT_CONTENT[] = "retained-after-response-loss";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x55));
    test_fill_identity(recovery_token, UINT8_C(0x56));
    memset(publication_id, UINT8_C(0x57), sizeof(publication_id));
    for (index = 0U; index < 5U; ++index) {
        test_fill_identity(request_ids[index], (uint8_t)(0x58U + index));
    }

    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    fake.stdout_content = STDOUT_CONTENT;
    fake.stdout_content_size = sizeof(STDOUT_CONTENT) - 1U;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    p6c_test_recovery_token_set(recovery_token);
    run_packet_size = service_build_start_request(
        run_packet, sizeof(run_packet),
        (uint16_t)P6C_REQUEST_RUN_ONCE, request_ids[0],
        operation_id, executable_digest, EXECUTABLE);
    CHECK(run_packet_size != 0U);
    p6c_test_service_io_set(
        run_packet, run_packet_size,
        failed_response, sizeof(failed_response),
        &failed_response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_LIMIT);
    CHECK(failed_response_size == 0U);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);

    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    publish_packet_size = service_build_publication_request(
        publish_packet, sizeof(publish_packet),
        (uint16_t)P6C_REQUEST_PUBLISH_BUNDLE, request_ids[1],
        operation_id, recovery_token, publication_id);
    CHECK(publish_packet_size != 0U);
    p6c_test_service_io_set(
        publish_packet, publish_packet_size,
        failed_response, sizeof(failed_response),
        &failed_response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_LIMIT);
    CHECK(failed_response_size == 0U);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);

    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    final_packet_sizes[0] = service_build_request(
        recover_packet, sizeof(recover_packet),
        (uint16_t)P6C_REQUEST_RECOVER, request_ids[2], NULL, 0U);
    final_packet_sizes[1] = service_build_publication_request(
        publish_packet, sizeof(publish_packet),
        (uint16_t)P6C_REQUEST_PUBLISH_BUNDLE, request_ids[3],
        operation_id, recovery_token, publication_id);
    final_packets[0] = recover_packet;
    final_packets[1] = publish_packet;
    p6c_test_service_io_set_packets(
        final_packets, final_packet_sizes, 2U,
        responses, sizeof(responses), response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 2U);
    CHECK(response_sizes[0] ==
          P6C_HEADER_SIZE + 4U + P6C_OPERATION_SUMMARY_BYTES);
    CHECK(responses[
              P6C_HEADER_SIZE + 4U +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_RESULT_RETAINED));
    CHECK((responses[
               P6C_HEADER_SIZE + 4U +
               P6C_SUMMARY_FLAGS_OFFSET + 1U] &
           UINT8_C(P6C_SUMMARY_FLAG_BUNDLE_COMMITTED)) != 0U);
    publish_offset = response_sizes[0];
    CHECK(responses[
              publish_offset +
              P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_PUBLISH_BUNDLE));
    CHECK(response_sizes[1] ==
          P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES +
              P6C_SHA256_BYTES);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);

    generation_descriptor = openat(
        directory.owner.descriptor, GENERATION,
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(generation_descriptor >= 0);
    manifest_descriptor = openat(
        generation_descriptor, "manifest.json",
        O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(manifest_descriptor >= 0);
    CHECK(pwrite(
              manifest_descriptor, "X", 1U, (off_t)0) == 1);
    CHECK(fsync(manifest_descriptor) == 0);
    CHECK(close(manifest_descriptor) == 0);
    CHECK(close(generation_descriptor) == 0);

    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    CHECK(service_build_request(
              recover_packet, sizeof(recover_packet),
              (uint16_t)P6C_REQUEST_RECOVER, request_ids[4],
              NULL, 0U) == sizeof(recover_packet));
    p6c_test_service_io_set(
        recover_packet, sizeof(recover_packet),
        responses, sizeof(responses), &recovery_response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(recovery_response_size ==
          P6C_HEADER_SIZE + 4U + P6C_OPERATION_SUMMARY_BYTES);
    CHECK(responses[
              P6C_HEADER_SIZE + 4U +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_RECOVERY_REQUIRED));
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);

    generation_descriptor = openat(
        directory.owner.descriptor, GENERATION,
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(generation_descriptor >= 0);
    CHECK(unlinkat(generation_descriptor, "stdout.bin", 0) == 0);
    CHECK(unlinkat(generation_descriptor, "stderr.bin", 0) == 0);
    CHECK(unlinkat(generation_descriptor, "authority.json", 0) == 0);
    CHECK(unlinkat(generation_descriptor, "manifest.json", 0) == 0);
    CHECK(close(generation_descriptor) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor, GENERATION,
              AT_REMOVEDIR) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "55555555555555555555555555555555.stderr", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "55555555555555555555555555555555.stdout", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "55555555555555555555555555555555.journal", 0) == 0);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int service_create_retained_journal(
    const struct test_directory *directory, const char *journal_name,
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES],
    const uint8_t supplied_request_digest[P6C_SHA256_BYTES],
    const uint8_t executable_digest[P6C_SHA256_BYTES],
    const uint8_t stdout_digest[P6C_SHA256_BYTES],
    const uint8_t stderr_digest[P6C_SHA256_BYTES],
    uint64_t stdout_size, uint64_t stderr_size,
    bool stdout_truncated, bool commit_retained_digests)
{
    struct p6c_journal journal;
    uint8_t request_digest[P6C_SHA256_BYTES];
    uint8_t reserved[P6C_RESERVED_PAYLOAD_BYTES];
    uint8_t cgroup_created[P6C_CGROUP_CREATED_PAYLOAD_BYTES];
    uint8_t transcript_payload[P6C_TRANSCRIPTS_PAYLOAD_BYTES];
    uint8_t result_payload[P6C_RESULT_PAYLOAD_BYTES];
    char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
    char cgroup_name[P6C_CGROUP_NAME_BYTES];
    enum p6c_operation_state states[] = {
        P6C_OPERATION_CHILD_CLONED,
        P6C_OPERATION_EXEC_CONFIRMED,
        P6C_OPERATION_RUNNING,
        P6C_OPERATION_STOP_REQUESTED,
        P6C_OPERATION_CGROUP_KILLED,
        P6C_OPERATION_CGROUP_EMPTY,
        P6C_OPERATION_CHILD_EXIT_OBSERVED,
        P6C_OPERATION_CHILD_REAPED
    };
    size_t index;

    if (supplied_request_digest == NULL) {
        memset(request_digest, UINT8_C(0x73), sizeof(request_digest));
    } else {
        memcpy(request_digest, supplied_request_digest,
               sizeof(request_digest));
    }
    memset(reserved, 0, sizeof(reserved));
    reserved[0] = UINT8_C(1);
    p6c_store_u32_be(&reserved[1], (uint32_t)getuid());
    memcpy(&reserved[5], recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    memcpy(&reserved[21], request_digest, P6C_SHA256_BYTES);
    test_operation_hex(operation_id, operation_hex);
    CHECK(snprintf(
              cgroup_name, sizeof(cgroup_name), "p6c-%s",
              operation_hex) ==
          (int)(P6C_CGROUP_NAME_BYTES - 1U));
    memset(cgroup_created, 0, sizeof(cgroup_created));
    memcpy(
        &cgroup_created[P6C_CGROUP_CREATED_NAME_OFFSET],
        cgroup_name, P6C_CGROUP_NAME_BYTES - 1U);
    test_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_DEVICE_OFFSET],
        UINT64_C(1));
    test_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_INODE_OFFSET],
        UINT64_C(1));
    if ((p6c_journal_create(
             &directory->owner, journal_name, operation_id, getuid(),
             &journal) != P6C_RESULT_OK) ||
        (p6c_journal_append(
             &journal, P6C_OPERATION_RESERVED, reserved,
             sizeof(reserved)) != P6C_RESULT_OK) ||
        (p6c_journal_append(
             &journal, P6C_OPERATION_EXECUTABLE_PINNED,
             executable_digest, P6C_SHA256_BYTES) != P6C_RESULT_OK) ||
        (p6c_journal_append_cgroup_allocation_intent(
             &journal, cgroup_name) != P6C_RESULT_OK) ||
        (p6c_journal_append(
             &journal, P6C_OPERATION_CGROUP_CREATED,
             cgroup_created, sizeof(cgroup_created)) !=
         P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    for (index = 0U; index < sizeof(states) / sizeof(states[0]); ++index) {
        uint8_t exit_payload[4] = {
            UINT8_C(0), UINT8_C(0), UINT8_C(0), UINT8_C(17)
        };
        const void *payload = NULL;
        size_t payload_length = 0U;

        if (states[index] == P6C_OPERATION_CHILD_EXIT_OBSERVED) {
            payload = exit_payload;
            payload_length = sizeof(exit_payload);
        }
        if (p6c_journal_append(
                &journal, states[index], payload,
                payload_length) != P6C_RESULT_OK) {
            return EXIT_FAILURE;
        }
    }
    memcpy(transcript_payload, stdout_digest, P6C_SHA256_BYTES);
    memcpy(&transcript_payload[P6C_SHA256_BYTES], stderr_digest,
           P6C_SHA256_BYTES);
    if (p6c_journal_append(
            &journal, P6C_OPERATION_TRANSCRIPTS_FINAL,
            transcript_payload, sizeof(transcript_payload)) !=
        P6C_RESULT_OK) {
        return EXIT_FAILURE;
    }
    if (commit_retained_digests &&
        (p6c_journal_append_transcript_digests(
             &journal, stdout_digest, stderr_digest) !=
         P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    memset(result_payload, 0, sizeof(result_payload));
    test_store_u64_be(
        &result_payload[P6C_RESULT_STDOUT_OBSERVED_OFFSET],
        stdout_size + (stdout_truncated ? UINT64_C(1) : UINT64_C(0)));
    test_store_u64_be(
        &result_payload[P6C_RESULT_STDOUT_RETAINED_OFFSET], stdout_size);
    test_store_u64_be(
        &result_payload[P6C_RESULT_STDERR_OBSERVED_OFFSET], stderr_size);
    test_store_u64_be(
        &result_payload[P6C_RESULT_STDERR_RETAINED_OFFSET], stderr_size);
    p6c_store_u32_be(
        &result_payload[P6C_RESULT_EXIT_STATUS_OFFSET], UINT32_C(17));
    if (stdout_truncated) {
        result_payload[P6C_RESULT_FLAGS_OFFSET] =
            P6C_RESULT_FLAG_STDOUT_TRUNCATED;
    }
    if ((p6c_journal_append(
             &journal, P6C_OPERATION_RESULT_RETAINED,
             result_payload, sizeof(result_payload)) != P6C_RESULT_OK) ||
        (p6c_journal_close(&journal) != P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static int service_create_removal_intent_journal(
    const struct test_directory *directory, const char *journal_name,
    const char *cgroup_name,
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES])
{
    struct p6c_journal journal;
    struct stat cgroup_status;
    uint8_t request_digest[P6C_SHA256_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t empty_digest[P6C_SHA256_BYTES];
    uint8_t reserved[P6C_RESERVED_PAYLOAD_BYTES];
    uint8_t cgroup_created[P6C_CGROUP_CREATED_PAYLOAD_BYTES];
    uint8_t transcript_payload[P6C_TRANSCRIPTS_PAYLOAD_BYTES];
    uint8_t result_payload[P6C_RESULT_PAYLOAD_BYTES];
    enum p6c_operation_state states[] = {
        P6C_OPERATION_CHILD_CLONED,
        P6C_OPERATION_EXEC_CONFIRMED,
        P6C_OPERATION_RUNNING,
        P6C_OPERATION_STOP_REQUESTED,
        P6C_OPERATION_CGROUP_KILLED,
        P6C_OPERATION_CGROUP_EMPTY,
        P6C_OPERATION_CHILD_EXIT_OBSERVED,
        P6C_OPERATION_CHILD_REAPED
    };
    size_t index;

    if (fstatat(
            directory->owner.descriptor, cgroup_name,
            &cgroup_status, AT_SYMLINK_NOFOLLOW) != 0) {
        return EXIT_FAILURE;
    }
    memset(request_digest, UINT8_C(0x91), sizeof(request_digest));
    memset(executable_digest, UINT8_C(0x92),
           sizeof(executable_digest));
    test_hash_bytes(NULL, 0U, empty_digest);
    memset(reserved, 0, sizeof(reserved));
    reserved[0] = UINT8_C(1);
    p6c_store_u32_be(&reserved[1], (uint32_t)getuid());
    memcpy(&reserved[5], recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    memcpy(&reserved[21], request_digest, P6C_SHA256_BYTES);
    memset(cgroup_created, 0, sizeof(cgroup_created));
    memcpy(
        &cgroup_created[P6C_CGROUP_CREATED_NAME_OFFSET],
        cgroup_name, P6C_CGROUP_NAME_BYTES - 1U);
    test_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_DEVICE_OFFSET],
        (uint64_t)cgroup_status.st_dev);
    test_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_INODE_OFFSET],
        (uint64_t)cgroup_status.st_ino);
    if ((p6c_journal_create(
             &directory->owner, journal_name, operation_id, getuid(),
             &journal) != P6C_RESULT_OK) ||
        (p6c_journal_append(
             &journal, P6C_OPERATION_RESERVED, reserved,
             sizeof(reserved)) != P6C_RESULT_OK) ||
        (p6c_journal_append(
             &journal, P6C_OPERATION_EXECUTABLE_PINNED,
             executable_digest, sizeof(executable_digest)) !=
         P6C_RESULT_OK) ||
        (p6c_journal_append_cgroup_allocation_intent(
             &journal, cgroup_name) != P6C_RESULT_OK) ||
        (p6c_journal_append(
             &journal, P6C_OPERATION_CGROUP_CREATED,
             cgroup_created, sizeof(cgroup_created)) !=
         P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    for (index = 0U; index < sizeof(states) / sizeof(states[0]); ++index) {
        uint8_t exit_payload[4] = {
            UINT8_C(0), UINT8_C(0), UINT8_C(0), UINT8_C(17)
        };
        const void *payload = NULL;
        size_t payload_length = 0U;

        if (states[index] == P6C_OPERATION_CHILD_EXIT_OBSERVED) {
            payload = exit_payload;
            payload_length = sizeof(exit_payload);
        }
        if (p6c_journal_append(
                &journal, states[index], payload,
                payload_length) != P6C_RESULT_OK) {
            return EXIT_FAILURE;
        }
    }
    memcpy(transcript_payload, empty_digest, P6C_SHA256_BYTES);
    memcpy(&transcript_payload[P6C_SHA256_BYTES], empty_digest,
           P6C_SHA256_BYTES);
    memset(result_payload, 0, sizeof(result_payload));
    p6c_store_u32_be(
        &result_payload[P6C_RESULT_EXIT_STATUS_OFFSET], UINT32_C(17));
    if ((p6c_journal_append(
             &journal, P6C_OPERATION_TRANSCRIPTS_FINAL,
             transcript_payload, sizeof(transcript_payload)) !=
         P6C_RESULT_OK) ||
        (p6c_journal_append_transcript_digests(
             &journal, empty_digest, empty_digest) != P6C_RESULT_OK) ||
        (p6c_journal_append_cgroup_removal_intent(
             &journal, (uint64_t)cgroup_status.st_dev,
             (uint64_t)cgroup_status.st_ino,
             result_payload) != P6C_RESULT_OK) ||
        (p6c_journal_close(&journal) != P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static int service_create_intent_fixture(
    struct test_directory *directory, const char *journal_name,
    const char *cgroup_name, const char *stdout_name,
    const char *stderr_name,
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES],
    bool populated)
{
    int cgroup_descriptor;
    static const uint8_t EMPTY_EVENTS[] =
        "populated 0\nfrozen 1\n";
    static const uint8_t POPULATED_EVENTS[] =
        "populated 1\nfrozen 1\n";
    const uint8_t *events = populated ?
        POPULATED_EVENTS : EMPTY_EVENTS;
    size_t events_size = populated ?
        sizeof(POPULATED_EVENTS) - 1U :
        sizeof(EMPTY_EVENTS) - 1U;

    if ((mkdirat(
             directory->owner.descriptor, cgroup_name,
             (mode_t)0700) != 0) ||
        ((cgroup_descriptor = openat(
              directory->owner.descriptor, cgroup_name,
              O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW)) < 0)) {
        return EXIT_FAILURE;
    }
    {
        int events_descriptor = openat(
            cgroup_descriptor, "cgroup.events",
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
            (mode_t)0600);

        if ((events_descriptor < 0) ||
            (write(events_descriptor, events, events_size) !=
             (ssize_t)events_size) ||
            (fsync(events_descriptor) != 0) ||
            (close(events_descriptor) != 0) ||
            (fsync(cgroup_descriptor) != 0) ||
            (close(cgroup_descriptor) != 0)) {
            return EXIT_FAILURE;
        }
    }
    if ((test_write_file(
             directory, stdout_name, NULL, 0U,
             (mode_t)0600) != EXIT_SUCCESS) ||
        (test_write_file(
             directory, stderr_name, NULL, 0U,
             (mode_t)0600) != EXIT_SUCCESS) ||
        (service_create_removal_intent_journal(
             directory, journal_name, cgroup_name,
             operation_id, recovery_token) != EXIT_SUCCESS)) {
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static int service_create_reserved_journal(
    const struct test_directory *directory, const char *journal_name,
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    const uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES],
    uint8_t request_digest_byte, uid_t opening_user)
{
    struct p6c_journal journal;
    uint8_t reserved[P6C_RESERVED_PAYLOAD_BYTES];

    memset(reserved, 0, sizeof(reserved));
    reserved[0] = UINT8_C(1);
    p6c_store_u32_be(&reserved[1], (uint32_t)opening_user);
    memcpy(&reserved[5], recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    memset(&reserved[21], (int)request_digest_byte, P6C_SHA256_BYTES);
    if ((p6c_journal_create(
             &directory->owner, journal_name, operation_id, getuid(),
             &journal) != P6C_RESULT_OK) ||
        (p6c_journal_append(
             &journal, P6C_OPERATION_RESERVED, reserved,
             sizeof(reserved)) != P6C_RESULT_OK) ||
        (p6c_journal_close(&journal) != P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static int service_run_ack_batch(
    const struct test_directory *directory,
    const uint8_t executable_digest[P6C_SHA256_BYTES],
    const char *executable, uint8_t first_operation,
    size_t operation_count)
{
    enum { P6C_TEST_BATCH_OPERATIONS = 21 };
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t zero_publication[P6C_SHA256_BYTES];
    uint8_t operation_ids[P6C_TEST_BATCH_OPERATIONS]
                         [P6C_OPERATION_ID_BYTES];
    uint8_t request_ids[3U * P6C_TEST_BATCH_OPERATIONS]
                       [P6C_REQUEST_ID_BYTES];
    uint8_t packets[3U * P6C_TEST_BATCH_OPERATIONS][1024];
    const uint8_t *packet_pointers[
        3U * P6C_TEST_BATCH_OPERATIONS];
    size_t packet_sizes[3U * P6C_TEST_BATCH_OPERATIONS];
    uint8_t responses[
        3U * P6C_TEST_BATCH_OPERATIONS *
        (P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES)];
    size_t response_sizes[3U * P6C_TEST_BATCH_OPERATIONS];
    size_t response_count = 0U;
    size_t packet_count = operation_count * 3U;
    size_t response_offset = 0U;
    size_t index;

    if ((directory == NULL) || (executable_digest == NULL) ||
        (executable == NULL) ||
        (operation_count > P6C_TEST_BATCH_OPERATIONS)) {
        return EXIT_FAILURE;
    }
    if (service_config_create(
            directory, sockets, &configuration, &peer) != EXIT_SUCCESS) {
        return EXIT_FAILURE;
    }
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(recovery_token, UINT8_C(0xe1));
    memset(zero_publication, 0, sizeof(zero_publication));
    p6c_test_recovery_token_set(recovery_token);
    for (index = 0U; index < packet_count; ++index) {
        test_fill_identity(
            request_ids[index], (uint8_t)(index + 1U));
        packet_pointers[index] = packets[index];
    }
    for (index = 0U; index < operation_count; ++index) {
        size_t packet_index = index * 3U;

        test_fill_identity(
            operation_ids[index],
            (uint8_t)(first_operation + (uint8_t)index));
        packet_sizes[packet_index] = service_build_start_request(
            packets[packet_index], sizeof(packets[packet_index]),
            (uint16_t)P6C_REQUEST_START,
            request_ids[packet_index], operation_ids[index],
            executable_digest, executable);
        packet_sizes[packet_index + 1U] =
            service_build_operation_request(
                packets[packet_index + 1U],
                sizeof(packets[packet_index + 1U]),
                (uint16_t)P6C_REQUEST_STOP,
                request_ids[packet_index + 1U],
                operation_ids[index], recovery_token);
        packet_sizes[packet_index + 2U] =
            service_build_publication_request(
                packets[packet_index + 2U],
                sizeof(packets[packet_index + 2U]),
                (uint16_t)P6C_REQUEST_ACK,
                request_ids[packet_index + 2U],
                operation_ids[index], recovery_token,
                zero_publication);
        if ((packet_sizes[packet_index] == 0U) ||
            (packet_sizes[packet_index + 1U] == 0U) ||
            (packet_sizes[packet_index + 2U] == 0U)) {
            return EXIT_FAILURE;
        }
    }
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, packet_count,
        responses, sizeof(responses), response_sizes,
        &response_count);
    {
        enum p6c_result service_result =
            p6c_service_run(&configuration);

        if ((service_result != P6C_RESULT_OK) ||
            (response_count != packet_count)) {
            (void)fprintf(
                stderr,
                "ack batch service=%d responses=%zu/%zu clones=%u/%zu\n",
                (int)service_result, response_count, packet_count,
                fake.calls[FAKE_STAGE_CLONE], operation_count);
            return EXIT_FAILURE;
        }
    }
    for (index = 0U; index < packet_count; ++index) {
        uint8_t expected_state =
            (uint8_t)((index % 3U == 0U) ?
                P6C_OPERATION_RUNNING :
                (index % 3U == 1U) ?
                    P6C_OPERATION_RESULT_RETAINED :
                    P6C_OPERATION_ACKNOWLEDGED);

        if ((response_sizes[index] !=
             P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES) ||
            (responses[
                 response_offset + P6C_HEADER_SIZE +
                 P6C_SUMMARY_STATE_OFFSET] != expected_state)) {
            (void)fprintf(
                stderr,
                "ack batch response=%zu size=%zu state=%u expected=%u"
                " status=%u\n",
                index, response_sizes[index],
                (unsigned int)responses[
                    response_offset + P6C_HEADER_SIZE +
                    P6C_SUMMARY_STATE_OFFSET],
                (unsigned int)expected_state,
                (unsigned int)responses[
                    response_offset + P6C_HEADER_SIZE + 1U]);
            return EXIT_FAILURE;
        }
        response_offset += response_sizes[index];
    }
    if (fake.calls[FAKE_STAGE_CLONE] != operation_count) {
        (void)fprintf(
            stderr, "ack batch clones=%u/%zu\n",
            fake.calls[FAKE_STAGE_CLONE], operation_count);
        return EXIT_FAILURE;
    }
    return service_config_destroy(&configuration, sockets[1]);
}

static int case_service_tombstone_exact_exhaustion(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t archived_id[P6C_OPERATION_ID_BYTES];
    uint8_t request_ids[2][P6C_REQUEST_ID_BYTES];
    uint8_t packets[2][1024];
    const uint8_t *packet_pointers[2] = {
        packets[0], packets[1]
    };
    size_t packet_sizes[2];
    uint8_t responses[512];
    size_t response_sizes[2];
    size_t response_count = 0U;
    size_t second_offset;
    size_t index;
    static const char EXECUTABLE[] = "ack-capacity.elf";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_run_ack_batch(
              &directory, executable_digest, EXECUTABLE,
              UINT8_C(1), P6C_TOMBSTONE_CAPACITY) ==
          EXIT_SUCCESS);

    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(17));
    test_fill_identity(archived_id, UINT8_C(1));
    test_fill_identity(request_ids[0], UINT8_C(0xf1));
    test_fill_identity(request_ids[1], UINT8_C(0xf2));
    packet_sizes[0] = service_build_start_request(
        packets[0], sizeof(packets[0]), (uint16_t)P6C_REQUEST_START,
        request_ids[0], operation_id, executable_digest, EXECUTABLE);
    packet_sizes[1] = service_build_start_request(
        packets[1], sizeof(packets[1]), (uint16_t)P6C_REQUEST_START,
        request_ids[1], archived_id, executable_digest,
        "changed.elf");
    CHECK(packet_sizes[0] != 0U);
    CHECK(packet_sizes[1] != 0U);
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, 2U,
        responses, sizeof(responses), response_sizes,
        &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 2U);
    CHECK(responses[P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
          UINT8_C(0xff));
    CHECK(responses[P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_LIMIT_EXCEEDED));
    second_offset = response_sizes[0];
    CHECK(responses[
              second_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
          UINT8_C(0xff));
    CHECK(responses[
              second_offset + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_CONFLICT));
    CHECK(fake.calls[FAKE_STAGE_CLONE] == 0U);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    for (index = 1U; index <= P6C_TOMBSTONE_CAPACITY; ++index) {
        uint8_t tombstone_id[P6C_OPERATION_ID_BYTES];
        char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
        char journal_name[41];

        test_fill_identity(tombstone_id, (uint8_t)index);
        test_operation_hex(tombstone_id, operation_hex);
        CHECK(snprintf(
                  journal_name, sizeof(journal_name), "%s.journal",
                  operation_hex) == 40);
        CHECK(unlinkat(
                  directory.owner.descriptor, journal_name, 0) == 0);
    }
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_tombstone_startup_over_capacity(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t empty_digest[P6C_SHA256_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[128];
    size_t response_size = 0U;
    int sockets[2];
    size_t index;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(recovery_token, UINT8_C(0xa1));
    memset(executable_digest, UINT8_C(0xa2),
           sizeof(executable_digest));
    test_hash_bytes(NULL, 0U, empty_digest);
    for (index = 0U; index <= P6C_TOMBSTONE_CAPACITY; ++index) {
        struct p6c_journal journal;
        enum p6c_journal_recovery recovery;
        uint8_t operation_id[P6C_OPERATION_ID_BYTES];
        char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
        char journal_name[41];

        test_fill_identity(operation_id, (uint8_t)(index + 1U));
        test_operation_hex(operation_id, operation_hex);
        CHECK(snprintf(
                  journal_name, sizeof(journal_name), "%s.journal",
                  operation_hex) == 40);
        CHECK(service_create_retained_journal(
                  &directory, journal_name, operation_id,
                  recovery_token, NULL, executable_digest,
                  empty_digest, empty_digest, UINT64_C(0),
                  UINT64_C(0), false, true) == EXIT_SUCCESS);
        CHECK(p6c_journal_recover(
                  &directory.owner, journal_name, operation_id,
                  getuid(), &journal, &recovery) == P6C_RESULT_OK);
        CHECK(recovery == P6C_JOURNAL_COMPLETE);
        CHECK(p6c_journal_append(
                  &journal, P6C_OPERATION_ACKNOWLEDGED,
                  NULL, 0U) == P6C_RESULT_OK);
        CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    }
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) ==
          EXIT_SUCCESS);
    test_fill_identity(request_id, UINT8_C(0xa3));
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_HELLO,
              request_id, NULL, 0U) == sizeof(packet));
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response),
        &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_LIMIT);
    CHECK(response_size == 0U);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    for (index = 0U; index <= P6C_TOMBSTONE_CAPACITY; ++index) {
        uint8_t operation_id[P6C_OPERATION_ID_BYTES];
        char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
        char journal_name[41];

        test_fill_identity(operation_id, (uint8_t)(index + 1U));
        test_operation_hex(operation_id, operation_hex);
        CHECK(snprintf(
                  journal_name, sizeof(journal_name), "%s.journal",
                  operation_hex) == 40);
        CHECK(unlinkat(
                  directory.owner.descriptor, journal_name, 0) == 0);
    }
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_duplicate_tombstone_fails_closed(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct p6c_journal journal;
    enum p6c_journal_recovery recovery;
    int sockets[2];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t empty_digest[P6C_SHA256_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[128];
    size_t response_size = 0U;
    enum p6c_result result;
    static const char JOURNAL[] =
        "7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a.journal";
    static const char DUPLICATE[] =
        "7b7b7b7b7b7b7b7b7b7b7b7b7b7b7b7b.journal";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x7a));
    test_fill_identity(recovery_token, UINT8_C(0x7c));
    test_fill_identity(request_id, UINT8_C(0x7d));
    memset(executable_digest, UINT8_C(0x7e),
           sizeof(executable_digest));
    test_hash_bytes(NULL, 0U, empty_digest);
    CHECK(service_create_retained_journal(
              &directory, JOURNAL, operation_id, recovery_token,
              NULL, executable_digest, empty_digest, empty_digest,
              UINT64_C(0), UINT64_C(0), false, true) ==
          EXIT_SUCCESS);
    CHECK(p6c_journal_recover(
              &directory.owner, JOURNAL, operation_id, getuid(),
              &journal, &recovery) == P6C_RESULT_OK);
    CHECK(recovery == P6C_JOURNAL_COMPLETE);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_ACKNOWLEDGED,
              NULL, 0U) == P6C_RESULT_OK);
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    CHECK(linkat(
              directory.owner.descriptor, JOURNAL,
              directory.owner.descriptor, DUPLICATE, 0) == 0);
    CHECK(fsync(directory.owner.descriptor) == 0);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_HELLO,
              request_id, NULL, 0U) == sizeof(packet));
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response),
        &response_size);
    result = p6c_service_run(&configuration);
    CHECK(result != P6C_RESULT_OK);
    CHECK(response_size == 0U);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor, DUPLICATE, 0) == 0);
    CHECK(test_directory_close(&directory, JOURNAL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

enum intent_restart_mode {
    INTENT_RESTART_ABSENT = 0,
    INTENT_RESTART_PRESENT,
    INTENT_RESTART_REPLACEMENT,
    INTENT_RESTART_POPULATED
};

static int service_removal_intent_restart_case(
    enum intent_restart_mode mode)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[512];
    size_t response_size = 0U;
    uint8_t expected_state;
    bool cgroup_present = true;
    static const char JOURNAL[] =
        "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1.journal";
    static const char CGROUP[] =
        "p6c-d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1";
    static const char STDOUT_NAME[] =
        "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1.stdout";
    static const char STDERR_NAME[] =
        "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1.stderr";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0xd1));
    test_fill_identity(recovery_token, UINT8_C(0xd2));
    test_fill_identity(request_id, UINT8_C(0xd3));
    CHECK(service_create_intent_fixture(
              &directory, JOURNAL, CGROUP, STDOUT_NAME, STDERR_NAME,
              operation_id, recovery_token,
              mode == INTENT_RESTART_POPULATED) == EXIT_SUCCESS);
    if ((mode == INTENT_RESTART_ABSENT) ||
        (mode == INTENT_RESTART_REPLACEMENT)) {
        char events_path[64];

        CHECK(snprintf(
                  events_path, sizeof(events_path), "%s/cgroup.events",
                  CGROUP) > 0);
        CHECK(unlinkat(
                  directory.owner.descriptor, events_path, 0) == 0);
        CHECK(unlinkat(
                  directory.owner.descriptor, CGROUP,
                  AT_REMOVEDIR) == 0);
        cgroup_present = false;
    }
    if (mode == INTENT_RESTART_REPLACEMENT) {
        int replacement_descriptor;
        int events_descriptor;
        static const uint8_t EVENTS[] =
            "populated 0\nfrozen 1\n";

        CHECK(mkdirat(
                  directory.owner.descriptor, "inode-reservation",
                  (mode_t)0700) == 0);
        CHECK(mkdirat(
                  directory.owner.descriptor, CGROUP,
                  (mode_t)0700) == 0);
        replacement_descriptor = openat(
            directory.owner.descriptor, CGROUP,
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        CHECK(replacement_descriptor >= 0);
        events_descriptor = openat(
            replacement_descriptor, "cgroup.events",
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
            (mode_t)0600);
        CHECK(events_descriptor >= 0);
        CHECK(write(
                  events_descriptor, EVENTS,
                  sizeof(EVENTS) - 1U) ==
              (ssize_t)(sizeof(EVENTS) - 1U));
        CHECK(fsync(events_descriptor) == 0);
        CHECK(close(events_descriptor) == 0);
        CHECK(close(replacement_descriptor) == 0);
        cgroup_present = true;
    }
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    if (mode == INTENT_RESTART_PRESENT) {
        fake.removal_root = &directory.owner;
        fake.removal_name = CGROUP;
    }
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_RECOVER,
              request_id, NULL, 0U) == sizeof(packet));
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response),
        &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_size ==
          P6C_HEADER_SIZE + 4U + P6C_OPERATION_SUMMARY_BYTES);
    expected_state = (mode == INTENT_RESTART_ABSENT) ?
        (uint8_t)P6C_OPERATION_RESULT_RETAINED :
        (uint8_t)P6C_OPERATION_RECOVERY_REQUIRED;
    CHECK(response[
              P6C_HEADER_SIZE + 4U +
              P6C_SUMMARY_STATE_OFFSET] == expected_state);
    CHECK(fake.calls[FAKE_STAGE_REMOVE] == 0U);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);

    if (cgroup_present) {
        char events_path[64];

        CHECK(snprintf(
                  events_path, sizeof(events_path), "%s/cgroup.events",
                  CGROUP) > 0);
        CHECK(unlinkat(
                  directory.owner.descriptor, events_path, 0) == 0);
        CHECK(unlinkat(
                  directory.owner.descriptor, CGROUP,
                  AT_REMOVEDIR) == 0);
    }
    if (mode == INTENT_RESTART_REPLACEMENT) {
        CHECK(unlinkat(
                  directory.owner.descriptor, "inode-reservation",
                  AT_REMOVEDIR) == 0);
    }
    CHECK(unlinkat(
              directory.owner.descriptor, STDOUT_NAME, 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor, STDERR_NAME, 0) == 0);
    CHECK(test_directory_close(&directory, JOURNAL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_restart_removal_intent_absent(void)
{
    return service_removal_intent_restart_case(
        INTENT_RESTART_ABSENT);
}

static int case_restart_removal_intent_present_empty(void)
{
    return service_removal_intent_restart_case(
        INTENT_RESTART_PRESENT);
}

static int case_restart_removal_intent_replacement_rejected(void)
{
    return service_removal_intent_restart_case(
        INTENT_RESTART_REPLACEMENT);
}

static int case_restart_removal_intent_populated_rejected(void)
{
    return service_removal_intent_restart_case(
        INTENT_RESTART_POPULATED);
}

enum prechild_restart_phase {
    PRECHILD_RESTART_BEFORE_MKDIR = 0,
    PRECHILD_RESTART_AFTER_MKDIR,
    PRECHILD_RESTART_AFTER_CREATED_APPEND
};

static int service_prechild_restart_case(
    enum prechild_restart_phase phase)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct p6c_journal journal;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    struct stat cgroup_status;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t request_digest[P6C_SHA256_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t reserved[P6C_RESERVED_PAYLOAD_BYTES];
    uint8_t cgroup_created[P6C_CGROUP_CREATED_PAYLOAD_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[512];
    size_t response_size = 0U;
    int sockets[2];
    static const char JOURNAL[] =
        "d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4.journal";
    static const char EXACT_CGROUP[] =
        "p6c-0123456789abcdef0123456789abcdef";
    static const char DERIVED_CGROUP[] =
        "p6c-d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4";
    static const uint8_t EVENTS[] =
        "populated 0\nfrozen 0\n";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0xd4));
    test_fill_identity(recovery_token, UINT8_C(0xd5));
    test_fill_identity(request_id, UINT8_C(0xd6));
    memset(request_digest, UINT8_C(0xd7), sizeof(request_digest));
    memset(executable_digest, UINT8_C(0xd8),
           sizeof(executable_digest));
    memset(reserved, 0, sizeof(reserved));
    reserved[0] = UINT8_C(1);
    p6c_store_u32_be(&reserved[1], (uint32_t)getuid());
    memcpy(&reserved[5], recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    memcpy(&reserved[21], request_digest, P6C_SHA256_BYTES);
    CHECK(p6c_journal_create(
              &directory.owner, JOURNAL, operation_id, getuid(),
              &journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_RESERVED, reserved,
              sizeof(reserved)) == P6C_RESULT_OK);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_EXECUTABLE_PINNED,
              executable_digest, sizeof(executable_digest)) ==
          P6C_RESULT_OK);
    CHECK(p6c_journal_append_cgroup_allocation_intent(
              &journal, EXACT_CGROUP) == P6C_RESULT_OK);
    if (phase >= PRECHILD_RESTART_AFTER_MKDIR) {
        int cgroup_descriptor;

        CHECK(mkdirat(
                  directory.owner.descriptor, EXACT_CGROUP,
                  (mode_t)0700) == 0);
        cgroup_descriptor = openat(
            directory.owner.descriptor, EXACT_CGROUP,
            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        CHECK(cgroup_descriptor >= 0);
        CHECK(test_write_file(
                  &directory,
                  "p6c-0123456789abcdef0123456789abcdef/cgroup.events",
                  EVENTS, sizeof(EVENTS) - 1U,
                  (mode_t)0600) == EXIT_SUCCESS);
        CHECK(fstat(cgroup_descriptor, &cgroup_status) == 0);
        CHECK(fsync(cgroup_descriptor) == 0);
        CHECK(close(cgroup_descriptor) == 0);
    }
    if (phase == PRECHILD_RESTART_AFTER_CREATED_APPEND) {
        memset(cgroup_created, 0, sizeof(cgroup_created));
        memcpy(
            &cgroup_created[P6C_CGROUP_CREATED_NAME_OFFSET],
            EXACT_CGROUP, P6C_CGROUP_NAME_BYTES - 1U);
        test_store_u64_be(
            &cgroup_created[P6C_CGROUP_CREATED_DEVICE_OFFSET],
            (uint64_t)cgroup_status.st_dev);
        test_store_u64_be(
            &cgroup_created[P6C_CGROUP_CREATED_INODE_OFFSET],
            (uint64_t)cgroup_status.st_ino);
        CHECK(p6c_journal_append(
                  &journal, P6C_OPERATION_CGROUP_CREATED,
                  cgroup_created, sizeof(cgroup_created)) ==
              P6C_RESULT_OK);
    }
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);

    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) ==
          EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    fake.removal_root = &directory.owner;
    fake.removal_name = EXACT_CGROUP;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_RECOVER,
              request_id, NULL, 0U) == sizeof(packet));
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response),
        &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_size ==
          P6C_HEADER_SIZE + 4U + P6C_OPERATION_SUMMARY_BYTES);
    CHECK(response[
              P6C_HEADER_SIZE + 4U +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_RECOVERY_REQUIRED));
    CHECK(fake.calls[FAKE_STAGE_CLONE] == 0U);
    CHECK(fake.calls[FAKE_STAGE_REMOVE] ==
          ((phase == PRECHILD_RESTART_BEFORE_MKDIR) ? 0U : 1U));
    errno = 0;
    CHECK(faccessat(
              directory.owner.descriptor, EXACT_CGROUP,
              F_OK, AT_SYMLINK_NOFOLLOW) != 0);
    CHECK(errno == ENOENT);
    errno = 0;
    CHECK(faccessat(
              directory.owner.descriptor, DERIVED_CGROUP,
              F_OK, AT_SYMLINK_NOFOLLOW) != 0);
    CHECK(errno == ENOENT);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, JOURNAL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_restart_cgroup_before_mkdir(void)
{
    return service_prechild_restart_case(
        PRECHILD_RESTART_BEFORE_MKDIR);
}

static int case_restart_cgroup_after_mkdir(void)
{
    return service_prechild_restart_case(
        PRECHILD_RESTART_AFTER_MKDIR);
}

static int case_restart_cgroup_after_created_append(void)
{
    return service_prechild_restart_case(
        PRECHILD_RESTART_AFTER_CREATED_APPEND);
}

static int case_service_restart_retained_transcript(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t stdout_digest[P6C_SHA256_BYTES];
    uint8_t stderr_digest[P6C_SHA256_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[256];
    uint8_t response[256];
    size_t packet_size;
    size_t response_size = 0U;
    static const uint8_t STDOUT_CONTENT[] = "restart-output";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x61));
    test_fill_identity(recovery_token, UINT8_C(0x62));
    test_fill_identity(request_id, UINT8_C(0x63));
    memset(executable_digest, UINT8_C(0x64),
           sizeof(executable_digest));
    test_hash_bytes(
        STDOUT_CONTENT, sizeof(STDOUT_CONTENT) - 1U, stdout_digest);
    test_hash_bytes(NULL, 0U, stderr_digest);
    CHECK(service_create_retained_journal(
              &directory,
              "61616161616161616161616161616161.journal",
              operation_id, recovery_token, NULL, executable_digest,
              stdout_digest, stderr_digest,
              (uint64_t)(sizeof(STDOUT_CONTENT) - 1U), UINT64_C(0),
              false, true) ==
          EXIT_SUCCESS);
    CHECK(test_write_file(
              &directory,
              "61616161616161616161616161616161.stdout",
              STDOUT_CONTENT, sizeof(STDOUT_CONTENT) - 1U,
              (mode_t)0600) == EXIT_SUCCESS);
    CHECK(test_write_file(
              &directory,
              "61616161616161616161616161616161.stderr",
              NULL, 0U, (mode_t)0600) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    packet_size = service_build_transcript_request(
        packet, sizeof(packet), request_id, operation_id,
        recovery_token, P6C_STREAM_STDOUT, UINT64_C(0), UINT32_C(64));
    CHECK(packet_size != 0U);
    p6c_test_service_io_set(
        packet, packet_size, response, sizeof(response), &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_size ==
          P6C_HEADER_SIZE + P6C_TRANSCRIPT_METADATA_BYTES +
              sizeof(STDOUT_CONTENT) - 1U);
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_READ_TRANSCRIPT));
    CHECK(response[P6C_HEADER_SIZE + P6C_TRANSCRIPT_FLAGS_OFFSET] ==
          P6C_TRANSCRIPT_FLAG_EOF);
    CHECK(memcmp(
              &response[P6C_HEADER_SIZE +
                        P6C_TRANSCRIPT_METADATA_BYTES],
              STDOUT_CONTENT, sizeof(STDOUT_CONTENT) - 1U) == 0);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "61616161616161616161616161616161.stderr", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "61616161616161616161616161616161.stdout", 0) == 0);
    CHECK(test_directory_close(
              &directory,
              "61616161616161616161616161616161.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_valid_rebuilt_untruncated_digest_conflicts(void)
{
    size_t mode;
    static const uint8_t STDOUT_CONTENT[] = "stdout-authenticated";
    static const uint8_t STDERR_CONTENT[] = "stderr-authenticated";
    static const uint8_t CONTRADICTORY[] = "unrelated-full-digest";

    for (mode = 0U; mode < 2U; ++mode) {
        struct test_directory directory;
        struct p6c_service_config configuration;
        struct p6c_peer_identity peer;
        int sockets[2];
        int descriptor;
        struct stat status;
        uint64_t record_count;
        uint64_t index;
        uint8_t operation_id[P6C_OPERATION_ID_BYTES];
        uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
        uint8_t executable_digest[P6C_SHA256_BYTES];
        uint8_t stdout_digest[P6C_SHA256_BYTES];
        uint8_t stderr_digest[P6C_SHA256_BYTES];
        uint8_t replacement_digest[P6C_SHA256_BYTES];
        uint8_t request_id[P6C_REQUEST_ID_BYTES];
        uint8_t packet[P6C_HEADER_SIZE];
        uint8_t response[512];
        size_t response_size = 0U;
        char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
        char journal_name[41];
        char stdout_name[40];
        char stderr_name[40];
        bool changed = false;

        CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
        test_fill_identity(
            operation_id, (uint8_t)(UINT8_C(0x65) + mode));
        test_fill_identity(
            recovery_token, (uint8_t)(UINT8_C(0x75) + mode));
        test_fill_identity(
            request_id, (uint8_t)(UINT8_C(0x85) + mode));
        memset(executable_digest, (int)(UINT8_C(0x95) + mode),
               sizeof(executable_digest));
        test_hash_bytes(
            STDOUT_CONTENT, sizeof(STDOUT_CONTENT) - 1U,
            stdout_digest);
        test_hash_bytes(
            STDERR_CONTENT, sizeof(STDERR_CONTENT) - 1U,
            stderr_digest);
        if (mode == 0U) {
            test_hash_bytes(
                CONTRADICTORY, sizeof(CONTRADICTORY) - 1U,
                replacement_digest);
        } else {
            memcpy(
                replacement_digest, stderr_digest,
                P6C_SHA256_BYTES);
        }
        test_operation_hex(operation_id, operation_hex);
        CHECK(snprintf(
                  journal_name, sizeof(journal_name), "%s.journal",
                  operation_hex) == 40);
        CHECK(snprintf(
                  stdout_name, sizeof(stdout_name), "%s.stdout",
                  operation_hex) == 39);
        CHECK(snprintf(
                  stderr_name, sizeof(stderr_name), "%s.stderr",
                  operation_hex) == 39);
        CHECK(service_create_retained_journal(
                  &directory, journal_name, operation_id,
                  recovery_token, NULL, executable_digest,
                  stdout_digest, stderr_digest,
                  (uint64_t)(sizeof(STDOUT_CONTENT) - 1U),
                  (uint64_t)(sizeof(STDERR_CONTENT) - 1U),
                  false, true) == EXIT_SUCCESS);
        CHECK(test_write_file(
                  &directory, stdout_name, STDOUT_CONTENT,
                  sizeof(STDOUT_CONTENT) - 1U,
                  (mode_t)0600) == EXIT_SUCCESS);
        CHECK(test_write_file(
                  &directory, stderr_name, STDERR_CONTENT,
                  sizeof(STDERR_CONTENT) - 1U,
                  (mode_t)0600) == EXIT_SUCCESS);
        descriptor = openat(
            directory.owner.descriptor, journal_name,
            O_RDWR | O_CLOEXEC | O_NOFOLLOW);
        CHECK(descriptor >= 0);
        CHECK(fstat(descriptor, &status) == 0);
        CHECK(status.st_size >= 0);
        CHECK(((uint64_t)status.st_size %
               (uint64_t)P6C_JOURNAL_RECORD_BYTES) == UINT64_C(0));
        record_count = (uint64_t)status.st_size /
                       (uint64_t)P6C_JOURNAL_RECORD_BYTES;
        for (index = UINT64_C(0); index < record_count; ++index) {
            uint8_t record[P6C_JOURNAL_RECORD_BYTES];
            off_t offset = (off_t)(
                index * (uint64_t)P6C_JOURNAL_RECORD_BYTES);
            uint16_t type;

            CHECK(pread(
                      descriptor, record, sizeof(record), offset) ==
                  (ssize_t)sizeof(record));
            type = (uint16_t)(
                ((uint16_t)record[10] << 8) |
                (uint16_t)record[11]);
            if (type !=
                (uint16_t)P6C_OPERATION_TRANSCRIPTS_FINAL) {
                continue;
            }
            memcpy(
                &record[72], replacement_digest,
                P6C_SHA256_BYTES);
            CHECK(pwrite(
                      descriptor, record, sizeof(record), offset) ==
                  (ssize_t)sizeof(record));
            changed = true;
            break;
        }
        CHECK(changed);
        CHECK(test_rebuild_journal_chain(
                  descriptor, record_count) == EXIT_SUCCESS);
        CHECK(close(descriptor) == 0);
        CHECK(service_config_create(
                  &directory, sockets, &configuration, &peer) ==
              EXIT_SUCCESS);
        CHECK(service_build_request(
                  packet, sizeof(packet),
                  (uint16_t)P6C_REQUEST_RECOVER,
                  request_id, NULL, 0U) == sizeof(packet));
        p6c_test_service_io_set(
            packet, sizeof(packet), response, sizeof(response),
            &response_size);
        CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
        CHECK(response_size ==
              P6C_HEADER_SIZE + 4U +
                  P6C_OPERATION_SUMMARY_BYTES);
        CHECK(response[
                  P6C_HEADER_SIZE + 4U +
                  P6C_SUMMARY_STATE_OFFSET] ==
              UINT8_C(P6C_OPERATION_RECOVERY_REQUIRED));
        CHECK(service_config_destroy(
                  &configuration, sockets[1]) == EXIT_SUCCESS);
        CHECK(unlinkat(
                  directory.owner.descriptor, stdout_name, 0) == 0);
        CHECK(unlinkat(
                  directory.owner.descriptor, stderr_name, 0) == 0);
        CHECK(test_directory_close(
                  &directory, journal_name) == EXIT_SUCCESS);
    }
    return EXIT_SUCCESS;
}

static int case_legacy_truncated_record_rejected(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t stdout_digest[P6C_SHA256_BYTES];
    uint8_t stderr_digest[P6C_SHA256_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[512];
    size_t response_size = 0U;
    static const uint8_t FULL_STDOUT[] = "abcd";
    static const uint8_t RETAINED_STDOUT[] = "abc";
    static const char JOURNAL[] =
        "67676767676767676767676767676767.journal";
    static const char STDOUT_NAME[] =
        "67676767676767676767676767676767.stdout";
    static const char STDERR_NAME[] =
        "67676767676767676767676767676767.stderr";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x67));
    test_fill_identity(recovery_token, UINT8_C(0x68));
    test_fill_identity(request_id, UINT8_C(0x69));
    memset(executable_digest, UINT8_C(0x6a),
           sizeof(executable_digest));
    test_hash_bytes(
        FULL_STDOUT, sizeof(FULL_STDOUT) - 1U, stdout_digest);
    test_hash_bytes(NULL, 0U, stderr_digest);
    CHECK(service_create_retained_journal(
              &directory, JOURNAL, operation_id, recovery_token,
              NULL, executable_digest, stdout_digest, stderr_digest,
              (uint64_t)(sizeof(RETAINED_STDOUT) - 1U), UINT64_C(0),
              true, false) == EXIT_SUCCESS);
    CHECK(test_write_file(
              &directory, STDOUT_NAME, RETAINED_STDOUT,
              sizeof(RETAINED_STDOUT) - 1U,
              (mode_t)0600) == EXIT_SUCCESS);
    CHECK(test_write_file(
              &directory, STDERR_NAME, NULL, 0U,
              (mode_t)0600) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_RECOVER,
              request_id, NULL, 0U) == sizeof(packet));
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response),
        &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_size ==
          P6C_HEADER_SIZE + 4U + P6C_OPERATION_SUMMARY_BYTES);
    CHECK(response[
              P6C_HEADER_SIZE + 4U +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_RECOVERY_REQUIRED));
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor, STDOUT_NAME, 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor, STDERR_NAME, 0) == 0);
    CHECK(test_directory_close(&directory, JOURNAL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_startup_populated_cgroup(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct p6c_journal journal;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    struct p6c_owned_fd cgroup_directory;
    int sockets[2];
    int cgroup_descriptor;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t request_digest[P6C_SHA256_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t reserved[P6C_RESERVED_PAYLOAD_BYTES];
    uint8_t cgroup_created[P6C_CGROUP_CREATED_PAYLOAD_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[512];
    size_t response_size = 0U;
    static const uint8_t ZERO[] = "0";
    static const uint8_t EVENTS[] = "populated 1\nfrozen 0\n";
    static const char CGROUP_NAME[] =
        "p6c-71717171717171717171717171717171";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x71));
    test_fill_identity(recovery_token, UINT8_C(0x72));
    test_fill_identity(request_id, UINT8_C(0x74));
    memset(request_digest, UINT8_C(0x75), sizeof(request_digest));
    memset(executable_digest, UINT8_C(0x76),
           sizeof(executable_digest));
    memset(reserved, 0, sizeof(reserved));
    reserved[0] = UINT8_C(1);
    p6c_store_u32_be(&reserved[1], (uint32_t)getuid());
    memcpy(&reserved[5], recovery_token, P6C_RECOVERY_TOKEN_BYTES);
    memcpy(&reserved[21], request_digest, P6C_SHA256_BYTES);
    CHECK(p6c_journal_create(
              &directory.owner,
              "71717171717171717171717171717171.journal",
              operation_id, getuid(), &journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_RESERVED, reserved,
              sizeof(reserved)) == P6C_RESULT_OK);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_EXECUTABLE_PINNED,
              executable_digest, sizeof(executable_digest)) ==
          P6C_RESULT_OK);
    CHECK(p6c_journal_append_cgroup_allocation_intent(
              &journal, CGROUP_NAME) ==
          P6C_RESULT_OK);
    CHECK(mkdirat(
              directory.owner.descriptor, CGROUP_NAME,
              (mode_t)0700) == 0);
    cgroup_descriptor = openat(
        directory.owner.descriptor, CGROUP_NAME,
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(cgroup_descriptor >= 0);
    p6c_owned_fd_reset(&cgroup_directory);
    CHECK(p6c_owned_fd_acquire(
              &cgroup_directory, cgroup_descriptor,
              P6C_DESCRIPTOR_DIRECTORY) == P6C_RESULT_OK);
    memset(cgroup_created, 0, sizeof(cgroup_created));
    memcpy(
        &cgroup_created[P6C_CGROUP_CREATED_NAME_OFFSET],
        CGROUP_NAME, P6C_CGROUP_NAME_BYTES - 1U);
    test_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_DEVICE_OFFSET],
        (uint64_t)cgroup_directory.device);
    test_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_INODE_OFFSET],
        (uint64_t)cgroup_directory.inode);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_CGROUP_CREATED,
              cgroup_created, sizeof(cgroup_created)) ==
          P6C_RESULT_OK);
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    {
        struct test_directory cgroup_files;

        memset(&cgroup_files, 0, sizeof(cgroup_files));
        cgroup_files.owner = cgroup_directory;
        CHECK(test_write_file(
                  &cgroup_files, "cgroup.freeze", ZERO,
                  sizeof(ZERO) - 1U, (mode_t)0600) == EXIT_SUCCESS);
        CHECK(test_write_file(
                  &cgroup_files, "cgroup.kill", ZERO,
                  sizeof(ZERO) - 1U, (mode_t)0600) == EXIT_SUCCESS);
        CHECK(test_write_file(
                  &cgroup_files, "cgroup.events", EVENTS,
                  sizeof(EVENTS) - 1U, (mode_t)0600) == EXIT_SUCCESS);
    }
    CHECK(p6c_owned_fd_close(&cgroup_directory) == P6C_RESULT_OK);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_RECOVER,
              request_id, NULL, 0U) == sizeof(packet));
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response), &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(fake.calls[FAKE_STAGE_FREEZE] >= 1U);
    CHECK(fake.calls[FAKE_STAGE_KILL] >= 1U);
    CHECK(fake.calls[FAKE_STAGE_EMPTY] >= 1U);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    cgroup_descriptor = openat(
        directory.owner.descriptor,
        "p6c-71717171717171717171717171717171",
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(cgroup_descriptor >= 0);
    CHECK(unlinkat(cgroup_descriptor, "cgroup.events", 0) == 0);
    CHECK(unlinkat(cgroup_descriptor, "cgroup.kill", 0) == 0);
    CHECK(unlinkat(cgroup_descriptor, "cgroup.freeze", 0) == 0);
    CHECK(close(cgroup_descriptor) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "p6c-71717171717171717171717171717171",
              AT_REMOVEDIR) == 0);
    CHECK(test_directory_close(
              &directory,
              "71717171717171717171717171717171.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_startup_malformed_journal_name(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[128];
    size_t response_size = 0U;
    static const uint8_t CONTENT[] = "malformed";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(test_write_file(
              &directory,
              "GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG.journal",
              CONTENT, sizeof(CONTENT) - 1U, (mode_t)0600) ==
          EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    test_fill_identity(request_id, UINT8_C(0x81));
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_HELLO,
              request_id, NULL, 0U) == sizeof(packet));
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response), &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_UNSAFE);
    CHECK(response_size == 0U);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(
              &directory,
              "GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_recover_ordered_tombstone_torn(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct p6c_journal journal;
    enum p6c_journal_recovery recovery;
    int sockets[2];
    int torn_descriptor;
    uint8_t operation_ids[3][P6C_OPERATION_ID_BYTES];
    uint8_t recovery_tokens[3][P6C_RECOVERY_TOKEN_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t empty_digest[P6C_SHA256_BYTES];
    uint8_t request_ids[3][P6C_REQUEST_ID_BYTES];
    uint8_t zero_publication[P6C_SHA256_BYTES];
    uint8_t packets[3][256];
    const uint8_t *packet_pointers[3];
    size_t packet_sizes[3];
    uint8_t responses[
        (2U * (P6C_HEADER_SIZE + 4U +
               (3U * P6C_OPERATION_SUMMARY_BYTES))) +
        P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES];
    size_t response_sizes[3];
    size_t response_count = 0U;
    size_t second_offset;
    size_t third_offset;
    size_t index;
    static const uint8_t TORN[] = {
        UINT8_C(0x50), UINT8_C(0x36), UINT8_C(0x4a)
    };
    static const char *const JOURNALS[3] = {
        "11111111111111111111111111111111.journal",
        "22222222222222222222222222222222.journal",
        "33333333333333333333333333333333.journal"
    };

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(executable_digest, UINT8_C(0x91),
           sizeof(executable_digest));
    test_hash_bytes(NULL, 0U, empty_digest);
    memset(zero_publication, 0, sizeof(zero_publication));
    for (index = 0U; index < 3U; ++index) {
        test_fill_identity(
            operation_ids[index], (uint8_t)(0x11U * (index + 1U)));
        test_fill_identity(
            recovery_tokens[index], (uint8_t)(0xa1U + index));
        test_fill_identity(
            request_ids[index], (uint8_t)(0xb1U + index));
        packet_pointers[index] = packets[index];
    }
    CHECK(service_create_retained_journal(
              &directory, JOURNALS[0], operation_ids[0],
              recovery_tokens[0], NULL, executable_digest, empty_digest,
              empty_digest, UINT64_C(0), UINT64_C(0), false, true) ==
          EXIT_SUCCESS);
    CHECK(test_write_file(
              &directory,
              "11111111111111111111111111111111.stdout",
              NULL, 0U, (mode_t)0600) == EXIT_SUCCESS);
    CHECK(test_write_file(
              &directory,
              "11111111111111111111111111111111.stderr",
              NULL, 0U, (mode_t)0600) == EXIT_SUCCESS);
    CHECK(service_create_retained_journal(
              &directory, JOURNALS[1], operation_ids[1],
              recovery_tokens[1], NULL, executable_digest, empty_digest,
              empty_digest, UINT64_C(0), UINT64_C(0), false, true) ==
          EXIT_SUCCESS);
    CHECK(p6c_journal_recover(
              &directory.owner, JOURNALS[1], operation_ids[1],
              getuid(), &journal, &recovery) == P6C_RESULT_OK);
    CHECK(recovery == P6C_JOURNAL_COMPLETE);
    CHECK(p6c_journal_append(
              &journal, P6C_OPERATION_ACKNOWLEDGED, NULL, 0U) ==
          P6C_RESULT_OK);
    CHECK(p6c_journal_close(&journal) == P6C_RESULT_OK);
    CHECK(service_create_reserved_journal(
              &directory, JOURNALS[2], operation_ids[2],
              recovery_tokens[2], UINT8_C(0x93), getuid()) ==
          EXIT_SUCCESS);
    torn_descriptor = openat(
        directory.owner.descriptor, JOURNALS[2],
        O_WRONLY | O_APPEND | O_CLOEXEC | O_NOFOLLOW);
    CHECK(torn_descriptor >= 0);
    CHECK(write(torn_descriptor, TORN, sizeof(TORN)) ==
          (ssize_t)sizeof(TORN));
    CHECK(fsync(torn_descriptor) == 0);
    CHECK(close(torn_descriptor) == 0);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    packet_sizes[0] = service_build_request(
        packets[0], sizeof(packets[0]), (uint16_t)P6C_REQUEST_RECOVER,
        request_ids[0], NULL, 0U);
    packet_sizes[1] = service_build_publication_request(
        packets[1], sizeof(packets[1]), (uint16_t)P6C_REQUEST_ACK,
        request_ids[1], operation_ids[1], recovery_tokens[1],
        zero_publication);
    packet_sizes[2] = service_build_request(
        packets[2], sizeof(packets[2]), (uint16_t)P6C_REQUEST_RECOVER,
        request_ids[2], NULL, 0U);
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, 3U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 3U);
    CHECK(response_sizes[0] ==
          P6C_HEADER_SIZE + 4U +
              (3U * P6C_OPERATION_SUMMARY_BYTES));
    for (index = 0U; index < 3U; ++index) {
        size_t summary = P6C_HEADER_SIZE + 4U +
                         (index * P6C_OPERATION_SUMMARY_BYTES);

        CHECK(memcmp(
                  &responses[
                      summary + P6C_SUMMARY_OPERATION_ID_OFFSET],
                  operation_ids[index], P6C_OPERATION_ID_BYTES) == 0);
    }
    CHECK(responses[
              P6C_HEADER_SIZE + 4U +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_RESULT_RETAINED));
    CHECK(responses[
              P6C_HEADER_SIZE + 4U +
              P6C_OPERATION_SUMMARY_BYTES +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_ACKNOWLEDGED));
    CHECK(responses[
              P6C_HEADER_SIZE + 4U +
              (2U * P6C_OPERATION_SUMMARY_BYTES) +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_RECOVERY_REQUIRED));
    second_offset = response_sizes[0];
    CHECK(responses[
              second_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_ACK));
    CHECK(responses[
              second_offset + P6C_HEADER_SIZE +
              P6C_SUMMARY_STATE_OFFSET] ==
          UINT8_C(P6C_OPERATION_ACKNOWLEDGED));
    third_offset = second_offset + response_sizes[1];
    CHECK(response_sizes[2] == response_sizes[0]);
    CHECK(memcmp(
              &responses[third_offset + P6C_HEADER_SIZE + 4U],
              operation_ids[0], P6C_OPERATION_ID_BYTES) == 0);
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "11111111111111111111111111111111.stderr", 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor,
              "11111111111111111111111111111111.stdout", 0) == 0);
    for (index = 0U; index < 3U; ++index) {
        CHECK(unlinkat(
                  directory.owner.descriptor, JOURNALS[index], 0) == 0);
    }
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_peer_mismatch_matrix(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uid_t foreign_user = (getuid() == (uid_t)0) ?
                             (uid_t)1 :
                             (uid_t)0;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t publication_id[P6C_SHA256_BYTES];
    uint8_t request_ids[8][P6C_REQUEST_ID_BYTES];
    uint8_t packets[8][1024];
    const uint8_t *packet_pointers[8];
    size_t packet_sizes[8];
    uint8_t responses[(7U * 121U) + 40U];
    size_t response_sizes[8];
    size_t response_count = 0U;
    size_t response_offset = 0U;
    size_t index;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    test_fill_identity(operation_id, UINT8_C(0x66));
    test_fill_identity(recovery_token, UINT8_C(0x67));
    memset(executable_digest, UINT8_C(0x68),
           sizeof(executable_digest));
    memset(publication_id, UINT8_C(0x69), sizeof(publication_id));
    CHECK(service_create_reserved_journal(
              &directory,
              "66666666666666666666666666666666.journal",
              operation_id, recovery_token, UINT8_C(0x6a),
              foreign_user) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    for (index = 0U; index < 8U; ++index) {
        test_fill_identity(request_ids[index], (uint8_t)(0x70U + index));
        packet_pointers[index] = packets[index];
    }
    packet_sizes[0] = service_build_start_request(
        packets[0], sizeof(packets[0]), (uint16_t)P6C_REQUEST_START,
        request_ids[0], operation_id, executable_digest, "absent.elf");
    packet_sizes[1] = service_build_operation_request(
        packets[1], sizeof(packets[1]), (uint16_t)P6C_REQUEST_STATUS,
        request_ids[1], operation_id, recovery_token);
    packet_sizes[2] = service_build_operation_request(
        packets[2], sizeof(packets[2]), (uint16_t)P6C_REQUEST_STOP,
        request_ids[2], operation_id, recovery_token);
    packet_sizes[3] = service_build_start_request(
        packets[3], sizeof(packets[3]), (uint16_t)P6C_REQUEST_RUN_ONCE,
        request_ids[3], operation_id, executable_digest, "absent.elf");
    packet_sizes[4] = service_build_transcript_request(
        packets[4], sizeof(packets[4]), request_ids[4], operation_id,
        recovery_token, P6C_STREAM_STDOUT, UINT64_C(0), UINT32_C(1));
    packet_sizes[5] = service_build_publication_request(
        packets[5], sizeof(packets[5]),
        (uint16_t)P6C_REQUEST_PUBLISH_BUNDLE, request_ids[5],
        operation_id, recovery_token, publication_id);
    packet_sizes[6] = service_build_publication_request(
        packets[6], sizeof(packets[6]), (uint16_t)P6C_REQUEST_ACK,
        request_ids[6], operation_id, recovery_token, publication_id);
    packet_sizes[7] = service_build_request(
        packets[7], sizeof(packets[7]), (uint16_t)P6C_REQUEST_RECOVER,
        request_ids[7], NULL, 0U);
    for (index = 0U; index < 8U; ++index) {
        CHECK(packet_sizes[index] != 0U);
    }
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, 8U, responses, sizeof(responses),
        response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == 8U);
    for (index = 0U; index < 7U; ++index) {
        size_t token_index;

        CHECK(response_sizes[index] == 121U);
        CHECK(responses[
                  response_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
              UINT8_C(0xff));
        CHECK(responses[
                  response_offset + P6C_HEADER_SIZE + 1U] ==
              UINT8_C(P6C_STATUS_UNAUTHORIZED));
        for (token_index = 0U;
             token_index < P6C_RECOVERY_TOKEN_BYTES;
             ++token_index) {
            CHECK(responses[
                      response_offset + P6C_HEADER_SIZE + 4U +
                      token_index] == UINT8_C(0));
        }
        response_offset += response_sizes[index];
    }
    CHECK(response_sizes[7] == P6C_HEADER_SIZE + 4U);
    CHECK(responses[
              response_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_RECOVER));
    CHECK(responses[
              response_offset + P6C_HEADER_SIZE + 3U] == UINT8_C(0));
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(
              &directory,
              "66666666666666666666666666666666.journal") ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_registry_capacity(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct fake_process fake;
    struct p6c_process_adapter adapter;
    int sockets[2];
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t operation_ids[P6C_MAX_OPERATIONS + 1U][P6C_OPERATION_ID_BYTES];
    uint8_t request_ids[P6C_MAX_OPERATIONS + 1U][P6C_REQUEST_ID_BYTES];
    uint8_t packets[P6C_MAX_OPERATIONS + 1U][1024];
    const uint8_t *packet_pointers[P6C_MAX_OPERATIONS + 1U];
    size_t packet_sizes[P6C_MAX_OPERATIONS + 1U];
    uint8_t responses[4096];
    size_t response_sizes[P6C_MAX_OPERATIONS + 1U];
    size_t response_count = 0U;
    size_t final_offset = 0U;
    size_t index;
    static const char EXECUTABLE[] = "capacity.elf";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&fake, 0, sizeof(fake));
    fake.confirmation = P6C_EXEC_CONFIRM_CLEAN_EOF;
    adapter = fake_adapter(&fake);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(recovery_token, UINT8_C(0xd0));
    p6c_test_recovery_token_set(recovery_token);
    for (index = 0U; index < P6C_MAX_OPERATIONS + 1U; ++index) {
        test_fill_identity(
            operation_ids[index], (uint8_t)(0x80U + index));
        test_fill_identity(
            request_ids[index], (uint8_t)(0xc0U + index));
        packet_pointers[index] = packets[index];
        packet_sizes[index] = service_build_start_request(
            packets[index], sizeof(packets[index]),
            (uint16_t)P6C_REQUEST_START, request_ids[index],
            operation_ids[index], executable_digest, EXECUTABLE);
        CHECK(packet_sizes[index] != 0U);
    }
    p6c_test_service_io_set_packets(
        packet_pointers, packet_sizes, P6C_MAX_OPERATIONS + 1U,
        responses, sizeof(responses), response_sizes, &response_count);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_count == P6C_MAX_OPERATIONS + 1U);
    CHECK(fake.calls[FAKE_STAGE_CLONE] == P6C_MAX_OPERATIONS);
    for (index = 0U; index < P6C_MAX_OPERATIONS; ++index) {
        CHECK(response_sizes[index] ==
              P6C_HEADER_SIZE + P6C_OPERATION_SUMMARY_BYTES);
        final_offset += response_sizes[index];
    }
    CHECK(responses[
              final_offset + P6C_HEADER_MESSAGE_TYPE_OFFSET] ==
          UINT8_C(0xff));
    CHECK(responses[
              final_offset + P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_LIMIT_EXCEEDED));
    CHECK(service_config_destroy(&configuration, sockets[1]) == EXIT_SUCCESS);
    for (index = 0U; index < P6C_MAX_OPERATIONS; ++index) {
        char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
        char journal_name[41];

        test_operation_hex(operation_ids[index], operation_hex);
        CHECK(snprintf(
                  journal_name, sizeof(journal_name), "%s.journal",
                  operation_hex) == 40);
        CHECK(unlinkat(
                  directory.owner.descriptor, journal_name, 0) == 0);
    }
    CHECK(test_remove_service_transcripts(&directory) == EXIT_SUCCESS);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    {
        char rejected_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
        char rejected_journal[41];

        test_operation_hex(
            operation_ids[P6C_MAX_OPERATIONS], rejected_hex);
        CHECK(snprintf(
                  rejected_journal, sizeof(rejected_journal),
                  "%s.journal", rejected_hex) == 40);
        CHECK(faccessat(
                  directory.owner.descriptor, rejected_journal,
                  F_OK, AT_SYMLINK_NOFOLLOW) != 0);
        CHECK(errno == ENOENT);
    }
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_socketpair_hello(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t packet[P6C_HEADER_SIZE + 3U];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t response[256];
    size_t response_size = 0U;
    size_t index;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) ==
          0);
    memset(&configuration, 0, sizeof(configuration));
    p6c_owned_fd_reset(&configuration.socket);
    p6c_owned_fd_reset(&configuration.journal_root);
    p6c_owned_fd_reset(&configuration.source_root);
    p6c_owned_fd_reset(&configuration.cgroup_root);
    p6c_owned_fd_reset(&configuration.evidence_root);
    CHECK(p6c_owned_fd_acquire(&configuration.socket, sockets[0],
                               P6C_DESCRIPTOR_SOCKET) == P6C_RESULT_OK);
    CHECK(service_owned_duplicate(
              &directory.owner, P6C_DESCRIPTOR_DIRECTORY,
              &configuration.journal_root) == EXIT_SUCCESS);
    CHECK(service_owned_duplicate(
              &directory.owner, P6C_DESCRIPTOR_DIRECTORY,
              &configuration.source_root) == EXIT_SUCCESS);
    CHECK(service_owned_duplicate(
              &directory.owner, P6C_DESCRIPTOR_CGROUP,
              &configuration.cgroup_root) == EXIT_SUCCESS);
    CHECK(service_owned_duplicate(
              &directory.owner, P6C_DESCRIPTOR_DIRECTORY,
              &configuration.evidence_root) == EXIT_SUCCESS);
    configuration.controller_user = getuid();
    peer.process_id = getpid();
    peer.user_id = getuid();
    peer.group_id = getgid();
    p6c_test_peer_override_set(true, &peer);
    for (index = 0U; index < sizeof(request_id); ++index) {
        request_id[index] = (uint8_t)(index + 1U);
    }
    p6c_encode_header_v1(
        packet, (uint16_t)P6C_REQUEST_HELLO, request_id, UINT32_C(3),
        p6c_crc32((const uint8_t *)"abc", 3U));
    memcpy(&packet[P6C_HEADER_SIZE], "abc", 3U);
    p6c_test_service_io_set(
        packet, sizeof(packet), response, sizeof(response), &response_size);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    CHECK(response_size >= P6C_HEADER_SIZE);
    CHECK(response[P6C_HEADER_MAGIC_OFFSET] == UINT8_C(0x50));
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0x80));
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_HELLO));
    p6c_test_service_io_set(NULL, 0U, NULL, 0U, NULL);
    p6c_test_peer_override_set(false, NULL);
    CHECK(p6c_service_config_close(&configuration) == P6C_RESULT_OK);
    CHECK(close(sockets[1]) == 0);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_production_socket_seqpacket(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[128];
    ssize_t amount;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    test_fill_identity(request_id, UINT8_C(0xa7));
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_HELLO,
              request_id, NULL, 0U) == sizeof(packet));
    amount = send(
        sockets[1], packet, sizeof(packet), MSG_NOSIGNAL);
    CHECK(amount == (ssize_t)sizeof(packet));
    CHECK(shutdown(sockets[1], SHUT_WR) == 0);
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    amount = recv(sockets[1], response, sizeof(response), 0);
    CHECK(amount == (ssize_t)(P6C_HEADER_SIZE + 8U));
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0x80));
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(P6C_REQUEST_HELLO));
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int service_send_hello_with_control(
    int socket_descriptor, const uint8_t *packet, size_t packet_size,
    size_t descriptor_count, bool send_credentials)
{
    struct iovec vector;
    struct msghdr message;
    unsigned char control[
        CMSG_SPACE(sizeof(int) * (P6C_MAX_OPERATIONS + 4U)) +
        CMSG_SPACE(sizeof(struct ucred))];
    int descriptors[P6C_MAX_OPERATIONS + 4U];
    struct cmsghdr *header;
    size_t control_size = 0U;
    size_t index;
    ssize_t amount;

    if (descriptor_count >
        sizeof(descriptors) / sizeof(descriptors[0])) {
        return EXIT_FAILURE;
    }
    memset(&message, 0, sizeof(message));
    memset(control, 0, sizeof(control));
    for (index = 0U; index < descriptor_count; ++index) {
        descriptors[index] = open("/dev/null", O_RDONLY | O_CLOEXEC);
        if (descriptors[index] < 0) {
            while (index > 0U) {
                --index;
                (void)close(descriptors[index]);
            }
            return EXIT_FAILURE;
        }
    }
    vector.iov_base = (void *)packet;
    vector.iov_len = packet_size;
    message.msg_iov = &vector;
    message.msg_iovlen = 1U;
    if ((descriptor_count != 0U) || send_credentials) {
        message.msg_control = control;
        if (descriptor_count != 0U) {
            control_size += CMSG_SPACE(sizeof(int) * descriptor_count);
        }
        if (send_credentials) {
            control_size += CMSG_SPACE(sizeof(struct ucred));
        }
        message.msg_controllen = control_size;
        header = (struct cmsghdr *)(void *)control;
        if (descriptor_count != 0U) {
            header->cmsg_level = SOL_SOCKET;
            header->cmsg_type = SCM_RIGHTS;
            header->cmsg_len =
                CMSG_LEN(sizeof(int) * descriptor_count);
            memcpy(
                CMSG_DATA(header), descriptors,
                sizeof(int) * descriptor_count);
            header = (struct cmsghdr *)(void *)&control[
                CMSG_SPACE(sizeof(int) * descriptor_count)];
        }
        if (send_credentials) {
            struct ucred credentials;

            credentials.pid = getpid();
            credentials.uid = getuid();
            credentials.gid = getgid();
            header->cmsg_level = SOL_SOCKET;
            header->cmsg_type = SCM_CREDENTIALS;
            header->cmsg_len = CMSG_LEN(sizeof(credentials));
            memcpy(CMSG_DATA(header), &credentials, sizeof(credentials));
        }
        amount = sendmsg(socket_descriptor, &message, MSG_NOSIGNAL);
    } else {
        amount = send(socket_descriptor, packet, packet_size, MSG_NOSIGNAL);
    }
    for (index = 0U; index < descriptor_count; ++index) {
        if (close(descriptors[index]) != 0) {
            return EXIT_FAILURE;
        }
    }
    return (amount == (ssize_t)packet_size) ?
               EXIT_SUCCESS :
               EXIT_FAILURE;
}

static int service_ancillary_hello_case(
    size_t descriptor_count, bool pass_credentials)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    int sockets[2];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t packet[P6C_HEADER_SIZE];
    uint8_t response[256];
    ssize_t amount;
    pid_t service;
    int service_status = 0;

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    if (pass_credentials) {
        int enabled = 1;

        if (setsockopt(
                configuration.socket.descriptor, SOL_SOCKET,
                SO_PASSCRED, &enabled, sizeof(enabled)) != 0) {
            CHECK(errno == EPERM);
            CHECK(service_config_destroy(
                      &configuration, sockets[1]) == EXIT_SUCCESS);
            CHECK(test_directory_close(
                      &directory, NULL) == EXIT_SUCCESS);
            return EXIT_SUCCESS;
        }
    }
    test_fill_identity(
        request_id,
        (uint8_t)(UINT8_C(0xd0) + (uint8_t)descriptor_count +
                  (pass_credentials ? UINT8_C(1) : UINT8_C(0))));
    CHECK(service_build_request(
              packet, sizeof(packet), (uint16_t)P6C_REQUEST_HELLO,
              request_id, NULL, 0U) == sizeof(packet));
    service = fork();
    CHECK(service >= (pid_t)0);
    if (service == (pid_t)0) {
        enum p6c_result service_result;

        (void)close(sockets[1]);
        service_result = p6c_service_run(&configuration);
        _exit((service_result == P6C_RESULT_OK) ?
                  EXIT_SUCCESS :
                  EXIT_FAILURE);
    }
    CHECK(p6c_owned_fd_close(&configuration.socket) == P6C_RESULT_OK);
    CHECK(service_send_hello_with_control(
              sockets[1], packet, sizeof(packet),
              descriptor_count, pass_credentials) == EXIT_SUCCESS);
    amount = recv(sockets[1], response, sizeof(response), 0);
    if ((amount < 0) && (errno == EPERM)) {
        amount = read(sockets[1], response, sizeof(response));
    }
    CHECK(amount == (ssize_t)(P6C_HEADER_SIZE + 85U));
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET] == UINT8_C(0xff));
    CHECK(response[P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] ==
          UINT8_C(0xff));
    CHECK(response[P6C_HEADER_SIZE] == UINT8_C(0));
    CHECK(response[P6C_HEADER_SIZE + 1U] ==
          UINT8_C(P6C_STATUS_INVALID_FRAME));
    CHECK(close(sockets[1]) == 0);
    sockets[1] = P6C_INVALID_DESCRIPTOR;
    CHECK(waitpid(service, &service_status, 0) == service);
    CHECK(WIFEXITED(service_status));
    CHECK(WEXITSTATUS(service_status) == EXIT_SUCCESS);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, NULL) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_service_ancillary_rejection_matrix(void)
{
    CHECK(service_ancillary_hello_case(1U, false) == EXIT_SUCCESS);
    CHECK(service_ancillary_hello_case(2U, false) == EXIT_SUCCESS);
    CHECK(service_ancillary_hello_case(
              P6C_MAX_OPERATIONS + 4U, false) == EXIT_SUCCESS);
    CHECK(service_ancillary_hello_case(0U, true) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_production_disconnect_real_child(void)
{
    struct test_directory directory;
    struct p6c_service_config configuration;
    struct p6c_peer_identity peer;
    struct real_child_process real;
    struct p6c_process_adapter adapter;
    int sockets[2];
    int output_descriptor;
    uint8_t elf[128];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t recovery_token[P6C_RECOVERY_TOKEN_BYTES];
    uint8_t packet[1024];
    size_t packet_size;
    int status;
    pid_t peer_child = (pid_t)-1;
    uint8_t output[64];
    ssize_t output_size;
    static const char EXECUTABLE[] = "disconnect-real.elf";
    static const char JOURNAL[] =
        "abababababababababababababababab.journal";
    static const char STDOUT_NAME[] =
        "abababababababababababababababab.stdout";
    static const char STDERR_NAME[] =
        "abababababababababababababababab.stderr";
    static const uint8_t EXPECTED_OUTPUT[] =
        "real-disconnect-child-output\n";

    CHECK(test_directory_create(&directory) == EXIT_SUCCESS);
    memset(elf, UINT8_C(0x5a), sizeof(elf));
    elf[0] = UINT8_C(0x7f);
    elf[1] = UINT8_C('E');
    elf[2] = UINT8_C('L');
    elf[3] = UINT8_C('F');
    test_hash_bytes(elf, sizeof(elf), executable_digest);
    CHECK(test_write_file(
              &directory, EXECUTABLE, elf, sizeof(elf),
              (mode_t)0700) == EXIT_SUCCESS);
    CHECK(service_config_create(
              &directory, sockets, &configuration, &peer) == EXIT_SUCCESS);
    memset(&real, 0, sizeof(real));
    real.child = (pid_t)-1;
    real.service_socket = configuration.socket.descriptor;
    adapter = real_child_adapter(&real);
    p6c_test_service_process_adapter_set(&adapter);
    test_fill_identity(operation_id, UINT8_C(0xab));
    test_fill_identity(request_id, UINT8_C(0xac));
    test_fill_identity(recovery_token, UINT8_C(0xad));
    p6c_test_recovery_token_set(recovery_token);
    packet_size = service_build_start_request(
        packet, sizeof(packet), (uint16_t)P6C_REQUEST_START,
        request_id, operation_id, executable_digest, EXECUTABLE);
    CHECK(packet_size != 0U);
    if (send(sockets[1], packet, packet_size, MSG_NOSIGNAL) !=
        (ssize_t)packet_size) {
        CHECK(errno == EPERM);
        peer_child = fork();
        CHECK(peer_child >= 0);
        if (peer_child == 0) {
            uint8_t response[512];

            (void)close(sockets[0]);
            ssize_t response_size;

            if (write(sockets[1], packet, packet_size) !=
                (ssize_t)packet_size) {
                _exit(112);
            }
            response_size = read(sockets[1], response, sizeof(response));
            if (response_size <= 0) {
                _exit(113);
            }
            if ((response[P6C_HEADER_MESSAGE_TYPE_OFFSET] !=
                 UINT8_C(0x80)) ||
                (response[P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] !=
                 UINT8_C(P6C_REQUEST_START))) {
                _exit((response_size > (ssize_t)P6C_HEADER_SIZE) ?
                          120 + response[P6C_HEADER_SIZE + 1U] :
                          119);
            }
            (void)close(sockets[1]);
            _exit(EXIT_SUCCESS);
        }
        CHECK(close(sockets[1]) == 0);
        sockets[1] = P6C_INVALID_DESCRIPTOR;
    } else {
        CHECK(shutdown(sockets[1], SHUT_WR) == 0);
    }
    CHECK(p6c_service_run(&configuration) == P6C_RESULT_OK);
    if (peer_child > 0) {
        CHECK(waitpid(peer_child, &status, 0) == peer_child);
        CHECK(WIFEXITED(status) && (WEXITSTATUS(status) == EXIT_SUCCESS));
    }
    CHECK(real.child > 0);
    errno = 0;
    CHECK(waitpid(real.child, &status, WNOHANG) == -1);
    CHECK(errno == ECHILD);
    CHECK(real.kill_calls == 1U);
    CHECK(real.empty_calls == 1U);
    CHECK(real.observe_calls == 1U);
    CHECK(real.reap_calls == 1U);
    CHECK(real.transcript_calls == 1U);
    output_descriptor = openat(
        directory.owner.descriptor, STDOUT_NAME,
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(output_descriptor >= 0);
    output_size = read(output_descriptor, output, sizeof(output));
    CHECK(output_size == (ssize_t)(sizeof(EXPECTED_OUTPUT) - 1U));
    CHECK(memcmp(
              output, EXPECTED_OUTPUT,
              sizeof(EXPECTED_OUTPUT) - 1U) == 0);
    CHECK(close(output_descriptor) == 0);
    CHECK(service_config_destroy(
              &configuration, sockets[1]) == EXIT_SUCCESS);
    CHECK(unlinkat(
              directory.owner.descriptor, STDOUT_NAME, 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor, STDERR_NAME, 0) == 0);
    CHECK(unlinkat(
              directory.owner.descriptor, JOURNAL, 0) == 0);
    CHECK(test_remove_empty_service_cgroups(&directory) == EXIT_SUCCESS);
    CHECK(test_directory_close(&directory, EXECUTABLE) == EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int test_copy_delegated_executable(
    const struct test_directory *directory, const char *name,
    uint8_t digest[P6C_SHA256_BYTES])
{
    static const char *const CANDIDATES[] = {
        "/usr/bin/yes", "/bin/yes"
    };
    struct p6c_sha256 hash;
    uint8_t buffer[16384];
    int input = P6C_INVALID_DESCRIPTOR;
    int output = P6C_INVALID_DESCRIPTOR;
    size_t candidate;
    int result = EXIT_FAILURE;

    for (candidate = 0U;
         candidate < sizeof(CANDIDATES) / sizeof(CANDIDATES[0]);
         ++candidate) {
        input = open(
            CANDIDATES[candidate],
            O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (input >= 0) {
            break;
        }
    }
    if (input < 0) {
        return EXIT_FAILURE;
    }
    output = openat(
        directory->owner.descriptor, name,
        O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
        (mode_t)0700);
    if (output < 0) {
        (void)close(input);
        return EXIT_FAILURE;
    }
    p6c_sha256_init(&hash);
    for (;;) {
        ssize_t amount = read(input, buffer, sizeof(buffer));
        size_t written = 0U;

        if (amount < 0) {
            if (errno == EINTR) {
                continue;
            }
            goto cleanup;
        }
        if (amount == 0) {
            break;
        }
        if (p6c_sha256_update(
                &hash, buffer, (size_t)amount) != P6C_RESULT_OK) {
            goto cleanup;
        }
        while (written < (size_t)amount) {
            ssize_t output_amount = write(
                output, &buffer[written],
                (size_t)amount - written);

            if (output_amount < 0) {
                if (errno == EINTR) {
                    continue;
                }
                goto cleanup;
            }
            if (output_amount == 0) {
                goto cleanup;
            }
            written += (size_t)output_amount;
        }
    }
    if ((fchmod(output, (mode_t)0700) != 0) ||
        (fsync(output) != 0) ||
        (p6c_sha256_final(&hash, digest) != P6C_RESULT_OK)) {
        goto cleanup;
    }
    result = EXIT_SUCCESS;

cleanup:
    if (close(input) != 0) {
        result = EXIT_FAILURE;
    }
    if (close(output) != 0) {
        result = EXIT_FAILURE;
    }
    if ((result != EXIT_SUCCESS) &&
        (unlinkat(directory->owner.descriptor, name, 0) != 0) &&
        (errno != ENOENT)) {
        result = EXIT_FAILURE;
    }
    return result;
}

static int case_opt_in_delegated_cgroup_disconnect(void)
{
    const char *delegated_root = getenv(
        "P6C_DELEGATED_CGROUP_TEST_ROOT");
    struct test_directory journal_directory;
    struct test_directory source_directory;
    struct test_directory evidence_directory;
    struct p6c_service_config configuration;
    struct p6c_journal journal;
    enum p6c_journal_recovery recovery;
    struct stat root_status;
    struct statfs filesystem_status;
    struct stat stderr_status;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
    uint8_t request_id[P6C_REQUEST_ID_BYTES];
    uint8_t executable_digest[P6C_SHA256_BYTES];
    uint8_t packet[1024];
    uint8_t controller_response[512];
    uint8_t output[16];
    size_t packet_size;
    int sockets[2] = {
        P6C_INVALID_DESCRIPTOR, P6C_INVALID_DESCRIPTOR
    };
    int root_descriptor = P6C_INVALID_DESCRIPTOR;
    int delegation_descriptor = P6C_INVALID_DESCRIPTOR;
    int output_descriptor = P6C_INVALID_DESCRIPTOR;
    int stderr_descriptor = P6C_INVALID_DESCRIPTOR;
    pid_t controller = (pid_t)-1;
    int controller_status = 0;
    enum p6c_result service_result = P6C_RESULT_SYSTEM;
    bool configuration_ready = false;
    bool journal_ready = false;
    int outcome = EXIT_FAILURE;
    size_t index;
    static const char EXECUTABLE[] = "delegated-yes";
    static const char JOURNAL[] =
        "e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5.journal";
    static const char STDOUT_NAME[] =
        "e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5.stdout";
    static const char STDERR_NAME[] =
        "e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5.stderr";

    memset(&journal_directory, 0, sizeof(journal_directory));
    memset(&source_directory, 0, sizeof(source_directory));
    memset(&evidence_directory, 0, sizeof(evidence_directory));
    p6c_owned_fd_reset(&journal_directory.owner);
    p6c_owned_fd_reset(&source_directory.owner);
    p6c_owned_fd_reset(&evidence_directory.owner);
    memset(&configuration, 0, sizeof(configuration));
    p6c_owned_fd_reset(&configuration.socket);
    p6c_owned_fd_reset(&configuration.journal_root);
    p6c_owned_fd_reset(&configuration.source_root);
    p6c_owned_fd_reset(&configuration.cgroup_root);
    p6c_owned_fd_reset(&configuration.evidence_root);
    p6c_owned_fd_reset(&journal.file);
    if ((delegated_root == NULL) || (delegated_root[0] == '\0')) {
        (void)fprintf(
            stderr,
            "P6C_DELEGATED_CGROUP_TEST_ROOT is required\n");
        return EXIT_FAILURE;
    }
    if (delegated_root[0] != '/') {
        (void)fprintf(
            stderr, "delegated cgroup root must be absolute\n");
        return EXIT_FAILURE;
    }
    root_descriptor = open(
        delegated_root,
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if ((root_descriptor < 0) ||
        (fstat(root_descriptor, &root_status) != 0) ||
        !S_ISDIR(root_status.st_mode) ||
        (root_status.st_uid != geteuid()) ||
        ((root_status.st_mode & (S_IWGRP | S_IWOTH)) != 0) ||
        (fstatfs(root_descriptor, &filesystem_status) != 0) ||
        (filesystem_status.f_type != (long)CGROUP2_SUPER_MAGIC)) {
        (void)fprintf(
            stderr,
            "caller-supplied root is not an owned delegated cgroup v2 root\n");
        goto cleanup;
    }
    delegation_descriptor = openat(
        root_descriptor, "cgroup.controllers",
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (delegation_descriptor < 0) {
        (void)fprintf(stderr, "missing cgroup.controllers delegation\n");
        goto cleanup;
    }
    if (close(delegation_descriptor) != 0) {
        delegation_descriptor = P6C_INVALID_DESCRIPTOR;
        goto cleanup;
    }
    delegation_descriptor = openat(
        root_descriptor, "cgroup.subtree_control",
        O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    if (delegation_descriptor < 0) {
        (void)fprintf(
            stderr,
            "cgroup.subtree_control is not delegated writable\n");
        goto cleanup;
    }
    if (close(delegation_descriptor) != 0) {
        delegation_descriptor = P6C_INVALID_DESCRIPTOR;
        goto cleanup;
    }
    delegation_descriptor = P6C_INVALID_DESCRIPTOR;
    if ((test_directory_create(&journal_directory) != EXIT_SUCCESS) ||
        (test_directory_create(&source_directory) != EXIT_SUCCESS) ||
        (test_directory_create(&evidence_directory) != EXIT_SUCCESS) ||
        (test_copy_delegated_executable(
             &source_directory, EXECUTABLE,
             executable_digest) != EXIT_SUCCESS) ||
        (socketpair(
             AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0,
             sockets) != 0) ||
        (p6c_owned_fd_acquire(
             &configuration.socket, sockets[0],
             P6C_DESCRIPTOR_SOCKET) != P6C_RESULT_OK) ||
        (service_owned_duplicate(
             &journal_directory.owner, P6C_DESCRIPTOR_DIRECTORY,
             &configuration.journal_root) != EXIT_SUCCESS) ||
        (service_owned_duplicate(
             &source_directory.owner, P6C_DESCRIPTOR_DIRECTORY,
             &configuration.source_root) != EXIT_SUCCESS) ||
        (p6c_owned_fd_acquire(
             &configuration.cgroup_root, root_descriptor,
             P6C_DESCRIPTOR_CGROUP) != P6C_RESULT_OK) ||
        (service_owned_duplicate(
             &evidence_directory.owner, P6C_DESCRIPTOR_DIRECTORY,
             &configuration.evidence_root) != EXIT_SUCCESS)) {
        goto cleanup;
    }
    configuration.cgroup_root.type = P6C_DESCRIPTOR_CGROUP;
    root_descriptor = P6C_INVALID_DESCRIPTOR;
    configuration.controller_user = getuid();
    configuration_ready = true;
    test_fill_identity(operation_id, UINT8_C(0xe5));
    test_fill_identity(request_id, UINT8_C(0xe6));
    packet_size = service_build_start_request(
        packet, sizeof(packet), (uint16_t)P6C_REQUEST_START,
        request_id, operation_id, executable_digest, EXECUTABLE);
    if (packet_size == 0U) {
        goto cleanup;
    }
    controller = fork();
    if (controller < (pid_t)0) {
        goto cleanup;
    }
    if (controller == (pid_t)0) {
        ssize_t amount;

        (void)close(configuration.socket.descriptor);
        if (send(
                sockets[1], packet, packet_size,
                MSG_NOSIGNAL) != (ssize_t)packet_size) {
            _exit(91);
        }
        amount = recv(
            sockets[1], controller_response,
            sizeof(controller_response), 0);
        if ((amount !=
             (ssize_t)(P6C_HEADER_SIZE +
                       P6C_OPERATION_SUMMARY_BYTES)) ||
            (controller_response[
                 P6C_HEADER_MESSAGE_TYPE_OFFSET] != UINT8_C(0x80)) ||
            (controller_response[
                 P6C_HEADER_MESSAGE_TYPE_OFFSET + 1U] !=
             UINT8_C(P6C_REQUEST_START)) ||
            (controller_response[
                 P6C_HEADER_SIZE +
                 P6C_SUMMARY_STATE_OFFSET] !=
             UINT8_C(P6C_OPERATION_RUNNING))) {
            _exit(92);
        }
        if (close(sockets[1]) != 0) {
            _exit(93);
        }
        _exit(EXIT_SUCCESS);
    }
    if (close(sockets[1]) != 0) {
        sockets[1] = P6C_INVALID_DESCRIPTOR;
        goto cleanup;
    }
    sockets[1] = P6C_INVALID_DESCRIPTOR;
    service_result = p6c_service_run(&configuration);
    if ((waitpid(controller, &controller_status, 0) != controller) ||
        !WIFEXITED(controller_status) ||
        (WEXITSTATUS(controller_status) != EXIT_SUCCESS) ||
        (service_result != P6C_RESULT_OK)) {
        controller = (pid_t)-1;
        goto cleanup;
    }
    controller = (pid_t)-1;
    if ((p6c_journal_recover(
             &journal_directory.owner, JOURNAL, operation_id,
             getuid(), &journal, &recovery) != P6C_RESULT_OK) ||
        (recovery != P6C_JOURNAL_COMPLETE) ||
        (journal.durable_state != P6C_OPERATION_RESULT_RETAINED) ||
        !journal.cgroup_allocation_intent ||
        !journal.cgroup_created_identity ||
        !journal.cgroup_removal_intent ||
        (journal.state_payload_lengths[
             P6C_OPERATION_CHILD_EXIT_OBSERVED] != 4U) ||
        (journal.state_payload_lengths[
             P6C_OPERATION_TRANSCRIPTS_FINAL] !=
         P6C_TRANSCRIPTS_PAYLOAD_BYTES) ||
        (journal.state_payload_lengths[
             P6C_OPERATION_RESULT_RETAINED] !=
         P6C_RESULT_PAYLOAD_BYTES)) {
        goto cleanup;
    }
    journal_ready = true;
    errno = 0;
    if ((faccessat(
             configuration.cgroup_root.descriptor,
             journal.cgroup_allocation_name,
             F_OK, AT_SYMLINK_NOFOLLOW) == 0) ||
        (errno != ENOENT)) {
        goto cleanup;
    }
    output_descriptor = openat(
        journal_directory.owner.descriptor, STDOUT_NAME,
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    stderr_descriptor = openat(
        journal_directory.owner.descriptor, STDERR_NAME,
        O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if ((output_descriptor < 0) || (stderr_descriptor < 0) ||
        (read(output_descriptor, output, sizeof(output)) !=
         (ssize_t)sizeof(output)) ||
        (fstat(stderr_descriptor, &stderr_status) != 0) ||
        (stderr_status.st_size != (off_t)0)) {
        goto cleanup;
    }
    for (index = 0U; index < sizeof(output); ++index) {
        uint8_t expected =
            ((index % 2U) == 0U) ? UINT8_C('y') : UINT8_C('\n');

        if (output[index] != expected) {
            goto cleanup;
        }
    }
    errno = 0;
    if ((waitpid((pid_t)-1, &controller_status, WNOHANG) !=
         (pid_t)-1) ||
        (errno != ECHILD)) {
        goto cleanup;
    }
    outcome = EXIT_SUCCESS;

cleanup:
    if (delegation_descriptor >= 0) {
        (void)close(delegation_descriptor);
    }
    if (root_descriptor >= 0) {
        (void)close(root_descriptor);
    }
    if (sockets[1] >= 0) {
        (void)close(sockets[1]);
    }
    if (controller > (pid_t)0) {
        (void)kill(controller, SIGKILL);
        (void)waitpid(controller, &controller_status, 0);
    }
    if (output_descriptor >= 0) {
        (void)close(output_descriptor);
    }
    if (stderr_descriptor >= 0) {
        (void)close(stderr_descriptor);
    }
    if (journal_ready) {
        (void)p6c_journal_close(&journal);
    }
    if (configuration_ready &&
        (p6c_service_config_close(&configuration) != P6C_RESULT_OK)) {
        outcome = EXIT_FAILURE;
    }
    if (p6c_owned_fd_is_live(&journal_directory.owner)) {
        if (((unlinkat(
                  journal_directory.owner.descriptor,
                  STDOUT_NAME, 0) != 0) &&
             (errno != ENOENT)) ||
            ((unlinkat(
                  journal_directory.owner.descriptor,
                  STDERR_NAME, 0) != 0) &&
             (errno != ENOENT)) ||
            ((unlinkat(
                  journal_directory.owner.descriptor,
                  JOURNAL, 0) != 0) &&
             (errno != ENOENT)) ||
            (test_directory_close(
                 &journal_directory, NULL) != EXIT_SUCCESS)) {
            outcome = EXIT_FAILURE;
        }
    }
    if (p6c_owned_fd_is_live(&source_directory.owner) &&
        (test_directory_close(
             &source_directory, EXECUTABLE) != EXIT_SUCCESS)) {
        outcome = EXIT_FAILURE;
    }
    if (p6c_owned_fd_is_live(&evidence_directory.owner) &&
        (test_directory_close(
             &evidence_directory, NULL) != EXIT_SUCCESS)) {
        outcome = EXIT_FAILURE;
    }
    return outcome;
}
struct test_case {
    const char *name;
    int (*function)(void);
};

static const struct test_case TEST_CASES[] = {
    {"sha256_vectors", case_sha256_vectors},
    {"owned_close_once", case_owned_close_once},
    {"descriptor_reuse", case_descriptor_reuse},
    {"partial_pair", case_partial_pair},
    {"pipe_acquisition_failure_matrix",
     case_pipe_acquisition_failure_matrix},
    {"pipe_end_blocking_flags", case_pipe_end_blocking_flags},
    {"journal_chain", case_journal_chain},
    {"journal_torn_tail", case_journal_torn_tail},
    {"journal_impossible_transition", case_journal_impossible_transition},
    {"journal_fsync_failure", case_journal_fsync_failure},
    {"journal_sequence_duplicate", case_journal_sequence_duplicate},
    {"journal_sequence_gap", case_journal_sequence_gap},
    {"journal_v1_rejected", case_journal_v1_rejected},
    {"journal_prior_digest", case_journal_prior_digest},
    {"journal_payload_digest", case_journal_payload_digest},
    {"journal_corrupt_transition", case_journal_corrupt_transition},
    {"journal_unknown_record", case_journal_unknown_record},
    {"journal_unsafe_objects", case_journal_unsafe_objects},
    {"credential_authority_revalidated_before_clone",
     case_credential_authority_revalidated_before_clone},
    {"executable_authority", case_executable_authority},
    {"executable_replacement_during_hash",
     case_executable_replacement_during_hash},
    {"peer_and_replay", case_peer_and_replay},
    {"production_peer_credentials", case_production_peer_credentials},
    {"production_pidfd_observe_reap",
     case_production_pidfd_observe_reap},
    {"transcript_truncation", case_transcript_truncation},
    {"transcript_faults", case_transcript_faults},
    {"production_blocking_pipe_drain",
     case_production_blocking_pipe_drain},
    {"truncated_retained_prefix_tamper",
     case_truncated_retained_prefix_tamper},
    {"untruncated_digest_contradiction",
     case_untruncated_digest_contradiction},
    {"transcript_recovery_both_zero_streams",
     case_transcript_recovery_both_zero_streams},
    {"process_success_stop_ack", case_process_success_stop_ack},
    {"process_repeated_stop", case_process_repeated_stop},
    {"boundary_child_cloned", case_boundary_child_cloned},
    {"boundary_exec_confirmed", case_boundary_exec_confirmed},
    {"boundary_running", case_boundary_running},
    {"boundary_stop_requested", case_boundary_stop_requested},
    {"boundary_cgroup_killed", case_boundary_cgroup_killed},
    {"boundary_cgroup_empty", case_boundary_cgroup_empty},
    {"boundary_child_exit_observed", case_boundary_child_exit_observed},
    {"boundary_child_reaped", case_boundary_child_reaped},
    {"boundary_transcripts_final", case_boundary_transcripts_final},
    {"boundary_result_retained", case_boundary_result_retained},
    {"operation_acquisition_failures",
     case_operation_acquisition_failures},
    {"clone3_errno_classification", case_clone3_errno_classification},
    {"cgroup_fake_files", case_cgroup_fake_files},
    {"cgroup_remove_substitution_window",
     case_cgroup_remove_substitution_window},
    {"exec_marker_bytes", case_exec_marker_bytes},
    {"exec_marker_partial", case_exec_marker_partial},
    {"exec_marker_timeout", case_exec_marker_timeout},
    {"exec_marker_quick_exit", case_exec_marker_quick_exit},
    {"exec_marker_error", case_exec_marker_error},
    {"post_clone_pidfd_acquire_failure",
     case_post_clone_pidfd_acquire_failure},
    {"post_clone_status_writer_close_failure",
     case_post_clone_status_writer_close_failure},
    {"post_clone_stdout_writer_close_failure",
     case_post_clone_stdout_writer_close_failure},
    {"post_clone_stderr_writer_close_failure",
     case_post_clone_stderr_writer_close_failure},
    {"post_clone_child_journal_failure_cleanup",
     case_post_clone_child_journal_failure_cleanup},
    {"stop_freeze_error", case_stop_freeze_error},
    {"stop_signal_error", case_stop_signal_error},
    {"stop_grace_error", case_stop_grace_error},
    {"stop_kill_error", case_stop_kill_error},
    {"stop_populated_timeout", case_stop_populated_timeout},
    {"stop_observe_error", case_stop_observe_error},
    {"stop_reap_error", case_stop_reap_error},
    {"stop_remove_error", case_stop_remove_error},
    {"stop_transcript_error", case_stop_transcript_error},
    {"removal_intent_failure_prevents_remove",
     case_removal_intent_failure_prevents_remove},
    {"result_append_failure_after_remove",
     case_result_append_failure_after_remove},
    {"restart_removal_intent_absent",
     case_restart_removal_intent_absent},
    {"restart_removal_intent_present_empty",
     case_restart_removal_intent_present_empty},
    {"restart_removal_intent_replacement_rejected",
     case_restart_removal_intent_replacement_rejected},
    {"restart_removal_intent_populated_rejected",
     case_restart_removal_intent_populated_rejected},
    {"restart_cgroup_before_mkdir",
     case_restart_cgroup_before_mkdir},
    {"restart_cgroup_after_mkdir",
     case_restart_cgroup_after_mkdir},
    {"restart_cgroup_after_created_append",
     case_restart_cgroup_after_created_append},
    {"retained_digest_record_rebind",
     case_retained_digest_record_rebind},
    {"full_stream_digest_independent_tamper",
     case_full_stream_digest_independent_tamper},
    {"disconnect_cleanup_failure_matrix",
     case_disconnect_cleanup_failure_matrix},
    {"service_start_dispatches", case_service_start_dispatches},
    {"prechild_created_append_failure_cleanup",
     case_prechild_created_append_failure_cleanup},
    {"disconnect_immediately_after_child_custody",
     case_disconnect_immediately_after_child_custody},
    {"disconnect_after_running", case_disconnect_after_running},
    {"disconnect_during_transcript_output",
     case_disconnect_during_transcript_output},
    {"disconnect_send_failure_after_start",
     case_disconnect_send_failure_after_start},
    {"disconnect_receive_failure_active",
     case_disconnect_receive_failure_active},
    {"disconnect_cleanup_retry_all_stages",
     case_disconnect_cleanup_retry_all_stages},
    {"disconnect_cleanup_retries_past_legacy_cap",
     case_disconnect_cleanup_retries_past_legacy_cap},
    {"disconnect_cleanup_held_authority",
     case_disconnect_cleanup_held_authority},
    {"service_replay_identical_request",
     case_service_replay_identical_request},
    {"service_replay_restart_duplicate",
     case_service_replay_restart_duplicate},
    {"service_replay_restart_uid_mismatch",
     case_service_replay_restart_uid_mismatch},
    {"service_replay_ledger_hardening",
     case_service_replay_ledger_hardening},
    {"service_replay_changed_payload_collision",
     case_service_replay_changed_payload_collision},
    {"service_replay_capacity_before_dispatch",
     case_service_replay_capacity_before_dispatch},
    {"service_replay_malformed_does_not_consume",
     case_service_replay_malformed_does_not_consume},
    {"service_ack_rejection_matrix", case_service_ack_rejection_matrix},
    {"service_read_rejection_matrix", case_service_read_rejection_matrix},
    {"service_publish_repeat_conflict",
     case_service_publish_repeat_conflict},
    {"service_peer_mismatch_matrix", case_service_peer_mismatch_matrix},
    {"service_recover_ordered_tombstone_torn",
     case_service_recover_ordered_tombstone_torn},
    {"service_tombstone_exact_exhaustion",
     case_service_tombstone_exact_exhaustion},
    {"service_tombstone_startup_over_capacity",
     case_service_tombstone_startup_over_capacity},
    {"duplicate_tombstone_fails_closed",
     case_duplicate_tombstone_fails_closed},
    {"service_registry_capacity", case_service_registry_capacity},
    {"service_response_failures_recover",
     case_service_response_failures_recover},
    {"service_restart_retained_transcript",
     case_service_restart_retained_transcript},
    {"valid_rebuilt_untruncated_digest_conflicts",
     case_valid_rebuilt_untruncated_digest_conflicts},
    {"legacy_truncated_record_rejected",
     case_legacy_truncated_record_rejected},
    {"service_run_once_read_ack", case_service_run_once_read_ack},
    {"service_start_replay", case_service_start_replay},
    {"service_start_status_stop", case_service_start_status_stop},
    {"service_startup_malformed_journal_name",
     case_service_startup_malformed_journal_name},
    {"service_startup_populated_cgroup",
     case_service_startup_populated_cgroup},
    {"service_startup_recover_enumerates",
     case_service_startup_recover_enumerates},
    {"service_socketpair_hello", case_service_socketpair_hello},
    {"production_socket_seqpacket", case_production_socket_seqpacket},
    {"service_ancillary_rejection_matrix",
     case_service_ancillary_rejection_matrix},
    {"production_disconnect_real_child",
     case_production_disconnect_real_child},
};

int main(int argc, char *argv[])
{
    size_t index;

    if ((argc == 2) &&
        (strcmp(argv[1], "--opt-in-delegated-cgroup") == 0)) {
        return case_opt_in_delegated_cgroup_disconnect();
    }
    if ((argc == 2) && (strcmp(argv[1], "--list") == 0)) {
        for (index = 0U;
             index < sizeof(TEST_CASES) / sizeof(TEST_CASES[0]);
             ++index) {
            (void)puts(TEST_CASES[index].name);
        }
        return EXIT_SUCCESS;
    }
    if (argc != 2) {
        (void)fputs("usage: test_authority CASE\n", stderr);
        return EXIT_FAILURE;
    }
    for (index = 0U;
         index < sizeof(TEST_CASES) / sizeof(TEST_CASES[0]);
         ++index) {
        if (strcmp(argv[1], TEST_CASES[index].name) == 0) {
            int result = TEST_CASES[index].function();

            if (result == EXIT_SUCCESS) {
                (void)printf("%s=PASS\n", TEST_CASES[index].name);
            }
            return result;
        }
    }
    (void)fprintf(stderr, "unknown test case: %s\n", argv[1]);
    return EXIT_FAILURE;
}
