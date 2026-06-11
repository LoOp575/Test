/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}", "./public/index.html"],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#09090B",
          800: "#121214",
          700: "#18181B",
          600: "#27272A",
          500: "#3F3F46",
          400: "#52525B",
          300: "#71717A",
          200: "#A1A1AA",
          100: "#D4D4D8",
          50: "#FAFAFA",
        },
        signal: {
          up: "#10b981",
          upSoft: "rgba(16,185,129,0.10)",
          down: "#f43f5e",
          downSoft: "rgba(244,63,94,0.10)",
          warn: "#f59e0b",
          warnSoft: "rgba(245,158,11,0.10)",
          info: "#3b82f6",
        },
      },
      fontFamily: {
        display: ["Outfit", "system-ui", "sans-serif"],
        sans: ["IBM Plex Sans", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      letterSpacing: {
        wider2: "0.18em",
        wider3: "0.24em",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: 0, transform: "translateY(6px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
        pulseDot: {
          "0%, 100%": { opacity: 0.4 },
          "50%": { opacity: 1 },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "fade-in": "fadeIn 0.5s ease forwards",
        "pulse-dot": "pulseDot 1.4s ease-in-out infinite",
        shimmer: "shimmer 2s linear infinite",
      },
    },
  },
  plugins: [],
};
