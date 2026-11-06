import { render, screen } from "@testing-library/react";
import { defaultValues, DecisionFields } from "@/components/DecisionFields";
import { SchemaField } from "@/lib/api";

// i18n keys returned as-is; the test does not depend on the message files.
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key,
}));

// A neutral question schema (not the retired hardcoded biology MODULE 5 one).
const FIELDS: SchemaField[] = [
  { key: "relevant", label: "Relevant", type: "boolean" },
  { key: "category", label: "Category", type: "choice", options: ["A", "B", "C"] },
  { key: "note", label: "Note", type: "text" },
];

// No model answer yet (initial undefined): the form must not pre-select an answer.
describe("defaultValues — no pre-filled answers without a model prediction", () => {
  it("leaves a choice field empty (was: 1st option pre-selected)", () => {
    const v = defaultValues(FIELDS);
    expect(v.category).toBe("");
  });

  it("leaves a boolean field unchecked and a text field empty", () => {
    const v = defaultValues(FIELDS);
    expect(v.relevant).toBe(false);
    expect(v.note).toBe("");
  });

  it("prefers the given (model / review) values over the defaults", () => {
    const v = defaultValues(FIELDS, { category: "B", relevant: true, note: "ok" });
    expect(v.category).toBe("B");
    expect(v.relevant).toBe(true);
    expect(v.note).toBe("ok");
  });

  it("does not invent fields that are not in the schema", () => {
    const v = defaultValues([]);
    expect(v).toEqual({});
  });
});

describe("DecisionFields — empty choice shows a neutral placeholder", () => {
  it("renders an unselected 'not answered' option for an empty choice", () => {
    render(<DecisionFields fields={FIELDS} values={defaultValues(FIELDS)} onChange={() => {}} />);
    // the empty default must show the placeholder, not a pre-selected answer
    const selects = screen.getAllByRole("combobox");
    const choice = selects.find(
      (s) => (s as HTMLSelectElement).value === ""
    ) as HTMLSelectElement | undefined;
    expect(choice).toBeTruthy();
    expect(choice!.querySelector("option[value='']")).toBeTruthy();
  });
});
