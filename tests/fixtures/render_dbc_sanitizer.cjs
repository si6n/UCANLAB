/**
 * M-05 sanitizer harness — exercises `sanitizeDbcIdentifier` from
 * reverseEngineeringEngine.ts in-process (no vitest in this repo).
 *
 * Driven by tests/unit/test_dbc_sanitizer_m05.py. Transpiles the real TS
 * module with the in-process `typescript` compiler and prints a JSON report
 * of (input, output, isDbcSafe) rows on stdout.
 *
 * Usage:
 *   node tests/fixtures/render_dbc_sanitizer.cjs <frontend-dir>
 */
const fs = require('node:fs');
const path = require('node:path');

const frontend = process.argv[2] ? path.resolve(process.argv[2]) : process.cwd();
const ts = require(path.join(frontend, 'node_modules', 'typescript'));

const srcPath = path.join(
  frontend,
  'src',
  'services',
  'reverseEngineeringEngine.ts',
);
const code = fs.readFileSync(srcPath, 'utf8');

const transpiled = ts.transpileModule(code, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
  fileName: 'reverseEngineeringEngine.ts',
});

// The module imports its sibling models; stage a CommonJS copy so the
// require() calls resolve without a bundler.
const staging = path.join(frontend, 'node_modules', '.m05-staging');
fs.mkdirSync(staging, { recursive: true });
const modulePath = path.join(staging, 'engine.cjs');
fs.writeFileSync(modulePath, transpiled.outputText);

let engine;
try {
  engine = require(modulePath);
} catch (err) {
  // Fall back: strip the relative imports the engine only uses for types.
  const stripped = transpiled.outputText.replace(
    /require\("\.\/[^"]+"\)/g,
    '({})',
  );
  fs.writeFileSync(modulePath, stripped);
  engine = require(modulePath);
}

const sanitize = engine.sanitizeDbcIdentifier;
if (typeof sanitize !== 'function') {
  console.error('sanitizeDbcIdentifier is not exported');
  process.exit(2);
}

// A DBC identifier must match the grammar's identifier rule.
const DBC_SAFE_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

const inputs = [
  'EngineSpeed',
  'engine speed (rpm)',
  '3WayCatTemp',
  'a-b.c/d',
  '  spaced  ',
  'üöçşğÜÖÇŞĞ',
  'SG_ weird $%^ name',
  'x'.repeat(200),
  '',
  '   ',
  '__leading__',
  '9lives',
  'a\nb\tc',
  'name;DROP TABLE',
  'a".b',
];

const rows = inputs.map((input) => {
  const out = sanitize(input);
  return {
    input,
    output: out,
    dbcSafe: DBC_SAFE_RE.test(out) && out.length <= 64,
    lengthOk: out.length >= 1 && out.length <= 64,
  };
});

process.stdout.write(JSON.stringify({ rows }, null, 0));
