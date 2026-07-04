// File-exported, but only `exposed` is re-exported through the entry barrel.
export function exposed(): number {
  return 1;
}

// File-exported yet unreachable from the barrel → narrowed to internal.
export function alsoExposed(): number {
  return 2;
}
