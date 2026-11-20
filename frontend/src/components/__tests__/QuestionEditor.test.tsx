import { render, screen, fireEvent } from "@testing-library/react";
import { QuestionEditor } from "@/components/QuestionEditor";

// i18n keys returned as-is: the test does not depend on the message files.
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key,
}));

const FIELDS = [{ key: "design", label: "Design", type: "choice" as const, options: ["A", "B"] }];

describe("QuestionEditor — orientation (spec 2026-11-06-ux-refonte-v4 R1)", () => {
  it("the question is labelled « orientation » (optional) with the helper text", () => {
    render(
      <QuestionEditor
        question=""
        fields={FIELDS}
        sections={["methods"]}
        onChange={() => {}}
      />
    );
    expect(screen.getByText("questionLabel")).toBeInTheDocument();
    expect(screen.getByText("questionHint")).toBeInTheDocument();
  });

  // R4 — the item list can be reordered and duplicated
  it("each item has move (up/down) and duplicate actions", () => {
    const two = [
      { key: "a", label: "A", type: "boolean" as const },
      { key: "b", label: "B", type: "text" as const },
    ];
    render(
      <QuestionEditor
        question="q?"
        fields={two}
        sections={["methods"]}
        onChange={() => {}}
      />
    );
    const ups = screen.getAllByRole("button", { name: "moveUp" });
    expect(ups).toHaveLength(2);
    expect(ups[0]).toBeDisabled(); // first item
    expect(ups[1]).toBeEnabled();
    const downs = screen.getAllByRole("button", { name: "moveDown" });
    expect(downs[0]).toBeEnabled();
    expect(downs[1]).toBeDisabled(); // last item
    expect(screen.getAllByRole("button", { name: "duplicate" })).toHaveLength(2);
  });

  it("move down swaps the order", () => {
    const onChange = jest.fn();
    const two = [
      { key: "a", label: "A", type: "boolean" as const },
      { key: "b", label: "B", type: "text" as const },
    ];
    render(<QuestionEditor question="q?" fields={two} sections={["methods"]} onChange={onChange} />);
    screen.getAllByRole("button", { name: "moveDown" })[0].click();
    const next = onChange.mock.calls.at(-1)?.[0] as { fields: { key: string }[] };
    expect(next.fields.map((f) => f.key)).toEqual(["b", "a"]);
  });

  // spec 2026-11-09-triage-seuils R3 — colored cell opt-in per question (default off)
  it("each question has a « badge » toggle, off by default, persisted in the field", () => {
    const onChange = jest.fn();
    render(<QuestionEditor question="q?" fields={FIELDS} sections={["methods"]} onChange={onChange} />);
    const box = screen.getByRole("checkbox", { name: "badgeLabel" });
    expect(box).not.toBeChecked();
    box.click();
    const next = onChange.mock.calls.at(-1)?.[0] as { fields: { badge?: boolean }[] };
    expect(next.fields[0].badge).toBe(true);
  });

  it("duplicate copies the item after itself (new key)", () => {
    const onChange = jest.fn();
    render(
      <QuestionEditor
        question="q?"
        fields={FIELDS}
        sections={["methods"]}
        onChange={onChange}
      />
    );
    screen.getByRole("button", { name: "duplicate" }).click();
    const last = onChange.mock.calls.at(-1)?.[0];
    expect(last.fields).toHaveLength(2);
    expect(last.fields[1].label).toBe("Design");
    expect(last.fields[1].key).toBe("design_2");
    expect(last.fields[0].key).toBe("design");
  });
});

// spec 2026-11-20-question-key-autodup — the key is not rendered; assertions
// ride on the onChange payload (next.fields[i].key)
describe("QuestionEditor — clés : auto-incrément anti-doublon", () => {
  const keysOf = (calls: unknown[][]) =>
    (calls.at(-1)?.[0] as { fields: { key: string }[] }).fields.map((f) => f.key);

  const labelAt = (i: number) => screen.getAllByLabelText("fieldLabel")[i];

  it("slug collision gets an incremented key", () => {
    const onChange = jest.fn();
    render(
      <QuestionEditor
        question="q?"
        fields={[
          { key: "w", label: "W", type: "boolean" as const },
          { key: "new_2", label: "", type: "boolean" as const },
        ]}
        sections={["methods"]}
        onChange={onChange}
      />
    );
    fireEvent.change(labelAt(1), { target: { value: "W" } });
    expect(keysOf(onChange.mock.calls)).toEqual(["w", "w_2"]);
  });

  it("triple collision increments to _3", () => {
    const onChange = jest.fn();
    render(
      <QuestionEditor
        question="q?"
        fields={[
          { key: "w", label: "W", type: "boolean" as const },
          { key: "w_2", label: "W", type: "boolean" as const },
          { key: "new_3", label: "", type: "boolean" as const },
        ]}
        sections={["methods"]}
        onChange={onChange}
      />
    );
    fireEvent.change(labelAt(2), { target: { value: "W" } });
    expect(keysOf(onChange.mock.calls)).toEqual(["w", "w_2", "w_3"]);
  });

  it("empty slug keeps the current key", () => {
    const onChange = jest.fn();
    render(
      <QuestionEditor
        question="q?"
        fields={[{ key: "new_4", label: "", type: "boolean" as const }]}
        sections={["methods"]}
        onChange={onChange}
      />
    );
    fireEvent.change(labelAt(0), { target: { value: "???" } });
    expect(keysOf(onChange.mock.calls)).toEqual(["new_4"]);
  });

  it("saved keys are never re-sluged", () => {
    const onChange = jest.fn();
    render(
      <QuestionEditor
        question="q?"
        fields={[{ key: "w", label: "W", type: "boolean" as const }]}
        sections={["methods"]}
        onChange={onChange}
      />
    );
    fireEvent.change(labelAt(0), { target: { value: "X" } });
    expect(keysOf(onChange.mock.calls)).toEqual(["w"]);
  });

  it("add field avoids a taken new_N", () => {
    const onChange = jest.fn();
    render(
      <QuestionEditor
        question="q?"
        fields={[
          { key: "new_1", label: "", type: "boolean" as const },
          { key: "new_3", label: "", type: "boolean" as const },
        ]}
        sections={["methods"]}
        onChange={onChange}
      />
    );
    screen.getByRole("button", { name: "+ addField" }).click();
    expect(keysOf(onChange.mock.calls)).toEqual(["new_1", "new_3", "new_2"]);
  });
});
