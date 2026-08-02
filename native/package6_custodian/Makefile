CC := gcc
PYTHON ?= uv run python

BUILD_DIR ?= /tmp/package6-custodian-build
REPOSITORY_ROOT := $(realpath $(CURDIR)/../..)
BUILD_DIR_INPUT := $(patsubst %/,%,$(BUILD_DIR))
EXISTING_BUILD_DIR := $(realpath $(BUILD_DIR_INPUT))
BUILD_DIR_PARENT := $(realpath $(dir $(BUILD_DIR_INPUT)))

ifeq ($(EXISTING_BUILD_DIR),)
ifeq ($(BUILD_DIR_PARENT),)
$(error BUILD_DIR parent must exist before the native build)
endif
RESOLVED_BUILD_DIR := $(abspath $(BUILD_DIR_PARENT)/$(notdir $(BUILD_DIR_INPUT)))
else
RESOLVED_BUILD_DIR := $(EXISTING_BUILD_DIR)
endif

ifneq ($(filter $(REPOSITORY_ROOT) $(REPOSITORY_ROOT)/%,$(RESOLVED_BUILD_DIR)),)
$(error BUILD_DIR must resolve outside the repository)
endif

CPPFLAGS := -I$(CURDIR)/include -D_GNU_SOURCE -D_FORTIFY_SOURCE=3
CFLAGS := -std=c11 -O2 -g0 -fstack-protector-strong -fPIE \
	-Wall -Wextra -Werror -Wconversion -Wformat=2 -Wshadow \
	-Wstrict-prototypes
LDFLAGS := -pie -Wl,-z,relro,-z,now,-z,noexecstack
SANITIZER_FLAGS := -fsanitize=address,undefined -fno-omit-frame-pointer

PRODUCTION_BINARY := $(RESOLVED_BUILD_DIR)/package6-custodian
PYTHON_BUILD_DIR := $(RESOLVED_BUILD_DIR)/python
PYTHON_INCLUDE := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_config_var('INCLUDEPY'))")
PYTHON_EXT_SUFFIX := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
PYTHON_CUSTODY_EXTENSION := $(PYTHON_BUILD_DIR)/_package6_fd_custody$(PYTHON_EXT_SUFFIX)
TEST_PROTOCOL_BINARY := $(RESOLVED_BUILD_DIR)/test_protocol
TEST_AUTHORITY_BINARY := $(RESOLVED_BUILD_DIR)/test_authority
TEST_PUBLICATION_BINARY := $(RESOLVED_BUILD_DIR)/test_publication
TEST_SERVICE_BINARY := $(RESOLVED_BUILD_DIR)/test-package6-custodian
TEST_BINARIES := $(TEST_PROTOCOL_BINARY) $(TEST_AUTHORITY_BINARY) \
	$(TEST_PUBLICATION_BINARY)
COMMON_SOURCES := src/protocol.c src/linux_authority.c src/sha256.c \
	src/journal.c src/transcript.c src/process.c src/cgroup.c \
	src/publication.c

.PHONY: all build test test-service prepare-private-build-dirs validate-output-paths FORCE

all: build

build: prepare-private-build-dirs $(PRODUCTION_BINARY) $(PYTHON_CUSTODY_EXTENSION)

test: $(TEST_BINARIES)
	@ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
		UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
		$(TEST_PROTOCOL_BINARY)
	@set -e; \
	test_cases="$$(ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
		UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
		$(TEST_AUTHORITY_BINARY) --list)"; \
	for test_case in $$test_cases; do \
		ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
		UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
		timeout 10s $(TEST_AUTHORITY_BINARY) "$$test_case" || exit $$?; \
	done
	@set -e; \
	test_cases="$$(ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
		UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
		$(TEST_PUBLICATION_BINARY) --list)"; \
	for test_case in $$test_cases; do \
		ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
		UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
		timeout 10s $(TEST_PUBLICATION_BINARY) "$$test_case" || exit $$?; \
	done

test-service: $(TEST_SERVICE_BINARY)

$(RESOLVED_BUILD_DIR):
	mkdir -p "$@"

$(PYTHON_BUILD_DIR): | $(RESOLVED_BUILD_DIR)
	mkdir -p "$@"

prepare-private-build-dirs:
	@umask 0077; install -d -m 0700 "$(RESOLVED_BUILD_DIR)" "$(PYTHON_BUILD_DIR)"

validate-output-paths: prepare-private-build-dirs
	@for directory in "$(RESOLVED_BUILD_DIR)" "$(PYTHON_BUILD_DIR)"; do \
		test "$$(stat -c '%u:%a' -- "$$directory")" = "$$(id -u):700" || { \
			printf '%s\n' "native build directory must be private and owned: $$directory" >&2; \
			exit 2; \
		}; \
	done
	@for output in "$(PRODUCTION_BINARY)" "$(PYTHON_CUSTODY_EXTENSION)" $(TEST_BINARIES) "$(TEST_SERVICE_BINARY)"; do \
		if test -L "$$output"; then \
			printf '%s\n' "refusing symlinked native output: $$output" >&2; \
			exit 2; \
		fi; \
		if test -e "$$output"; then \
			if ! test -f "$$output"; then \
				printf '%s\n' "refusing non-regular native output: $$output" >&2; \
				exit 2; \
			fi; \
			if test "$$(stat -c '%h' -- "$$output")" != 1; then \
				printf '%s\n' "refusing multiply-linked native output: $$output" >&2; \
				exit 2; \
			fi; \
			resolved_output=$$(realpath -e -- "$$output"); \
			case "$$resolved_output" in \
				"$(REPOSITORY_ROOT)"|"$(REPOSITORY_ROOT)"/*) \
					printf '%s\n' "refusing native output inside repository: $$output" >&2; \
					exit 2 ;; \
			esac; \
		fi; \
	done

$(PRODUCTION_BINARY): src/main.c FORCE | validate-output-paths
	$(CC) $(CPPFLAGS) $(CFLAGS) src/main.c \
		-o "$@" $(LDFLAGS)

$(PYTHON_CUSTODY_EXTENSION): src/python_fd_custody.c FORCE | validate-output-paths $(PYTHON_BUILD_DIR)
	@set -eu; \
		temporary=$$(mktemp "$(PYTHON_BUILD_DIR)/.package6-fd-custody.XXXXXX"); \
		trap 'rm -f -- "$$temporary"' EXIT HUP INT TERM; \
		chmod 0600 "$$temporary"; \
		umask 0177; $(CC) $(CPPFLAGS) -DP6FD_TESTING -I"$(PYTHON_INCLUDE)" \
			-std=c11 -O2 -g0 -fstack-protector-strong -fPIC \
			-Wall -Wextra -Werror -Wconversion -Wformat=2 -Wshadow \
			-Wstrict-prototypes -shared src/python_fd_custody.c \
			-o "$$temporary" -Wl,-z,relro,-z,now,-z,noexecstack; \
		test "$$(stat -c '%u:%a:%h' -- "$$temporary")" = "$$(id -u):600:1"; \
		temporary_identity=$$(stat -c '%d:%i' -- "$$temporary"); \
		mv -Tf -- "$$temporary" "$@"; \
		if test -L "$@" || ! test -f "$@"; then \
			printf '%s\n' "published native extension must be a regular non-symlink" >&2; \
			exit 2; \
		fi; \
		if test "$$(stat -c '%d:%i' -- "$@")" != "$$temporary_identity"; then \
			printf '%s\n' "native extension publication provenance changed" >&2; \
			exit 2; \
		fi; \
		if test "$$(stat -c '%u:%a:%h' -- "$@")" != "$$(id -u):600:1"; then \
			printf '%s\n' "published native extension policy changed" >&2; \
			exit 2; \
		fi; \
		for directory in "$(RESOLVED_BUILD_DIR)" "$(PYTHON_BUILD_DIR)"; do \
			if test -L "$$directory" || ! test -d "$$directory" || \
				test "$$(stat -c '%u:%a' -- "$$directory")" != "$$(id -u):700"; then \
				printf '%s\n' "native extension publication parent policy changed" >&2; \
				exit 2; \
			fi; \
		done; \
		trap - EXIT HUP INT TERM

FORCE:

$(TEST_SERVICE_BINARY): tests/test_service_main.c $(COMMON_SOURCES) \
		include/p6c_protocol.h include/p6c_types.h FORCE | validate-output-paths
	$(CC) $(CPPFLAGS) -DP6C_TESTING $(CFLAGS) \
		tests/test_service_main.c $(COMMON_SOURCES) \
		-o "$@" $(LDFLAGS)

$(TEST_PROTOCOL_BINARY): tests/test_protocol.c $(COMMON_SOURCES) \
		include/p6c_protocol.h include/p6c_types.h FORCE | validate-output-paths
	$(CC) $(CPPFLAGS) $(CFLAGS) $(SANITIZER_FLAGS) tests/test_protocol.c \
		$(COMMON_SOURCES) \
		-o "$@" $(LDFLAGS) $(SANITIZER_FLAGS)

$(TEST_AUTHORITY_BINARY): tests/test_authority.c $(COMMON_SOURCES) \
		include/p6c_protocol.h include/p6c_types.h FORCE | validate-output-paths
	$(CC) $(CPPFLAGS) -DP6C_TESTING $(CFLAGS) $(SANITIZER_FLAGS) \
		tests/test_authority.c $(COMMON_SOURCES) \
		-o "$@" $(LDFLAGS) $(SANITIZER_FLAGS)

$(TEST_PUBLICATION_BINARY): tests/test_publication.c $(COMMON_SOURCES) \
		include/p6c_protocol.h include/p6c_types.h FORCE | validate-output-paths
	$(CC) $(CPPFLAGS) -DP6C_TESTING $(CFLAGS) $(SANITIZER_FLAGS) \
		tests/test_publication.c $(COMMON_SOURCES) \
		-o "$@" $(LDFLAGS) $(SANITIZER_FLAGS)
