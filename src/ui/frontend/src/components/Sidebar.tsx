import React from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  LayoutDashboard,
  Wand2,
  Cpu,
  Share2,
  FileText,
  Radio,
} from 'lucide-react';
import { ActiveTab } from '../types/can';

interface SidebarProps {
  activeTab: ActiveTab;
  onSelectTab: (tab: ActiveTab) => void;
  channel: string;
  isSimulating: boolean;
  isEstopActive: boolean;
}

interface NavItem {
  id: ActiveTab;
  label: string;
  icon: LucideIcon;
  subItems?: { id: ActiveTab; label: string; icon: LucideIcon }[];
}

const navSections: { section: string; items: NavItem[] }[] = [
  {
    section: 'Teşhis',
    items: [
      {
        id: 'dashboard',
        label: 'Dashboard',
        icon: LayoutDashboard,
      },
      {
        id: 'signal_discovery',
        label: 'Reverse Engineer',
        icon: Wand2,
      },
    ],
  },
  {
    section: 'Araçlar',
    items: [
      {
        id: 'ecu_flashing',
        label: 'ECU Flashing',
        icon: Cpu,
      },
      {
        id: 'pinout_guide',
        label: 'Pinout Rehberi',
        icon: Share2,
      },
      {
        id: 'reports',
        label: 'Rapor & Export',
        icon: FileText,
      },
    ],
  },
];

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  onSelectTab,
  channel,
  isSimulating,
  isEstopActive,
}) => {
  const statusLabel = isEstopActive
    ? 'Durduruldu'
    : isSimulating
      ? 'Canlı Akış'
      : 'Bağlı';

  return (
    <aside className="flex h-screen w-[240px] shrink-0 flex-col border-r border-slate-200 bg-white">
      {/* Brand Header */}
      <div className="flex h-[60px] shrink-0 items-center gap-3 border-b border-slate-200 px-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 shadow-sm shadow-brand-900/15">
          <Radio className="h-5 w-5 text-white" strokeWidth={2.2} />
        </div>
        <div className="min-w-0">
          <h1 className="truncate text-[15px] font-bold tracking-tight text-slate-950">
            <a
              href="https://ucanlab.org"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-brand-600 transition-colors"
              title="ucanlab.org"
            >
              ucanlab.org
            </a>
          </h1>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        {navSections.map((section) => (
          <div key={section.section} className="mb-5">
            <div className="px-3 pb-2 text-xs font-bold uppercase tracking-[0.14em] text-slate-500">
              {section.section}
            </div>
            <div className="space-y-1">
              {section.items.map((item) => {
                const isActive = activeTab === item.id;
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    onClick={() => onSelectTab(item.id)}
                    className={`focus-ring group flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[13px] font-semibold transition-all duration-150 ${
                      isActive
                        ? 'bg-brand-600 text-white shadow-sm shadow-brand-500/30'
                        : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 active:scale-[0.98]'
                    }`}
                  >
                    <Icon
                      className={`h-4 w-4 shrink-0 ${
                        isActive ? 'text-white' : 'text-slate-500 group-hover:text-slate-600'
                      }`}
                      strokeWidth={2}
                    />
                    <span className="block truncate leading-tight">{item.label}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Bottom: Bus Status Card */}
      <div className="shrink-0 border-t border-slate-200 p-3">
        <div className="surface-soft p-3">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2 shrink-0">
              {!isEstopActive && isSimulating && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal-400 opacity-75"></span>
              )}
              <span
                className={`relative inline-flex h-2 w-2 rounded-full ${
                  isEstopActive ? 'bg-rose-500' : isSimulating ? 'bg-signal-500' : 'bg-amber-500'
                }`}
              ></span>
            </span>
            <span className="text-xs font-semibold text-slate-900">{statusLabel}</span>
          </div>
          <p className="mt-1.5 font-mono text-xs font-semibold text-slate-500">{channel}</p>
        </div>
      </div>
    </aside>
  );
};
