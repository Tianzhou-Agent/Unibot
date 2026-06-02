/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // App surfaces
        app: {
          bg: "#F6F8FB",
          card: "#FFFFFF",
          inverse: "#0F172A",
          soft: "#F8FAFC",
        },
        // Text
        ink: {
          DEFAULT: "#0F172A",
          muted: "#64748B",
          subtle: "#94A3B8",
          inverse: "#F8FAFC",
          onDark: "#E2E8F0",
          onDarkMuted: "#93C5FD",
        },
        // Accent (blue)
        accent: {
          DEFAULT: "#2563EB",
          hover: "#1D4ED8",
          active: "#1D4ED8",
          soft: "#EFF6FF",
          softer: "#DBEAFE",
          ring: "#BFDBFE",
        },
        // Borders
        line: {
          DEFAULT: "#E2E8F0",
          strong: "#CBD5E1",
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
          bg: "#0F172A",
          surface: "#1E293B",
          border: "#334155",
          hover: "#1E293B",
          active: "#1D4ED8",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        display: ["Geist", "Inter", "system-ui", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        DEFAULT: "8px",
        md: "10px",
        lg: "12px",
        xl: "14px",
      },
      boxShadow: {
        card: "0 1px 2px 0 rgba(15, 23, 42, 0.04)",
        soft: "0 1px 3px 0 rgba(15, 23, 42, 0.06)",
      },
    },
  },
  plugins: [],
};
