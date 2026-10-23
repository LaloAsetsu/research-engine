import { expect, test } from "@playwright/test";
import { ANA, TEMP } from "../src/test/auth-mock";
import { expectNoAxeViolations, signIn } from "./fixtures";

// These tests sign in against the real API in fake mode, with its seeded test accounts, and no
// mock between the browser and the server. Each account is used by one test only (`beto` for the
// failed sign-in, which counts toward the rate limit), because the server and its accounts live
// until the run ends and a changed password stays changed.
const BETO = { username: "beto", password: "test-password-beto-02" };

test.describe("sign-in against the real API", () => {
  test.describe.configure({ retries: 0 }); // the first sign-in of an account happens once

  test("answers a wrong password with one message", async ({ page }) => {
    await page.goto("/login");
    await signIn(page, { ...ANA, ...BETO }, "not-the-password");
    await expect(page.getByRole("alert")).toHaveText("Wrong username or password.");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("signs in, shows the service status, and signs out", async ({ page }) => {
    await page.goto("/benchmarks");
    await expect(page).toHaveURL(/\/login\?next=%2Fbenchmarks$/);
    await signIn(page, ANA);
    await expect(page).toHaveURL(/\/benchmarks$/);
    await expect(page.getByRole("heading", { name: "Benchmarks" })).toBeVisible();
    await expect(
      page.getByRole("status", { name: /Service status: Ready \(test data\)/ }),
    ).toBeVisible();
    await expectNoAxeViolations(page);

    await page.getByRole("button", { name: "Ana Prueba's account" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeFocused();
    await page.goto("/ask");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("keeps the app closed until a temporary password is changed", async ({ page }) => {
    await page.goto("/login");
    await signIn(page, TEMP);
    await expect(page.getByRole("heading", { name: "Change your password" })).toBeFocused();
    await expectNoAxeViolations(page);
    await page.goto("/ask");
    await expect(page.getByRole("heading", { name: "Change your password" })).toBeVisible();

    await page.getByLabel("Current password").fill("not-the-password");
    await page.getByLabel("New password", { exact: true }).fill("a-brand-new-password");
    await page.getByLabel("Repeat the new password").fill("a-brand-new-password");
    await page.getByRole("button", { name: "Save password" }).click();
    await expect(page.getByRole("alert")).toBeVisible();

    await page.getByLabel("Current password").fill(TEMP.password);
    await page.getByRole("button", { name: "Save password" }).click();
    await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Cuenta Temporal's account" })).toBeVisible();
  });
});
