// `is_async` must hold for all three shapes: async function declaration, async arrow,
// and async class method.

export async function loadOne(): Promise<number> {
  return 1;
}

export const loadAll = async (): Promise<number> => {
  return 2;
};

export class Repo {
  async deposit(): Promise<number> {
    return 3;
  }
}
