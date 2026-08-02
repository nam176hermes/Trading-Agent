#include "p6c_protocol.h"
#include "p6c_types.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>


enum p6c_cli_option {
    P6C_CLI_SOCKET = 0,
    P6C_CLI_JOURNAL_ROOT,
    P6C_CLI_SOURCE_ROOT,
    P6C_CLI_CGROUP_ROOT,
    P6C_CLI_EVIDENCE_ROOT,
    P6C_CLI_CONTROLLER_UID,
    P6C_CLI_LIVE_EXECUTION,
    P6C_CLI_LIVE_TRADING,
    P6C_CLI_OPTION_COUNT
};

struct p6c_cli_values {
    bool seen[P6C_CLI_OPTION_COUNT];
    int descriptors[5];
    uid_t controller_user;
};

static int p6c_invalid_invocation(void)
{
    (void)fputs("package6-custodian: invalid invocation\n", stderr);
    return EXIT_FAILURE;
}

static int p6c_ambient_activation_present(void)
{
    static const char *const NAMES[] = {
        "LISTEN_FDS",
        "LISTEN_PID",
        "P6C_FAILPOINT",
        "LIVE_EXECUTION",
        "LIVE_TRADING"
    };
    size_t index;

    for (index = 0U; index < sizeof(NAMES) / sizeof(NAMES[0]); ++index) {
        if (getenv(NAMES[index]) != NULL) {
            return 1;
        }
    }
    return 0;
}

static enum p6c_cli_option p6c_option_name(
    const char *argument, const char **value)
{
    static const struct {
        const char *prefix;
        enum p6c_cli_option option;
    } OPTIONS[] = {
        {"--socket-fd=", P6C_CLI_SOCKET},
        {"--journal-root-fd=", P6C_CLI_JOURNAL_ROOT},
        {"--source-root-fd=", P6C_CLI_SOURCE_ROOT},
        {"--cgroup-root-fd=", P6C_CLI_CGROUP_ROOT},
        {"--evidence-root-fd=", P6C_CLI_EVIDENCE_ROOT},
        {"--controller-uid=", P6C_CLI_CONTROLLER_UID},
        {"--live-execution=", P6C_CLI_LIVE_EXECUTION},
        {"--live-trading=", P6C_CLI_LIVE_TRADING}
    };
    size_t index;

    for (index = 0U; index < sizeof(OPTIONS) / sizeof(OPTIONS[0]); ++index) {
        size_t prefix_length = strlen(OPTIONS[index].prefix);

        if (strncmp(argument, OPTIONS[index].prefix, prefix_length) == 0) {
            *value = &argument[prefix_length];
            return OPTIONS[index].option;
        }
    }
    return P6C_CLI_OPTION_COUNT;
}

static int p6c_parse_uintmax(
    const char *text, uintmax_t maximum, uintmax_t *value)
{
    char *end = NULL;
    uintmax_t parsed;

    if ((text == NULL) || (text[0] == '\0') || (text[0] == '+') ||
        (text[0] == '-') || (value == NULL)) {
        return 0;
    }
    errno = 0;
    parsed = strtoumax(text, &end, 10);
    if ((errno != 0) || (end == text) || (*end != '\0') ||
        (parsed > maximum)) {
        return 0;
    }
    *value = parsed;
    return 1;
}

static int p6c_parse_cli(
    int argc, char *argv[], struct p6c_cli_values *values)
{
    int argument_index;
    size_t index;

    if ((argc != (int)P6C_CLI_OPTION_COUNT + 1) || (values == NULL)) {
        return 0;
    }
    memset(values, 0, sizeof(*values));
    for (index = 0U;
         index < sizeof(values->descriptors) /
                     sizeof(values->descriptors[0]);
         ++index) {
        values->descriptors[index] = P6C_INVALID_DESCRIPTOR;
    }
    for (argument_index = 1; argument_index < argc; ++argument_index) {
        const char *option_value = NULL;
        enum p6c_cli_option option =
            p6c_option_name(argv[argument_index], &option_value);

        if ((option >= P6C_CLI_OPTION_COUNT) || values->seen[option]) {
            return 0;
        }
        values->seen[option] = true;
        if (option <= P6C_CLI_EVIDENCE_ROOT) {
            uintmax_t descriptor;

            if (!p6c_parse_uintmax(option_value, (uintmax_t)INT_MAX,
                                   &descriptor) ||
                (descriptor <= (uintmax_t)STDERR_FILENO)) {
                return 0;
            }
            values->descriptors[(size_t)option] = (int)descriptor;
        } else if (option == P6C_CLI_CONTROLLER_UID) {
            uintmax_t user;

            if (!p6c_parse_uintmax(
                    option_value, (uintmax_t)UINT32_MAX, &user) ||
                ((uintmax_t)(uid_t)user != user)) {
                return 0;
            }
            values->controller_user = (uid_t)user;
        } else if (strcmp(option_value, "false") != 0) {
            return 0;
        }
    }
    for (index = 0U; index < P6C_CLI_OPTION_COUNT; ++index) {
        if (!values->seen[index]) {
            return 0;
        }
    }
    for (index = 0U;
         index < sizeof(values->descriptors) /
                     sizeof(values->descriptors[0]);
         ++index) {
        size_t other;

        for (other = index + 1U;
             other < sizeof(values->descriptors) /
                         sizeof(values->descriptors[0]);
             ++other) {
            if (values->descriptors[index] == values->descriptors[other]) {
                return 0;
            }
        }
    }
    return 1;
}

static enum p6c_result p6c_capture_descriptor(
    int descriptor, enum p6c_descriptor_type type,
    struct p6c_owned_fd *owner)
{
    int descriptor_flags;

    descriptor_flags = fcntl(descriptor, F_GETFD);
    if ((descriptor_flags < 0) ||
        (fcntl(descriptor, F_SETFD, descriptor_flags | FD_CLOEXEC) != 0)) {
        return P6C_RESULT_UNSAFE;
    }
    return p6c_owned_fd_acquire(owner, descriptor, type);
}

static void p6c_service_config_reset(
    struct p6c_service_config *configuration)
{
    memset(configuration, 0, sizeof(*configuration));
    p6c_owned_fd_reset(&configuration->socket);
    p6c_owned_fd_reset(&configuration->journal_root);
    p6c_owned_fd_reset(&configuration->source_root);
    p6c_owned_fd_reset(&configuration->cgroup_root);
    p6c_owned_fd_reset(&configuration->evidence_root);
}

int main(int argc, char *argv[])
{
    struct p6c_cli_values values;
    struct p6c_service_config configuration;
    enum p6c_result service_result;
    enum p6c_result close_result;

    if ((argc == 2) && (strcmp(argv[1], "--version") == 0)) {
        (void)printf("package6-custodian protocol-v%u\n",
                     (unsigned int)P6C_PROTOCOL_VERSION);
        return EXIT_SUCCESS;
    }
    if (p6c_ambient_activation_present() ||
        !p6c_parse_cli(argc, argv, &values)) {
        return p6c_invalid_invocation();
    }
    p6c_service_config_reset(&configuration);
    if ((p6c_capture_descriptor(
             values.descriptors[P6C_CLI_SOCKET],
             P6C_DESCRIPTOR_SOCKET, &configuration.socket) !=
         P6C_RESULT_OK) ||
        (p6c_capture_descriptor(
             values.descriptors[P6C_CLI_JOURNAL_ROOT],
             P6C_DESCRIPTOR_DIRECTORY, &configuration.journal_root) !=
         P6C_RESULT_OK) ||
        (p6c_capture_descriptor(
             values.descriptors[P6C_CLI_SOURCE_ROOT],
             P6C_DESCRIPTOR_DIRECTORY, &configuration.source_root) !=
         P6C_RESULT_OK) ||
        (p6c_capture_descriptor(
             values.descriptors[P6C_CLI_CGROUP_ROOT],
             P6C_DESCRIPTOR_CGROUP, &configuration.cgroup_root) !=
         P6C_RESULT_OK) ||
        (p6c_capture_descriptor(
             values.descriptors[P6C_CLI_EVIDENCE_ROOT],
             P6C_DESCRIPTOR_DIRECTORY, &configuration.evidence_root) !=
         P6C_RESULT_OK)) {
        (void)p6c_service_config_close(&configuration);
        return p6c_invalid_invocation();
    }
    configuration.cgroup_root.type = P6C_DESCRIPTOR_CGROUP;
    configuration.controller_user = values.controller_user;
    service_result = p6c_service_run(&configuration);
    close_result = p6c_service_config_close(&configuration);
    if ((service_result == P6C_RESULT_OK) &&
        (close_result == P6C_RESULT_OK)) {
        return EXIT_SUCCESS;
    }
    (void)fputs("package6-custodian: service failure\n", stderr);
    return EXIT_FAILURE;
}
