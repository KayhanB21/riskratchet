// Fixture: top-level function declarations.
// Expected discovery: `add`, `greet`, `parseConfig` (3 functions).

export function add(a: number, b: number): number {
  return a + b;
}

function greet(name: string): string {
  if (name.length === 0) {
    return "hello, stranger";
  }
  return `hello, ${name}`;
}

export async function parseConfig(raw: string): Promise<Record<string, unknown>> {
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}
