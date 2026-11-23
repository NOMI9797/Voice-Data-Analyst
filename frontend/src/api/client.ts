export type Health = {
  status: string;
  llm_provider: string;
  llm_key_set: boolean;
  databases: Record<string, string>;
};

export type Source = {
  id: string;
  name: string;
  engine: "postgresql" | "mysql" | "sqlite" | "duckdb";
  dialect: string;
  origin: "builtin" | "connection" | "upload";
  active: boolean;
  location: string;
  display_url: string;
  created_at: string;
  status?: "ok" | "error";
  profile_status?: string;
  tables?: string[];
  error?: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export type Retrieval = {
  question: string;
  source_id: string | null;
  source_name: string | null;
  dialect: string | null;
  tables: { table: string; score: number; bridge: boolean; value_hits: string[] }[];
  source_scores: { source_id: string; name: string; score: number }[];
  ambiguous_source: boolean;
  value_hits: { table: string; match: string }[];
  schema_text: string;
  ms: number;
};

export type Transcript = {
  text: string;
  duration_s: number;
  avg_logprob: number | null;
  no_speech: boolean;
  ms: number;
};

export type CorrectionResult = {
  original: string;
  corrected: string;
  corrections: { original: string; corrected: string; score: number; locations: { source: string; table: string; column: string }[] }[];
  entities: { text: string; value: string; locations: { source: string; table: string; column: string }[] }[];
  ambiguous: { original: string; corrected: string; alternatives: string[] }[];
  ms: number;
};

export const api = {
  correct: (text: string) =>
    request<CorrectionResult>("/correct", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  stt: (wav: Blob, hint = "") => {
    const form = new FormData();
    form.append("audio", wav, "question.wav");
    form.append("hint", hint);
    return request<Transcript>("/stt", { method: "POST", body: form });
  },
  retrieve: (question: string) =>
    request<Retrieval>("/schema/retrieve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
  reprofile: (id: string) => request<{ status: string }>(`/sources/${id}/profile`, { method: "POST" }),
  health: () => request<Health>("/health"),
  listSources: () => request<Source[]>("/sources"),
  addConnection: (name: string, url: string) =>
    request<Source>("/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, url }),
    }),
  uploadFiles: (files: File[], name = "") => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("name", name);
    return request<Source>("/sources/upload", { method: "POST", body: form });
  },
  setActive: (id: string, active: boolean) =>
    request<Source>(`/sources/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    }),
  removeSource: (id: string) => request<void>(`/sources/${id}`, { method: "DELETE" }),
};
