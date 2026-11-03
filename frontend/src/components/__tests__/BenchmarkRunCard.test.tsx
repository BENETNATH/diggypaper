import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import * as api from "@/lib/api";
import { BenchmarkRunCard } from "@/components/BenchmarkRunCard";
import { BenchmarkDisagreements } from "@/components/BenchmarkDisagreements";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getBenchmark: jest.fn(),
  listBenchmarkArticles: jest.fn(),
  createPrompt: jest.fn(),
}));

const runningRun: api.BenchmarkRun = {
  id: "b1",
  project_id: "p1",
  search_id: null,
  prompt_template_id: null,
  models_tested: ["m1"],
  variants: null,
  options: null,
  progress: { done: 3, total: 10, model: "m1" },
  status: "RUNNING",
  created_at: null,
};

const completedRun: api.BenchmarkRun = { ...runningRun, status: "COMPLETED", progress: null };

const grid = {
  total: 1,
  page: 1,
  per_page: 25,
  items: [
    {
      article_id: "a1",
      title: "Article premier",
      gold: null,
      disagree: false,
      outputs: [{ model: "m1", variant: null, decision: { ai: true }, error: null }],
    },
  ],
};

beforeEach(() => {
  (api.getBenchmark as jest.Mock).mockReset().mockResolvedValue({ results: [] });
  (api.listBenchmarkArticles as jest.Mock).mockReset().mockResolvedValue(grid);
});

// UI feedback 2026-11-03: the benchmark card must open while its run is in progress,
// to show the progress and the first answers as they arrive.
describe("BenchmarkRunCard — open while running", () => {
  it("RUNNING: the expand action is offered (progress + first answers)", () => {
    render(<BenchmarkRunCard projectId="p1" run={runningRun} onStop={() => {}} onDelete={() => {}} onRerun={() => {}} />);
    expect(screen.getByRole("button", { name: /liveResults/ })).toBeInTheDocument();
    // the progress bar is still shown
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("RUNNING: expanding fetches the partial results and the first answers", async () => {
    render(<BenchmarkRunCard projectId="p1" run={runningRun} onStop={() => {}} onDelete={() => {}} onRerun={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /liveResults/ }));
    await waitFor(() => expect(api.getBenchmark).toHaveBeenCalledWith("p1", "b1"));
    await waitFor(() => expect(api.listBenchmarkArticles).toHaveBeenCalled());
    expect(screen.getByText("partialHint")).toBeInTheDocument();
  });

  it("RUNNING: the live grid shows the first answers as they arrive", async () => {
    render(<BenchmarkRunCard projectId="p1" run={runningRun} onStop={() => {}} onDelete={() => {}} onRerun={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /liveResults/ }));
    expect(await screen.findByText("Article premier")).toBeInTheDocument();
    expect(screen.getByText("liveTitle")).toBeInTheDocument();
  });

  it("COMPLETED: the existing results action is unchanged (disagreement view, not live)", async () => {
    (api.getBenchmark as jest.Mock).mockResolvedValue({
      results: [
        {
          model_name: "m1",
          variant: null,
          n_scored: 10,
          execution_time_seconds: 12,
          accuracy: 0.9,
          precision_score: 0.9,
          recall_score: 0.9,
          f1_score: 0.9,
          details: null,
        },
      ],
    });
    render(<BenchmarkRunCard projectId="p1" run={completedRun} onStop={() => {}} onDelete={() => {}} onRerun={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /results/ }));
    await waitFor(() => expect(api.getBenchmark).toHaveBeenCalledWith("p1", "b1"));
    expect(screen.queryByText("liveTitle")).not.toBeInTheDocument();
    expect(screen.getByText("disagreeTitle")).toBeInTheDocument();
    expect(screen.queryByText("partialHint")).not.toBeInTheDocument();
  });
});

describe("BenchmarkDisagreements — live mode (first answers while running)", () => {
  const fields: api.SchemaField[] = [{ key: "ai", label: "AI", type: "boolean" }];

  it("live: the « first answers » title, no disagreement filter", () => {
    render(<BenchmarkDisagreements projectId="p1" runId="b1" fields={fields} live />);
    expect(screen.getByText("liveTitle")).toBeInTheDocument();
    expect(screen.queryByText("onlyDisagree")).not.toBeInTheDocument();
  });

  it("not live: the disagreement title and filter stay", () => {
    render(<BenchmarkDisagreements projectId="p1" runId="b1" fields={fields} />);
    expect(screen.getByText("disagreeTitle")).toBeInTheDocument();
    expect(screen.getByText("onlyDisagree")).toBeInTheDocument();
  });

  it("live: the grid refreshes by itself (10 s)", async () => {
    jest.useFakeTimers();
    try {
      render(<BenchmarkDisagreements projectId="p1" runId="b1" fields={fields} live />);
      await act(async () => {
        await jest.advanceTimersByTimeAsync(10_000);
      });
      expect((api.listBenchmarkArticles as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2);
    } finally {
      jest.useRealTimers();
    }
  });
});

// spec 2026-11-03 pointers: a finished (e.g. interrupted/failed) run can be
// relaunched or deleted — while a run is in progress, only the stop action exists.
describe("BenchmarkRunCard — delete / rerun", () => {
  it("COMPLETED: « rerun » and « delete » are offered", () => {
    render(
      <BenchmarkRunCard projectId="p1" run={completedRun} onStop={() => {}} onDelete={() => {}} onRerun={() => {}} />
    );
    expect(screen.getByRole("button", { name: /rerun/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /delete/ })).toBeInTheDocument();
  });

  it("RUNNING: neither is offered (only the stop action)", () => {
    render(
      <BenchmarkRunCard projectId="p1" run={runningRun} onStop={() => {}} onDelete={() => {}} onRerun={() => {}} />
    );
    expect(screen.queryByRole("button", { name: /rerun/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/ })).not.toBeInTheDocument();
  });

  it("delete / rerun report the run id to the page", () => {
    const onDelete = jest.fn();
    const onRerun = jest.fn();
    render(
      <BenchmarkRunCard projectId="p1" run={completedRun} onStop={() => {}} onDelete={onDelete} onRerun={onRerun} />
    );
    fireEvent.click(screen.getByRole("button", { name: /rerun/ }));
    expect(onRerun).toHaveBeenCalledWith("b1");
    fireEvent.click(screen.getByRole("button", { name: /delete/ }));
    expect(onDelete).toHaveBeenCalledWith("b1");
  });
});

// 2026-11-03: without a gold reference the recall/precision/F1/accuracy columns
// are empty — hide them and keep only the columns that carry information
// (agreement, time, n scored, errors).
describe("BenchmarkRunCard — ranking table (contextual columns)", () => {
  const base: api.BenchmarkResult = {
    model_name: "coder",
    variant: "complet",
    n_scored: 10,
    execution_time_seconds: 12,
    recall_score: null,
    precision_score: null,
    f1_score: null,
    accuracy: null,
    details: { agreement: 1.0 },
  };
  const noGold = (): api.BenchmarkResult[] => [
    base,
    { ...base, variant: "direct", execution_time_seconds: 30 },
  ];
  const prf = { tp: 5, fp: 0, fn: 5, tn: 0, precision: 1.0, recall: 0.5, f1: 0.66, accuracy: 0.5, specificity: 1.0 };
  const withGold = (): api.BenchmarkResult[] => [
    {
      ...base,
      recall_score: 0.5,
      precision_score: 1.0,
      f1_score: 0.66,
      accuracy: 0.5,
      details: {
        agreement: 1.0,
        scored: { n: 10, relevance: prf, items: { Pertinence: { type: "boolean", n: 10, ...prf } }, accuracy: 0.5 },
        primary: { item: "Pertinence", metric: "recall", value: 0.5 },
      },
    },
    {
      ...base,
      variant: "direct",
      execution_time_seconds: 30,
      recall_score: 0.3,
      precision_score: 0.5,
      f1_score: 0.37,
      accuracy: 0.4,
      details: {
        agreement: 0.5,
        scored: { n: 10, relevance: prf, items: { Pertinence: { type: "boolean", n: 10, ...prf } }, accuracy: 0.4 },
        primary: { item: "Pertinence", metric: "recall", value: 0.3 },
      },
    },
  ];
  const expand = async (results: api.BenchmarkResult[]) => {
    (api.getBenchmark as jest.Mock).mockResolvedValue({ results });
    render(<BenchmarkRunCard projectId="p1" run={completedRun} onStop={() => {}} onDelete={() => {}} onRerun={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /results/ }));
    await waitFor(() => expect(api.getBenchmark).toHaveBeenCalledWith("p1", "b1"));
  };

  it("no gold: score columns hidden, agreement + time stay", async () => {
    await expand(noGold());
    expect(screen.queryByText("recall")).not.toBeInTheDocument();
    expect(screen.queryByText("precision")).not.toBeInTheDocument();
    expect(screen.queryByText("f1")).not.toBeInTheDocument();
    expect(screen.queryByText("accuracy")).not.toBeInTheDocument();
    expect(screen.getByText("agreement")).toBeInTheDocument();
    expect(screen.getByText("time")).toBeInTheDocument();
  });

  it("with gold: score columns shown", async () => {
    await expand(withGold());
    expect(screen.getByText("recall")).toBeInTheDocument();
    expect(screen.getByText("precision")).toBeInTheDocument();
    expect(screen.getByText("f1")).toBeInTheDocument();
    expect(screen.getByText("accuracy")).toBeInTheDocument();
  });
});
