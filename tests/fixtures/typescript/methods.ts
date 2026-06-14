// Fixture: class methods and interface method signatures.
// Expected discovery: `Account.constructor`, `Account.deposit`,
// `Account.withdraw`, `Account.balance` (getter) — 4 functions with bodies.
// Interface method *signatures* (`Ledger.record`, `Ledger.total`) have no body
// and are expected to be EXCLUDED.

export interface Ledger {
  record(amount: number): void;
  total(): number;
}

export class Account implements Ledger {
  private entries: number[] = [];

  constructor(private readonly owner: string) {}

  deposit(amount: number): void {
    if (amount <= 0) {
      throw new Error("amount must be positive");
    }
    this.entries.push(amount);
  }

  withdraw(amount: number): void {
    if (amount > this.balance) {
      throw new Error("insufficient funds");
    }
    this.entries.push(-amount);
  }

  get balance(): number {
    return this.entries.reduce((a, b) => a + b, 0);
  }

  record(amount: number): void {
    this.entries.push(amount);
  }

  total(): number {
    return this.balance;
  }
}
