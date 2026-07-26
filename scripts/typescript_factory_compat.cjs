"use strict";

// Compatibility bridge for openapi-zod-client@1.18.3 -> tanu@0.1.13.
// tanu calls the TypeScript 4.9 factory with the removed decorators argument.
// Preserve all other TypeScript module behavior and patch only that old shape.

const Module = require("node:module");

const originalLoad = Module._load;
const patchedFactories = new WeakSet();

function patchTypeScriptFactory(ts) {
  if (!ts || !ts.factory || patchedFactories.has(ts.factory)) {
    return;
  }

  const originalCreateTypeAlias = ts.factory.createTypeAliasDeclaration;
  if (typeof originalCreateTypeAlias !== "function") {
    return;
  }

  ts.factory.createTypeAliasDeclaration = function createTypeAliasCompat(...args) {
    if (args.length === 5) {
      const [, modifiers, name, typeParameters, type] = args;
      return originalCreateTypeAlias.call(
        ts.factory,
        modifiers,
        name,
        typeParameters,
        type,
      );
    }
    return originalCreateTypeAlias.apply(ts.factory, args);
  };
  patchedFactories.add(ts.factory);
}

Module._load = function loadWithTypeScriptFactoryCompat(request, parent, isMain) {
  const loaded = originalLoad.call(this, request, parent, isMain);
  if (request === "typescript") {
    patchTypeScriptFactory(loaded);
  }
  return loaded;
};
