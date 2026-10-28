import "./globals.css";

export const metadata = { title: "Story Tool Web V1", description: "Mock-only Story Tool foundation" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><nav className="nav"><a className="logo" href="#dashboard">STORY<span>TOOL</span></a><div className="nav-links"><a className="active" href="#queue">Queue</a><a href="#jobs">Jobs</a><a href="#settings">Settings</a></div><span className="identity">Mock identity · local</span></nav>{children}</body></html>;
}
