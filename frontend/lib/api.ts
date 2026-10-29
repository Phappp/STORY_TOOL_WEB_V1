export type StageState = "pending" | "queued" | "running" | "complete" | "blocked" | "error";
export type StageView = { step: "B1" | "B2" | "B3" | "B4" | "B5"; state: StageState };
export type Workspace = { id: string; workspace_id: string; name: string; slug: string; timezone: string; mode: "active" | "archive" | "maintenance"; ownership: "legacy" | "none" | "web"; admission: boolean; version: number; created_at: string; updated_at: string };
export type Story = { id: string; workspace_id: string; version: number; created_at: string; updated_at: string; display_no: number; title: string; seed: string; type: string; source: string; scope: string; lifecycle: "active" | "trashed" | "archived"; revision_id: string; generation: number; operator_done: boolean; stages: StageView[] };
export type JobState = "queued" | "waiting_dependencies" | "leasing" | "running" | "retry_wait" | "paused" | "blocked" | "awaiting_operator" | "cancelling" | "cancelled" | "succeeded" | "failed" | "needs_reconciliation" | "superseded";
export type Job = { id: string; workspace_id: string; version: number; created_at: string; updated_at: string; story_id: string; stage: string; state: JobState; progress: number; attempts: number; binding: { provider: "none" } };
export type Page<T> = { items: T[]; next_cursor: string | null; limit: number };
export type Settings = { key: "ui"; value: { columns: string[]; copyFields: string[]; theme: "system" | "dark" | "light" }; version: number; updated_at: string };
type Envelope<T> = { data: T; meta: { requestId: string; serverTime: string; schemaVersion: number; idempotentReplay?: boolean } };

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<{ data: T; replayed: boolean }> {
  const response = await fetch(`${apiBase}${path}`, { ...init, headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers }, cache: "no-store" });
  const body = await response.json().catch(() => null) as Envelope<T> | { error?: { message?: string }; detail?: { detail?: string } } | null;
  if (!response.ok) {
    const reason = body && "error" in body ? body.error?.message : body && "detail" in body ? body.detail?.detail : undefined;
    throw new Error(reason ?? `API request failed (${response.status})`);
  }
  return { data: (body as Envelope<T>).data, replayed: Boolean((body as Envelope<T>).meta?.idempotentReplay) };
}

function idempotencyHeaders(): HeadersInit { return { "Idempotency-Key": crypto.randomUUID() }; }

export async function getWorkspaces(): Promise<Workspace[]> { return (await request<Page<Workspace>>("/workspaces")).data.items; }
export async function createWorkspace(body: { name: string; slug: string; timezone: string; storage_location_alias: string }): Promise<Workspace> { return (await request<Workspace>("/workspaces", { method: "POST", headers: idempotencyHeaders(), body: JSON.stringify(body) })).data; }
export async function getStories(workspaceId: string, search = "", lifecycle: "active" | "trashed" = "active"): Promise<Story[]> { const query = new URLSearchParams({ limit: "100", lifecycle }); if (search) query.set("search", search); return (await request<Page<Story>>(`/workspaces/${workspaceId}/stories?${query}`)).data.items; }
export async function createStory(workspaceId: string, body: { seed: string; type: string; prompt_version_id: string; scope: "caption" }): Promise<Story> { return (await request<Story>(`/workspaces/${workspaceId}/stories`, { method: "POST", headers: idempotencyHeaders(), body: JSON.stringify({ ...body, source: "manual", auto_start: false }) })).data; }
export async function updateStory(workspaceId: string, story: Story, body: { type?: string; operator_done?: boolean }): Promise<Story> { return (await request<Story>(`/workspaces/${workspaceId}/stories/${story.id}`, { method: "PATCH", headers: { "If-Match": `"${story.version}"` }, body: JSON.stringify(body) })).data; }
export async function trashStory(workspaceId: string, story: Story): Promise<Story> { return (await request<Story>(`/workspaces/${workspaceId}/stories/${story.id}`, { method: "DELETE", headers: { ...idempotencyHeaders(), "If-Match": `"${story.version}"` }, body: JSON.stringify({ reason: "user_requested" }) })).data; }
export async function restoreStory(workspaceId: string, story: Story): Promise<Story> { return (await request<Story>(`/workspaces/${workspaceId}/stories/${story.id}/restore`, { method: "POST", headers: { ...idempotencyHeaders(), "If-Match": `"${story.version}"` }, body: "{}" })).data; }
export async function getJobs(workspaceId: string): Promise<Job[]> { return (await request<Page<Job>>(`/workspaces/${workspaceId}/jobs?limit=100`)).data.items; }
export async function createJob(workspaceId: string, storyId: string): Promise<Job> { return (await request<Job>(`/workspaces/${workspaceId}/jobs`, { method: "POST", headers: idempotencyHeaders(), body: JSON.stringify({ story_id: storyId, stage: "manual_save" }) })).data; }
export async function getSettings(workspaceId: string): Promise<Settings> { return (await request<Settings>(`/workspaces/${workspaceId}/settings/ui`)).data; }
export async function putSettings(workspaceId: string, settings: Settings): Promise<Settings> { return (await request<Settings>(`/workspaces/${workspaceId}/settings/ui`, { method: "PUT", headers: { "If-Match": `"${settings.version}"` }, body: JSON.stringify({ value: settings.value }) })).data; }
export type BatchPreview = { filename: string; source_hash: string; rows: Array<{ row_no: number; input: string; type: string; enabled: boolean; errors: string[] }>; errors: Array<{ row_no: number; codes: string[] }>; valid_count: number; duplicate_count: number };
export async function previewBatch(workspaceId: string, file: File): Promise<BatchPreview> { const body = new FormData(); body.append("file", file); return (await request<BatchPreview>(`/workspaces/${workspaceId}/batches/preview`, { method: "POST", body })).data; }
export async function confirmBatch(workspaceId: string, preview: BatchPreview): Promise<{ id: string; counts: Record<string, number>; replayed: boolean }> { return (await request<{ id: string; counts: Record<string, number>; replayed: boolean }>(`/workspaces/${workspaceId}/batches`, { method: "POST", headers: idempotencyHeaders(), body: JSON.stringify(preview) })).data; }
export async function getBatches(workspaceId: string): Promise<Array<{ id: string; filename: string; state: string; import_state: string; counts: Record<string, number> }>> { return (await request<{ items: Array<{ id: string; filename: string; state: string; import_state: string; counts: Record<string, number> }>; next_cursor: string | null; limit: number }>(`/workspaces/${workspaceId}/batches`)).data.items; }
