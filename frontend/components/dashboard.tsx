"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { createJob, createStory, createWorkspace, getJobs, getSettings, getStories, getWorkspaces, Job, Settings, Story, updateStory, Workspace, putSettings } from "../lib/api";

const blankSettings: Settings = { key: "ui", value: { columns: [], copyFields: [], theme: "system" }, version: 0, updated_at: "" };

export function Dashboard() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [stories, setStories] = useState<Story[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [settings, setSettings] = useState<Settings>(blankSettings);
  const [selectedStory, setSelectedStory] = useState<Story | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadWorkspaces = useCallback(async () => {
    const rows = await getWorkspaces();
    setWorkspaces(rows);
    setWorkspaceId((current) => rows.some((item) => item.id === current) ? current : rows[0]?.id ?? "");
  }, []);

  const loadData = useCallback(async (id: string, query: string) => {
    if (!id) { setStories([]); setJobs([]); setSettings(blankSettings); return; }
    const [nextStories, nextJobs, nextSettings] = await Promise.all([getStories(id, query), getJobs(id), getSettings(id)]);
    setStories(nextStories);
    setJobs(nextJobs);
    setSettings(nextSettings);
    setSelectedStory((current) => current ? nextStories.find((item) => item.id === current.id) ?? null : null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    getWorkspaces().then(async (rows) => {
      if (cancelled) return;
      setWorkspaces(rows);
      const chosen = rows.some((item) => item.id === workspaceId) ? workspaceId : rows[0]?.id ?? "";
      if (!workspaceId && chosen) setWorkspaceId(chosen);
      if (chosen) await loadData(chosen, search);
      else { setStories([]); setJobs([]); setSettings(blankSettings); }
    }).catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Unable to load workspace data"); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [workspaceId, search, loadData]);

  async function run(action: () => Promise<unknown>) {
    setSaving(true); setError(null);
    try { await action(); if (workspaceId) await loadData(workspaceId, search); await loadWorkspaces(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Request failed"); }
    finally { setSaving(false); }
  }

  async function submitWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    await run(async () => { const workspace = await createWorkspace({ name: String(form.get("name")), slug: String(form.get("slug")), timezone: "Asia/Bangkok", storage_location_alias: "local-development" }); setWorkspaceId(workspace.id); });
    event.currentTarget.reset();
  }

  async function submitStory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!workspaceId) return; const form = new FormData(event.currentTarget);
    await run(async () => { const story = await createStory(workspaceId, { seed: String(form.get("seed")), type: String(form.get("type")), prompt_version_id: "00000000-0000-4000-8000-000000000000", scope: "caption" }); setSelectedStory(story); });
    event.currentTarget.reset();
  }

  if (loading) return <main className="shell"><p className="muted" role="status">Loading development workspace…</p></main>;

  return <main className="shell">
    <header className="hero" id="dashboard"><div><p className="eyebrow">STORY TOOL WEB V1</p><h1>Workspace dashboard</h1><p className="muted">Persistent local data · workers and AI providers are disabled</p></div><span className="badge">Local development</span></header>
    {error && <div className="error" role="alert"><strong>Request failed</strong><span>{error}</span><button className="button secondary" onClick={() => void loadData(workspaceId, search)}>Retry</button></div>}
    <section className="panel workspace-bar"><label htmlFor="workspace">Workspace</label><select id="workspace" value={workspaceId} onChange={(event) => { setWorkspaceId(event.target.value); setSelectedStory(null); }}><option value="">Choose a workspace</option>{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button className="button secondary" onClick={() => void run(loadWorkspaces)} disabled={saving}>Refresh</button></section>
    {!workspaces.length && <section className="panel"><div className="panel-heading"><div><p className="eyebrow">GET STARTED</p><h2>Create a workspace</h2></div></div><form className="form-row" onSubmit={submitWorkspace}><label>Workspace name<input name="name" required maxLength={120} placeholder="My stories" /></label><label>URL slug<input name="slug" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxLength={80} placeholder="my-stories" /></label><button className="button" disabled={saving}>Create workspace</button></form></section>}
    {workspaceId && <>
      <section className="summary"><div><span className="muted">Stories</span><strong>{stories.length}</strong></div><div><span className="muted">Queued jobs</span><strong>{jobs.filter((job) => job.state === "queued").length}</strong></div><div><span className="muted">Persistence</span><strong>MongoDB</strong></div></section>
      <section className="panel" id="queue"><div className="panel-heading"><div><p className="eyebrow">STORY QUEUE</p><h2>Stories</h2></div><input className="search" aria-label="Search stories" placeholder="Search stories" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
        <form className="story-form" onSubmit={submitStory}><label>Story seed<textarea name="seed" required maxLength={140000} rows={3} placeholder="Enter the source text for a manual Story record…" /></label><div className="form-row"><label>TYPE<input name="type" required maxLength={80} defaultValue="General" /></label><button className="button" disabled={saving}>Create Story</button></div><p className="muted">Creates a persisted manual Story with all five stages pending. No prompt is generated and no worker is started.</p></form>
        {!stories.length ? <p className="empty">No Stories in this workspace yet. Create one above.</p> : <div className="story-list">{stories.map((story) => <article className={`story-row ${selectedStory?.id === story.id ? "selected" : ""}`} key={story.id}><button className="story-open" onClick={() => setSelectedStory(story)}><span className="story-number">{String(story.display_no).padStart(2, "0")}</span><span><strong>{story.title}</strong><small>{story.type} · {story.source} · version {story.version}</small></span></button><div className="stages">{story.stages.map((stage) => <span className={`stage ${stage.state}`} key={stage.step}><b>{stage.step}</b><small>{stage.state}</small></span>)}</div></article>)}</div>}
      </section>
      {selectedStory && <StoryDetail key={selectedStory.id} story={selectedStory} disabled={saving} onSave={(body) => void run(async () => setSelectedStory(await updateStory(workspaceId, selectedStory, body)))} onCreateJob={() => void run(async () => { await createJob(workspaceId, selectedStory.id); })} />}
      <section className="panel" id="jobs"><div className="panel-heading"><div><p className="eyebrow">PERSISTED QUEUE</p><h2>Jobs</h2></div><span className="muted">API never accepts worker state changes</span></div>{!jobs.length ? <p className="empty">No queued jobs.</p> : <div className="job-list">{jobs.map((job) => <div className="job-row" key={job.id}><div><strong>{job.stage}</strong><span className="muted">Story {job.story_id.slice(0, 8)}</span></div><span className={`job-state ${job.state}`}>{job.state}</span><div className="progress"><span style={{ width: `${job.progress}%` }} /></div><span className="muted progress-label">{job.progress}%</span></div>)}</div>}</section>
      <section className="panel" id="settings"><div className="panel-heading"><div><p className="eyebrow">WORKSPACE SETTINGS</p><h2>Preferences</h2></div></div><form className="form-row" onSubmit={(event) => { event.preventDefault(); void run(async () => setSettings(await putSettings(workspaceId, settings))); }}><label>Theme<select value={settings.value.theme} onChange={(event) => setSettings({ ...settings, value: { ...settings.value, theme: event.target.value as Settings["value"]["theme"] } })}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label><button className="button secondary" disabled={saving}>Save settings (v{settings.version})</button></form></section>
    </>}
  </main>;
}

function StoryDetail({ story, disabled, onSave, onCreateJob }: { story: Story; disabled: boolean; onSave: (body: { type?: string; operator_done?: boolean }) => void; onCreateJob: () => void }) {
  const [type, setType] = useState(story.type);
  const [done, setDone] = useState(story.operator_done);
  return <section className="panel detail"><div className="panel-heading"><div><p className="eyebrow">STORY DETAIL · {story.id.slice(0, 8)}</p><h2>{story.title}</h2></div><span className="muted">Version {story.version}</span></div><p className="seed-preview">{story.seed}</p><div className="form-row"><label>TYPE<input value={type} onChange={(event) => setType(event.target.value)} maxLength={80} /></label><label className="checkbox"><input type="checkbox" checked={done} onChange={(event) => setDone(event.target.checked)} /> Operator done</label><button className="button" disabled={disabled} onClick={() => onSave({ type, operator_done: done })}>Save Story</button><button className="button secondary" disabled={disabled} onClick={onCreateJob}>Add local queued job</button></div><p className="muted">Revision {story.revision_id} · no AI activity · five stages remain pending until a future worker phase.</p></section>;
}
