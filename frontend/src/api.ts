export type Repository = {
  repository_id: string;
  url: string;
  owner: string;
  name: string;
  status: string;
  phase: string;
  progress_current?: number;
  progress_total?: number;
  warnings: string[];
  skipped: Record<string, number>;
  active_snapshot_id: string | null;
  active_commit: string | null;
};

export type IngestionJob = {
  job_id: string;
  repository_id: string;
  status: string;
  phase: string;
  progress_current?: number;
  progress_total?: number;
  error: string | null;
  warnings: string[];
  skipped: Record<string, number>;
};

export type ChatSession = {
  session_id: string;
  repository_id: string;
  title: string;
};

export type Citation = {
  path: string;
  start_line: number;
  end_line: number;
  snippet: string;
  commit_sha: string | null;
  local_ref: string | null;
  github_permalink: string | null;
  stale: boolean;
};

export type ChatMessage = {
  message_id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  model: string | null;
  snapshot_id: string | null;
  citations: Citation[];
};

export type FileEntry = {
  path: string;
  kind: "file" | "directory";
  indexable: boolean;
  skipped_reason: string | null;
  size: number;
};

export type FilePreview = {
  path: string;
  content: string;
  previewable: boolean;
  reason: string | null;
  size: number;
};

export type StreamEvent =
  | { event: "retrieval_started"; data: Record<string, unknown> }
  | { event: "sources"; data: Citation[] | { citations?: Citation[]; sources?: Citation[] } }
  | { event: "token"; data: { token?: string; delta?: string } }
  | { event: "final"; data: { message?: ChatMessage; citations?: Citation[]; content?: string } }
  | { event: "error"; data: { code: string; message: string; details?: unknown } };

type ApiErrorPayload = {
  code?: string;
  message?: string;
  detail?: unknown;
};

export class ApiError extends Error {
  code: string;
  details: unknown;

  constructor(payload: ApiErrorPayload, fallback: string) {
    super(payload.message ?? fallback);
    this.code = payload.code ?? "api_error";
    this.details = payload.detail;
  }
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!response.ok) {
    let payload: ApiErrorPayload = {};
    try {
      payload = (await response.json()) as ApiErrorPayload;
    } catch {
      payload = { message: response.statusText };
    }
    throw new ApiError(payload, `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export function listRepositories() {
  return request<{ repositories: Repository[] }>("/repositories");
}

export function submitRepository(url: string) {
  return request<{
    repository_id: string;
    url: string;
    job_id: string;
    status: string;
    phase: string;
  }>("/repositories", { method: "POST", body: JSON.stringify({ url }) });
}

export function refreshRepository(repositoryId: string) {
  return request<{
    job_id: string;
    repository_id: string;
    status: string;
    phase: string;
    warnings: string[];
    skipped: Record<string, number>;
    full_rebuild_available: boolean;
  }>(`/repositories/${repositoryId}/refresh`, { method: "POST" });
}

export function getIngestionJob(jobId: string) {
  return request<IngestionJob>(`/ingestion-jobs/${jobId}`);
}

export function listChatSessions(repositoryId: string) {
  return request<{ sessions: ChatSession[] }>(`/repositories/${repositoryId}/chat-sessions`);
}

export function createChatSession(repositoryId: string, title: string) {
  return request<ChatSession>(`/repositories/${repositoryId}/chat-sessions`, {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export function listChatMessages(sessionId: string) {
  return request<{ messages: ChatMessage[] }>(`/chat-sessions/${sessionId}/messages`);
}

export function listFileTree(repositoryId: string) {
  return request<{ entries: FileEntry[] }>(`/repositories/${repositoryId}/files/tree`);
}

export function getFileContent(repositoryId: string, path: string) {
  return request<FilePreview>(
    `/repositories/${repositoryId}/files/content?path=${encodeURIComponent(path)}`,
  );
}

export async function streamChatMessage(
  sessionId: string,
  content: string,
  snapshotId: string | null,
  onEvent: (event: StreamEvent) => void,
) {
  const response = await fetch(`${API_BASE}/chat-sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, snapshot_id: snapshotId }),
  });
  if (!response.ok || response.body === null) {
    throw new Error("Unable to open chat stream.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const event = parseSseChunk(chunk);
      if (event) onEvent(event);
    }
  }
}

function parseSseChunk(chunk: string): StreamEvent | null {
  const eventLine = chunk.split("\n").find((line) => line.startsWith("event:"));
  const dataLine = chunk.split("\n").find((line) => line.startsWith("data:"));
  if (!eventLine || !dataLine) return null;

  const event = eventLine.replace("event:", "").trim();
  const data = JSON.parse(dataLine.replace("data:", "").trim()) as StreamEvent["data"];
  return { event, data } as StreamEvent;
}
