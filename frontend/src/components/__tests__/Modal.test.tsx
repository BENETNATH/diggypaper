import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { Modal } from "@/components/ui/Modal";

jest.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));

// audit 2026-11-10 P2-1: the modal had no focus trap and did not restore focus.
describe("Modal — focus management", () => {
  it("traps Tab inside the dialog and wraps around", () => {
    render(
      <div>
        <button type="button" id="trigger">
          trigger
        </button>
        <Modal title="Titre" onClose={() => {}}>
          <input aria-label="champ 1" />
          <button type="button" id="ok">
            valider
          </button>
        </Modal>
      </div>
    );
    const dialog = screen.getByRole("dialog");
    // focus starts on the dialog when it opens
    expect(dialog).toHaveFocus();
    const champ1 = screen.getByRole("textbox", { name: "champ 1" });
    const ok = screen.getByRole("button", { name: "valider" });
    const close = screen.getByRole("button", { name: "close" }); // × — first in DOM order
    // jsdom does not perform the native tab order — we simulate the browser
    // reaching the last control, then the trap must wrap back to the first.
    ok.focus();
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Tab" });
    expect(document.activeElement).toBe(close);
    // Shift+Tab from the first control wraps to the last
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(ok);
    // the controls stay reachable inside the dialog
    expect(dialog.contains(champ1)).toBe(true);
  });

  it("restores focus to the trigger when closed with Escape", () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button type="button" id="trigger2" onClick={() => setOpen(true)}>
            trigger
          </button>
          {open && (
            <Modal title="Titre" onClose={() => setOpen(false)}>
              <input aria-label="champ" />
            </Modal>
          )}
        </div>
      );
    }
    render(<Harness />);
    // the browser focuses the clicked trigger before the modal mounts
    const trigger2 = document.getElementById("trigger2")!;
    trigger2.focus();
    fireEvent.click(trigger2);
    screen.getByRole("textbox", { name: "champ" }).focus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(document.getElementById("trigger2"));
  });
});
