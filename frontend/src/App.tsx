import { NavLink, Navigate, Route, Routes, useParams } from "react-router-dom";
import { BookOpen, FileStack, History, Laptop, Moon, ScrollText, Settings as Cog, Sun, UserRound } from "lucide-react";
import { lastRfp, useReviewer, useRfps, useTheme } from "./hooks";
import { cx } from "./ui";
import RfpList from "./pages/RfpList";
import Studio from "./pages/Studio";
import Clarifications from "./pages/Clarifications";
import Knowledge from "./pages/Knowledge";
import Audit from "./pages/Audit";
import Settings from "./pages/Settings";

function NavItem({ to, icon: Icon, children, end }: { to: string; icon: typeof Cog; children: string; end?: boolean }) {
  return (
    <NavLink to={to} end={end}
      className={({ isActive }) => cx("flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-medium transition sm:px-3",
        isActive ? "bg-accent-soft text-accent" : "text-muted hover:bg-panel2 hover:text-ink")}>
      <Icon className="size-4" /> <span className="hidden md:inline">{children}</span><span className="md:hidden sr-only">{children}</span>
    </NavLink>
  );
}

function Header() {
  const { data: rfps } = useRfps();
  const [theme, setTheme] = useTheme();
  const [reviewer, setReviewer] = useReviewer();
  const studioId = lastRfp.get() ?? rfps?.[0]?.id;
  const next = { system: "light", light: "dark", dark: "system" } as const;
  const ThemeIcon = theme === "dark" ? Moon : theme === "light" ? Sun : Laptop;
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-panel/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 sm:gap-x-3 sm:px-4">
        <NavLink to="/" aria-label="RFP AI home" className="flex items-center gap-2 pr-1 text-lg font-bold tracking-tight sm:pr-2">
          <span className="grid size-7 place-items-center rounded-lg bg-accent text-sm text-on-solid">R</span>
          <span className="hidden sm:inline">RFP<span className="text-accent">AI</span></span>
        </NavLink>
        <nav className="flex min-w-0 items-center gap-0.5 sm:gap-1" aria-label="Main">
          <NavItem to="/" icon={FileStack} end>Active RFPs</NavItem>
          {studioId && <NavItem to={`/rfps/${studioId}`} icon={ScrollText}>Response Studio</NavItem>}
          <NavItem to="/knowledge" icon={BookOpen}>Knowledge Base</NavItem>
          <NavItem to="/audit" icon={History}>Audit Log</NavItem>
          <NavItem to="/settings" icon={Cog}>Settings</NavItem>
        </nav>
        <div className="ml-auto flex min-w-0 items-center gap-1 sm:gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted" title="Your name is recorded on every approval and edit">
            <UserRound className="hidden size-4 sm:block" />
            <span className="sr-only">Reviewing as</span>
            <input aria-label="Reviewing as" className="input !w-16 !py-1 sm:!w-28" defaultValue={reviewer}
                   onBlur={(e) => setReviewer(e.target.value)} onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
          </label>
          <button className="btn btn-ghost btn-sm" onClick={() => setTheme(next[theme])} aria-label={`Theme: ${theme}. Click to change`} title={`Theme: ${theme}`}>
            <ThemeIcon className="size-4" />
          </button>
        </div>
      </div>
    </header>
  );
}

function StudioRoute() {
  const { id } = useParams();
  return <Studio key={id} rfpId={Number(id)} />;
}
function ClarRoute() {
  const { id } = useParams();
  return <Clarifications key={id} rfpId={Number(id)} />;
}

export default function App() {
  return (
    <div className="flex min-h-full flex-col">
      <Header />
      <main className="mx-auto w-full max-w-[1800px] flex-1 px-4 py-4">
        <Routes>
          <Route path="/" element={<RfpList />} />
          <Route path="/rfps/:id" element={<StudioRoute />} />
          <Route path="/rfps/:id/clarifications" element={<ClarRoute />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
