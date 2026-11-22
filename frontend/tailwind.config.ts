import type { Config } from "tailwindcss";

/**
 * Design tokens (design-system skill) : les classes Tailwind consomment des
 * variables sémantiques définies dans globals.css (OKLCH, light + dark).
 * On ne code JAMAIS une valeur hex brute dans un composant — toujours un token.
 */
const config: Config = {
  content: [
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        /* Les 9 tokens avec usages /NN pointent vers le placeholder alpha
           (spec 2026-11-21-tokens-alpha) : var() opaque empêchait la
           génération des règles d'opacité par Tailwind v3. */
        foreground: "oklch(var(--foreground-raw) / <alpha-value>)",
        surface: "var(--surface)",
        "surface-muted": "oklch(var(--surface-muted-raw) / <alpha-value>)",
        border: "oklch(var(--border-raw) / <alpha-value>)",
        muted: {
          DEFAULT: "oklch(var(--muted-raw) / <alpha-value>)",
          foreground: "var(--muted-foreground)",
        },
        primary: {
          DEFAULT: "oklch(var(--primary-raw) / <alpha-value>)",
          hover: "var(--primary-hover)",
          foreground: "var(--primary-foreground)",
        },
        danger: {
          DEFAULT: "oklch(var(--danger-raw) / <alpha-value>)",
          foreground: "var(--danger-foreground)",
        },
        success: "oklch(var(--success-raw) / <alpha-value>)",
        warning: "oklch(var(--warning-raw) / <alpha-value>)",
        focus: "oklch(var(--focus-raw) / <alpha-value>)",
        overlay: "var(--overlay)",
        "cell-good": "var(--cell-good)",
        "cell-warn": "var(--cell-warn)",
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "monospace"],
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
      },
      transitionDuration: {
        fast: "var(--duration-fast)",
        base: "var(--duration-base)",
      },
    },
  },
  plugins: [],
};
export default config;
