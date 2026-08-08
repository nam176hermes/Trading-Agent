#![forbid(unsafe_op_in_unsafe_fn)]

use std::ffi::CString;
use std::os::raw::{c_char, c_int, c_long, c_uint, c_void};
use std::path::Path;
use std::process;
use std::ptr;

const ARGUMENT_ERROR: i32 = 64;
const AUTHORITY_ERROR: i32 = 65;
const COPY_ERROR: i32 = 66;
const SEAL_ERROR: i32 = 67;
const FORK_ERROR: i32 = 68;
const WAIT_ERROR: i32 = 69;
const EXEC_ERROR: i32 = 70;

const O_RDONLY: c_int = 0;
const O_CLOEXEC: c_int = 0o2000000;
const O_NOFOLLOW: c_int = 0o400000;
const S_IFMT: u32 = 0o170000;
const S_IFREG: u32 = 0o100000;
const MFD_CLOEXEC: c_uint = 0x0001;
const MFD_ALLOW_SEALING: c_uint = 0x0002;
const F_ADD_SEALS: c_int = 1033;
const F_GET_SEALS: c_int = 1034;
const F_SEAL_SEAL: c_int = 0x0001;
const F_SEAL_SHRINK: c_int = 0x0002;
const F_SEAL_GROW: c_int = 0x0004;
const F_SEAL_WRITE: c_int = 0x0008;
const REQUIRED_SEALS: c_int = F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE;
const AT_EMPTY_PATH: c_int = 0x1000;

#[cfg(not(target_arch = "x86_64"))]
compile_error!("nautilus-sealed-uv-exec supports x86_64 Linux only");

#[cfg(not(target_os = "linux"))]
compile_error!("nautilus-sealed-uv-exec supports Linux only");

#[cfg(target_arch = "x86_64")]
const SYS_MEMFD_CREATE: c_long = 319;
#[cfg(target_arch = "x86_64")]
const SYS_EXECVEAT: c_long = 322;

#[repr(C)]
struct Timespec {
    seconds: i64,
    nanoseconds: i64,
}

#[repr(C)]
struct Stat {
    device: u64,
    inode: u64,
    links: u64,
    mode: u32,
    uid: u32,
    gid: u32,
    padding: i32,
    rdevice: u64,
    size: i64,
    block_size: i64,
    blocks: i64,
    accessed: Timespec,
    modified: Timespec,
    changed: Timespec,
    reserved: [i64; 3],
}

unsafe extern "C" {
    fn open(pathname: *const c_char, flags: c_int) -> c_int;
    fn fstat(fd: c_int, statbuf: *mut Stat) -> c_int;
    fn read(fd: c_int, buffer: *mut c_void, count: usize) -> isize;
    fn write(fd: c_int, buffer: *const c_void, count: usize) -> isize;
    fn fcntl(fd: c_int, command: c_int, ...) -> c_int;
    fn fork() -> c_int;
    fn waitpid(pid: c_int, status: *mut c_int, options: c_int) -> c_int;
    fn _exit(status: c_int) -> !;
    fn chdir(path: *const c_char) -> c_int;
    fn syscall(number: c_long, ...) -> c_long;
}

#[derive(Clone, Copy)]
enum Failure {
    Arguments,
    Authority,
    Copy,
    Seal,
    Fork,
    Wait,
    Exec,
}

impl Failure {
    fn code(self) -> i32 {
        match self {
            Self::Arguments => ARGUMENT_ERROR,
            Self::Authority => AUTHORITY_ERROR,
            Self::Copy => COPY_ERROR,
            Self::Seal => SEAL_ERROR,
            Self::Fork => FORK_ERROR,
            Self::Wait => WAIT_ERROR,
            Self::Exec => EXEC_ERROR,
        }
    }

    fn message(self) -> &'static [u8] {
        match self {
            Self::Arguments => b"sealed-uv-exec: arguments\n",
            Self::Authority => b"sealed-uv-exec: authority\n",
            Self::Copy => b"sealed-uv-exec: copy\n",
            Self::Seal => b"sealed-uv-exec: seal\n",
            Self::Fork => b"sealed-uv-exec: fork\n",
            Self::Wait => b"sealed-uv-exec: wait\n",
            Self::Exec => b"sealed-uv-exec: exec\n",
        }
    }
}

struct Invocation {
    program: CString,
    digest: [u8; 32],
    uid: u32,
    gid: u32,
    cwd: CString,
    action: Action,
}

#[derive(Clone, Copy)]
enum Action {
    Version,
    SyncFrozenTest,
}

fn parse_arguments() -> Result<Invocation, Failure> {
    let arguments = std::env::args().collect::<Vec<_>>();
    if arguments.len() != 15
        || arguments[1] != "--program"
        || arguments[3] != "--sha256"
        || arguments[5] != "--uid"
        || arguments[7] != "--gid"
        || arguments[9] != "--mode"
        || arguments[11] != "--cwd"
        || arguments[13] != "--action"
        || arguments[9 + 1] != "0755"
    {
        return Err(Failure::Arguments);
    }
    if !Path::new(&arguments[2]).is_absolute() || !Path::new(&arguments[12]).is_absolute() {
        return Err(Failure::Authority);
    }
    let action = match arguments[14].as_str() {
        "version" => Action::Version,
        "sync-frozen-test" => Action::SyncFrozenTest,
        _ => return Err(Failure::Arguments),
    };
    let cwd = Path::new(&arguments[12]);
    if !cwd.is_dir() {
        return Err(Failure::Authority);
    }
    Ok(Invocation {
        program: CString::new(arguments[2].as_bytes()).map_err(|_| Failure::Arguments)?,
        digest: parse_digest(&arguments[4]).ok_or(Failure::Arguments)?,
        uid: arguments[6].parse::<u32>().map_err(|_| Failure::Arguments)?,
        gid: arguments[8].parse::<u32>().map_err(|_| Failure::Arguments)?,
        cwd: CString::new(arguments[12].as_bytes()).map_err(|_| Failure::Arguments)?,
        action,
    })
}

fn parse_digest(value: &str) -> Option<[u8; 32]> {
    if value.len() != 64 || !value.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
        return None;
    }
    let mut digest = [0_u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        digest[index] = (hex_value(chunk[0])? << 4) | hex_value(chunk[1])?;
    }
    Some(digest)
}

fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        _ => None,
    }
}

fn source_fd(invocation: &Invocation) -> Result<c_int, Failure> {
    // SAFETY: program is a retained NUL-terminated CString and flags need no mode argument.
    let fd = unsafe { open(invocation.program.as_ptr(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW) };
    if fd < 0 {
        return Err(Failure::Authority);
    }
    let mut info = std::mem::MaybeUninit::<Stat>::uninit();
    // SAFETY: info points to enough writable storage for the x86_64 Linux stat ABI.
    if unsafe { fstat(fd, info.as_mut_ptr()) } != 0 {
        return Err(Failure::Authority);
    }
    // SAFETY: fstat returned success and initialized info.
    let info = unsafe { info.assume_init() };
    if (info.mode & S_IFMT) != S_IFREG
        || info.links != 1
        || (info.mode & 0o7777) != 0o755
        || info.uid != invocation.uid
        || info.gid != invocation.gid
    {
        return Err(Failure::Authority);
    }
    Ok(fd)
}

fn sealed_memfd() -> Result<c_int, Failure> {
    let name = CString::new("nautilus-sealed-uv").expect("static string has no NUL");
    // SAFETY: syscall arguments match memfd_create(2) on x86_64 Linux.
    let fd = unsafe { syscall(SYS_MEMFD_CREATE, name.as_ptr(), MFD_CLOEXEC | MFD_ALLOW_SEALING) };
    if fd < 0 {
        return Err(Failure::Seal);
    }
    Ok(fd as c_int)
}

fn copy_and_verify(source: c_int, destination: c_int, expected: [u8; 32]) -> Result<(), Failure> {
    let mut hash = Sha256::new();
    let mut buffer = [0_u8; 8192];
    loop {
        // SAFETY: buffer is writable for its full declared length.
        let read_count = unsafe { read(source, buffer.as_mut_ptr().cast::<c_void>(), buffer.len()) };
        if read_count < 0 {
            return Err(Failure::Copy);
        }
        if read_count == 0 {
            break;
        }
        let bytes = &buffer[..read_count as usize];
        hash.update(bytes);
        write_all(destination, bytes)?;
    }
    if hash.finish() != expected {
        return Err(Failure::Authority);
    }
    Ok(())
}

fn write_all(fd: c_int, bytes: &[u8]) -> Result<(), Failure> {
    let mut remaining = bytes;
    while !remaining.is_empty() {
        // SAFETY: remaining points to readable memory for its exact declared length.
        let written = unsafe { write(fd, remaining.as_ptr().cast::<c_void>(), remaining.len()) };
        if written <= 0 {
            return Err(Failure::Copy);
        }
        remaining = &remaining[written as usize..];
    }
    Ok(())
}

fn seal(fd: c_int) -> Result<(), Failure> {
    // SAFETY: fcntl arguments match F_ADD_SEALS and F_GET_SEALS for this memfd.
    if unsafe { fcntl(fd, F_ADD_SEALS, REQUIRED_SEALS) } != 0 {
        return Err(Failure::Seal);
    }
    // SAFETY: F_GET_SEALS has no third argument.
    let observed = unsafe { fcntl(fd, F_GET_SEALS) };
    if observed != REQUIRED_SEALS {
        return Err(Failure::Seal);
    }
    Ok(())
}

fn execute(fd: c_int, invocation: &Invocation) -> Result<i32, Failure> {
    let arguments = match invocation.action {
        Action::Version => ["uv", "--version"].as_slice(),
        Action::SyncFrozenTest => ["uv", "sync", "--frozen", "--extra", "test"].as_slice(),
    };
    let arguments = arguments
        .iter()
        .map(|argument| CString::new(*argument).expect("static string has no NUL"))
        .collect::<Vec<_>>();
    let mut argument_pointers = arguments.iter().map(|argument| argument.as_ptr()).collect::<Vec<_>>();
    argument_pointers.push(ptr::null());
    let environment = [
        CString::new("PATH=/usr/bin:/bin").expect("static string has no NUL"),
        CString::new("PYTHONDONTWRITEBYTECODE=1").expect("static string has no NUL"),
        CString::new("PYTHONHASHSEED=0").expect("static string has no NUL"),
        CString::new("PYTHONNOUSERSITE=1").expect("static string has no NUL"),
        CString::new("UV_OFFLINE=1").expect("static string has no NUL"),
    ];
    let mut environment_pointers = environment.iter().map(|entry| entry.as_ptr()).collect::<Vec<_>>();
    environment_pointers.push(ptr::null());
    let empty_path = CString::new("").expect("static string has no NUL");

    // SAFETY: fork has no Rust aliases and both branches follow its C contract.
    let child = unsafe { fork() };
    if child < 0 {
        return Err(Failure::Fork);
    }
    if child == 0 {
        // SAFETY: all values were allocated before fork; chdir and execveat receive valid pointers.
        unsafe {
            if chdir(invocation.cwd.as_ptr()) != 0 {
                child_fail(Failure::Exec);
            }
            syscall(
                SYS_EXECVEAT,
                fd,
                empty_path.as_ptr(),
                argument_pointers.as_ptr(),
                environment_pointers.as_ptr(),
                AT_EMPTY_PATH,
            );
            child_fail(Failure::Exec);
        }
    }
    let mut status = 0;
    // SAFETY: status points to initialized writable memory and child came from fork.
    if unsafe { waitpid(child, &mut status, 0) } != child {
        return Err(Failure::Wait);
    }
    if status & 0x7f == 0 {
        return Ok((status >> 8) & 0xff);
    }
    Ok(128 + (status & 0x7f))
}

unsafe fn child_fail(failure: Failure) -> ! {
    let message = failure.message();
    // SAFETY: message is a static byte slice and descriptor 2 is stderr.
    unsafe {
        write(2, message.as_ptr().cast::<c_void>(), message.len());
        _exit(failure.code());
    }
}

fn main() {
    let result = parse_arguments().and_then(|invocation| {
        let source = source_fd(&invocation)?;
        let image = sealed_memfd()?;
        copy_and_verify(source, image, invocation.digest)?;
        seal(image)?;
        execute(image, &invocation)
    });
    match result {
        Ok(status) => process::exit(status),
        Err(failure) => {
            let message = failure.message();
            // SAFETY: message is a static byte slice and descriptor 2 is stderr.
            unsafe {
                write(2, message.as_ptr().cast::<c_void>(), message.len());
            }
            process::exit(failure.code());
        }
    }
}

struct Sha256 {
    state: [u32; 8],
    buffer: [u8; 64],
    length: usize,
    bits: u64,
}

impl Sha256 {
    fn new() -> Self {
        Self {
            state: [
                0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
                0x1f83d9ab, 0x5be0cd19,
            ],
            buffer: [0; 64],
            length: 0,
            bits: 0,
        }
    }

    fn update(&mut self, mut input: &[u8]) {
        self.bits = self.bits.wrapping_add((input.len() as u64).wrapping_mul(8));
        if self.length != 0 {
            let copied = (64 - self.length).min(input.len());
            self.buffer[self.length..self.length + copied].copy_from_slice(&input[..copied]);
            self.length += copied;
            input = &input[copied..];
            if self.length == 64 {
                let block = self.buffer;
                self.transform(&block);
                self.length = 0;
            }
        }
        while input.len() >= 64 {
            let block: &[u8; 64] = input[..64].try_into().expect("fixed slice length");
            self.transform(block);
            input = &input[64..];
        }
        self.buffer[..input.len()].copy_from_slice(input);
        self.length = input.len();
    }

    fn finish(mut self) -> [u8; 32] {
        self.buffer[self.length] = 0x80;
        self.length += 1;
        if self.length > 56 {
            self.buffer[self.length..].fill(0);
            let block = self.buffer;
            self.transform(&block);
            self.length = 0;
        }
        self.buffer[self.length..56].fill(0);
        self.buffer[56..].copy_from_slice(&self.bits.to_be_bytes());
        let block = self.buffer;
        self.transform(&block);
        let mut output = [0_u8; 32];
        for (index, value) in self.state.iter().enumerate() {
            output[index * 4..index * 4 + 4].copy_from_slice(&value.to_be_bytes());
        }
        output
    }

    fn transform(&mut self, block: &[u8; 64]) {
        const ROUND: [u32; 64] = [
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
            0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
            0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
            0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
            0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
            0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
            0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
            0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
            0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
        ];
        let mut words = [0_u32; 64];
        for (index, chunk) in block.chunks_exact(4).enumerate() {
            words[index] = u32::from_be_bytes(chunk.try_into().expect("fixed chunk length"));
        }
        for index in 16..64 {
            let first = words[index - 15].rotate_right(7)
                ^ words[index - 15].rotate_right(18)
                ^ (words[index - 15] >> 3);
            let second = words[index - 2].rotate_right(17)
                ^ words[index - 2].rotate_right(19)
                ^ (words[index - 2] >> 10);
            words[index] = words[index - 16]
                .wrapping_add(first)
                .wrapping_add(words[index - 7])
                .wrapping_add(second);
        }
        let mut a = self.state[0];
        let mut b = self.state[1];
        let mut c = self.state[2];
        let mut d = self.state[3];
        let mut e = self.state[4];
        let mut f = self.state[5];
        let mut g = self.state[6];
        let mut h = self.state[7];
        for index in 0..64 {
            let first = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let choice = (e & f) ^ ((!e) & g);
            let temporary_one = h
                .wrapping_add(first)
                .wrapping_add(choice)
                .wrapping_add(ROUND[index])
                .wrapping_add(words[index]);
            let second = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let majority = (a & b) ^ (a & c) ^ (b & c);
            let temporary_two = second.wrapping_add(majority);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temporary_one);
            d = c;
            c = b;
            b = a;
            a = temporary_one.wrapping_add(temporary_two);
        }
        self.state[0] = self.state[0].wrapping_add(a);
        self.state[1] = self.state[1].wrapping_add(b);
        self.state[2] = self.state[2].wrapping_add(c);
        self.state[3] = self.state[3].wrapping_add(d);
        self.state[4] = self.state[4].wrapping_add(e);
        self.state[5] = self.state[5].wrapping_add(f);
        self.state[6] = self.state[6].wrapping_add(g);
        self.state[7] = self.state[7].wrapping_add(h);
    }
}
