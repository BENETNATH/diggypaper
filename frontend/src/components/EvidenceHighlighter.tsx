"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { cleanQuote, highlightSegments } from "@/lib/highlight";

interface EvidenceHighlighterProps {
  /** Texte de l'article. */
  text: string;
  /** Extraits verbatim à surligner (ceux du formulaire : réponse du modèle ou ajoutés à la main). */
  quotes: string[] | null | undefined;
  /** Classe Tailwind du `<mark>` (bicolore). */
  markClassName?: string;
  /** Si fourni : un « + » flottant apparaît sur le texte sélectionné et ajoute l'extrait. */
  onAdd?: (quote: string) => void;
  /** Extrait à révéler (cliquée dans le formulaire) : le texte scrolle sur son surlignage.
   *  `n` est un nonce pour relancer le scroll sur le même extrait. */
  focus?: { quote: string; n: number } | null;
}

/**
 * Surligne les extraits exacts dans le texte avec une balise `<mark>`. Sélectionner du texte fait
 * apparaître un petit « + » : un clic l'ajoute aux extraits de preuve de l'annotation.
 */
export function EvidenceHighlighter({
  text,
  quotes,
  markClassName = "bg-yellow-200 text-amber-900 rounded-sm px-0.5",
  onAdd,
  focus,
}: EvidenceHighlighterProps) {
  const t = useTranslations("annotate");
  const box = useRef<HTMLDivElement>(null);
  const [pick, setPick] = useState<{ quote: string; x: number; y: number } | null>(null);

  // Clic sur un extrait du formulaire : le document scrolle sur son surlignage,
  // qui clignote quelques instants (la citation est verbatim, on la cherche dans
  // le texte des <mark> ; premier match si elle apparaît plusieurs fois).
  // Sur mobile le panneau document apparaît dans le même rendu (onglet) : on
  // retente au frame suivant tant que le <mark> n'a pas de boîte (height = 0).
  // Le scroll est calculé explicitement sur l'ancêtre scrollable (la section) :
  // le div de contenu ne scrolle pas, et scrollIntoView({block:"center"}) se
  // mettait à calculer par rapport à la fenêtre en pleine réorganisation du
  // layout, laissant le surlignage bas du panneau au lieu de le centrer.
  // Tant que le flash est visible, on recentre si le contenu se réflowe
  // (ex. image du texte qui termine de charger et pousse le surlignage).
  useEffect(() => {
    if (!focus) return;
    let raf = 0;
    let flashTimer = 0;
    let done = false;

    const findScroller = (node: HTMLElement): HTMLElement => {
      let cur: HTMLElement | null = node.parentElement;
      while (cur && cur !== document.body) {
        const oy = getComputedStyle(cur).overflowY;
        if ((oy === "auto" || oy === "scroll") && cur.scrollHeight > cur.clientHeight) {
          return cur;
        }
        cur = cur.parentElement;
      }
      return document.scrollingElement as HTMLElement;
    };

    const center = (el: HTMLElement, target: Element) => {
      const scroller = findScroller(el);
      const boxRect = scroller.getBoundingClientRect();
      const markRect = target.getBoundingClientRect();
      // Centre le mark dans le panneau, indépendamment de la position de la page.
      const offset = markRect.top - boxRect.top + scroller.scrollTop;
      const targetTop = offset - scroller.clientHeight / 2 + markRect.height / 2;
      scroller.scrollTo({ top: targetTop, behavior: "smooth" });
      // Panneau lui-même hors de la fenêtre (mobile) : on le ramène dans le viewport.
      if (boxRect.top < 0 || boxRect.bottom > window.innerHeight) {
        window.scrollBy({ top: boxRect.top, behavior: "smooth" });
      }
      return offset;
    };

    const tryScroll = () => {
      if (done) return;
      const el = box.current;
      if (!el) return;
      const marks = Array.from(el.querySelectorAll("mark"));
      const target =
        marks.find((m) => m.textContent === focus.quote) ??
        marks.find((m) => (m.textContent ?? "").includes(focus.quote));
      if (!target) return;
      if (target.getBoundingClientRect().height === 0) {
        raf = requestAnimationFrame(tryScroll);
        return;
      }
      done = true;
      let lastOffset = center(el, target);
      target.classList.add("evidence-flash");
      // Ré-centrage si le mark a dérivé (reflow : image qui charge).
      const settle = () => {
        const cur = box.current;
        if (!cur || !target.isConnected || !target.classList.contains("evidence-flash")) return;
        const scroller = findScroller(cur);
        const markRect = target.getBoundingClientRect();
        if (markRect.height === 0) return;
        const offset =
          markRect.top - scroller.getBoundingClientRect().top + scroller.scrollTop;
        if (Math.abs(offset - lastOffset) > 8) {
          lastOffset = offset;
          center(cur, target);
        }
        raf = requestAnimationFrame(settle);
      };
      raf = requestAnimationFrame(settle);
      flashTimer = window.setTimeout(
        () => target.classList.remove("evidence-flash"),
        2200,
      );
    };

    raf = requestAnimationFrame(tryScroll);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(flashTimer);
    };
  }, [focus]);

  useEffect(() => {
    if (!onAdd) return;
    const update = () => {
      const sel = window.getSelection();
      const el = box.current;
      if (!sel || sel.isCollapsed || sel.rangeCount === 0 || !el) return setPick(null);
      const range = sel.getRangeAt(0);
      if (!el.contains(range.commonAncestorContainer)) return setPick(null);
      const quote = cleanQuote(range.toString());
      if (!quote) return setPick(null);
      const r = range.getBoundingClientRect();
      setPick({ quote, x: Math.min(r.right, window.innerWidth - 40), y: Math.max(r.top - 36, 8) });
    };
    const hide = () => setPick(null);
    document.addEventListener("selectionchange", update);
    window.addEventListener("scroll", hide, true);
    return () => {
      document.removeEventListener("selectionchange", update);
      window.removeEventListener("scroll", hide, true);
    };
  }, [onAdd]);

  const segments = highlightSegments(text, quotes);
  return (
    <div ref={box} className="whitespace-pre-wrap leading-relaxed">
      {segments.map((seg, i) =>
        seg.highlighted ? (
          <mark key={i} className={markClassName}>
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
      {onAdd && pick && (
        <button
          type="button"
          // mousedown ne doit pas effacer la sélection avant le clic
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            onAdd(pick.quote);
            window.getSelection()?.removeAllRanges();
            setPick(null);
          }}
          style={{ position: "fixed", left: pick.x, top: pick.y }}
          className="z-50 flex h-8 w-8 items-center justify-center rounded-full bg-primary text-lg font-bold leading-none text-white shadow-lg hover:opacity-90"
          title={t("addSelection")}
          aria-label={t("addSelection")}
        >
          +
        </button>
      )}
    </div>
  );
}
