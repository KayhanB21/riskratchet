// Export reachability via separate `export { … }` clauses (not just inline `export`):
// `Svc` and `helper` are public though declared without an inline `export`.

class Svc {
  run(): void {}
}

export { Svc };

function helper(): void {}

export { helper as default };

function hidden(): void {}
