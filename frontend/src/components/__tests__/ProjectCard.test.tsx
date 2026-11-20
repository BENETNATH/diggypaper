import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ProjectCard from "@/components/ProjectCard";
import * as api from "@/lib/api";

type Catalog = Record<string, Record<string, string>>;

// House pattern (ProjectDashboard.test): real fr messages, minimal ICU.
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
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listMembers: jest.fn(),
  deleteProject: jest.fn(),
}));

const listMembers = api.listMembers as jest.Mock;
const deleteProject = api.deleteProject as jest.Mock;

function makeProject(over: Partial<api.Project> = {}): api.Project {
  return {
    id: "p1",
    title: "SABV",
    description: "following discussion with Fabrice",
    status: "COMPLETED",
    created_by: "u1",
    default_language: "en",
    created_at: null,
    article_count: 1495,
    member_count: 3,
    role_in_project: "LEAD",
    ...over,
  };
}

const members = [
  { user_id: "u1", email: "benoit@lab.fr", full_name: "Benoît Petit-Demoulière", role: "LEAD" as const },
  { user_id: "u2", email: "marie@lab.fr", full_name: "Marie Lambert", role: "ANNOTATOR" as const },
  { user_id: "u3", email: "fabrice@lab.fr", full_name: null, role: "ANNOTATOR" as const },
];

function mount(
  project: api.Project = makeProject(),
  user: { role?: string } | null = { role: "RESEARCHER" },
  onDeleted: jest.Mock = jest.fn()
) {
  listMembers.mockClear();
  deleteProject.mockClear();
  listMembers.mockResolvedValue(members);
  deleteProject.mockResolvedValue(undefined);
  return render(<ProjectCard project={project} user={user} onDeleted={onDeleted} />);
}

// the chip's wrapper div (position:relative, popup anchor)
const chipWrapper = () => screen.getByRole("button", { name: "Partagé · 3" }).parentElement as HTMLElement;

describe("ProjectCard — R1 trash icon (spec 2026-11-20-projets-quick-actions)", () => {
  it("visible for a project LEAD, with title/aria from projects.deleteProjectTitle", () => {
    mount();
    const trash = screen.getByRole("button", { name: "Supprimer le projet" });
    expect(trash).toHaveAttribute("title", "Supprimer le projet");
    expect(trash.querySelector("svg")).not.toBeNull();
  });

  it("hidden for an ANNOTATOR (not lead, not admin)", () => {
    mount(makeProject({ role_in_project: "ANNOTATOR" }));
    expect(screen.queryByRole("button", { name: "Supprimer le projet" })).not.toBeInTheDocument();
  });

  it("hidden for a RESEARCHER who is not a member, visible for a global ADMIN", () => {
    mount(makeProject({ role_in_project: null }), { role: "RESEARCHER" });
    expect(screen.queryByRole("button", { name: "Supprimer le projet" })).not.toBeInTheDocument();
    mount(makeProject({ role_in_project: null }), { role: "ADMIN" });
    expect(screen.getByRole("button", { name: "Supprimer le projet" })).toBeInTheDocument();
  });

  it("clicking the trash does NOT navigate (button is outside the anchor)", () => {
    mount();
    const trash = screen.getByRole("button", { name: "Supprimer le projet" });
    expect(trash.closest("a")).toBeNull();
  });
});

describe("ProjectCard — R2 delete confirmation modal", () => {
  it("opens the modal with the title interpolated in the body", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Supprimer le projet" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleName("Supprimer le projet");
    expect(
      screen.getByText(
        "Supprimer « SABV » et tout son contenu ? Cette action est irréversible : articles, corpus, analyses, résultats et membres seront supprimés."
      )
    ).toBeInTheDocument();
  });

  it("Supprimer calls deleteProject and reloads the list (onDeleted)", async () => {
    const onDeleted = jest.fn();
    mount(makeProject(), undefined, onDeleted);
    fireEvent.click(screen.getByRole("button", { name: "Supprimer le projet" }));
    fireEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    await waitFor(() => expect(deleteProject).toHaveBeenCalledWith("p1"));
    expect(onDeleted).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Annuler closes the modal without deleting", () => {
    const onDeleted = jest.fn();
    mount(makeProject(), undefined, onDeleted);
    fireEvent.click(screen.getByRole("button", { name: "Supprimer le projet" }));
    fireEvent.click(screen.getByRole("button", { name: "Annuler" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deleteProject).not.toHaveBeenCalled();
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("a failed delete keeps the modal open with the error message (retry possible), no reload", async () => {
    const onDeleted = jest.fn();
    mount(makeProject(), undefined, onDeleted);
    deleteProject.mockRejectedValue(new Error("lead required"));
    fireEvent.click(screen.getByRole("button", { name: "Supprimer le projet" }));
    fireEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    await waitFor(() =>
      // the test's next-intl mock returns the raw key for the root namespace
      expect(screen.getByText("errors.leadRequired")).toBeInTheDocument()
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onDeleted).not.toHaveBeenCalled();
  });

  it("Escape does not close the modal while a delete is in flight", async () => {
    mount(makeProject());
    deleteProject.mockImplementation(() => new Promise(() => {}));
    fireEvent.click(screen.getByRole("button", { name: "Supprimer le projet" }));
    fireEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Supprimer" })).toBeDisabled()
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("ProjectCard — R3/R4 shared chip + members popup", () => {
  it("chip visible only when member_count >= 2", () => {
    mount(makeProject({ member_count: 1 }));
    expect(screen.queryByRole("button", { name: "Partagé · 1" })).not.toBeInTheDocument();
    mount(makeProject({ member_count: 3 }));
    expect(screen.getByRole("button", { name: "Partagé · 3" })).toBeInTheDocument();
  });

  it("the chip is a button with aria-haspopup (hover AND keyboard AND click)", () => {
    mount();
    const chip = screen.getByRole("button", { name: "Partagé · 3" });
    expect(chip).toHaveAttribute("aria-haspopup", "true");
    expect(chip).toHaveAttribute("aria-expanded", "false");
  });

  it("hover opens the popup: skeleton first, then names + role labels (team.lead / team.annotator)", async () => {
    listMembers.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(members), 10))
    );
    mount();
    fireEvent.mouseEnter(chipWrapper());
    // loading: 2 skeleton lines, no name yet
    expect(chipWrapper().querySelectorAll(".skeleton").length).toBe(2);
    expect(screen.queryByText("Benoît Petit-Demoulière")).not.toBeInTheDocument();
    await screen.findByText("Benoît Petit-Demoulière");
    expect(screen.getByText("Responsable de projet")).toBeInTheDocument();
    expect(screen.getAllByText("Annotateur")).toHaveLength(2);
    // lazy fetch done
    expect(listMembers).toHaveBeenCalledWith("p1");
  });

  it("full_name null falls back to the email", async () => {
    mount();
    fireEvent.mouseEnter(chipWrapper());
    await screen.findByText("fabrice@lab.fr");
  });

  it("members are cached: a second open does not re-fetch", async () => {
    mount();
    fireEvent.mouseEnter(chipWrapper());
    await screen.findByText("Benoît Petit-Demoulière");
    fireEvent.mouseLeave(chipWrapper());
    expect(screen.queryByText("Benoît Petit-Demoulière")).not.toBeInTheDocument();
    fireEvent.mouseEnter(chipWrapper());
    await screen.findByText("Benoît Petit-Demoulière");
    expect(listMembers).toHaveBeenCalledTimes(1);
  });

  it("clicking the chip opens the popup without following the card link (preventDefault)", async () => {
    mount();
    const chip = screen.getByRole("button", { name: "Partagé · 3" });
    const ev = new MouseEvent("click", { bubbles: true, cancelable: true });
    chip.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    await screen.findByText("Benoît Petit-Demoulière");
  });

  it("a failed members fetch does not cache the empty state: the next open re-fetches", async () => {
    mount();
    listMembers.mockRejectedValueOnce(new Error("503"));
    fireEvent.mouseEnter(chipWrapper());
    await waitFor(() => expect(listMembers).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 10)); // let the rejection settle
    fireEvent.mouseLeave(chipWrapper());
    fireEvent.mouseEnter(chipWrapper());
    await screen.findByText("Benoît Petit-Demoulière");
    expect(listMembers).toHaveBeenCalledTimes(2);
  });

  it("closes on mouse leave, on Escape and on outside click", async () => {
    mount();
    // mouse leave
    fireEvent.mouseEnter(chipWrapper());
    await screen.findByText("Benoît Petit-Demoulière");
    fireEvent.mouseLeave(chipWrapper());
    expect(screen.queryByText("Benoît Petit-Demoulière")).not.toBeInTheDocument();
    // Escape
    fireEvent.mouseEnter(chipWrapper());
    await screen.findByText("Benoît Petit-Demoulière");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText("Benoît Petit-Demoulière")).not.toBeInTheDocument();
    // outside click
    fireEvent.mouseEnter(chipWrapper());
    await screen.findByText("Benoît Petit-Demoulière");
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText("Benoît Petit-Demoulière")).not.toBeInTheDocument();
  });

  it("keyboard focus on the chip opens the popup", async () => {
    mount();
    fireEvent.focus(screen.getByRole("button", { name: "Partagé · 3" }));
    await screen.findByText("Benoît Petit-Demoulière");
  });
});
