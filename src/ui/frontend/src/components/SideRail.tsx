import React from 'react';
import {
  LayoutDashboard,
  Cpu,
  Zap,
  GitBranch,
  FileText,
  Settings,
  Terminal,
  PanelLeft,
} from 'lucide-react';
import { NAV_GROUPS } from '../data/constants';

interface SideRailProps {
  activeTab: string;
  onSelectTab: (tabId: string) => void;
  channel?: string;
  isSimulating?: boolean;
  isEstopActive?: boolean;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  onNewSession?: () => void;
}

export const SideRail: React.FC<SideRailProps> = ({
  activeTab,
  onSelectTab,
  channel = 'vcan0',
  isSimulating = false,
  isEstopActive = false,
  collapsed = false,
  onToggleCollapse,
  onNewSession,
}) => {
  const renderIcon = (iconName: string, isActive: boolean) => {
    const iconClass = `h-4 w-4 shrink-0 transition-colors ${
      isActive ? 'text-accent' : 'text-text-mid group-hover:text-text-hi'
    }`;
    switch (iconName) {
      case 'LayoutDashboard':
        return <LayoutDashboard className={iconClass} />;
      case 'Cpu':
        return <Cpu className={iconClass} />;
      case 'Zap':
        return <Zap className={iconClass} />;
      case 'GitBranch':
        return <GitBranch className={iconClass} />;
      case 'FileText':
        return <FileText className={iconClass} />;
      case 'Settings':
        return <Settings className={iconClass} />;
      default:
        return <Terminal className={iconClass} />;
    }
  };

  return (
    <aside
      className={`rail-collapse flex h-full shrink-0 select-none flex-col justify-between glass-rail overflow-hidden ${
        collapsed ? 'rail-collapsed' : ''
      }`}
      style={{ width: collapsed ? 48 : 168 }}
    >
      {/* Upper Area: Sidebar Header with Toggle + Grouped Nav */}
      <div className="flex flex-col min-h-0 flex-1">
        {/* Top Header Row (h-11 matching Toolbar) — hosting PanelLeft toggle with fixed alignment, zero jumping */}
        <div className="flex h-11 shrink-0 items-center px-2">
          {onToggleCollapse && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onToggleCollapse();
              }}
              aria-label={collapsed ? 'Kenar çubuğunu aç' : 'Kenar çubuğunu daralt'}
              title={collapsed ? 'Kenar çubuğunu aç' : 'Kenar çubuğunu daralt'}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[6px] text-text-mid transition-colors hover:bg-bg-row-hover hover:text-text-hi"
              style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
            >
              <PanelLeft className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Grouped Navigation */}
        <div className="flex flex-col gap-3 overflow-y-auto px-2 py-1">
          {NAV_GROUPS.map((group) => (
            <div key={group.id} className="flex flex-col gap-0.5">
              {/* Smooth Micro-Label */}
              <div
                className={`micro-label overflow-hidden whitespace-nowrap transition-all duration-200 px-2 select-none ${
                  collapsed ? 'h-0 opacity-0 py-0' : 'h-4 opacity-100 py-0.5'
                }`}
              >
                {group.title}
              </div>

              {/* Group Items */}
              <div className="flex flex-col gap-0.5">
                {group.items.map((item) => {
                  const isActive = activeTab === item.id;
                  return (
                    <button
                      key={item.id}
                      onClick={() => onSelectTab(item.id)}
                      title={collapsed ? item.label : undefined}
                      aria-label={item.label}
                      className={`group relative flex h-8 w-full items-center rounded-[6px] font-sans text-[12px] font-medium transition-colors ${
                        isActive
                          ? 'bg-accent-soft text-accent-text font-semibold shadow-xs'
                          : 'text-text-mid hover:bg-bg-row-hover hover:text-text-hi'
                      }`}
                    >
                      {/* ACTIVE = 3px accent LEFT EDGE-BAR */}
                      {isActive && (
                        <div className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-accent" />
                      )}
                      {/* Fixed 32px icon container aligned with toggle button */}
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center">
                        {renderIcon(item.icon, isActive)}
                      </div>
                      {/* Label with smooth width and opacity transition */}
                      <span
                        className={`rail-label truncate overflow-hidden whitespace-nowrap transition-all duration-200 ${
                          collapsed
                            ? 'max-w-0 opacity-0 -translate-x-1 pointer-events-none'
                            : 'max-w-[105px] opacity-100 translate-x-0 ml-1'
                        }`}
                      >
                        {item.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Pinned footer status — perfectly aligned with toggle and nav icons */}
      <div className="flex h-10 shrink-0 items-center px-2">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center">
          <span
            className={`status-dot shrink-0 ${
              isEstopActive
                ? 'status-dot-danger animate-pulse'
                : isSimulating
                ? 'status-dot-ok animate-pulse'
                : 'status-dot-idle'
            }`}
          />
        </div>
        <span
          className={`rail-label font-mono text-[10.5px] text-text-low truncate overflow-hidden whitespace-nowrap transition-all duration-200 ${
            collapsed ? 'max-w-0 opacity-0 pointer-events-none' : 'max-w-[110px] opacity-100 ml-1'
          }`}
        >
          {channel} · {isEstopActive ? 'ESTOP' : isSimulating ? 'CANLI' : 'HAZIR'}
        </span>
      </div>
    </aside>
  );
};
