/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Outfit", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      colors: {
        paper: {
          50: "#f6f3eb",
          100: "#ebe4d4",
          200: "#ddd2b8",
        },
        ink: {
          900: "#1c1915",
          700: "#3f382f",
          500: "#6f655a",
        },
        pine: {
          800: "#1c4f38",
          700: "#1f6b4a",
          500: "#2f8f62",
          300: "#8fd0ae",
        },
      },
    },
  },
  plugins: [],
};
