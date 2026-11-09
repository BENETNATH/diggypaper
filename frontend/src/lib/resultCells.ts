/** Colored result cells + AI/human per-field coupling (spec 2026-11-09-triage-seuils R3/R4).
 * Pure functions, unit-tested — the rendering reads their output. */

/** Human review state of a field: same answer as the model → validated,
 * different → corrected; no human answer for the field → none. */
export function deriveHumanFieldState(
  modelJson: Record<string, unknown> | null | undefined,
  humanJson: Record<string, unknown> | null | undefined,
  field: string,
): "validated" | "corrected" | "none" {
  const human = humanJson?.[field];
  if (human == null) return "none";
  return modelJson?.[field] === human ? "validated" : "corrected";
}

/** Initials of the last annotator: "Benoît Petit" → "BP", "marie@x.fr" → "M". */
export function initials(name: string | null | undefined): string {
  if (!name) return "";
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

/** Cell background by R3: grey on an error row; neutral when the badge option is
 * off for the question or there is no field confidence (NULL: old predictions, R2);
 * green when the confidence reaches the project's accept threshold; amber below
 * it (with an answer present); neutral when there is nothing to show. */
export function cellTone(args: {
  rowError: boolean;
  badgeOn: boolean;
  fieldConfidence: number | null | undefined;
  answerPresent: boolean;
  accept: number | null | undefined;
}): "error" | "good" | "warn" | "neutral" {
  if (args.rowError) return "error";
  if (!args.badgeOn) return "neutral";
  const conf = args.fieldConfidence;
  if (conf == null) return "neutral";
  if (args.accept != null && conf >= args.accept) return "good";
  return args.answerPresent ? "warn" : "neutral";
}

/** Corner icon: human reviewed the field → "validated" (✓) / "corrected" (!);
 * answered but not re-read → "unread" (·); empty cell or error row → none. */
export function cellIcon(args: {
  rowError: boolean;
  modelValue: unknown;
  humanState: "validated" | "corrected" | "none";
}): "validated" | "corrected" | "unread" | "none" {
  if (args.rowError) return "none";
  if (args.humanState !== "none") return args.humanState;
  return args.modelValue == null ? "none" : "unread";
}
