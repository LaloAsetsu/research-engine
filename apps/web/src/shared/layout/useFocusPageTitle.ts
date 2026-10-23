import { useEffect, useRef } from "react";
import { NavigationType, useNavigationType } from "react-router";

/**
 * Moves focus to the page heading (`#page-title`) so a screen reader starts at the new content.
 * It runs when `key` changes and after any navigation, but not on a fresh page load.
 */
export function useFocusPageTitle(key: unknown): void {
  const navigationType = useNavigationType();
  const mounted = useRef(false);
  useEffect(() => {
    const firstRun = !mounted.current;
    mounted.current = true;
    if (firstRun && navigationType === NavigationType.Pop) return;
    document.getElementById("page-title")?.focus();
  }, [key, navigationType]);
}
