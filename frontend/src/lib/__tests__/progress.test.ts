import { etaSeconds, formatDuration, pushSample, ratePerSecond } from "../progress";

describe("progress", () => {
  it("estimates the speed over the window and the remaining time", () => {
    let s = pushSample([], 0, 0);
    s = pushSample(s, 10_000, 20);
    s = pushSample(s, 20_000, 40);
    const rate = ratePerSecond(s);
    expect(rate).toBeCloseTo(2);
    expect(etaSeconds(40, 100, rate)).toBeCloseTo(30);
  });

  it("n'annonce rien tant que l'estimation n'est pas fiable", () => {
    expect(ratePerSecond([])).toBeNull();
    expect(ratePerSecond(pushSample([], 0, 5))).toBeNull();
    const short = pushSample(pushSample([], 0, 0), 2_000, 5); // 2 s : trop court
    expect(ratePerSecond(short)).toBeNull();
    expect(etaSeconds(5, 100, null)).toBeNull();
    expect(etaSeconds(100, 100, 3)).toBeNull();
  });

  it("resets to zero if the counter goes back (new analysis)", () => {
    const s = pushSample(pushSample([], 0, 50), 10_000, 60);
    expect(pushSample(s, 20_000, 3)).toEqual([{ t: 20_000, done: 3 }]);
  });

  it("ignores samples too close together without progress", () => {
    const s = pushSample([], 0, 5);
    expect(pushSample(s, 500, 5)).toBe(s);
  });

  it("formats the duration", () => {
    expect(formatDuration(45)).toBe("45 s");
    expect(formatDuration(14 * 60)).toBe("14 min");
    expect(formatDuration(65 * 60)).toBe("65 min");
    expect(formatDuration(3 * 3600 + 5 * 60)).toBe("3 h 05");
  });
});
