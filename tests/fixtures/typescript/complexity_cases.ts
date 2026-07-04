// Fixture: cyclomatic complexity (P20 slice 4, since 0.2.14). Each function's expected
// McCabe count is asserted in tests/test_typescript_complexity.py. The numbers exercise the
// two deliberate TS decisions: `??` IS counted, optional chaining `?.` is NOT, and nested
// functions are pruned (each is its own unit).

// No branches → CC 1.
export function straight(x: number): number {
  const y = x + 1;
  return y;
}

// if(+1) · else-if(+1) · &&(+1) · ternary(+1) · ||(+1) · ternary(+1) → CC 7.
export function branchy(a: number, b: number): number {
  if (a > 0) {
    return 1;
  } else if (b > 0) {
    return 2;
  }
  const c = a > 0 && b > 0 ? a : b;
  return a || b ? c : 0;
}

// for-of(+1) · while(+1) · do(+1) · case(+1) · case(+1) [default NOT counted] · catch(+1)
// · ??(+1) → CC 8.
export function loopy(items: number[]): number {
  let total = 0;
  for (const it of items) {
    total += it;
  }
  while (total > 100) {
    total -= 1;
  }
  do {
    total += 1;
  } while (total < 0);
  switch (total) {
    case 1:
      total = 10;
      break;
    case 2:
      total = 20;
      break;
    default:
      total = 0;
  }
  try {
    total += 1;
  } catch (e) {
    total = -1;
  }
  return total ?? 0;
}

// ??(+1) only → CC 2. The two `?.` are not counted; the nested arrow's ternary is pruned.
export function optionalChainAndNested(obj: { a?: { b?: number } }): number {
  const v = obj?.a?.b ?? 0;
  const inner = (n: number) => (n > 0 ? 1 : 2);
  return v + inner(v);
}
