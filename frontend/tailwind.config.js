/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // App surfaces
        app: {
          bg: "#FFFFFF",
          card: "#FFFFFF",
          inverse: "#0F172A",
          soft: "#FAFAFA",
        },
        // Text
        ink: {
          DEFAULT: "#0F1115",
          muted: "#61666B",
          subtle: "#81858C",
          inverse: "#F8FAFC",
          onDark: "#0F1115",
          onDarkMuted: "#81858C",
        },
        // Accent (blue)
        accent: {
          DEFAULT: "#4176E6",
          hover: "#3569D7",
          active: "#3569D7",
          soft: "#E4EDFD",
          softer: "#EDF3FE",
          ring: "#D3E2FF",
        },
        // Borders
        line: {
          DEFAULT: "rgba(0, 0, 0, 0.04)",
          strong: "rgba(0, 0, 0, 0.10)",
          dark: "#334155",
        },
        // Status
        success: { DEFAULT: "#16A34A", soft: "#F0FDF4", deep: "#166534" },
        warning: { DEFAULT: "#D97706", soft: "#FFFBEB", deep: "#92400E", ring: "#FDE68A" },
        danger: { DEFAULT: "#DC2626", soft: "#FEF2F2", deep: "#B84A42", ring: "#FECACA" },
        // Custom warm chip color from design
        chip: {
          DEFAULT: "#F1D6C8",
        },
        // Sidebar dark
        sidebar: {
          bg: "#FAFAFA",
          surface: "#FFFFFF",
          border: "rgba(0, 0, 0, 0.04)",
          hover: "#EBEEF2",
          active: "#EBEEF2",
        },
      },
      fontFamily: {
        sans: ["Noto Sans SC", "Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        display: ["Noto Sans SC", "Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Geist Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        DEFAULT: "8px",
        md: "10px",
        lg: "12px",
        xl: "14px",
      },
      boxShadow: {
        card: "0 1px 2px 0 rgba(15, 17, 21, 0.04)",
        soft: "0 2px 12px 0 rgba(26, 26, 26, 0.05)",
      },
    },
  },
  plugins: [],
};
