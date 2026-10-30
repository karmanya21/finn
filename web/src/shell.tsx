// App chrome shared by every screen: header, bottom tab bar, hash navigation.

const here = () => window.location.hash.replace(/^#/, "") || "/";

/** Navigate, remembering where from, so a sheet can close by stepping back. */
export const go = (path: string) => {
  const from = here();
  window.location.hash = path;
  history.replaceState({ from }, "");
};

/** Leave an overlay (a promise sheet) for `path` without adding a history entry: step back
 *  if that's where we came from, else swap this entry for it. Back then lands where the
 *  user was before, not on the sheet again. */
export const closeTo = (path: string) => {
  if (history.state?.from === path) history.back();
  else window.location.replace(`#${path}`);
};

export type Tab = "today" | "filings" | "portfolio";

const TABS: { key: Tab; label: string; path: string; icon: string }[] = [
  { key: "today", label: "Today", path: "/", icon: "M4 12h16M4 6h16M4 18h10" },
  { key: "filings", label: "Filings", path: "/filings", icon: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6" },
  { key: "portfolio", label: "Portfolio", path: "/portfolio", icon: "M4 20V10M10 20V4M16 20v-7M22 20H2" },
];

export function Shell({ children, back, tab, action }: {
  children: React.ReactNode; back?: boolean; tab?: Tab; action?: React.ReactNode;
}) {
  return (
    <div className={`app ${tab ? "with-tabs" : ""}`}>
      <header className="top">
        {back ? <button className="back" onClick={() => history.length > 1 ? history.back() : go("/")} aria-label="Back">‹</button> : null}
        <a className="wordmark" href="#/">FINN</a>
        {action ? <span className="top-action">{action}</span> : null}
      </header>
      <main>{children}</main>
      {tab && (
        <nav className="tabbar" aria-label="Sections">
          {TABS.map((t) => (
            <a key={t.key} href={`#${t.path}`} className={`tab ${t.key === tab ? "on" : ""}`}>
              <svg viewBox="0 0 24 24" aria-hidden><path d={t.icon} /></svg>
              <span>{t.label}</span>
            </a>
          ))}
        </nav>
      )}
    </div>
  );
}
