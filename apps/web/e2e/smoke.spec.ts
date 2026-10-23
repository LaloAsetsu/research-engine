import { ANA, expect, expectNoAxeViolations, TEMP, signIn, test } from "./fixtures";

// The page loads in each scheme, so no scan reads a page that is still switching colors.
for (const colorScheme of ["light", "dark"] as const) {
  test.describe(`${colorScheme} theme`, () => {
    test.use({ colorScheme });

    test("sends a visitor to the login screen, which has no accessibility violations", async ({
      page,
      authMock,
    }) => {
      void authMock;
      await page.goto("/benchmarks");
      await expect(page).toHaveURL(/\/login\?next=%2Fbenchmarks$/);
      await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
      if (colorScheme === "dark") await expect(page.locator("html")).toHaveClass(/dark/);
      else await expect(page.locator("html")).not.toHaveClass(/dark/);
      await expectNoAxeViolations(page);
    });

    test("shows a forced password change with no accessibility violations", async ({
      page,
      authMock,
    }) => {
      void authMock;
      await page.goto("/login");
      await signIn(page, TEMP);
      await expect(page.getByRole("heading", { name: "Change your password" })).toBeVisible();
      await expectNoAxeViolations(page);
    });
  });
}

test.describe("login", () => {
  test("shows one message for a wrong password", async ({ page, authMock }) => {
    void authMock;
    await page.goto("/login");
    await signIn(page, ANA, "not-the-password");
    await expect(page.getByRole("alert")).toHaveText("Wrong username or password.");
  });

  test("signs in, returns to the screen asked for and shows the API status", async ({
    page,
    authMock,
  }) => {
    void authMock;
    await page.goto("/benchmarks");
    await signIn(page, ANA);
    await expect(page).toHaveURL(/\/benchmarks$/);
    await expect(page.getByRole("heading", { name: "Benchmarks" })).toBeFocused();
    // /status is answered by the fixture here and by the real API in real-api.spec.ts
    await expect(
      page.getByRole("status", { name: /Service status: Ready \(test data\)/ }),
    ).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("walks the four screens", async ({ page, authMock }) => {
    void authMock;
    await page.goto("/login");
    await signIn(page, ANA);
    const nav = page.getByRole("navigation", { name: "Main" });
    for (const [link, heading] of [
      ["Benchmarks", "Benchmarks"],
      ["Question sets", "Question sets"],
      ["Circuits", "Circuits"],
      ["Ask", "Ask"],
    ] as const) {
      await nav.getByRole("link", { name: link }).click();
      await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    }
  });

  test("signs out", async ({ page, authMock }) => {
    void authMock;
    await page.goto("/login");
    await signIn(page, ANA);
    await page.getByRole("button", { name: "Ana Prueba's account" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeFocused();
    await page.goto("/ask");
    await expect(page).toHaveURL(/\/login$/);
  });
});

test.describe("forced password change", () => {
  test("follows the first login, then opens the app", async ({ page, authMock }) => {
    void authMock;
    await page.goto("/login");
    await signIn(page, TEMP);
    await expect(page.getByRole("heading", { name: "Change your password" })).toBeFocused();
    // the app stays closed until the password is changed
    await page.goto("/ask");
    await expect(page.getByRole("heading", { name: "Change your password" })).toBeVisible();
    await page.getByLabel("Current password").fill(TEMP.password);
    await page.getByLabel("New password", { exact: true }).fill("a-brand-new-password");
    await page.getByLabel("Repeat the new password").fill("a-brand-new-password");
    await page.getByRole("button", { name: "Save password" }).click();
    await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();
  });
});

test.describe("theme and language", () => {
  test("remember the choice across a reload, with no flash of the wrong theme", async ({
    page,
    authMock,
  }) => {
    void authMock;
    await page.goto("/login");
    await signIn(page, ANA);
    await page.getByRole("button", { name: "Theme: System" }).click();
    await page.getByRole("menuitemradio", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(page.getByRole("menu")).toBeHidden();
    await expectNoAxeViolations(page);
    await page.getByRole("button", { name: "Language: English" }).click();
    await page.getByRole("menuitemradio", { name: "Español" }).click();
    await expect(page.getByRole("heading", { name: "Preguntar" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "es");

    // theme-init.js sets the class before the app starts, so it is there at DOMContentLoaded
    await page.addInitScript(() => {
      document.addEventListener("DOMContentLoaded", () => {
        (window as unknown as { darkAtLoad: boolean }).darkAtLoad =
          document.documentElement.classList.contains("dark");
      });
    });
    await page.reload();
    await expect(page.getByRole("heading", { name: "Preguntar" })).toBeVisible();
    expect(
      await page.evaluate(() => (window as unknown as { darkAtLoad: boolean }).darkAtLoad),
    ).toBe(true);
  });
});

test.describe("browser language", () => {
  test.describe("Spanish", () => {
    test.use({ locale: "es-MX" });
    test("is used when the browser asks for it", async ({ page, authMock }) => {
      void authMock;
      await page.goto("/login");
      await expect(page.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
    });
  });

  test.describe("another language", () => {
    test.use({ locale: "fr-FR" });
    test("falls back to Spanish", async ({ page, authMock }) => {
      void authMock;
      await page.goto("/login");
      await expect(page.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
    });
  });

  test.describe("English", () => {
    test.use({ locale: "en-GB" });
    test("is used when the browser asks for it", async ({ page, authMock }) => {
      void authMock;
      // theme-init.js sets the document language before the app starts
      await page.addInitScript(() => {
        document.addEventListener("DOMContentLoaded", () => {
          (window as unknown as { langAtLoad: string }).langAtLoad = document.documentElement.lang;
        });
      });
      await page.goto("/login");
      await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
      expect(
        await page.evaluate(() => (window as unknown as { langAtLoad: string }).langAtLoad),
      ).toBe("en");
    });
  });
});
