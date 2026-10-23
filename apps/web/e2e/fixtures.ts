import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type Page } from "@playwright/test";
import { ANA, createAuthMock, LUIS, type MockUser } from "../src/test/auth-mock";

export { ANA, LUIS };

interface Fixtures {
  /** Answers the sign-in endpoints with the shared mock; /health and /status reach the real API. */
  authMock: ReturnType<typeof createAuthMock>;
}

export const test = base.extend<Fixtures>({
  authMock: async ({ page }, provide) => {
    const mock = createAuthMock();
    await page.route("**/api/v1/auth/**", async (route) => {
      const request = route.request();
      const body: unknown = request.postData()
        ? JSON.parse(request.postData() ?? "null")
        : undefined;
      const answer = mock.handle({
        method: request.method(),
        path: new URL(request.url()).pathname,
        body,
        csrfHeader: request.headers()["x-csrf-token"],
      });
      if (answer === null) return route.fallback();
      return route.fulfill({
        status: answer.status,
        headers: answer.headers,
        ...(answer.body === undefined ? {} : { body: JSON.stringify(answer.body) }),
      });
    });
    await provide(mock);
  },
});

export { expect };

export async function signIn(page: Page, user: MockUser, password = user.password) {
  await page.getByLabel("Username").fill(user.username);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

/** Fails on any WCAG 2.2 A/AA violation of the page as it is now. */
export async function expectNoAxeViolations(page: Page) {
  // Let every color change finish: a color in the middle of a fade is not one of ours. Looping
  // animations (a spinner) never finish and do not change colors, so they are not waited for.
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .every(
        (animation) =>
          animation.playState === "finished" ||
          animation.playState === "idle" ||
          animation.effect?.getComputedTiming().iterations === Infinity,
      ),
  );
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const summary = results.violations.flatMap((violation) =>
    violation.nodes.map(
      (node) =>
        `${violation.id}: ${node.target.join(" ")} (${node.any[0]?.message ?? node.failureSummary})`,
    ),
  );
  expect(summary).toEqual([]);
}
