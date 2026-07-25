'use strict';
// Shared coverage sample. `classify` has two branch points; the test exercises only the
// positive path, leaving the negative and zero arms (and their lines) uncovered — so every
// producer must emit a partial line + partial branch report over identical source lines.
function classify(n) {
  if (n > 0) {
    return 'pos';
  } else if (n < 0) {
    return 'neg';
  }
  return 'zero';
}

function greet(name) {
  return 'hi ' + name;
}

module.exports = { classify, greet };
