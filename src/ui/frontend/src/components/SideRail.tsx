import React from 'react';
import {
  LayoutDashboard,
  Cpu,
  Zap,
  GitBranch,
  FileText,
  Settings,
  Terminal,
} from 'lucide-react';
import { NAV_GROUPS } from '../data/constants';

interface SideRailProps {
  activeTab: string;
  onSelectTab: (tabId: string) => void;
  channel?: string;
  isSimulating?: boolean;
  isEstopActive?: boolean;
  collapsed?: boolean;
  width?: number;
  isDragging?: boolean;
  onToggleCollapse?: () => void;
  onNewSession?: () => void;
}

export const SideRail: React.FC<SideRailProps> = ({
  activeTab,
  onSelectTab,
  channel,
  isSimulating,
  isEstopActive,
  collapsed = false,
  width = 200,
  isDragging = false,
  onToggleCollapse,
}) => {
  const renderIcon = (iconName: string, isActive: boolean) => {
    const iconClass = `h-4 w-4 shrink-0 transition-colors ${
      isActive ? 'text-text-hi' : 'text-text-mid group-hover:text-text-hi'
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
    <nav className="flex h-full w-full shrink-0 select-none flex-col justify-between bg-transparent overflow-hidden">
      {/* Upper Area: Grouped Navigation */}
      <div className="flex flex-col min-h-0 flex-1 w-full overflow-y-auto px-2 py-2">
        <div className="flex flex-col gap-3">
          {NAV_GROUPS.map((group) => (
            <div key={group.id} className="flex flex-col gap-0.5">
              {/* Group Micro-Label */}
              <div className="overflow-hidden whitespace-nowrap px-2.5 pt-1.5 pb-1 text-[10px] font-semibold tracking-wider text-text-low/70 uppercase select-none">
                {group.title}
              </div>

              {/* Group Navigation Items */}
              <div className="flex flex-col gap-0.5">
                {group.items.map((item) => {
                  const isActive = activeTab === item.id;
                  return (
                    <button
                      key={item.id}
                      onClick={() => onSelectTab(item.id)}
                      aria-label={item.label}
                      className={`group relative flex h-8 w-full items-center gap-2.5 rounded-[6px] px-2.5 font-sans text-[12px] transition-all duration-140 cursor-pointer active:scale-[0.98] ${
                        isActive
                          ? 'bg-bg-row-selected text-text-hi font-medium shadow-2xs'
                          : 'text-text-mid hover:bg-bg-row-hover hover:text-text-hi font-normal'
                      }`}
                    >
                      {/* Active indicator bar */}
                      {isActive && (
                        <span className="absolute left-1 top-2 bottom-2 w-[2.5px] rounded-full bg-text-hi" />
                      )}
                      {/* Icon */}
                      <div className="flex h-4 w-4 shrink-0 items-center justify-center">
                        {renderIcon(item.icon, isActive)}
                      </div>
                      {/* Label */}
                      <span className="truncate overflow-hidden whitespace-nowrap">
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

      {/* Bottom Status Card */}
      <div className="p-2 border-t border-border mt-auto">
        <div className="flex items-center justify-between rounded-[8px] bg-surface-inset border border-border px-2.5 py-1.5 text-[11px]">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className={`h-2 w-2 rounded-full shrink-0 transition-all ${
                isEstopActive
                  ? 'bg-del animate-ping'
                  : isSimulating
                  ? 'bg-add shadow-[0_0_6px_rgba(52,211,153,0.6)]'
                  : 'bg-text-faint'
              }`}
            />
            <span className="truncate font-mono text-[11px] font-medium text-text-hi">
              {channel || 'vcan0'}
            </span>
          </div>
          <span className="font-mono text-[10px] text-text-low shrink-0">
            {isEstopActive ? 'KİLİTLİ' : isSimulating ? 'CAN Canlı' : 'Beklemede'}
          </span>
        </div>
      </div>
    </nav>
  );
};
