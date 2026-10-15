import { detectSwipe, SWIPE_THRESHOLD } from "@/lib/swipe";

describe("detectSwipe", () => {
  it("détecte un swipe à droite", () => {
    expect(detectSwipe({ x: 100, y: 100 }, { x: 200, y: 105 })).toBe("right");
  });

  it("détecte un swipe à gauche", () => {
    expect(detectSwipe({ x: 200, y: 100 }, { x: 100, y: 105 })).toBe("left");
  });

  it("ignore un mouvement vertical dominant", () => {
    expect(detectSwipe({ x: 100, y: 100 }, { x: 110, y: 200 })).toBe("none");
  });

  it("ignore un mouvement trop petit", () => {
    expect(detectSwipe({ x: 100, y: 100 }, { x: 120, y: 100 })).toBe("none");
  });

  it("utilise le seuil par défaut", () => {
    // dx = 50 = threshold → swipe
    expect(detectSwipe({ x: 0, y: 0 }, { x: SWIPE_THRESHOLD, y: 0 })).toBe("right");
    // dx = 49 < threshold → none
    expect(detectSwipe({ x: 0, y: 0 }, { x: SWIPE_THRESHOLD - 1, y: 0 })).toBe("none");
  });

  it("permet un seuil custom", () => {
    expect(detectSwipe({ x: 0, y: 0 }, { x: 30, y: 0 }, 20)).toBe("right");
    expect(detectSwipe({ x: 0, y: 0 }, { x: 30, y: 0 }, 50)).toBe("none");
  });

  it("renvoie none si aucun mouvement", () => {
    expect(detectSwipe({ x: 100, y: 100 }, { x: 100, y: 100 })).toBe("none");
  });
});
