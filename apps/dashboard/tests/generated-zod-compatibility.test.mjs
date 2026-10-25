import assert from 'node:assert/strict';
import { createRequire, registerHooks } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

// Exercise the checked-in client with the generator's actual dependency graph.
// Its legacy peer range must not hide a runtime break after a security override.
const require = createRequire(import.meta.url);
const generatorRequire = createRequire(require.resolve('openapi-zod-client'));
const toolRoot = fileURLToPath(new URL('..', import.meta.url));
const { normalizeGeneratedZod } = require('../../../scripts/normalize_generated_zod.cjs');
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL?.endsWith('/generated/dashboard/api-schemas.ts')
      && ['zod', '@zodios/core'].includes(specifier)) {
      return { shortCircuit: true, url: pathToFileURL(generatorRequire.resolve(specifier)).href };
    }
    return nextResolve(specifier, context);
  },
});

const { createApiClient, schemas } = await import('../../../generated/dashboard/api-schemas.ts');

test('normalization preserves literals and nested bounds without weakening validation', () => {
  const source = `
    const example = 'z.record(z.number()).readonly().max(999)';
    const schema = z.object({
      values: z.array(z.record(z.number())).readonly().min(1).max(2),
    });
    result.example = example;
    result.schema = schema;
  `;
  const normalized = normalizeGeneratedZod(source, toolRoot);
  assert.equal(normalizeGeneratedZod(normalized, toolRoot), normalized);
  const result = {};
  runInNewContext(normalized, { z: generatorRequire('zod').z, result });
  assert.equal(result.example, 'z.record(z.number()).readonly().max(999)');
  assert.ok(Object.isFrozen(result.schema.parse({ values: [{ score: 1 }] }).values));
  for (const values of [[], [{ score: '1' }], [{}, {}, {}]]) {
    assert.equal(result.schema.safeParse({ values }).success, false);
  }
});

test('generated client typechecks against the patched toolchain dependencies', () => {
  const ts = require('typescript');
  const options = {
    noEmit: true, strict: true, skipLibCheck: true, esModuleInterop: true,
    target: ts.ScriptTarget.ES2017,
    module: ts.ModuleKind.NodeNext, moduleResolution: ts.ModuleResolutionKind.NodeNext,
    baseUrl: toolRoot,
    paths: {
      zod: [path.join(path.dirname(generatorRequire.resolve('zod/package.json')), 'index.d.ts')],
      '@zodios/core': [generatorRequire.resolve('@zodios/core').replace(/\.js$/, '.d.ts')],
    },
  };
  const entry = fileURLToPath(new URL('../../../generated/dashboard/api-schemas.ts', import.meta.url));
  const diagnostics = ts.getPreEmitDiagnostics(ts.createProgram([entry], options));
  assert.equal(diagnostics.length, 0, ts.formatDiagnostics(diagnostics, {
    getCanonicalFileName: (name) => name, getCurrentDirectory: () => toolRoot,
    getNewLine: () => '\n',
  }));
});

test('generated client preserves strict response validation with patched Zod', async () => {
  const response = {
    data: { status: 'UP' },
    freshness: null,
    generated_at: '2026-10-25T00:00:00Z',
    schema_version: '2.0.0',
    trace_id: 'dependency-compatibility',
  };
  let data = response;
  const client = createApiClient('https://fixture.invalid', {
    axiosConfig: {
      adapter: async (config) => ({
        data, status: 200, statusText: 'OK',
        headers: { 'content-type': 'application/json' }, config,
      }),
    },
  });
  assert.deepEqual(await client.get('/health/live'), response);
  data = { ...response, data: { status: 'UP', unexpected: true } };
  await assert.rejects(client.get('/health/live'), /Invalid response/);
  data = { ...response, generated_at: 'not-a-timestamp' };
  await assert.rejects(client.get('/health/live'), /Invalid response/);
});

test('generated numeric records retain their legacy value constraints', () => {
  const sentiment = schemas.MarketAssetSnapshot.unwrap().shape.sentiment_distribution;
  assert.deepEqual(sentiment.parse({ positive: 0.75, negative: 0.25 }), {
    positive: 0.75, negative: 0.25,
  });
  assert.equal(sentiment.parse(null), null);
  assert.equal(sentiment.safeParse({ positive: '0.75' }).success, false);
});

test('generated readonly arrays enforce the canonical 4096-row bound', () => {
  const dates = Array(4096).fill('2026-10-25T00:00:00Z');
  const accepted = schemas.MarketContinuity.parse({
    timeframe: '1d', missing_open_times: dates,
  });
  assert.equal(accepted.missing_open_times.length, 4096);
  assert.ok(Object.isFrozen(accepted.missing_open_times));
  assert.equal(schemas.MarketContinuity.safeParse({
    timeframe: '1d', missing_open_times: [...dates, dates[0]],
  }).success, false);
});

test('generated defaults still pass through validation and readonly wrappers', () => {
  const empty = schemas.MarketContinuity.parse({ timeframe: '1d' });
  assert.ok(Object.isFrozen(empty.missing_open_times));
  assert.ok(Object.isFrozen(empty.duplicate_open_times));
  const result = {};
  const normalized = normalizeGeneratedZod(`
    result.valid = z.array(z.number()).readonly().optional().default([1]);
    result.invalid = z.number().min(2).optional().default(1);
  `, toolRoot);
  runInNewContext(normalized, { z: generatorRequire('zod').z, result });
  assert.ok(Object.isFrozen(result.valid.parse(undefined)));
  assert.equal(result.invalid.safeParse(undefined).success, false);
});
