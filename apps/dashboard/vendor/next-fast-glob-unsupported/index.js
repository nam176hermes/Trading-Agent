// ponytail: @next/eslint-plugin-next only globs settings.next.rootDir; this repo never sets it.
// Replaces fast-glob -> micromatch -> braces (GHSA-vfj7-8cjw-p6xm, no upstream fix). Remove when plugin drops fast-glob or braces ships a fix.
exports.globSync = () => { throw new Error("settings.next.rootDir globbing is unsupported in this repo (fast-glob removed for GHSA-vfj7-8cjw-p6xm)"); };
