/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef6ff",
          100: "#d9ebff",
          200: "#b7d9ff",
          300: "#85c0ff",
          400: "#4b9fff",
          500: "#1f7dff",
          600: "#0f60e6",
          700: "#0d4bb8",
          800: "#103f8f",
          900: "#123671",
          950: "#0b1f42",
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
