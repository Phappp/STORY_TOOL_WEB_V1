import "./globals.css";

export const metadata = { title: "Story Tool Web V1", description: "Persistent local Story Tool workspace" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><nav className="nav"><a className="logo" href="#dashboard">STORY<span>TOOL</span></a><div className="nav-links"><a className="active" href="#queue">Stories</a><a href="#jobs">Jobs</a><a href="#settings">Settings</a></div><span className="identity">Development identity · local only</span></nav>{children}</body></html>;
}
