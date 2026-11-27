import { renderHook, act } from "@testing-library/react";
import { usePolling } from "@/components/ui/usePolling";

describe("usePolling", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("keeps polling after an error and clears the error on the next success", async () => {
    const fn = jest.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue(1);
    const { result, unmount } = renderHook(() => usePolling(fn, 1000, () => false));
    await act(async () => {});
    expect(result.current.error).toBe("boom");
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(fn).toHaveBeenCalledTimes(2);
    expect(result.current.data).toBe(1);
    expect(result.current.error).toBeNull();
    unmount();
    jest.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
