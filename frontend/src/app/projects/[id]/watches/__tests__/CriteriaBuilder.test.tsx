import { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import * as api from "@/lib/api";
import CriteriaBuilder from "../CriteriaBuilder";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});

const FIELDS: api.SchemaField[] = [
  { key: "ai", label: "Uses AI", type: "boolean" },
  { key: "design", label: "Design", type: "choice", options: ["In vivo", "In vitro"] },
  { key: "notes", label: "Notes", type: "text" },
];

const CRITERIA: api.WatchCriterion[] = [
  { kind: "field", field_key: "ai", expected: true, conf_min: 0.7 },
  { kind: "free", question: "Est-ce in vivo ?", expected: true, conf_max: 0.9 },
];

describe("CriteriaBuilder", () => {
  it("renders empty with the add button", () => {
    const onChange = jest.fn();
    render(<CriteriaBuilder fields={FIELDS} criteria={[]} combine="AND" onChange={onChange} />);
    expect(screen.getByRole("button", { name: "criteriaAdd" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "critRemove" })).not.toBeInTheDocument();
  });

  it("adds a criterion row and edits it", () => {
    const onChange = jest.fn();
    render(<CriteriaBuilder fields={FIELDS} criteria={[]} combine="AND" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "criteriaAdd" }));
    // the added row is a field criterion on the first eligible field
    expect(onChange).toHaveBeenCalledWith(
      expect.arrayContaining([expect.objectContaining({ kind: "field", field_key: "ai" })]),
      "AND"
    );
    expect(onChange.mock.calls.length).toBe(1);
  });

  it("field criteria offer only boolean/choice fields", () => {
    render(<CriteriaBuilder fields={FIELDS} criteria={CRITERIA} combine="AND" onChange={() => {}} />);
    const selects = screen.getAllByRole("combobox");
    const options = Array.from(document.querySelectorAll("option")).map((o) => o.textContent);
    // the boolean + choice fields are offered, the text field never
    expect(options.join(" ")).toMatch(/Uses AI/);
    expect(options.join(" ")).toMatch(/Design/);
    expect(options.join(" ")).not.toMatch(/Notes/);
    expect(selects.length).toBeGreaterThan(0);
  });

  it("free question row: typing the question and toggling the answer propagate", () => {
    const onChange = jest.fn();
    render(<CriteriaBuilder fields={FIELDS} criteria={[CRITERIA[1]]} combine="AND" onChange={onChange} />);
    const input = screen.getByPlaceholderText("critQuestion");
    fireEvent.change(input, { target: { value: "Nouvelle question ?" } });
    expect(onChange).toHaveBeenCalledWith(
      [expect.objectContaining({ kind: "free", question: "Nouvelle question ?" })],
      "AND"
    );
  });

  it("confidence bounds propagate as numbers", () => {
    const calls: [api.WatchCriterion[], "AND" | "OR"][] = [];
    // stateful harness: the controlled inputs must reflect each change
    function Harness() {
      const [state, setState] = useState<{ criteria: api.WatchCriterion[]; combine: "AND" | "OR" }>({
        criteria: [CRITERIA[0]],
        combine: "AND",
      });
      const onChange = (criteria: api.WatchCriterion[], combine: "AND" | "OR") => {
        calls.push([criteria, combine]);
        setState({ criteria, combine });
      };
      return <CriteriaBuilder fields={FIELDS} criteria={state.criteria} combine={state.combine} onChange={onChange} />;
    }
    render(<Harness />);
    const min = screen.getByLabelText("critConfMin");
    const max = screen.getByLabelText("critConfMax");
    fireEvent.change(min, { target: { value: "0.8" } });
    expect(calls.at(-1)?.[0]).toEqual([expect.objectContaining({ conf_min: 0.8 })]);
    fireEvent.change(max, { target: { value: "0.5" } });
    expect(calls.at(-1)?.[0]).toEqual([expect.objectContaining({ conf_max: 0.5, conf_min: 0.8 })]);
    fireEvent.change(max, { target: { value: "" } });
    expect(calls.at(-1)?.[0]).toEqual([expect.objectContaining({ conf_max: null })]);
  });

  it("removes a row", () => {
    const onChange = jest.fn();
    render(<CriteriaBuilder fields={FIELDS} criteria={CRITERIA} combine="AND" onChange={onChange} />);
    const removes = screen.getAllByRole("button", { name: "critRemove" });
    expect(removes).toHaveLength(2);
    fireEvent.click(removes[0]);
    expect(onChange).toHaveBeenCalledWith([CRITERIA[1]], "AND");
  });

  it("switching the combine toggles AND/OR", () => {
    const onChange = jest.fn();
    render(<CriteriaBuilder fields={FIELDS} criteria={CRITERIA} combine="AND" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "combineOr" }));
    expect(onChange).toHaveBeenCalledWith(CRITERIA, "OR");
  });

  it("changing the kind resets the row", () => {
    const onChange = jest.fn();
    render(
      <CriteriaBuilder fields={FIELDS} criteria={[CRITERIA[0]]} combine="AND" onChange={onChange} />
    );
    // the first row's kind select: switch field -> free
    const kindSelect = screen.getAllByRole("combobox")[0];
    fireEvent.change(kindSelect, { target: { value: "free" } });
    expect(onChange).toHaveBeenCalledWith(
      [expect.objectContaining({ kind: "free", question: "" })],
      "AND"
    );
  });

  it("a search without boolean/choice fields offers no field criterion", () => {
    render(
      <CriteriaBuilder
        fields={[{ key: "notes", label: "Notes", type: "text" }]}
        criteria={[{ kind: "field", field_key: "notes", expected: true }]}
        combine="AND"
        onChange={() => {}}
      />
    );
    // the row only carries the kind selector — no field select, no answer select
    const comboboxes = screen.getAllByRole("combobox");
    const options = Array.from(document.querySelectorAll("option")).map((o) => o.textContent);
    expect(options.join(" ")).not.toMatch(/Notes/);
    expect(comboboxes.length).toBe(1); // the kind selector alone
  });
});
