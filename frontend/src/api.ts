export type Row = Record<string, any>;
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
let token = sessionStorage.getItem("mes-token") || "";
export const setToken = (value: string) => {
  token = value;
  value
    ? sessionStorage.setItem("mes-token", value)
    : sessionStorage.removeItem("mes-token");
};
export async function api(
  path: string,
  method = "GET",
  body?: unknown,
  key?: string,
): Promise<any> {
  const response = await fetch("/api" + path, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...(method !== "GET"
        ? { "Idempotency-Key": key || crypto.randomUUID() }
        : {}),
    },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  const data = await response
    .json()
    .catch(() => ({ detail: response.statusText }));
  if (!response.ok)
    throw new ApiError(
      response.status,
      data.code || "API",
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
export async function employeeLogin(
  employee_no: string,
  device: string,
  reuse: boolean,
) {
  const r = await fetch("/api/auth/operator", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Device-Token": device },
    body: JSON.stringify({ employee_no, reuse }),
  });
  const d = await r.json();
  if (!r.ok) throw new ApiError(r.status, d.code, d.detail);
  return d;
}
export async function eventStream(onUpdate: () => void, signal: AbortSignal) {
  try {
    const r = await fetch("/api/events", {
      headers: { Authorization: `Bearer ${token}` },
      signal,
    });
    if (!r.ok || !r.body) return;
    const reader = r.body.getReader(),
      decoder = new TextDecoder();
    let buffer = "";
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const packets = buffer.split("\n\n");
      buffer = packets.pop() || "";
      for (const p of packets) if (p.startsWith("event: update")) onUpdate();
    }
  } catch (e) {
    if (!signal.aborted) console.warn("Realtime disconnected", e);
  }
}
export async function downloadTemplate() {
  const r = await fetch("/api/import/template", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw new Error("Template unavailable");
  const u = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = u;
  a.download = "runcard-template.xlsx";
  a.click();
  URL.revokeObjectURL(u);
}
