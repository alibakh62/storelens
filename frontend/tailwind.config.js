/** @type {import('tailwindcss').Config} */
const v = (n) => `rgb(var(--${n}) / <alpha-value>)`;
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: v("bg"), surface: v("surface"), surface2: v("surface-2"), border: v("border"),
        text: v("text"), muted: v("muted"), accent: v("accent"), warn: v("warn"), crit: v("crit"),
        info: v("info"), money: v("money"),
      },
      fontFamily: { sans: ["'IBM Plex Sans'", "system-ui", "sans-serif"], mono: ["'IBM Plex Mono'", "monospace"] },
      fontSize: { xs: ["12px", "16px"], sm: ["14px", "20px"], base: ["16px", "24px"], lg: ["20px", "28px"],
                  xl: ["28px", "34px"], "2xl": ["40px", "44px"] },
      borderRadius: { card: "12px" },
    },
  },
  plugins: [],
};
