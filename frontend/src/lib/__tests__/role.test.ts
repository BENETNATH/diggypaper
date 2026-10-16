import { isProjectLead } from "@/lib/role";

describe("isProjectLead", () => {
  it("LEAD du projet ou administrateur", () => {
    expect(isProjectLead({ role_in_project: "LEAD" }, { role: "RESEARCHER" })).toBe(true);
    expect(isProjectLead({ role_in_project: null }, { role: "ADMIN" })).toBe(true);
  });
  it("un annotateur, un invité ou rien : non", () => {
    expect(isProjectLead({ role_in_project: "ANNOTATOR" }, { role: "RESEARCHER" })).toBe(false);
    expect(isProjectLead({ role_in_project: null }, { role: "RESEARCHER" })).toBe(false);
    expect(isProjectLead(null, null)).toBe(false);
  });
});
