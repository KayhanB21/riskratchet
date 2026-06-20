// Namespace members must not collide with same-named top-level functions:
// `Foo.bar` (exported within the namespace) vs the top-level `bar`.

namespace Foo {
  export function bar(): number {
    return 1;
  }
}

function bar(): number {
  return 2;
}
