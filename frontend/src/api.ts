export type Role = "pvz_operator" | "seller" | "logist" | "admin";

/** Все запросы к backend идут через прокси Vite: /api/* → backend. */
export async function api<T>(path: string, role: Role, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Role": role, ...init.headers },
  });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json() as Promise<T>;
}
