import 'server-only';

import { randomUUID } from 'crypto';
import fs from 'fs';
import path from 'path';

const PRIVATE_DIRECTORY_MODE = 0o700;
const PRIVATE_FILE_MODE = 0o600;

function stateError(message: string): Error {
  return new Error(`Unsafe local state file: ${message}`);
}

function isMissing(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}

function requireRegularFile(stat: fs.Stats, target: string): void {
  if (!stat.isFile()) throw stateError(`${target} is not a regular file`);
}

function sameFile(left: fs.Stats, right: fs.Stats): boolean {
  return left.dev === right.dev && left.ino === right.ino;
}

function requireAbsoluteTarget(target: string): void {
  if (!path.isAbsolute(target) || path.normalize(target) !== target) {
    throw stateError(`${target} is not a canonical absolute path`);
  }
}

function requireTrustedAncestorChain(directory: string): void {
  const chain: string[] = [];
  for (let current = directory; ; current = path.dirname(current)) {
    chain.push(current);
    const parent = path.dirname(current);
    if (parent === current) break;
  }
  for (const current of chain.reverse()) {
    let metadata: fs.Stats;
    try {
      metadata = fs.lstatSync(current);
    } catch {
      throw stateError(`${current} is an unsafe ancestor`);
    }
    if (!metadata.isDirectory()) throw stateError(`${current} is an unsafe ancestor`);
    const writableByAnotherPrincipal = (metadata.mode & 0o022) !== 0;
    const stickyDirectory = (metadata.mode & 0o1000) !== 0;
    if (writableByAnotherPrincipal && !stickyDirectory) {
      throw stateError(`${current} is writable by another principal`);
    }
  }
}

function requireTrustedExistingAncestors(directory: string): void {
  let existing = directory;
  while (true) {
    try {
      fs.lstatSync(existing);
      break;
    } catch (error) {
      if (!isMissing(error)) throw stateError(`${existing} is an unsafe ancestor`);
      const parent = path.dirname(existing);
      if (parent === existing) throw stateError(`${directory} has no trusted ancestor`);
      existing = parent;
    }
  }
  requireTrustedAncestorChain(existing);
}

function requireDirectoryStillAnchored(directory: string, directoryFd: number): void {
  requireTrustedAncestorChain(directory);
  const byPath = fs.lstatSync(directory);
  const byDescriptor = fs.fstatSync(directoryFd);
  if (!byPath.isDirectory() || !sameFile(byPath, byDescriptor)) {
    throw stateError(`${directory} changed during local state mutation`);
  }
}

function openExistingRegularFile(target: string): { fd: number; before: fs.Stats } | null {
  let before: fs.Stats;
  try {
    before = fs.lstatSync(target);
  } catch (error) {
    if (isMissing(error)) return null;
    throw stateError(`cannot inspect ${target}`);
  }
  requireRegularFile(before, target);

  let fd: number;
  try {
    fd = fs.openSync(target, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  } catch {
    throw stateError(`cannot open ${target}`);
  }
  try {
    const opened = fs.fstatSync(fd);
    requireRegularFile(opened, target);
    if (!sameFile(before, opened)) throw stateError(`${target} changed while opening`);
    return { fd, before };
  } catch (error) {
    fs.closeSync(fd);
    throw error;
  }
}

function openStateDirectory(
  directory: string,
  options: { create: boolean; makePrivate: boolean },
): number | null {
  if (options.create) {
    requireTrustedExistingAncestors(directory);
    fs.mkdirSync(directory, { recursive: true, mode: PRIVATE_DIRECTORY_MODE });
  } else {
    try {
      fs.lstatSync(directory);
    } catch (error) {
      if (isMissing(error)) return null;
      throw stateError(`${directory} is not a safe state directory`);
    }
  }
  requireTrustedAncestorChain(directory);

  let fd: number;
  try {
    fd = fs.openSync(
      directory,
      fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW,
    );
  } catch (error) {
    if (!options.create && isMissing(error)) return null;
    throw stateError(`${directory} is not a safe state directory`);
  }

  try {
    const opened = fs.fstatSync(fd);
    if (!opened.isDirectory()) {
      throw stateError(`${directory} is not a directory`);
    }
    const currentUid = typeof process.getuid === 'function' ? process.getuid() : null;
    if (currentUid !== null && opened.uid !== currentUid) {
      throw stateError(`${directory} is not owned by the current user`);
    }
    if ((opened.mode & 0o022) !== 0) {
      throw stateError(`${directory} is writable by another principal`);
    }
    if (options.makePrivate) fs.fchmodSync(fd, PRIVATE_DIRECTORY_MODE);
    return fd;
  } catch (error) {
    fs.closeSync(fd);
    throw error;
  }
}

/**
 * Reads a local state file only when the path resolves to the same bounded
 * regular file before and after opening it. Missing state is represented by
 * null; every other unsafe or unreadable state throws for callers to fail
 * closed.
 */
export function readLocalStateFile(target: string, maxBytes: number): string | null {
  requireAbsoluteTarget(target);
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) throw new RangeError('maxBytes must be a non-negative integer');
  const directoryFd = openStateDirectory(path.dirname(target), { create: false, makePrivate: false });
  if (directoryFd === null) return null;
  let opened: ReturnType<typeof openExistingRegularFile>;
  try {
    opened = openExistingRegularFile(target);
  } catch (error) {
    fs.closeSync(directoryFd);
    throw error;
  }
  if (!opened) {
    fs.closeSync(directoryFd);
    return null;
  }

  const { fd, before } = opened;
  try {
    const initial = fs.fstatSync(fd);
    if (initial.size > maxBytes) throw stateError(`${target} is too large`);
    const buffer = Buffer.allocUnsafe(maxBytes + 1);
    let offset = 0;
    while (offset < buffer.length) {
      const bytesRead = fs.readSync(fd, buffer, offset, buffer.length - offset, offset);
      if (bytesRead === 0) break;
      offset += bytesRead;
    }
    if (offset > maxBytes) throw stateError(`${target} is too large`);
    const after = fs.fstatSync(fd);
    requireRegularFile(after, target);
    if (!sameFile(before, after) || after.size > maxBytes) {
      throw stateError(`${target} changed while reading`);
    }
    try {
      return new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, offset));
    } catch {
      throw stateError(`${target} is not valid UTF-8`);
    }
  } finally {
    fs.closeSync(fd);
    fs.closeSync(directoryFd);
  }
}

/** Writes a private durable replacement in a private directory. */
export function writePrivateLocalStateFile(target: string, content: string): void {
  requireAbsoluteTarget(target);
  const directory = path.dirname(target);
  const directoryFd = openStateDirectory(directory, { create: true, makePrivate: true });
  if (directoryFd === null) throw stateError(`${directory} is unavailable`);

  let temporary: string | null = path.join(directory, `.${path.basename(target)}.tmp.${randomUUID()}`);
  try {
    requireDirectoryStillAnchored(directory, directoryFd);
    const fd = fs.openSync(
      temporary,
      fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY | fs.constants.O_NOFOLLOW,
      PRIVATE_FILE_MODE,
    );
    try {
      fs.fchmodSync(fd, PRIVATE_FILE_MODE);
      fs.writeFileSync(fd, content, 'utf8');
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    requireDirectoryStillAnchored(directory, directoryFd);
    fs.renameSync(temporary, target);
    temporary = null;
    fs.fsyncSync(directoryFd);
  } finally {
    if (temporary) {
      try {
        requireDirectoryStillAnchored(directory, directoryFd);
        fs.unlinkSync(temporary);
      } catch { /* Best-effort cleanup only while the trusted path remains anchored. */ }
    }
    fs.closeSync(directoryFd);
  }
}

/**
 * Reads and validates existing bounded state before replacing it. If reading or
 * validation throws, the existing bytes remain untouched for forensic review.
 */
export function updatePrivateLocalStateFile(
  target: string,
  maxBytes: number,
  update: (existing: string | null) => string,
): void {
  const existing = readLocalStateFile(target, maxBytes);
  const replacement = update(existing);
  if (typeof replacement !== 'string') throw new TypeError('local state update must return a string');
  writePrivateLocalStateFile(target, replacement);
}

/** Removes only an existing regular state file, then persists the directory entry change. */
export function removePrivateLocalStateFile(target: string): boolean {
  requireAbsoluteTarget(target);
  const directoryFd = openStateDirectory(path.dirname(target), { create: false, makePrivate: false });
  if (directoryFd === null) return false;
  let opened: ReturnType<typeof openExistingRegularFile>;
  try {
    opened = openExistingRegularFile(target);
  } catch (error) {
    fs.closeSync(directoryFd);
    throw error;
  }
  if (!opened) {
    fs.closeSync(directoryFd);
    return false;
  }
  try {
    const { fd, before } = opened;
    try {
      const current = fs.fstatSync(fd);
      if (!sameFile(before, current)) throw stateError(`${target} changed before removal`);
      requireDirectoryStillAnchored(path.dirname(target), directoryFd);
      const entryNow = fs.lstatSync(target);
      requireRegularFile(entryNow, target);
      if (!sameFile(before, entryNow)) throw stateError(`${target} changed before removal`);
      fs.unlinkSync(target);
      fs.fsyncSync(directoryFd);
      return true;
    } finally {
      fs.closeSync(fd);
    }
  } finally {
    fs.closeSync(directoryFd);
  }
}
