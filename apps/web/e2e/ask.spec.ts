import { DANI, expect as baseExpect, expectNoAxeViolations, signIn, test } from "./fixtures";
import type { Page } from "@playwright/test";

// These tests run the Ask screen in a browser against the real API in fake mode: the real HTTP
// layer, the real event stream (EventSource) and the invented papers of the fake corpus. Like the
// circuit screen's tests, each one signs in through the sign-in page as `dani`, a seeded test
// account of that API (no mock), and the tests of this file run one after the other, so the
// account never has more than one question open at a time (the API allows three).

// The model servers answer one question at a time, so an answer can wait behind another one's.
const expect = baseExpect.configure({ timeout: 15_000 });

test.describe.configure({ mode: "default" });

const TRANSMON_TITLE = "Coherence limits of fixed-frequency transmon qubits (synthetic)";
const TELEPORT_TITLE = "A compact teleportation circuit with mid-circuit measurement (synthetic)";

async function openAsk(page: Page) {
  await page.goto("/login");
  await signIn(page, DANI);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Ask" }).click();
  await expect(page.getByRole("heading", { name: "Ask", level: 1 })).toBeVisible();
}

async function ask(page: Page, question: string) {
  await page.getByLabel("Your question").fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}

/** Asks and waits for the answer to be on the screen. */
async function askAndWait(page: Page, question: string) {
  await ask(page, question);
  await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();
}

function sources(page: Page) {
  return page.getByRole("region", { name: "Sources" });
}

function steps(page: Page) {
  return page.getByRole("list", { name: "Progress of your question" });
}

test.describe("A1: ask and get an answer with cited sources", () => {
  test("shows the phases in order and then the answer", async ({ page }) => {
    await openAsk(page);
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeDisabled();
    await ask(page, "What limits T1 in transmons?");

    const current = steps(page).locator("li[aria-current='step']");
    // The fake engine pauses between its phases, so each one stays on screen for a moment.
    await expect(current).toContainText("Searching the papers");
    await expect(current).toContainText("Writing the answer");
    await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();
    await expect(steps(page)).toHaveCount(0);

    await expect(page.getByText(/limited mostly by dielectric loss/)).toBeVisible();
    const chips = sources(page).getByRole("button");
    await expect(chips.first()).toBeVisible();
    expect(await chips.count()).toBeGreaterThan(0);
    await expect(chips.first()).toHaveText(/[0-9a-f]{16}\s*pages? \d/);
    await expect(page.locator(".markdown .katex").first()).toBeVisible();
  });

  test("sends with the keyboard, and refuses an empty question", async ({ page }) => {
    await openAsk(page);
    const box = page.getByLabel("Your question");
    await box.fill("   ");
    await box.press("Control+Enter");
    await expect(page.getByText("Write a question to send it.")).toBeVisible();
    await expect(steps(page)).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toHaveCount(0);

    await box.fill("surface code threshold");
    await box.press("Control+Enter");
    await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();
    await expect(page.getByRole("note")).toContainText("not in the index");
  });

  test("answers in Spanish with Spanish labels", async ({ page }) => {
    await openAsk(page);
    await page.getByRole("button", { name: /Language/ }).click();
    await page.getByRole("menuitemradio", { name: "Español" }).click();
    await page.getByLabel("Tu pregunta").fill("¿Qué limita la coherencia de los transmones?");
    await page.getByRole("button", { name: "Preguntar" }).click();
    await expect(page.getByRole("heading", { name: "Respuesta", level: 2 })).toBeVisible();
    await expect(page.getByText(/pérdidas dieléctricas/)).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Fuentes" }).getByRole("button").first(),
    ).toContainText(/página/);
  });

  test("says when the papers have nothing to answer with", async ({ page }) => {
    await openAsk(page);
    await askAndWait(page, "How do octopuses taste?");
    await expect(page.getByText("This answer cites no paper.")).toBeVisible();
  });
});

test.describe("A2: read the cited paper at its page", () => {
  test("opens the panel at the cited page, pages through it and closes with Escape", async ({
    page,
  }) => {
    await openAsk(page);
    await askAndWait(page, "What limits T1 in transmons?");
    const chip = sources(page).getByRole("button").first();
    await chip.click();

    const panel = page.getByRole("dialog", { name: TRANSMON_TITLE });
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("status")).toContainText(/Page \d+ of \d+/);
    await expect(panel.locator(".katex").first()).toBeVisible();
    const before = await panel.getByRole("status").innerText();

    const next = panel.getByRole("button", { name: "Next page" });
    await expect(next).toBeEnabled();
    await next.click();
    await expect(panel.getByRole("status")).not.toHaveText(before);
    await panel.getByRole("button", { name: "Previous page" }).click();
    await expect(panel.getByRole("status")).toHaveText(before);

    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    await expect(chip).toBeFocused();
  });

  test("opens from a citation inside the text", async ({ page }) => {
    await openAsk(page);
    await askAndWait(page, "What limits T1 in transmons?");
    const inline = page.locator(".markdown button[data-opener]").first();
    await inline.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("dialog")).toBeHidden();
    await expect(inline).toBeFocused();
  });

  test("says section for a LaTeX source", async ({ page }) => {
    await openAsk(page);
    await askAndWait(page, "teleportation circuit");
    const chip = sources(page).getByRole("button").first();
    await expect(chip).toContainText(/section/);
    await chip.click();
    const panel = page.getByRole("dialog", { name: TELEPORT_TITLE });
    await expect(panel.getByRole("status")).toContainText(/Section \d+ of 3/);
    await expect(panel.getByRole("button", { name: "Next section" })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Previous section" })).toBeVisible();
  });

  test("works from the keyboard alone", async ({ page }) => {
    await openAsk(page);
    await askAndWait(page, "Grover search noisy oracle");
    const chip = sources(page).getByRole("button").first();
    await chip.focus();
    await page.keyboard.press("Enter");
    const panel = page.getByRole("dialog");
    await expect(panel).toBeVisible();
    // focus is inside the panel, and Tab stays there
    await page.keyboard.press("Tab");
    await expect(panel.locator(":focus")).toHaveCount(1);
    await page.keyboard.press("Escape");
    await expect(chip).toBeFocused();
  });
});

test.describe("A3: see what the model read", () => {
  test("lists the passages, collapsed until asked for, and opens each at its page", async ({
    page,
  }) => {
    await openAsk(page);
    await askAndWait(page, "What limits T1 in transmons?");
    const show = page.getByRole("button", { name: "Show the passages" });
    await expect(show).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("Passage 1")).toBeHidden();

    await show.click();
    await expect(page.getByRole("button", { name: "Hide the passages" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await expect(page.getByText("Passage 1")).toBeVisible();
    await expect(page.getByText("Passage 2")).toBeVisible();

    const open = page.getByRole("button", { name: /^Open the paper at/ }).first();
    await open.click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(open).toBeFocused();
  });
});

test.describe("A4: options for one question", () => {
  test("the request carries the options and defaults send nothing", async ({ page }) => {
    await openAsk(page);
    const bodies: Record<string, unknown>[] = [];
    await page.route("**/api/v1/asks", async (route) => {
      const request = route.request();
      if (request.method() === "POST") {
        bodies.push(request.postDataJSON() as Record<string, unknown>);
      }
      await route.continue();
    });

    await askAndWait(page, "What limits T1 in transmons?");
    expect(bodies[0]).toEqual({
      question: "What limits T1 in transmons?",
      use_index: true,
      bm25: null,
      whole_blocks: null,
      return_contexts: true,
    });

    const trigger = page.getByRole("button", { name: "Options for this question" });
    await trigger.click();
    await expect(
      page.getByRole("radio", { name: "Server setting (default)" }).first(),
    ).toBeChecked();
    await page.getByRole("radio", { name: "Always" }).click();
    await page
      .getByRole("radiogroup", { name: "Complete equations and tables" })
      .getByRole("radio", { name: "Yes" })
      .click();
    await page.keyboard.press("Escape");
    await expect(trigger).toContainText("2 changed");

    await askAndWait(page, "surface code threshold");
    expect(bodies[1]).toMatchObject({ bm25: "always", whole_blocks: true, use_index: true });
  });

  test("an answer without the index has no passages and no search step", async ({ page }) => {
    await openAsk(page);
    await page.getByRole("button", { name: "Options for this question" }).click();
    await page.getByRole("switch", { name: "Search with the index" }).click();
    await page.keyboard.press("Escape");
    await ask(page, "Grover search noisy oracle");
    await expect(steps(page).getByRole("listitem")).toHaveCount(2);
    await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();
    await expect(page.getByText("No passages came with this answer.")).toBeVisible();
    await expect(page.getByText(/without the index/)).toBeVisible();
  });
});

const READY = {
  mode: "fake",
  api_version: "0.1.0",
  started_at: "2026-01-01T00:00:00Z",
  uptime_s: 12.5,
  ready: true,
};

async function engineState(page: Page, ask: Record<string, unknown>) {
  await page.route("**/api/v1/status", (route) =>
    route.fulfill({
      status: 200,
      json: {
        ...READY,
        ask: {
          state: "ready",
          reason: null,
          index_generation: null,
          queued: 0,
          busy: false,
          ...ask,
        },
      },
    }),
  );
}

test.describe("A5: when the server cannot answer", () => {
  test("a starting engine closes the form and says so", async ({ page }) => {
    await engineState(page, { state: "warming_up" });
    await openAsk(page);
    await expect(page.getByText("The engine is starting")).toBeVisible();
    await page.getByLabel("Your question").fill("A question");
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeDisabled();
    await expectNoAxeViolations(page);
  });

  test("an engine that reports a problem shows why and still takes the question", async ({
    page,
  }) => {
    await engineState(page, { state: "unavailable", reason: "The index could not be opened." });
    await openAsk(page);
    await expect(page.getByText(/The index could not be opened\./)).toBeVisible();
    await page.getByLabel("Your question").fill("surface code threshold");
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeEnabled();
    await expectNoAxeViolations(page);
  });

  test("a model server that fails during the ask is shown with a way to try again", async ({
    page,
  }) => {
    await openAsk(page);
    await ask(page, "simulate-unavailable please");
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("The service cannot answer right now");
    await expect(alert).toContainText("The model server did not answer");
    await expect(alert).toContainText("The service suggests waiting 5 s.");
    await expectNoAxeViolations(page);

    const again = alert.getByRole("button", { name: "Try again" });
    await expect(again).toBeEnabled();
    await again.click();
    await expect(alert).toContainText("The service cannot answer right now");
  });

  test("an upstream error is named as one", async ({ page }) => {
    await openAsk(page);
    await ask(page, "simulate-upstream please");
    await expect(page.getByRole("alert")).toContainText("A model server returned an error");
  });

  test("a refusal at the door (503) is shown with its message", async ({ page }) => {
    await openAsk(page);
    await page.route("**/api/v1/asks", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      return route.fulfill({
        status: 503,
        headers: { "content-type": "application/problem+json", "retry-after": "3" },
        json: {
          type: "about:blank",
          title: "Service Unavailable",
          status: 503,
          detail: "The engine is starting. Try again in a few seconds.",
          instance: "/api/v1/asks",
        },
      });
    });
    await ask(page, "What limits T1 in transmons?");
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("The engine is starting. Try again in a few seconds.");
    await expect(alert).toContainText("The service suggests waiting 3 s.");
  });
});

test.describe("what the stream does", () => {
  test("a stream the server refuses is recovered from the API", async ({ page }) => {
    await openAsk(page);
    let refused = 0;
    await page.route("**/api/v1/asks/*/events", async (route) => {
      refused += 1;
      if (refused === 1) return route.fulfill({ status: 500, body: "boom" });
      return route.continue();
    });
    await askAndWait(page, "What limits T1 in transmons?");
    expect(refused).toBeGreaterThanOrEqual(2);
  });

  test("cancelling stops the wait and lets another question go", async ({ page }) => {
    await openAsk(page);
    await ask(page, "What limits T1 in transmons?");
    const cancel = page.getByRole("button", { name: "Cancel question" });
    await expect(cancel).toBeEnabled();
    await cancel.click();
    await expect(page.getByText("You cancelled this question.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeEnabled();
    // the cancelled answer never shows up
    await page.waitForTimeout(2500);
    await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toHaveCount(0);
    await askAndWait(page, "surface code threshold");
  });

  test("the question keeps going when the screen is left", async ({ page }) => {
    await openAsk(page);
    await ask(page, "What limits T1 in transmons?");
    await page.getByRole("link", { name: "Benchmarks" }).click();
    await expect(page.getByRole("heading", { name: "Benchmarks", level: 1 })).toBeVisible();
    await page.getByRole("link", { name: "Ask", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();
  });
});

test.describe("the history of this tab", () => {
  test("keeps the questions, shows them again, and forgets them when signing out", async ({
    page,
  }) => {
    await openAsk(page);
    await askAndWait(page, "What limits T1 in transmons?");
    await page.getByLabel("Your question").fill("");
    await askAndWait(page, "teleportation circuit");
    const history = page.getByRole("region", { name: "This session" });
    await expect(history.getByRole("listitem")).toHaveCount(2);
    await history.getByRole("button", { name: /What limits T1/ }).click();
    await expect(page.getByText(/limited mostly by dielectric loss/)).toBeVisible();

    await page.getByRole("button", { name: /'s account$/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await signIn(page, DANI);
    await expect(page.getByRole("heading", { name: "Ask", level: 1 })).toBeVisible();
    await expect(page.getByText("Your questions will appear here.")).toBeVisible();
    await expect(page.getByLabel("Your question")).toHaveValue("");
  });
});

for (const colorScheme of ["light", "dark"] as const) {
  test.describe(`accessibility in the ${colorScheme} theme`, () => {
    test.use({ colorScheme });

    test("has no violations at rest, while waiting, with an answer, with the passages, and with the paper open", async ({
      page,
    }) => {
      await openAsk(page);
      await expectNoAxeViolations(page);

      await ask(page, "What limits T1 in transmons?");
      await expect(steps(page)).toBeVisible();
      await expectNoAxeViolations(page);

      await expect(page.getByRole("heading", { name: "Answer", level: 2 })).toBeVisible();
      await expectNoAxeViolations(page);

      await page.getByRole("button", { name: "Show the passages" }).click();
      await expect(page.getByText("Passage 1")).toBeVisible();
      await expectNoAxeViolations(page);

      await page.getByRole("button", { name: "Options for this question" }).click();
      await expect(page.getByRole("dialog", { name: "Options" })).toBeVisible();
      await expectNoAxeViolations(page);
      await page.keyboard.press("Escape");

      await sources(page).getByRole("button").first().click();
      await expect(page.getByRole("dialog", { name: TRANSMON_TITLE })).toBeVisible();
      await expect(page.locator("[role=dialog] .katex").first()).toBeVisible();
      await expectNoAxeViolations(page);
    });

    test("has no violations on a failure", async ({ page }) => {
      await openAsk(page);
      await ask(page, "simulate-unavailable please");
      await expect(page.getByRole("alert")).toBeVisible();
      await expectNoAxeViolations(page);
    });
  });
}

test.describe("a narrow screen", () => {
  test.use({ viewport: { width: 375, height: 760 } });

  test("does not scroll sideways, with an answer and with the paper open", async ({ page }) => {
    await openAsk(page);
    await askAndWait(page, "surface code threshold");
    const overflow = () =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
    expect(await overflow()).toBeLessThanOrEqual(0);
    await sources(page).getByRole("button").first().click();
    const panel = page.getByRole("dialog");
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box?.width ?? 0).toBeLessThanOrEqual(375);
    await expectNoAxeViolations(page);
  });
});
