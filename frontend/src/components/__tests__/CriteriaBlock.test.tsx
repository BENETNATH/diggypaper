import { useState } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { Criteria, SearchInfo } from "@/lib/api";
import { CriteriaBlock } from "@/components/CriteriaBlock";

// i18n keys returned as-is: the tests do not depend on the message files —
// except derivedFrom, which is namespaced (the chain preview must use the
// project's « issue de … », not the searches' « Dérivée de »).
jest.mock("next-intl", () => {
  const t = (ns: string) => (key: string) => (key === "derivedFrom" ? `${ns}.${key}` : key);
  return { useLocale: () => "fr", useTranslations: (ns: string) => t(ns) };
});
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  criteriaCount: jest.fn(),
}));
const criteriaCount = api.criteriaCount as jest.Mock;

const SEARCH = {
  id: "sA",
  name: "Pertinence",
  kind: "standard",
  is_default: false,
  question: null,
  fields: [
    { key: "rodent", label: "Modèle rongeur", type: "boolean" },
    { key: "strain", label: "Souche", type: "choice", options: ["BALB/c", "C57"] },
    { key: "dur", label: "Durée", type: "text" },
  ],
  text_sections: null,
  parent: null,
  parent_spec: null,
  created_at: null,
  predicted: 100,
} as unknown as SearchInfo;

/** Stateful harness: the block is controlled, the test drives its value. */
function Harness({ initial = {}, newName = "" }: { initial?: Criteria; newName?: string }) {
  const [value, setValue] = useState<Criteria>(initial);
  return (
    <CriteriaBlock
      projectId="p1"
      search={SEARCH}
      value={value}
      onChange={setValue}
      newName={newName}
      onCount={jest.fn()}
    />
  );
}

function renderBlock(overrides: { initial?: Criteria; newName?: string } = {}) {
  criteriaCount.mockClear();
  criteriaCount.mockResolvedValue(42);
  return render(<Harness {...overrides} />);
}

test("the counter is the server's (debounced) and reports through onCount", async () => {
  renderBlock();
  expect(screen.getByTestId("crit-count").textContent).toBe("critCounting");
  // 300 ms debounce, then the count renders.
  await waitFor(() => expect(screen.getByTestId("crit-count").textContent).toBe("critCount"));
  expect(criteriaCount).toHaveBeenCalledTimes(1);
  expect(criteriaCount).toHaveBeenCalledWith("p1", "sA", {});
});

test("a status pill toggles the criteria family and re-queries", async () => {
  renderBlock();
  const pill = screen.getByRole("button", { name: "critStatusReview" });
  expect(pill).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(pill);
  await waitFor(() => expect(criteriaCount.mock.calls.at(-1)?.[2]).toEqual({ status: ["NEEDS_HUMAN_REVIEW"] }));
  expect(pill).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(pill);
  await waitFor(() => expect(criteriaCount.mock.calls.at(-1)?.[2]).toEqual({}));
});

test("the answer row is driven by the field type (boolean: Oui/Non)", async () => {
  renderBlock();
  const field = screen.getByLabelText("critResponse");
  fireEvent.change(field, { target: { value: "rodent" } });
  // default value: "true" (Oui)
  const value = screen.getAllByLabelText("critResponse").at(-1)!;
  expect(value).toHaveValue("true");
  expect(screen.getByText("critYes")).toBeInTheDocument();
  fireEvent.change(value, { target: { value: "false" } });
  await waitFor(() =>
    expect(criteriaCount.mock.calls.at(-1)?.[2]).toEqual({
      response: { field: "rodent", op: "=", value: "false" },
    })
  );
});

test("text fields offer the contains operator and a free value", async () => {
  renderBlock();
  fireEvent.change(screen.getByLabelText("critResponse"), { target: { value: "dur" } });
  const ops = screen.getByLabelText("critOpEq");
  expect(ops).toHaveValue("=");
  fireEvent.change(ops, { target: { value: "contains" } });
  fireEvent.change(screen.getAllByLabelText("critResponse").at(-1)!, { target: { value: "120" } });
  await waitFor(() =>
    expect(criteriaCount.mock.calls.at(-1)?.[2]).toEqual({
      response: { field: "dur", op: "contains", value: "120" },
    })
  );
});

test("0 kept: the danger message shows and the counter reads 0", async () => {
  renderBlock();
  criteriaCount.mockResolvedValue(0); // the debounced call picks the new implementation up
  await waitFor(() => expect(screen.getByTestId("crit-count").textContent).toBe("critCount"));
  expect(screen.getByTestId("crit-count").className).toContain("text-danger");
  expect(screen.getByText("critZero")).toBeInTheDocument();
});

test("the chain preview shows « B issue de … » · k critères (n) when criteria are active", async () => {
  renderBlock({
    initial: {
      response: { field: "rodent", op: "=", value: "true" },
      confidence: [0.15, 0.85],
      status: ["NEEDS_HUMAN_REVIEW"],
    },
    newName: "B",
  });
  const preview = await screen.findByText(/critChainIntro/);
  // k = 3 families active, n = the live count, existing « derivedFrom » vocabulary.
  const bold = preview.parentElement?.querySelector("b") ?? preview;
  await waitFor(() => expect(bold.textContent).toContain("(42)"));
  expect(bold.textContent).toContain("B");
  expect(bold.textContent).toContain("project.derivedFrom");
  expect(bold.textContent).not.toContain("searches.derivedFrom");
  expect(bold.textContent).toContain("critChainK");
});

test("no criteria active: no chain preview", () => {
  renderBlock();
  expect(screen.queryByText(/critChainIntro/)).toBeNull();
});

test("« Aucun » clears the answer family: the payload keeps the other families, no response without field", async () => {
  renderBlock({
    initial: {
      response: { field: "rodent", op: "=", value: "true" },
      confidence: [0.95, 0.95],
    },
  });
  await waitFor(() =>
    expect(criteriaCount.mock.calls.at(-1)?.[2]).toEqual({
      response: { field: "rodent", op: "=", value: "true" },
      confidence: [0.95, 0.95],
    })
  );
  // Reset the field select to « Aucun »: the response family is dropped
  // entirely — a response object left without field would 422 on the server
  // and the counter would stay « calculating » forever.
  const fieldSelect = screen.getAllByLabelText("critResponse")[0];
  fireEvent.change(fieldSelect, { target: { value: "" } });
  await waitFor(() =>
    expect(criteriaCount.mock.calls.at(-1)?.[2]).toEqual({ confidence: [0.95, 0.95] })
  );
});
