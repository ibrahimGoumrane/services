import { NavLink } from "react-router-dom";
import { Database, Link2, FileSpreadsheet } from "lucide-react";

export function NavHeader() {
  const linkClass = ({
    isActive,
  }: {
    isActive: boolean;
  }) =>
    `flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all duration-300 ${
      isActive
        ? "bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 shadow-lg shadow-indigo-500/10"
        : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 border border-transparent"
    }`;

  return (
    <header className="border-b border-slate-800/40 bg-[#060b16]/70 backdrop-blur-2xl sticky top-0 z-50 nav-glow">
      <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
        <NavLink to="/" className="flex items-center gap-3 group">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-cyan-500 flex items-center justify-center shadow-lg shadow-indigo-500/20 border border-white/10 group-hover:shadow-indigo-500/30 transition-shadow">
            <Database className="w-4 h-4 text-white" />
          </div>
          <span className="font-bold text-slate-100 tracking-tight text-lg">
            FormaFast
          </span>
          <span className="px-2 py-0.5 rounded-full bg-indigo-500/15 text-[10px] font-mono text-indigo-300 border border-indigo-500/20">
            v2
          </span>
        </NavLink>

        <nav className="flex items-center gap-2">
          <NavLink to="/single-url" className={linkClass}>
            <Link2 className="w-4 h-4" />
            Single URL
          </NavLink>
          <NavLink to="/batch" className={linkClass}>
            <FileSpreadsheet className="w-4 h-4" />
            Batch CSV
          </NavLink>
        </nav>
      </div>
    </header>
  );
}
