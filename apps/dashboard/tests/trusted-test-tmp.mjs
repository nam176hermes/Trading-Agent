import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const TEMP_NAMES = ['TMPDIR', 'TEMP', 'TMP'];

function validateRoot(candidate) {
  if (!path.isAbsolute(candidate)) throw new Error('test temp root must be absolute');
  const normalized = path.resolve(candidate);
  let canonical;
  try {
    canonical = fs.realpathSync(normalized);
  } catch (error) {
    throw new Error('test temp root does not exist', { cause: error });
  }
  if (canonical !== normalized) {
    throw new Error('test temp root must be canonical and contain no symlinks');
  }

  const allowedOwners = new Set([0, process.geteuid()]);
  const parts = canonical.split(path.sep).filter(Boolean);
  let current = path.parse(canonical).root;
  for (const part of parts) {
    current = path.join(current, part);
    const metadata = fs.lstatSync(current);
    if (metadata.isSymbolicLink()) throw new Error('test temp root must contain no symlinks');
    if (!metadata.isDirectory()) throw new Error(`test temp component is not a directory: ${current}`);
    if (!allowedOwners.has(metadata.uid)) throw new Error(`test temp component has an untrusted owner: ${current}`);
    if ((metadata.mode & 0o022) !== 0) throw new Error(`test temp path has a writable ancestor: ${current}`);
  }

  const leaf = fs.lstatSync(canonical);
  if (leaf.uid !== process.geteuid()) throw new Error('test temp root must be owned by the current user');
  if ((leaf.mode & 0o777) !== 0o700) throw new Error('test temp root mode must be 0700');
  return canonical;
}

function fallbackRoot() {
  const candidate = path.join(os.homedir(), '.cache', 'trading-agent', 'test-tmp');
  fs.mkdirSync(candidate, { recursive: true, mode: 0o700 });
  if (fs.lstatSync(candidate).isSymbolicLink()) {
    throw new Error('fallback test temp root must not be a symlink');
  }
  fs.chmodSync(candidate, 0o700);
  return validateRoot(candidate);
}

function selectRoot(environment) {
  if (environment.TRADING_TEST_TMP_ROOT) {
    return validateRoot(environment.TRADING_TEST_TMP_ROOT);
  }
  if (environment.TMPDIR) {
    try {
      return validateRoot(environment.TMPDIR);
    } catch {
      // Unsafe ambient temp locations are expected on WSL/Windows hosts.
    }
  }
  return fallbackRoot();
}

export function createTrustedTestEnvironment(component, sourceEnvironment = process.env) {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(component)) {
    throw new Error('invalid test temp component name');
  }
  const root = selectRoot(sourceEnvironment);
  const directory = fs.mkdtempSync(path.join(root, `${component}-`));
  fs.chmodSync(directory, 0o700);
  const identity = fs.lstatSync(directory);
  const environment = { ...sourceEnvironment };
  for (const name of TEMP_NAMES) environment[name] = directory;

  let cleaned = false;
  return {
    directory,
    environment,
    cleanup() {
      if (cleaned) return;
      const current = fs.lstatSync(directory);
      if (
        current.isSymbolicLink()
        || !current.isDirectory()
        || current.uid !== process.geteuid()
        || (current.mode & 0o777) !== 0o700
        || current.dev !== identity.dev
        || current.ino !== identity.ino
      ) {
        throw new Error('test temp session directory identity changed; refusing cleanup');
      }
      fs.rmSync(directory, { recursive: true });
      cleaned = true;
    },
  };
}
