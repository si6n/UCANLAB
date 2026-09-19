/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // ZERON DESIGN TOKENS (Reactive Dark & Light theme via CSS variables)
        'bg-app': 'var(--bg-app)',
        'bg-rail': 'var(--bg-rail)',
        'bg-chrome': 'var(--bg-chrome)',
        'bg-panel': 'var(--bg-panel)',
        'bg-card': 'var(--bg-card)',
        'bg-popover': 'var(--bg-popover)',
        'bg-row-hover': 'var(--bg-row-hover)',
        'bg-row-selected': 'var(--bg-row-selected)',
        'bg-row-anomaly': 'var(--bg-row-anomaly)',
        'glass-rail': 'var(--glass-rail)',
        'glass-panel': 'var(--glass-panel)',
        'glass-chrome': 'var(--glass-chrome)',
        'glass-popover': 'var(--glass-popover)',
        'glass-row-sel': 'var(--glass-row-sel)',
        'glass-row-anom': 'var(--glass-row-anom)',
        'table-body': 'var(--table-body)',

        // Hairlines & Borders
        'border-whisper': 'var(--border)',
        'border-strong': 'var(--border-strong)',
        'border-row': 'var(--border-row)',
        'border-focus': 'var(--border-focus)',

        // Text ramp — Geist hierarchy
        'text-hi': 'var(--text-hi)',
        'text-body': 'var(--text-body)',
        'text-mid': 'var(--text-mid)',
        'text-low': 'var(--text-low)',
        'text-faint': 'var(--text-faint)',

        // Accent — Zeron Blue
        accent: {
          DEFAULT: 'var(--accent)',
          text: 'var(--accent-text)',
          soft: 'var(--accent-soft)',
          glow: 'var(--accent-glow)',
          line: 'var(--accent-line)',
        },

        // Diff & State Colors
        del: 'var(--del)',
        deledge: 'var(--del-edge)',
        delbg: 'var(--del-bg)',
        add: 'var(--add)',
        addedge: 'var(--add-edge)',
        addbg: 'var(--add-bg)',
        brandamber: 'var(--brand-amber)',

        // Semantic Aliases
        danger: {
          DEFAULT: 'var(--del)',
          soft: 'var(--del-bg)',
          border: 'var(--del-edge)',
        },
        warn: {
          DEFAULT: 'var(--warn)',
          soft: 'var(--warn-bg)',
          border: 'var(--warn-edge)',
        },
        ok: {
          DEFAULT: 'var(--add)',
          soft: 'var(--add-bg)',
          border: 'var(--add-edge)',
        },
        'info-teal': {
          DEFAULT: 'var(--info-teal)',
          soft: 'var(--info-teal-bg)',
          border: 'var(--info-teal-edge)',
        },

        // Slate / Zinc fallbacks harmonized
        slate: {
          50: '#f8fafc',
          100: '#f1f5f9',
          200: '#e2e8f0',
          300: '#cbd5e1',
          400: '#94a3b8',
          500: '#64748b',
          600: '#475569',
          700: '#334155',
          800: '#1e293b',
          900: '#0f172a',
        },
        zinc: {
          50: '#fafafa',
          100: '#f4f4f5',
          200: '#e4e4e7',
          300: '#d4d4d8',
          400: '#a1a1aa',
          500: '#71717a',
          800: '#27272a',
          900: '#18181b',
        },
      },
      fontFamily: {
        sans: ['Geist', 'Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['Geist Mono', 'JetBrains Mono', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      borderRadius: {
        panel: '12px',
        box: '10px',
        pill: '8px',
        btn: '6px',
        chip: '5px',
        tag: '4px',
        code: '4px',
        circle: '9999px',
      },
      boxShadow: {
        'chrome-inset': 'inset 0 1px 0 rgba(255, 255, 255, 0.05)',
        'card-subtle': '0 1px 2px rgba(0, 0, 0, 0.04), 0 4px 12px rgba(0, 0, 0, 0.03)',
      },
      animation: {
        'row-enter': 'rowEnter 120ms ease-out forwards',
        'pulse-subtle': 'subtlePulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
      keyframes: {
        rowEnter: {
          '0%': { opacity: '0', transform: 'translateY(-3px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        subtlePulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
      },
    },
  },
  plugins: [],
}
