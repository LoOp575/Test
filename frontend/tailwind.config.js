/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}", "./public/index.html"],
  theme: {
    extend: {
      colors: {
        paper: "#FAF8F5",
        card: "#FFFFFF",
        line: "#E8E5DE",
        lineStrong: "#D9D5CB",
        ink: "#1F2721",
        muted: "#68716A",
        faint: "#9AA096",
        pine: {
          50: "#F1F5F2",
          100: "#E3EBE6",
          200: "#C3D4C9",
          300: "#97B4A4",
          400: "#5E8572",
          500: "#2E5A47",
          600: "#1F4A38",
          700: "#144338",
          800: "#10332B",
          900: "#0C2721",
        },
        gold: {
          100: "#F3ECDE",
          200: "#E4D5B8",
          300: "#D2B985",
          400: "#C6A15C",
          500: "#B18A44",
          600: "#8F6E33",
          700: "#6E5223",
        },
        up: "#1F6F4A",
        down: "#B3402F",
        warn: "#A97B1F",
      },
      fontFamily: {
        display: ["Fraunces", "Georgia", "serif"],
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      letterSpacing: {
        micro: "0.14em",
        wide2: "0.22em",
      },
      boxShadow: {
        card: "0 1px 2px rgba(20,30,25,0.05), 0 10px 30px rgba(20,30,25,0.06)",
        lift: "0 2px 4px rgba(20,30,25,0.07), 0 16px 44px rgba(20,30,25,0.12)",
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: 0, transform: "translateY(8px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
        softBlink: {
          "0%, 100%": { opacity: 0.45 },
          "50%": { opacity: 1 },
        },
      },
      animation: {
        "fade-up": "fadeUp 0.55s cubic-bezier(0.22,1,0.36,1) forwards",
        "soft-blink": "softBlink 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
