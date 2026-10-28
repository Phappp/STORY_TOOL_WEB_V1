"use client";

import { useEffect, useState } from "react";
import { getJobs, getStories, Job, Story } from "../lib/api";

const stageLabels = ["B1", "B2", "B3", "B4", "B5"] as const;

export function Dashboard() {
  const [stories, setStories] = useState<Story[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getStories(), getJobs()]).then(([nextStories, nextJobs]) => {
      setStories(nextStories);
      setJobs(nextJobs);
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Unable to reach the API");
    }).finally(() => setLoading(false));
  }, []);

  if (loading) return <main className="shell"><p className="muted">Loading mock workspace…</p></main>;
  if (error) return <main className="shell"><div className="error"><strong>Backend unavailable</strong><span>{error}. Start FastAPI on 127.0.0.1:8000.</span></div></main>;

  return <main className="shell">
    <header className="hero" id="dashboard"><div><p className="eyebrow">STORY TOOL WEB V1</p><h1>Workspace dashboard</h1><p className="muted">Mock-only foundation · no live AI providers connected</p></div><span className="badge">Development</span></header>
    <section className="summary"><div><span className="muted">Stories</span><strong>{stories.length}</strong></div><div><span className="muted">Active jobs</span><strong>{jobs.filter((job) => job.state === "running").length}</strong></div><div><span className="muted">API source</span><strong>Mock</strong></div></section>
    <section className="panel" id="queue"><div className="panel-heading"><div><p className="eyebrow">QUEUE</p><h2>Story Queue</h2></div><span className="muted">{stories.length} items</span></div><div className="story-list">{stories.map((story) => <article className="story-row" key={story.id}><div className="story-title"><span className="story-number">{String(story.display_no).padStart(2, "0")}</span><div><h3>{story.title}</h3><span className="muted">{story.type} · updated {new Date(story.updated_at).toLocaleTimeString()}</span></div></div><div className="stages">{stageLabels.map((step) => { const stage = story.stages.find((item) => item.step === step); return <span className={`stage ${stage?.state ?? "pending"}`} key={step}><b>{step}</b><small>{stage?.state ?? "pending"}</small></span>; })}</div></article>)}</div></section>
    <section className="panel" id="jobs"><div className="panel-heading"><div><p className="eyebrow">ORCHESTRATION</p><h2>Jobs</h2></div></div><div className="job-list">{jobs.map((job) => <div className="job-row" key={job.id}><div><strong>{job.stage}</strong><span className="muted">{job.story_id}</span></div><span className={`job-state ${job.state}`}>{job.state}</span><div className="progress"><span style={{ width: `${job.progress}%` }} /></div><span className="muted progress-label">{job.progress}%</span></div>)}</div></section>
    <section className="panel" id="settings"><div className="panel-heading"><div><p className="eyebrow">LOCAL DEVELOPMENT</p><h2>Settings</h2></div></div><p className="muted">Mock identity · AI providers disabled · API {process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000/api/v1"}</p></section>
  </main>;
}
