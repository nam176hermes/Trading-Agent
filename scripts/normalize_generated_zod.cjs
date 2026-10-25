"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

function normalizeGeneratedZod(source, toolRoot) {
  const toolRequire = createRequire(path.join(toolRoot, "package.json"));
  const ts = toolRequire("typescript");
  const generatorRequire = createRequire(toolRequire.resolve("openapi-zod-client"));
  const prettier = generatorRequire("prettier");
  const bounds = new Set(["min", "max", "length"]);
  const isZodCall = (node, name) => ts.isCallExpression(node)
    && ts.isPropertyAccessExpression(node.expression)
    && ts.isIdentifier(node.expression.expression)
    && node.expression.expression.text === "z"
    && node.expression.name.text === name;
  function isArrayChain(node) {
    while (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && bounds.has(node.expression.name.text)) node = node.expression.expression;
    return isZodCall(node, "array");
  }
  const parsed = ts.createSourceFile("api-schemas.ts", source, ts.ScriptTarget.Latest, true);
  const transformed = ts.transform(parsed, [(context) => {
    const f = context.factory;
    function visit(node) {
      node = ts.visitEachChild(node, visit, context);
      // Zod 3 parsed defaults through the inner schema. Zod 4's default
      // short-circuits that validation; prefault preserves the old behavior.
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
        && node.expression.name.text === "default") {
        let root = node.expression.expression;
        while (ts.isCallExpression(root) && ts.isPropertyAccessExpression(root.expression)) {
          root = root.expression.expression;
        }
        if (ts.isIdentifier(root) && root.text === "z") {
          return f.updateCallExpression(node,
            f.updatePropertyAccessExpression(node.expression,
              node.expression.expression, "prefault"), node.typeArguments, node.arguments);
        }
      }
      if (isZodCall(node, "record") && node.arguments.length === 1) {
        const stringKey = f.createCallExpression(
          f.createPropertyAccessExpression(f.createIdentifier("z"), "string"), undefined, [],
        );
        return f.updateCallExpression(node, node.expression, node.typeArguments,
          [stringKey, ...node.arguments]);
      }
      if (!ts.isCallExpression(node) || !ts.isPropertyAccessExpression(node.expression)
        || !bounds.has(node.expression.name.text)) return node;
      const wrapper = node.expression.expression;
      if (!ts.isCallExpression(wrapper) || wrapper.arguments.length !== 0
        || !ts.isPropertyAccessExpression(wrapper.expression)
        || wrapper.expression.name.text !== "readonly"
        || !isArrayChain(wrapper.expression.expression)) return node;
      const bounded = f.updateCallExpression(node,
        f.updatePropertyAccessExpression(node.expression, wrapper.expression.expression,
          node.expression.name), node.typeArguments, node.arguments);
      return f.updateCallExpression(wrapper,
        f.updatePropertyAccessExpression(wrapper.expression, bounded, wrapper.expression.name),
        wrapper.typeArguments, wrapper.arguments);
    }
    return (node) => ts.visitNode(node, visit);
  }]);
  try {
    return prettier.format(ts.createPrinter().printFile(transformed.transformed[0]), {
      parser: "typescript",
    });
  } finally {
    transformed.dispose();
  }
}

module.exports = { normalizeGeneratedZod };
if (require.main === module) {
  const [toolRoot, output] = process.argv.slice(2);
  fs.writeFileSync(output, normalizeGeneratedZod(fs.readFileSync(output, "utf8"), toolRoot));
}
