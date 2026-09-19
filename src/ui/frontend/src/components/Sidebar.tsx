import React from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  LayoutDashboard,
  Wand2,
  Cpu,
  Share2,
  FileText,
  Radio,
  Settings,
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
}

const navSections: { section: string; items: NavItem[] }[] = [
  {
    section: 'Teşhis',
    items: [
      {
        id: 'dashboard',
        label: 'CAN Dashboard',
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
    // ponytail: zeron style collapsible sidebar; add nested project trees when workspace multi-bus added.
    <aside className="glass-surface glass-sidebar flex h-full w-[230px] shrink-0 flex-col border-r border-white/[0.08] select-none text-zinc-300">
      {/* Brand Header */}
      <div className="flex h-11 shrink-0 items-center gap-2.5 border-b border-white/[0.08] px-4 bg-white/[0.02]">
        <div className="flex h-6 w-6 items-center justify-center rounded-lg bg-indigo-600/20 border border-indigo-500/30 text-indigo-400 shadow-sm">
          <Radio className="h-3.5 w-3.5" strokeWidth={2.4} />
        </div>
        <div className="min-w-0">
          <h1 className="truncate text-xs font-bold tracking-tight text-white">
            <a
              href="https://ucanlab.org"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-indigo-400 transition-colors"
              title="ucanlab.org"
            >
              UCanLab v13.0
            </a>
          </h1>
        </div>
      </div>

      {/* Navigation Sections */}
      <nav className="flex-1 overflow-y-auto px-2.5 py-3 space-y-4">
        {navSections.map((section) => (
          <div key={section.section} className="space-y-1">
            <div className="px-2 pb-1 text-[10px] font-bold uppercase tracking-[0.14em] text-zinc-500">
              {section.section}
            </div>
            <div className="space-y-0.5">
              {section.items.map((item) => {
                const isActive = activeTab === item.id;
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    onClick={() => onSelectTab(item.id)}
                    className={`focus-ring group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs font-medium transition-all duration-150 ${
                      isActive
                        ? 'bg-indigo-600/90 text-white shadow-sm shadow-indigo-600/20 font-semibold'
                        : 'text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-100 active:scale-[0.98]'
                    }`}
                  >
                    <Icon
                      className={`h-4 w-4 shrink-0 ${
                        isActive ? 'text-white' : 'text-zinc-500 group-hover:text-zinc-300'
                      }`}
                      strokeWidth={2}
                    />
                    <span className="block truncate">{item.label}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        {/* Bottom Section: Ayarlar (Yeni sayfa şeklinde açılır) */}
        <div className="pt-2 border-t border-white/[0.08] space-y-0.5">
          <div className="px-2 pb-1 text-[10px] font-bold uppercase tracking-[0.14em] text-zinc-500">
            Sistem
          </div>
          <button
            onClick={() => onSelectTab('settings')}
            className={`focus-ring group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs font-medium transition-all duration-150 ${
              activeTab === 'settings'
                ? 'bg-indigo-600/90 text-white shadow-sm shadow-indigo-600/20 font-semibold'
                : 'text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-100 active:scale-[0.98]'
            }`}
          >
            <Settings
              className={`h-4 w-4 shrink-0 ${
                activeTab === 'settings' ? 'text-white' : 'text-zinc-500 group-hover:text-zinc-300'
              }`}
              strokeWidth={2}
            />
            <span className="block truncate">Ayarlar</span>
          </button>
        </div>
      </nav>

      {/* Bottom: CAN Bus Status Card */}
      <div className="shrink-0 border-t border-white/[0.08] p-2.5 bg-black/20">
        <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2 shrink-0">
                {!isEstopActive && isSimulating && (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                )}
                <span
                  className={`relative inline-flex h-2 w-2 rounded-full ${
                    isEstopActive ? 'bg-rose-500' : isSimulating ? 'bg-emerald-500' : 'bg-amber-500'
                  }`}
                />
              </span>
              <span className="text-[11px] font-semibold text-zinc-200">{statusLabel}</span>
            </div>
            <span className="font-mono text-[10px] font-bold text-zinc-400">{channel}</span>
          </div>
        </div>
      </div>
    </aside>
  );
};
