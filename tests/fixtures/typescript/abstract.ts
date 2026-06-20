// Abstract class: methods get the class prefix; the abstract signature has no body
// (`abstract_method_signature`) and is excluded.

export abstract class Shape {
  abstract area(): number;

  concrete(): number {
    return 1;
  }
}
