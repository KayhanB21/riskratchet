function covered(n) {
  const a = n + 1;
  return a;
}
function partial(x) {
  let r = 0;
  if (x > 0) {
    r = 1;
  } else {
    r = 2;
  }
  return r;
}
covered(5);
partial(5);
