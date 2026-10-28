export type StageState = "pending" | "queued" | "running" | "complete" | "blocked" | "error";
export type JobState = "queued" | "running" | "succeeded" | "failed" | "blocked";

export type StageView = { step: "B1" | "B2" | "B3" | "B4" | "B5"; state: StageState };
export type Story = {
  id: string;
  display_no: number;
  title: string;
  type: string;
  lifecycle: "active" | "trashed" | "archived";
  updated_at: string;
  stages: StageView[];
};
export type Job = { id: string; story_id: string; stage: string; state: JobState; progress: number; updated_at: string };

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000/api/v1";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, { headers: { Accept: "application/json" }, cache: "no-store" });
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export function getStories(): Promise<Story[]> { return getJson<Story[]>("/stories"); }
export function getJobs(): Promise<Job[]> { return getJson<Job[]>("/jobs"); }
