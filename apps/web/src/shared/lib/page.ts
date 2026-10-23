/** Reloads the document. A function of its own so tests can replace it (jsdom cannot reload). */
export function reloadPage(): void {
  window.location.reload();
}
