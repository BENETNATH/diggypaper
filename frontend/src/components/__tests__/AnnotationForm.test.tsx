import { render, screen } from "@testing-library/react";
import { AnnotationForm } from "@/components/AnnotationForm";
import { SchemaField } from "@/lib/api";

// i18n keys returned as-is (same convention as the other component tests).
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key,
}));

const FIELDS: SchemaField[] = [{ key: "relevant", label: "Relevant", type: "boolean" }];

// P-4 : une quote absente du texte est marquée « non trouvée » et ne scrolle pas.
describe("AnnotationForm — quote not found in the text (spec 2026-11-23 P-4)", () => {
  it("absent quote → indicator shown, the click does not scroll", () => {
    const onQuoteClick = jest.fn();
    render(
      <AnnotationForm
        fields={FIELDS}
        onSubmit={jest.fn()}
        text="hello world"
        initial={{ evidence_quotes: ["absent quote"] }}
        onQuoteClick={onQuoteClick}
      />
    );
    expect(screen.getByText("quoteNotFound")).toBeInTheDocument();
    screen.getByRole("button", { name: "absent quote" }).click();
    expect(onQuoteClick).not.toHaveBeenCalled();
  });

  it("found quote → no indicator, the click scrolls", () => {
    const onQuoteClick = jest.fn();
    render(
      <AnnotationForm
        fields={FIELDS}
        onSubmit={jest.fn()}
        text="hello world"
        initial={{ evidence_quotes: ["hello"] }}
        onQuoteClick={onQuoteClick}
      />
    );
    expect(screen.queryByText("quoteNotFound")).toBeNull();
    screen.getByRole("button", { name: "hello" }).click();
    expect(onQuoteClick).toHaveBeenCalledWith("hello");
  });

  it("no text prop → no indicator (nothing to check against)", () => {
    render(
      <AnnotationForm
        fields={FIELDS}
        onSubmit={jest.fn()}
        initial={{ evidence_quotes: ["any quote"] }}
        onQuoteClick={jest.fn()}
      />
    );
    expect(screen.queryByText("quoteNotFound")).toBeNull();
  });
});

// P-5 : les quotes du modèle (dict par champ) → puces ≤ 3 sous chaque question.
// `quotes={[]}` isole les puces du bloc global (liste vide, contrôlée par la page).
describe("AnnotationForm — per-field evidence bullets (spec 2026-11-23 P-5)", () => {
  it("dict evidence → clickable bullets under its field, at most 3", () => {
    const onQuoteClick = jest.fn();
    render(
      <AnnotationForm
        fields={FIELDS}
        onSubmit={jest.fn()}
        initial={{
          evidence_quotes: { relevant: ["quote 1", "quote 2", "quote 3", "quote 4"] },
        }}
        quotes={[]}
        onQuoteClick={onQuoteClick}
      />
    );
    const bullets = screen.getAllByRole("button", { name: /^quote \d$/ });
    expect(bullets).toHaveLength(3); // le 4e n'est pas affiché
    bullets[0].click();
    expect(onQuoteClick).toHaveBeenCalledWith("quote 1");
  });

  it("dict field with an empty list → no bullet", () => {
    render(
      <AnnotationForm
        fields={FIELDS}
        onSubmit={jest.fn()}
        initial={{ evidence_quotes: { relevant: [] } }}
        quotes={[]}
        onQuoteClick={jest.fn()}
      />
    );
    expect(screen.queryByRole("button", { name: /^quote \d$/ })).toBeNull();
  });

  it("flat legacy list → no per-field bullet, the global block keeps the quote", () => {
    render(
      <AnnotationForm
        fields={FIELDS}
        onSubmit={jest.fn()}
        initial={{ evidence_quotes: ["flat quote"] }}
        onQuoteClick={jest.fn()}
      />
    );
    expect(screen.queryByRole("button", { name: /^quote \d$/ })).toBeNull();
    // le bloc global « Evidence quotes » est intact : la quote y est affichée/éditée
    expect(screen.getByRole("button", { name: "flat quote" })).toBeInTheDocument();
  });
});
