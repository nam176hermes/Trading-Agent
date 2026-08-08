#![forbid(unsafe_op_in_unsafe_fn)]

use std::ffi::{CString, OsStr};
use std::os::raw::c_char;
use std::os::unix::ffi::OsStrExt;
use std::process;
use std::ptr;

const GUARD_ENTRYPOINT: &str = env!("NAUTILUS_GUARD_ENTRYPOINT");
const GUARDED_PYTHON: &str = env!("NAUTILUS_GUARD_PYTHON");
const LAUNCHER: &str = env!("NAUTILUS_GUARD_LAUNCHER");
const PROFILE: &str = env!("NAUTILUS_GUARD_PROFILE");
const REQUEST: &str = env!("NAUTILUS_GUARD_REQUEST");
const SIDECAR: &str = env!("NAUTILUS_GUARD_SIDECAR");

const REJECTED_ARGV: i32 = 64;
const EXEC_FAILED: i32 = 70;

unsafe extern "C" {
    fn execve(
        pathname: *const c_char,
        argv: *const *const c_char,
        envp: *const *const c_char,
    ) -> i32;
}

fn expected_guard_argv() -> [&'static [u8]; 9] {
    [
        GUARD_ENTRYPOINT.as_bytes(),
        GUARDED_PYTHON.as_bytes(),
        b"-I",
        b"-S",
        LAUNCHER.as_bytes(),
        b"--profile",
        PROFILE.as_bytes(),
        REQUEST.as_bytes(),
        SIDECAR.as_bytes(),
    ]
}

fn exact_argv() -> bool {
    let expected = expected_guard_argv();
    let actual = std::env::args_os().collect::<Vec<_>>();
    actual.len() == expected.len()
        && actual
            .iter()
            .zip(expected)
            .all(|(observed, required)| OsStr::new(observed).as_bytes() == required)
}

fn main() {
    if !exact_argv() {
        process::exit(REJECTED_ARGV);
    }

    let guarded_argv = [
        GUARDED_PYTHON,
        "-I",
        "-S",
        LAUNCHER,
        "--profile",
        PROFILE,
        REQUEST,
        SIDECAR,
    ];
    let Ok(arguments) = guarded_argv
        .iter()
        .map(|argument| CString::new(*argument))
        .collect::<Result<Vec<_>, _>>()
    else {
        process::exit(EXEC_FAILED);
    };
    let mut argument_pointers = arguments
        .iter()
        .map(|argument| argument.as_ptr())
        .collect::<Vec<_>>();
    argument_pointers.push(ptr::null());
    let empty_environment = [ptr::null()];

    // SAFETY: all strings are retained CStrings, both pointer arrays are
    // null-terminated, and the environment is deliberately empty. A return
    // from execve is always a closed failure.
    unsafe {
        execve(
            arguments[0].as_ptr(),
            argument_pointers.as_ptr(),
            empty_environment.as_ptr(),
        );
    }
    process::exit(EXEC_FAILED);
}
