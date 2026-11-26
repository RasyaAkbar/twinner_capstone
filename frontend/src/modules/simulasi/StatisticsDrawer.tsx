import { useEffect, useId, type ReactNode } from 'react';
export function StatisticsDrawer({title,onClose,children}:{title:string;onClose:()=>void;children:ReactNode}) {
 const id=useId();
 useEffect(()=>{const handler=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose()};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler)},[onClose]);
 return <aside className="sr-drawer" aria-labelledby={id}><header><h4 id={id}>{title}</h4><button type="button" aria-label="Tutup detail" onClick={onClose}>×</button></header><div className="sr-drawer-body" aria-live="polite">{children}</div></aside>;
}
