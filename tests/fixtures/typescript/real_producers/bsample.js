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
