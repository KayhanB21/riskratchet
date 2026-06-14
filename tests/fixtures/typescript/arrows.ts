// Fixture: arrow functions, including const-assigned and nested.
// Expected discovery: `double`, `clamp`, `makeCounter`, and the nested
// `increment` returned by `makeCounter`. The inline callback passed to `.map`
// is the open question (slice 2 decides whether inline callbacks count).

export const double = (x: number): number => x * 2;

export const clamp = (value: number, lo: number, hi: number): number => {
  if (value < lo) {
    return lo;
  }
  if (value > hi) {
    return hi;
  }
  return value;
};

export const makeCounter = (start: number) => {
  let count = start;
  const increment = (): number => {
    count += 1;
    return count;
  };
  return increment;
};

export const scaleAll = (xs: number[], factor: number): number[] =>
  xs.map((x) => x * factor);
