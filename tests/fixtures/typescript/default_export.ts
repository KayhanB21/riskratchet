// Fixture: default-exported function plus a named helper.
// Expected discovery: `createClient` (default export, public) and `buildHeaders`
// (named, not exported — internal).

interface ClientOptions {
  baseUrl: string;
  token?: string;
}

function buildHeaders(token?: string): Record<string, string> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (token) {
    headers.authorization = `Bearer ${token}`;
  }
  return headers;
}

export default function createClient(options: ClientOptions) {
  return {
    get(path: string) {
      return fetch(`${options.baseUrl}${path}`, {
        headers: buildHeaders(options.token),
      });
    },
  };
}
