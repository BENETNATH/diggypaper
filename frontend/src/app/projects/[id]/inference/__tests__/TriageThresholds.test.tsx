import { render, screen } from "@testing-library/react";
import { ACCEPT_PRESETS, REJECT_PRESETS, TriageThresholds } from "../TriageThresholds";

// i18n keys returned as-is (with their params): the test does not depend on the message files.
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key,
}));

// spec 2026-11-09-triage-seuils-fonds-champs R1/§3 — threshold selectors,
// valid presets only (no free value in v1).
describe("TriageThresholds — preset selectors", () => {
  it("offers only the valid presets, defaults 0.85 / 0.15, « disabled » per family", () => {
    render(<TriageThresholds value={{ accept: null, reject: null }} onChange={() => {}} />);
    const accept = screen.getByLabelText("thresholdsAccept") as HTMLSelectElement;
    const reject = screen.getByLabelText("thresholdsReject") as HTMLSelectElement;
    // only the valid presets — and the presets match the server validation
    expect([...accept.options].map((o) => o.value)).toEqual(["0.85", "0.7", "0.9", ""]);
    expect([...reject.options].map((o) => o.value)).toEqual(["0.15", "0.1", "0.25", ""]);
    expect(ACCEPT_PRESETS).toEqual([0.85, 0.7, 0.9]);
    expect(REJECT_PRESETS).toEqual([0.15, 0.1, 0.25]);
    // no policy → the defaults are shown
    expect(accept.value).toBe("0.85");
    expect(reject.value).toBe("0.15");
  });

  it("changing accept keeps the reject value (full policy sent)", () => {
    const onChange = jest.fn();
    render(<TriageThresholds value={{ accept: 0.85, reject: 0.15 }} onChange={onChange} />);
    (screen.getByLabelText("thresholdsAccept") as HTMLSelectElement).value = "0.7";
    screen.getByLabelText("thresholdsAccept").dispatchEvent(new Event("change", { bubbles: true }));
    expect(onChange).toHaveBeenCalledWith({ accept: 0.7, reject: 0.15 });
  });

  it("« disabled » → null for the family (never auto-accepted / never auto-rejected)", () => {
    const onChange = jest.fn();
    render(<TriageThresholds value={{ accept: 0.7, reject: 0.25 }} onChange={onChange} />);
    (screen.getByLabelText("thresholdsReject") as HTMLSelectElement).value = "";
    screen.getByLabelText("thresholdsReject").dispatchEvent(new Event("change", { bubbles: true }));
    expect(onChange).toHaveBeenCalledWith({ accept: 0.7, reject: null });
  });
});
