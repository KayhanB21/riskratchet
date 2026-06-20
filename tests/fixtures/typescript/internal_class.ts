// A non-exported class: its method is internal (exercises the class-not-exported
// branch). `legacy` is a named `function_expression` (kind "function").

class Internal {
  helper(): number {
    return 1;
  }
}

const legacy = function (): number {
  return 2;
};
