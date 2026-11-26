import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { navigation } from './navigation';
import { Icon } from '../shared/components/Icon';
import { ServiceStatus } from '../shared/components/ServiceStatus';
import { useAuth } from '../shared/auth/AuthProvider';
import { allowed } from '../shared/auth/permissions';

export function AppLayout() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem('twinner.sidebar.collapsed') === 'true'; }
    catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem('twinner.sidebar.collapsed', String(collapsed)); }
    catch { /* Storage may be unavailable. */ }
  }, [collapsed]);
  const { user, signOut } = useAuth();
  const { pathname } = useLocation();
  const isSimulationResult =
  /^\/simulasi\/\d+\/result\/?$/.test(pathname);
  const menu = allowed(navigation, user);
  const current = menu.find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`));

  return (
    <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}>
      <a className="skip-link" href="#main-content">Lewati ke konten</a>
      <aside id="sidebar" className={`sidebar ${menuOpen ? 'is-open' : ''}`}>
        <div className="sidebar-brand-header">
        <NavLink aria-label="Digital Twin PuTI" className="brand" to="/" onClick={() => setMenuOpen(false)}>
          <span className="brand-mark"><Icon name="folder-open-dot" /></span>
          <span className="brand-text"><strong>Digital Twin PuTI</strong><small>Simulasi proyek</small></span>
        </NavLink>
        <button type="button" className="sidebar-collapse-toggle" aria-controls="sidebar" aria-expanded={!collapsed} aria-label={collapsed ? 'Perluas sidebar' : 'Ciutkan sidebar'} title={collapsed ? 'Perluas sidebar' : 'Ciutkan sidebar'} onClick={() => setCollapsed(v => !v)}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/><path d={collapsed ? 'm13 9 3 3-3 3' : 'm16 9-3 3 3 3'}/></svg>
        </button>
        </div>
        <div className="navigation-section">
          <p className="eyebrow">Halaman</p>
          <nav aria-label="Navigasi utama">
            {menu.map((item) => (
              <NavLink key={item.path} aria-label={item.label} title={item.label} to={item.path} onClick={() => setMenuOpen(false)} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                <Icon name={item.icon} />
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="context-section">
          <p className="eyebrow">Konteks</p>
          <dl className="context-card">
            <div><dt>Dataset</dt><dd>Belum tersedia</dd></div>
            <div><dt>Proyek aktif</dt><dd>—</dd></div>
            <div><dt>Resource</dt><dd>—</dd></div>
            <div><dt>Simulasi terakhir</dt><dd>—</dd></div>
          </dl>
        </div>
        <div className="sidebar-footer"><span className="workspace-avatar">P</span><div><strong>Workspace PuTI</strong><small>Perencanaan &amp; simulasi</small></div></div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <button type="button" className="menu-toggle" aria-controls="sidebar" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>Menu</button>
          <div className="topbar-title"><strong>{current?.label ?? 'Digital Twin PuTI'}</strong><small>Workspace PuTI</small></div>
          <div className="topbar-actions">
            <span className="selector"><Icon name="calendar-days" size="sm" />Belum ada dataset<Icon name="chevron-down" size="sm" /></span>
            <div className="user-menu">
              <span className="topbar-avatar" aria-hidden="true"><Icon name="user" size="lg" /></span>
              <span className="user-name"><strong>{user?.full_name}</strong><small>@{user?.username}</small></span>
              <button type="button" className="button button-ghost" onClick={signOut}>Keluar</button>
            </div>
          </div>
        </header>
        <main id="main-content" className="page-content" tabIndex={-1}>
          {current && !isSimulationResult && <div className="page-heading"><p className="eyebrow">Workspace / PuTI</p><h1>{current.label}</h1><p>{current.description}</p></div>}
          <Outlet />
        </main>
        <footer className="main-footer"><span>Digital Twin PuTI</span><ServiceStatus /></footer>
      </div>
    </div>
  );
}
