import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach } from "vitest";
import i18n from "@/shared/i18n";
import { useUiStore } from "@/shared/stores/ui-store";
import { installMatchMedia, setSystemDark } from "./media";
import { resetAuthMock, server } from "./server";

installMatchMedia();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));

beforeEach(async () => {
  resetAuthMock();
  setSystemDark(false);
  window.localStorage.clear();
  document.cookie = "re_csrf=csrf-test-token; Path=/"; // what a sign-in sets
  document.documentElement.className = "";
  await i18n.changeLanguage("en");
  useUiStore.setState({ theme: "system", language: "en" });
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => server.close());

// Radix menus ask the DOM for things jsdom does not have.
Element.prototype.hasPointerCapture = () => false;
Element.prototype.setPointerCapture = () => undefined;
Element.prototype.releasePointerCapture = () => undefined;
Element.prototype.scrollIntoView = () => undefined;
