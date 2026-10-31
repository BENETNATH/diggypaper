"use client";

import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/**
 * True only after the component has mounted on the client. Lets us avoid reading
 * browser-only globals (e.g. sessionStorage) during the server render / hydration.
 */
export function useHasMounted(): boolean {
  return useSyncExternalStore(subscribe, () => true, () => false);
}
