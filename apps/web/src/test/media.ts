/** jsdom has no matchMedia: a stand-in whose "dark" answer a test can flip. */
let dark = false;
const listeners = new Set<() => void>();

export function installMatchMedia(): void {
  window.matchMedia = (query: string) =>
    ({
      get matches() {
        return query.includes("dark") ? dark : false;
      },
      media: query,
      onchange: null,
      addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}

export function setSystemDark(value: boolean): void {
  dark = value;
  for (const listener of [...listeners]) listener();
}
