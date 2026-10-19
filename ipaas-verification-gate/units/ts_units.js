// The removable units of the added lines of TypeScript and JavaScript files, for the gate's per-unit proof.
//
// Usage: node ts_units.js <path to the typescript package> < request.json
// Input:  {"files": [{"path": "/abs/File.tsx", "added_lines": [3, 4, 9]}]}
// Output: {"units": [...], "unparsed": [paths]}. A unit is listed only when its removal still parses. A
// file that is not valid UTF-8 or does not parse is listed as unparsed, never skipped in silence.

const fs = require("fs");
const ts = require(process.argv[2]);

const KINDS = ts.SyntaxKind;
const DECLARATIONS = new Set([
  KINDS.ImportDeclaration, KINDS.ExportDeclaration, KINDS.InterfaceDeclaration, KINDS.TypeAliasDeclaration,
  KINDS.FunctionDeclaration, KINDS.ClassDeclaration, KINDS.EnumDeclaration, KINDS.ModuleDeclaration,
  KINDS.VariableStatement, KINDS.ExportAssignment,
]);
const STATEMENT_HOLDERS = new Set([
  KINDS.Block, KINDS.SourceFile, KINDS.ModuleBlock, KINDS.CaseClause, KINDS.DefaultClause,
]);
const ENTRY_HOLDERS = new Set([KINDS.ObjectLiteralExpression, KINDS.ArrayLiteralExpression]);
const IGNORED_ATTRIBUTES = new Set(["key"]);
const SCOPES = new Set([
  KINDS.FunctionDeclaration, KINDS.MethodDeclaration, KINDS.ClassDeclaration, KINDS.ArrowFunction,
  KINDS.FunctionExpression,
]);

function scriptKind(path) {
  if (path.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (path.endsWith(".jsx")) return ts.ScriptKind.JSX;
  if (path.endsWith(".js")) return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

function parse(path, text, withParents) {
  return ts.createSourceFile(path, text, ts.ScriptTarget.Latest, withParents, scriptKind(path));
}

function parses(path, text) {
  return parse(path, text, false).parseDiagnostics.length === 0;
}

// The bytes one UTF-16 code unit takes in UTF-8. A surrogate pair takes four, all counted on its first half.
function utf8Width(code) {
  if (code < 0x80) return 1;
  if (code < 0x800) return 2;
  if (code >= 0xd800 && code < 0xdc00) return 4;
  if (code >= 0xdc00 && code < 0xe000) return 0;
  return 3;
}

// A unit's offsets are bytes, for the gate's byte splice. One table per file maps a JavaScript string
// index to its byte offset, instead of encoding the whole prefix again for every unit.
function byteOffsets(text) {
  const offsets = new Uint32Array(text.length + 1);
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    offsets[index + 1] = offsets[index] + utf8Width(code);
  }
  return offsets;
}

function entrySpans(node, siblings) {
  const index = siblings.indexOf(node);
  const spans = [];
  if (siblings[index + 1]) spans.push([node.getStart(), siblings[index + 1].getStart()]);
  if (index > 0) spans.push([siblings[index - 1].getEnd(), node.getEnd()]);
  spans.push([node.getStart(), node.getEnd()]);
  return spans;
}

function isAndExpression(node) {
  return node.kind === KINDS.BinaryExpression && node.operatorToken.kind === KINDS.AmpersandAmpersandToken;
}

function isFallback(node) {
  return node.kind === KINDS.BinaryExpression &&
    [KINDS.BarBarToken, KINDS.QuestionQuestionToken].includes(node.operatorToken.kind);
}

function candidatesAt(node, source) {
  const parent = node.parent;
  const found = [];
  const whole = [[node.getStart(), node.getEnd()]];
  if (parent && STATEMENT_HOLDERS.has(parent.kind) && !DECLARATIONS.has(node.kind)) {
    found.push({ kind: node.kind === KINDS.IfStatement ? "guard" : "statement", spans: whole, replacement: "" });
  } else if (parent && ENTRY_HOLDERS.has(parent.kind)) {
    const siblings = parent.properties || parent.elements;
    found.push({ kind: "entry", spans: entrySpans(node, Array.from(siblings)), replacement: "" });
  } else if (node.kind === KINDS.JsxAttribute && !IGNORED_ATTRIBUTES.has(node.name.getText(source))) {
    found.push({ kind: "attribute", spans: whole, replacement: "" });
  } else if (parent && [KINDS.JsxElement, KINDS.JsxFragment].includes(parent.kind) &&
             [KINDS.JsxElement, KINDS.JsxSelfClosingElement, KINDS.JsxExpression].includes(node.kind)) {
    found.push({ kind: "child", spans: whole, replacement: "" });
  }
  if (isAndExpression(node)) found.push({ kind: "guard", spans: whole, replacement: node.right.getText(source) });
  if (isFallback(node)) found.push({ kind: "fallback", spans: whole, replacement: node.left.getText(source) });
  return found;
}

// The function or class a unit sits in, `Tile#onClick`, so two identical units in two functions are two units.
function scopeOf(node, source) {
  const names = [];
  for (let holder = node.parent; holder; holder = holder.parent) {
    const declared = holder.parent && holder.parent.kind === KINDS.VariableDeclaration && holder.parent.name;
    const named = holder.name || declared;
    if (SCOPES.has(holder.kind) && named) names.unshift(named.getText(source));
  }
  return names.join("#");
}

function statementLine(source, node) {
  let holder = node;
  while (holder.parent && !STATEMENT_HOLDERS.has(holder.parent.kind)) holder = holder.parent;
  return source.getLineAndCharacterOfPosition(holder.getStart()).line + 1;
}

// A unit inside a bigger one on the same line is the sharper question, so the bigger one goes,
// except a guard: deleting a guard asks something its parts do not.
function innermost(units) {
  return units.filter((outer) => outer.kind === "guard" || !units.some((inner) =>
    inner !== outer && inner.start_line === outer.start_line && inner.start >= outer.start &&
    inner.end <= outer.end && inner.end - inner.start < outer.end - outer.start));
}

function unitsFor(path, addedLines, unparsed) {
  const bytes = fs.readFileSync(path);
  const text = bytes.toString("utf8");
  // An invalid byte decodes to U+FFFD, three bytes wide, and every offset after it would drift.
  const source = Buffer.from(text, "utf8").equals(bytes) ? parse(path, text, true) : null;
  if (!source || source.parseDiagnostics.length > 0) {
    unparsed.push(path);
    return [];
  }
  const added = new Set(addedLines);
  const offsets = byteOffsets(text);
  const units = [];
  const visit = (node) => {
    const startLine = source.getLineAndCharacterOfPosition(node.getStart()).line + 1;
    if (added.has(startLine)) {
      for (const candidate of candidatesAt(node, source)) {
        const span = candidate.spans.find(([from, to]) =>
          parses(path, text.slice(0, from) + candidate.replacement + text.slice(to)));
        if (!span) continue;
        const original = text.slice(span[0], span[1]);
        const end = source.getLineAndCharacterOfPosition(node.getEnd());
        units.push({
          path, kind: candidate.kind, start: offsets[span[0]], end: offsets[span[1]], scope: scopeOf(node, source),
          replacement: candidate.replacement, original, start_line: startLine, end_line: end.line + 1,
          start_column: source.getLineAndCharacterOfPosition(node.getStart()).character, end_column: end.character,
          anchor_line: statementLine(source, node),
        });
      }
    }
    ts.forEachChild(node, visit);
  };
  ts.forEachChild(source, visit);
  const seen = new Set();
  const unique = units.filter((unit) => {
    const key = `${unit.start}:${unit.end}:${unit.replacement}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return innermost(unique);
}

const request = JSON.parse(fs.readFileSync(0, "utf8"));
const unparsed = [];
const units = request.files.flatMap((file) => unitsFor(file.path, file.added_lines, unparsed));
process.stdout.write(JSON.stringify({ units, unparsed }));
