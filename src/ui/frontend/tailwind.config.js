const neutralBorders = {
  50: '#1c1c1e', 100: '#242426', 200: '#303033', 300: '#45454a',
  400: '#616167', 500: '#85858a', 600: '#45454a', 700: '#343438',
  800: '#303033', 900: '#242426', 950: '#1c1c1e',
};

const statusSurfaces = (soft, strong) => ({ 50: soft, 100: strong, 200: strong });
const statusText = (color) => ({ 500: color, 600: color, 700: color, 800: color, 900: color, 950: color });
const statusBorders = (color) => ({ 100: color, 200: color, 300: color });
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f4f2ff",
          100: "#e8e3ff",
          200: "#d2caff",
          300: "#bcb0ff",
          400: "#a596ff",
          500: "#8b7cf6",
          600: "#6552c9",
          700: "#5543ad",
          800: "#44358c",
          900: "#30265e",
          950: "#1c1830",
        },
        signal: {
          50: "#ecfdf6",
          100: "#d1fae9",
          400: "#2dd4a7",
          500: "#14b892",
          600: "#0d9276",
        },
        slate: {
          50: "#F8FAFC",
          100: "#F1F5F9",
          200: "#E5E9F0",
          300: "#CBD5E1",
          400: "#94A3B8",
          500: "#64748B",
          600: "#475569",
          700: "#334155",
          800: "#1E293B",
          900: "#0F172A",
          950: "#020617",
        },
      },
      backgroundColor: {
        white: '#0e0e0e',
        slate: {
          50: '#161618', 100: '#202022', 200: '#303033', 300: '#45454a',
          400: '#616167', 500: '#85858a', 600: '#45454a', 700: '#343438',
          800: '#262628', 850: '#202022', 900: '#0d0d0d', 950: '#060606',
        },
        brand: { 50: '#1b1923', 100: '#272335', 200: '#39314f' },
        signal: statusSurfaces('#11201b', '#193329'),
        rose: statusSurfaces('#261719', '#3b2024'),
        amber: statusSurfaces('#252115', '#38301b'),
        indigo: statusSurfaces('#1b1923', '#272335'),
      },
      textColor: {
        slate: {
          50: '#f4f4f5', 100: '#e8e8ea', 200: '#d6d6d9', 300: '#bdbdc3',
          400: '#929298', 500: '#a9a9ae', 600: '#bdbdc3', 700: '#d6d6d9',
          800: '#e8e8ea', 900: '#f4f4f5', 950: '#fafafa',
        },
        brand: { 600: '#a596ff', 700: '#bcb0ff', 800: '#d2caff', 900: '#e8e3ff', 950: '#f4f2ff' },
        signal: statusText('#34d399'),
        rose: statusText('#f87171'),
        amber: statusText('#facc15'),
        indigo: statusText('#bcb0ff'),
      },
      borderColor: {
        DEFAULT: '#303033',
        slate: neutralBorders,
        brand: { 100: '#302b41', 200: '#39314f', 300: '#51456f' },
        signal: statusBorders('#285542'),
        rose: statusBorders('#67343b'),
        amber: statusBorders('#61522c'),
        indigo: statusBorders('#39314f'),
      },
      divideColor: { slate: neutralBorders },
      ringOffsetColor: { white: '#0e0e0e' },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      boxShadow: {
        xs: "0 1px 2px 0 rgba(15, 23, 42, 0.04)",
        sm: "0 1px 2px 0 rgba(15, 23, 42, 0.04), 0 2px 4px -1px rgba(15, 23, 42, 0.03)",
        card: "0 1px 2px 0 rgba(15, 23, 42, 0.03), 0 4px 16px -4px rgba(15, 23, 42, 0.05)",
        "card-hover": "0 2px 4px 0 rgba(15, 23, 42, 0.04), 0 12px 32px -6px rgba(15, 23, 42, 0.10)",
        "card-elevated": "0 4px 8px -2px rgba(15, 23, 42, 0.05), 0 20px 48px -12px rgba(15, 23, 42, 0.14)",
        glow: "0 0 24px -4px rgba(71, 87, 234, 0.30)",
        "glow-emerald": "0 0 18px -4px rgba(16, 185, 129, 0.30)",
      },
      maxWidth: {
        "8xl": "88rem",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          "0%": { opacity: "0", transform: "scale(0.96)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.5s ease-out both",
        "scale-in": "scale-in 0.18s ease-out both",
      },
    },
  },
  plugins: [],
}
