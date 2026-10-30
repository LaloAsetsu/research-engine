/**
 * What the page keeps in memory for one signed-in person (answers, drafts) must not outlive the
 * session: the next person to sign in on this tab would find it. Stores register here, and the
 * places that end a session (signing out, a 401 from any request) call `endSession`.
 */
const listeners = new Set<() => void>();

/** Runs `listener` whenever the session ends. Returns a function that stops listening. */
export function onSessionEnd(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function endSession(): void {
  for (const listener of [...listeners]) listener();
}
