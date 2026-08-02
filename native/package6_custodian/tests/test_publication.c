#include "p6c_types.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>


#define CHECK(condition)                                                          \
    do {                                                                          \
        if (!(condition)) {                                                       \
            (void)fprintf(stderr,                                                 \
                          "test_publication: check failed at line %d\n",          \
                          __LINE__);                                               \
            return EXIT_FAILURE;                                                  \
        }                                                                         \
    } while (0)

struct publication_fixture {
    char path[128];
    struct p6c_owned_fd root;
    struct p6c_journal journal;
    uint8_t operation_id[P6C_OPERATION_ID_BYTES];
};

static void publication_id_hex(
    const uint8_t identity[P6C_OPERATION_ID_BYTES],
    char output[(P6C_OPERATION_ID_BYTES * 2U) + 1U])
{
    static const char HEX[] = "0123456789abcdef";
    size_t index;

    for (index = 0U; index < P6C_OPERATION_ID_BYTES; ++index) {
        output[index * 2U] = HEX[identity[index] >> 4];
        output[(index * 2U) + 1U] =
            HEX[identity[index] & UINT8_C(0x0f)];
    }
    output[P6C_OPERATION_ID_BYTES * 2U] = '\0';
}

static void publication_store_u64_be(
    uint8_t output[static 8], uint64_t value)
{
    size_t index;

    for (index = 0U; index < 8U; ++index) {
        output[7U - index] = (uint8_t)(value >> (index * 8U));
    }
}

static int publication_advance_existing_journal(
    const struct p6c_owned_fd *root,
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    struct p6c_journal *journal)
{
    char operation_hex[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
    char cgroup_name[P6C_CGROUP_NAME_BYTES];
    uint8_t cgroup_created[P6C_CGROUP_CREATED_PAYLOAD_BYTES];
    int state;

    publication_id_hex(operation_id, operation_hex);
    if (snprintf(cgroup_name, sizeof(cgroup_name), "p6c-%s",
                 operation_hex) != 36) {
        return EXIT_FAILURE;
    }
    memset(cgroup_created, 0, sizeof(cgroup_created));
    memcpy(cgroup_created, cgroup_name, P6C_CGROUP_NAME_BYTES - 1U);
    publication_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_DEVICE_OFFSET],
        (uint64_t)root->device);
    publication_store_u64_be(
        &cgroup_created[P6C_CGROUP_CREATED_INODE_OFFSET],
        (uint64_t)root->inode);
    if ((p6c_journal_append(
             journal, P6C_OPERATION_RESERVED, NULL, 0U) !=
         P6C_RESULT_OK) ||
        (p6c_journal_append(
             journal, P6C_OPERATION_EXECUTABLE_PINNED, NULL, 0U) !=
         P6C_RESULT_OK) ||
        (p6c_journal_append_cgroup_allocation_intent(
             journal, cgroup_name) != P6C_RESULT_OK) ||
        (p6c_journal_append(
             journal, P6C_OPERATION_CGROUP_CREATED,
             cgroup_created, sizeof(cgroup_created)) != P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    for (state = P6C_OPERATION_CHILD_CLONED;
         state <= P6C_OPERATION_RESULT_RETAINED;
         ++state) {
        if (p6c_journal_append(
                journal, (enum p6c_operation_state)state,
                NULL, 0U) != P6C_RESULT_OK) {
            return EXIT_FAILURE;
        }
    }
    return EXIT_SUCCESS;
}

static int publication_fixture_create(struct publication_fixture *fixture)
{
    char template[] = "/tmp/p6c-publication-XXXXXX";
    char *created;
    int descriptor;

    memset(fixture, 0, sizeof(*fixture));
    p6c_owned_fd_reset(&fixture->root);
    created = mkdtemp(template);
    if (created == NULL) {
        return EXIT_FAILURE;
    }
    if (strlen(created) >= sizeof(fixture->path)) {
        return EXIT_FAILURE;
    }
    (void)strcpy(fixture->path, created);
    descriptor = open(created, O_RDONLY | O_DIRECTORY | O_CLOEXEC |
                                   O_NOFOLLOW);
    if ((descriptor < 0) ||
        (p6c_owned_fd_acquire(&fixture->root, descriptor,
                              P6C_DESCRIPTOR_DIRECTORY) !=
         P6C_RESULT_OK)) {
        return EXIT_FAILURE;
    }
    memset(fixture->operation_id, UINT8_C(0x91),
           sizeof(fixture->operation_id));
    if (p6c_journal_create(&fixture->root, "publication.journal",
                           fixture->operation_id, getuid(),
                           &fixture->journal) != P6C_RESULT_OK) {
        return EXIT_FAILURE;
    }
    if (publication_advance_existing_journal(
            &fixture->root, fixture->operation_id,
            &fixture->journal) != EXIT_SUCCESS) {
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static void publication_remove_known_files(
    const struct p6c_owned_fd *directory)
{
    static const char *const NAMES[] = {
        "result.json", "stdout.bin", "manifest.json"
    };
    size_t index;

    if ((directory == NULL) || !p6c_owned_fd_is_live(directory)) {
        return;
    }
    for (index = 0U; index < sizeof(NAMES) / sizeof(NAMES[0]); ++index) {
        if ((unlinkat(directory->descriptor, NAMES[index], 0) != 0) &&
            (errno != ENOENT)) {
            abort();
        }
    }
}

static int publication_fixture_close(
    struct publication_fixture *fixture,
    struct p6c_publication_result *publication)
{
    int result = EXIT_SUCCESS;

    if (publication != NULL) {
        if (publication->renamed) {
            publication_remove_known_files(
                p6c_owned_fd_is_live(&publication->committed_directory) ?
                    &publication->committed_directory :
                    &publication->staging_directory);
        } else {
            publication_remove_known_files(
                &publication->staging_directory);
        }
        if (p6c_publication_close(publication) != P6C_RESULT_OK) {
            result = EXIT_FAILURE;
        }
        if (publication->renamed) {
            if ((unlinkat(fixture->root.descriptor,
                          publication->generation_name,
                          AT_REMOVEDIR) != 0) &&
                (errno != ENOENT)) {
                result = EXIT_FAILURE;
            }
        } else if (publication->staging_name[0] != '\0') {
            if ((unlinkat(fixture->root.descriptor,
                          publication->staging_name,
                          AT_REMOVEDIR) != 0) &&
                (errno != ENOENT)) {
                result = EXIT_FAILURE;
            }
        }
    }
    if (p6c_journal_close(&fixture->journal) != P6C_RESULT_OK) {
        result = EXIT_FAILURE;
    }
    if ((unlinkat(fixture->root.descriptor,
                  "publication.journal", 0) != 0) &&
        (errno != ENOENT)) {
        result = EXIT_FAILURE;
    }
    if (p6c_owned_fd_close(&fixture->root) != P6C_RESULT_OK) {
        result = EXIT_FAILURE;
    }
    if ((rmdir(fixture->path) != 0) && (errno != ENOENT)) {
        result = EXIT_FAILURE;
    }
    return result;
}

static const uint8_t RESULT_CONTENT[] = "{\"status\":\"paper\"}\n";
static const uint8_t STDOUT_CONTENT[] = "bounded transcript\n";

static void publication_items(struct p6c_publication_item items[2])
{
    items[0].name = "result.json";
    items[0].content = RESULT_CONTENT;
    items[0].content_length = sizeof(RESULT_CONTENT) - 1U;
    items[0].candidate_identity = "candidate-placeholder";
    items[1].name = "stdout.bin";
    items[1].content = STDOUT_CONTENT;
    items[1].content_length = sizeof(STDOUT_CONTENT) - 1U;
    items[1].candidate_identity = "candidate-placeholder";
}

static int case_publication_success(void)
{
    struct publication_fixture fixture;
    struct p6c_publication_result publication;
    struct p6c_publication_item items[2];
    char expected_generation[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
    int manifest;
    char buffer[P6C_MAX_MANIFEST_BYTES + 1U];
    ssize_t amount;
    enum p6c_result publish_result;
    enum p6c_journal_recovery recovery;

    CHECK(publication_fixture_create(&fixture) == EXIT_SUCCESS);
    publication_items(items);
    memset(&publication, 0, sizeof(publication));
    publish_result = p6c_publish_bundle(
        &fixture.root, fixture.operation_id,
        P6C_OPERATION_RESULT_RETAINED, items, 2U,
        &fixture.journal, &publication);
    if (publish_result != P6C_RESULT_OK) {
        (void)fprintf(stderr, "publish result=%d renamed=%d verified=%d\n",
                      (int)publish_result, publication.renamed ? 1 : 0,
                      publication.verified ? 1 : 0);
        return EXIT_FAILURE;
    }
    CHECK(publication.renamed);
    CHECK(publication.verified);
    CHECK(!publication.recovery_required);
    CHECK(fixture.journal.bundle_committed);
    publication_id_hex(fixture.operation_id, expected_generation);
    CHECK(strcmp(publication.generation_name, expected_generation) == 0);
    manifest = openat(publication.committed_directory.descriptor,
                      "manifest.json",
                      O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(manifest >= 0);
    amount = read(manifest, buffer, sizeof(buffer) - 1U);
    CHECK(amount > 0);
    buffer[(size_t)amount] = '\0';
    CHECK(strstr(buffer, "\"live_execution\":false") != NULL);
    CHECK(strstr(buffer, "\"live_trading\":false") != NULL);
    CHECK(strstr(buffer, "\"cleanup_state\":13") != NULL);
    CHECK(strstr(buffer, "\"candidate\":\"candidate-placeholder\"") !=
          NULL);
    CHECK(close(manifest) == 0);
    CHECK(p6c_journal_close(&fixture.journal) == P6C_RESULT_OK);
    CHECK(p6c_journal_recover(
              &fixture.root, "publication.journal",
              fixture.operation_id, getuid(), &fixture.journal,
              &recovery) == P6C_RESULT_OK);
    CHECK(recovery == P6C_JOURNAL_COMPLETE);
    CHECK(fixture.journal.bundle_committed);
    CHECK(fixture.journal.durable_state ==
          P6C_OPERATION_RESULT_RETAINED);
    CHECK(publication_fixture_close(&fixture, &publication) ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int publication_fault_case(enum p6c_failpoint failpoint,
                                  bool expect_renamed)
{
    struct publication_fixture fixture;
    struct p6c_publication_result publication;
    struct p6c_publication_item items[2];
    struct stat status;
    char generation[(P6C_OPERATION_ID_BYTES * 2U) + 1U];

    CHECK(publication_fixture_create(&fixture) == EXIT_SUCCESS);
    publication_items(items);
    memset(&publication, 0, sizeof(publication));
    p6c_test_failpoint_set(failpoint);
    CHECK(p6c_publish_bundle(
              &fixture.root, fixture.operation_id,
              P6C_OPERATION_RESULT_RETAINED, items, 2U,
              &fixture.journal, &publication) ==
          P6C_RESULT_RECOVERY_REQUIRED);
    p6c_test_failpoint_set(P6C_FAIL_NONE);
    CHECK(publication.recovery_required);
    CHECK(publication.renamed == expect_renamed);
    CHECK(expect_renamed ?
              (p6c_owned_fd_is_live(&publication.committed_directory) ||
               p6c_owned_fd_is_live(&publication.staging_directory)) :
              p6c_owned_fd_is_live(&publication.staging_directory));
    publication_id_hex(fixture.operation_id, generation);
    if (expect_renamed) {
        CHECK(fstatat(fixture.root.descriptor, generation, &status,
                      AT_SYMLINK_NOFOLLOW) == 0);
        CHECK(S_ISDIR(status.st_mode));
    } else {
        CHECK(fstatat(fixture.root.descriptor, generation, &status,
                      AT_SYMLINK_NOFOLLOW) != 0);
        CHECK(errno == ENOENT);
    }
    CHECK(publication_fixture_close(&fixture, &publication) ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int case_publication_partial_write(void)
{
    return publication_fault_case(P6C_FAIL_PUBLICATION_WRITE, false);
}

static int case_publication_file_fsync(void)
{
    return publication_fault_case(P6C_FAIL_PUBLICATION_FILE_FSYNC, false);
}

static int case_publication_manifest(void)
{
    return publication_fault_case(P6C_FAIL_PUBLICATION_MANIFEST, false);
}

static int case_publication_rename(void)
{
    return publication_fault_case(P6C_FAIL_PUBLICATION_RENAME, false);
}

static int case_publication_post_commit_fsync(void)
{
    return publication_fault_case(P6C_FAIL_PUBLICATION_ROOT_FSYNC, true);
}

static int case_publication_post_commit_verify(void)
{
    return publication_fault_case(P6C_FAIL_PUBLICATION_VERIFY, true);
}

static int case_publication_collision_preserves_foreign(void)
{
    struct publication_fixture fixture;
    struct p6c_publication_result publication;
    struct p6c_publication_item items[2];
    char generation[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
    int directory;
    int sentinel;
    char content[16];
    ssize_t amount;

    CHECK(publication_fixture_create(&fixture) == EXIT_SUCCESS);
    publication_items(items);
    publication_id_hex(fixture.operation_id, generation);
    CHECK(mkdirat(fixture.root.descriptor, generation, (mode_t)0700) == 0);
    directory = openat(fixture.root.descriptor, generation,
                       O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(directory >= 0);
    sentinel = openat(directory, "foreign.sentinel",
                      O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC |
                          O_NOFOLLOW,
                      (mode_t)0600);
    CHECK(sentinel >= 0);
    CHECK(write(sentinel, "foreign\n", 8U) == 8);
    CHECK(close(sentinel) == 0);
    memset(&publication, 0, sizeof(publication));
    CHECK(p6c_publish_bundle(
              &fixture.root, fixture.operation_id,
              P6C_OPERATION_RESULT_RETAINED, items, 2U,
              &fixture.journal, &publication) == P6C_RESULT_CONFLICT);
    CHECK(!publication.renamed);
    CHECK(publication.recovery_required);
    sentinel = openat(directory, "foreign.sentinel",
                      O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(sentinel >= 0);
    amount = read(sentinel, content, sizeof(content));
    CHECK(amount == 8);
    CHECK(memcmp(content, "foreign\n", 8U) == 0);
    CHECK(close(sentinel) == 0);
    CHECK(unlinkat(directory, "foreign.sentinel", 0) == 0);
    CHECK(close(directory) == 0);
    CHECK(unlinkat(fixture.root.descriptor, generation, AT_REMOVEDIR) == 0);
    CHECK(publication_fixture_close(&fixture, &publication) ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

static int publication_advance_journal(
    const struct p6c_owned_fd *root, const char *name,
    const uint8_t operation_id[P6C_OPERATION_ID_BYTES],
    struct p6c_journal *journal)
{
    if (p6c_journal_create(root, name, operation_id, getuid(), journal) !=
        P6C_RESULT_OK) {
        return EXIT_FAILURE;
    }
    return publication_advance_existing_journal(root, operation_id, journal);
}

static void publication_racing_child(
    struct publication_fixture *fixture, struct p6c_journal *journal)
{
    struct p6c_publication_result publication;
    struct p6c_publication_item items[2];
    enum p6c_result result;

    publication_items(items);
    memset(&publication, 0, sizeof(publication));
    result = p6c_publish_bundle(
        &fixture->root, fixture->operation_id,
        P6C_OPERATION_RESULT_RETAINED, items, 2U, journal, &publication);
    if (result == P6C_RESULT_OK) {
        (void)p6c_publication_close(&publication);
        _exit(10);
    }
    if (result == P6C_RESULT_CONFLICT) {
        publication_remove_known_files(&publication.staging_directory);
        (void)p6c_publication_close(&publication);
        if ((publication.staging_name[0] != '\0') &&
            (unlinkat(fixture->root.descriptor,
                      publication.staging_name, AT_REMOVEDIR) != 0)) {
            _exit(30);
        }
        _exit(20);
    }
    _exit(40);
}

static int case_publication_concurrent_commit(void)
{
    struct publication_fixture fixture;
    struct p6c_journal second_journal;
    struct p6c_publication_result committed;
    char generation[(P6C_OPERATION_ID_BYTES * 2U) + 1U];
    pid_t first;
    pid_t second;
    int first_status;
    int second_status;
    int descriptor;
    int first_code;
    int second_code;

    CHECK(publication_fixture_create(&fixture) == EXIT_SUCCESS);
    CHECK(publication_advance_journal(
              &fixture.root, "publication2.journal",
              fixture.operation_id, &second_journal) == EXIT_SUCCESS);
    first = fork();
    CHECK(first >= 0);
    if (first == 0) {
        publication_racing_child(&fixture, &fixture.journal);
    }
    second = fork();
    CHECK(second >= 0);
    if (second == 0) {
        publication_racing_child(&fixture, &second_journal);
    }
    CHECK(waitpid(first, &first_status, 0) == first);
    CHECK(waitpid(second, &second_status, 0) == second);
    CHECK(WIFEXITED(first_status));
    CHECK(WIFEXITED(second_status));
    first_code = WEXITSTATUS(first_status);
    second_code = WEXITSTATUS(second_status);
    CHECK(((first_code == 10) && (second_code == 20)) ||
          ((first_code == 20) && (second_code == 10)));

    publication_id_hex(fixture.operation_id, generation);
    descriptor = openat(fixture.root.descriptor, generation,
                        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    CHECK(descriptor >= 0);
    memset(&committed, 0, sizeof(committed));
    p6c_owned_fd_reset(&committed.staging_directory);
    p6c_owned_fd_reset(&committed.committed_directory);
    CHECK(p6c_owned_fd_acquire(
              &committed.committed_directory, descriptor,
              P6C_DESCRIPTOR_DIRECTORY) == P6C_RESULT_OK);
    committed.renamed = true;
    (void)strcpy(committed.generation_name, generation);
    CHECK(p6c_journal_close(&second_journal) == P6C_RESULT_OK);
    CHECK(unlinkat(fixture.root.descriptor,
                   "publication2.journal", 0) == 0);
    CHECK(publication_fixture_close(&fixture, &committed) ==
          EXIT_SUCCESS);
    return EXIT_SUCCESS;
}

struct test_case {
    const char *name;
    int (*function)(void);
};

static const struct test_case TEST_CASES[] = {
    {"publication_success", case_publication_success},
    {"publication_partial_write", case_publication_partial_write},
    {"publication_file_fsync", case_publication_file_fsync},
    {"publication_manifest", case_publication_manifest},
    {"publication_rename", case_publication_rename},
    {"publication_post_commit_fsync", case_publication_post_commit_fsync},
    {"publication_post_commit_verify", case_publication_post_commit_verify},
    {"publication_collision_preserves_foreign",
     case_publication_collision_preserves_foreign},
    {"publication_concurrent_commit",
     case_publication_concurrent_commit},
};

int main(int argc, char *argv[])
{
    size_t index;

    if ((argc == 2) && (strcmp(argv[1], "--list") == 0)) {
        for (index = 0U;
             index < sizeof(TEST_CASES) / sizeof(TEST_CASES[0]);
             ++index) {
            (void)puts(TEST_CASES[index].name);
        }
        return EXIT_SUCCESS;
    }
    if (argc != 2) {
        (void)fputs("usage: test_publication CASE\n", stderr);
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
