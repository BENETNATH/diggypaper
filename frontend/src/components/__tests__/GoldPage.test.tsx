import { Suspense } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import GoldPage from "@/app/projects/[id]/gold/page";
import * as api from "@/lib/api";
import { SearchInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Catalog = Record<string, Record<string, string>>;

// The real fr messages: the spec pins the hint « 1 à {n} articles (analyse courante) ».
/* eslint-disable @typescript-eslint/no-require-imports */
jest.mock("next-intl", () => {
  const fr = require("../../messages/fr.json") as Catalog;
  return {
    useLocale: () => "fr",
    useTranslations: (ns: string) => (key: string, params?: Record<string, unknown>) => {
      const t = (fr[ns] ?? {})[key];
      if (t == null) return key;
      return String(t).replace(/\{(\w+)\}/g, (m: string, k: string) =>
        String((params ?? {})[k] ?? m)
      );
    },
  };
});
/* eslint-enable @typescript-eslint/no-require-imports */
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => ({ get: () => null, toString: () => "" }),
  usePathname: () => "/projects/p1/gold",
}));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listGold: jest.fn(),
  getProject: jest.fn(),
  listSearches: jest.fn(),
  createSearch: jest.fn(),
}));

const mockUseAuth = useAuth as unknown as jest.Mock;
const listGold = api.listGold as jest.Mock;
const getProject = api.getProject as jest.Mock;
const listSearches = api.listSearches as jest.Mock;

const search: SearchInfo = {
  id: "s1",
  name: "S1",
  kind: "standard",
  is_default: true,
  question: "q?",
  fields: [],
  text_sections: null,
  parent: null,
  parent_spec: null,
  created_at: null,
  created_by: null,
  lot_size: 10,
  predicted: 0,
  reviewed: 0,
  breakdown: {},
  to_review: 0,
  last_job: null,
  last_benchmark: null,
};

function mountGold() {
  mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
  listGold.mockResolvedValue([]);
  getProject.mockResolvedValue({
    role_in_project: "LEAD",
    question_set: true,
    question: "q?",
    fields: [],
    text_sections: ["methods"],
  });
  listSearches.mockResolvedValue([search]);
  return render(
    <Suspense fallback={null}>
      <GoldPage />
    </Suspense>
  );
}

// spec 2026-11-02-frontend-vocabulary-a11y-polish §6
describe("gold page — blind sample: hierarchy + bounds", () => {
  it("« Créer et commencer la relecture » is secondary (primary stays « Ajouter toutes les relectures au gold »)", async () => {
    mountGold();
    const blind = await screen.findByRole("button", { name: "Créer et commencer la relecture" });
    expect(blind).not.toHaveClass("bg-primary");
    expect(blind).toHaveClass("border-border");
    const addAll = screen.getByRole("button", { name: "Ajouter toutes les relectures au gold" });
    expect(addAll).toHaveClass("bg-primary");
  });

  it("« Nombre d'articles »: min=1, max = the current analysis's lot", async () => {
    mountGold();
    const input = await screen.findByLabelText(/Nombre d'articles/);
    expect(input).toHaveAttribute("min", "1");
    expect(input).toHaveAttribute("max", "10");
  });

  it("the blindNHint bound is shown", async () => {
    mountGold();
    expect(await screen.findByText("1 à 10 articles (analyse courante)")).toBeInTheDocument();
  });
});

describe("gold page — spec v4 R5 : revues des autres membres", () => {
  const row = {
    id: "g1",
    article_id: "a1",
    article_title: "Titre A",
    ground_truth_json: { ai: true, confidence_score: 0.9 },
    search_id: "s1",
    source: {
      annotation_id: "ann1",
      by: "Alain",
      at: "2026-11-01T00:00:00",
      status: "VALIDATED" as const,
      blind: false,
    },
    added_by: "Alain",
    stale: false,
    human_reviews: [
      {
        user_id: "u-other",
        by: "Alice",
        at: "2026-11-02T00:00:00",
        status: "VALIDATED",
        blind: false,
        answers: { ai: false, confidence_score: 0.8 },
        confidence: 0.8,
      },
      {
        user_id: "u-me",
        by: "Moi",
        at: "2026-11-03T00:00:00",
        status: "VALIDATED",
        blind: false,
        answers: { ai: true, confidence_score: 0.5 },
        confidence: 0.5,
      },
    ],
  };

  it("shows the others' latest human review (name + answer), never the caller's own", async () => {
    mockUseAuth.mockReturnValue({ user: { id: "u-me", role: "RESEARCHER" }, loading: false });
    listGold.mockResolvedValue([row]);
    getProject.mockResolvedValue({
      role_in_project: "ANOTATOR",
      question_set: true,
      question: "q?",
      fields: [{ key: "ai", label: "IA", type: "boolean" }],
      text_sections: ["methods"],
    });
    listSearches.mockResolvedValue([search]);
    render(
      <Suspense fallback={null}>
        <GoldPage />
      </Suspense>
    );
    expect(await screen.findByText(/Revue humaine \(les autres\)/)).toBeInTheDocument();
    // Alice answered « Non » on the IA question, 80 % confidence
    expect(screen.getByText("Alice — IA : non · 80 %")).toBeInTheDocument();
    // the caller's own review stays out of the « les autres » block
    expect(screen.queryByText("Moi")).not.toBeInTheDocument();
  });
});

describe("gold page — deleted current search (dead stored id)", () => {
  it("no « search not found »: the 404 falls back to the default search and stays usable", async () => {
    sessionStorage.setItem("diggy:search:p1", "dead-id");
    mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
    // the deleted search 404s; the default (no id) succeeds
    listGold.mockImplementation((_p, sid) =>
      sid === "dead-id" ? Promise.reject(new api.ApiError(404, "search not found")) : Promise.resolve([])
    );
    getProject.mockImplementation((_p, sid) =>
      sid === "dead-id"
        ? Promise.reject(new api.ApiError(404, "search not found"))
        : Promise.resolve({
            role_in_project: "LEAD",
            question_set: true,
            question: "q?",
            fields: [],
            text_sections: ["methods"],
          })
    );
    listSearches.mockResolvedValue([search]); // dead-id is not in the list
    render(
      <Suspense fallback={null}>
        <GoldPage />
      </Suspense>
    );
    // the 404 on the dead search falls back to the default search (no id)
    await waitFor(() => expect(listGold).toHaveBeenCalledWith("p1", undefined));
    // the raw « search not found » error is not shown
    expect(screen.queryByText(/search not found/i)).not.toBeInTheDocument();
    // the gold flow is still offered (a way to build the gold / create a lot)
    expect(
      await screen.findByRole("button", { name: "Créer et commencer la relecture" })
    ).toBeInTheDocument();
    sessionStorage.removeItem("diggy:search:p1");
  });
});
