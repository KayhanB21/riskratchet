// Intentionally broken: a genuine syntax error so tree-sitter sets ERROR nodes. The
// discovery path must skip the whole file (and warn) rather than emit partial results.
export function oops( {
  return
