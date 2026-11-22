export type Health = {
  status: string;
  llm_provider: string;
  llm_key_set: boolean;
  databases: Record<string, string>;
};

export async function getHealth(): Promise<Health> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
