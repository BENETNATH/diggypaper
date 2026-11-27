"use client";

/**
 * Root-layout failure: replaces the whole document, so no i18n provider nor
 * global styles are available — plain text, inline style.
 */
export default function GlobalError({ retry }: { error: unknown; retry: () => void }) {
  return (
    <html>
      <body style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", textAlign: "center" }}>
        <h1>Diggypaper</h1>
        <p>Error / Erreur</p>
        <button type="button" onClick={() => retry()}>
          ↻ Retry / Réessayer
        </button>
      </body>
    </html>
  );
}
