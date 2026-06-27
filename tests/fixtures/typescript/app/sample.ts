function covered(): number {
  const a = 1;
  return a;
}

function partial(x: number): number {
  let r = 0;
  if (x > 0) {
    r = 1;
  } else {
    r = 2;
  }
  return r;
}
