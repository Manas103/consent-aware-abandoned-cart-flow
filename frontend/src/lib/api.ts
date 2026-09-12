import { FlowPayload } from "./flowPayload";

export async function createFlow(apiBaseUrl: string, payload: FlowPayload): Promise<{ id: number }> {
  const res = await fetch(`${apiBaseUrl}/api/flows`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(`create_flow failed: ${res.status}`);
  }
  return res.json();
}
