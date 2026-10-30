import { readFileSync } from "node:fs";
import { expect, test, type Browser, type Page } from "@playwright/test";
import { expectNoAxeViolations } from "./fixtures";
import { apiOf, demoFile, md5, seedDraft, signInAs, type VersionAnswer } from "./sets-helpers";

// These tests run against the real API in fake mode, signed in with its seeded test accounts.
// The three demo sets are only read. A test that changes something makes its own copy of the demo
// draft (`seedDraft`) under a name of its own, so the tests never meet in one set and can run in
// parallel. The recorded answers of src/features/qsets/fixtures/captured.json are checked at the
// end against the API, so the unit tests cannot rest on answers that went out of date.

const ORIGIN = "http://127.0.0.1:4173";

async function rowIds(page: Page): Promise<string[]> {
  const links = page.getByRole("table", { name: /Items \d+ to \d+ of \d+/ }).getByRole("link");
  return links.allTextContents();
}

/** A second person in a browser of their own. */
async function secondPerson(browser: Browser, who: "beto" | "dani" | "ana", next: string) {
  const context = await browser.newContext({ baseURL: ORIGIN, reducedMotion: "reduce" });
  const page = await context.newPage();
  await signInAs(page, who, next);
  return { page, context };
}

test.describe("S1: browsing the sets and their items", () => {
  test("the catalog shows every version with its status, items, review progress and md5", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    await expect(page.getByRole("heading", { level: 1, name: "Question sets" })).toBeVisible();
    const frozen = page
      .getByRole("row")
      .filter({ has: page.getByRole("link", { name: "demo-papers v1" }) });
    await expect(frozen).toContainText("Frozen");
    await expect(frozen).toContainText("6");
    await expect(frozen).toContainText("cb9cb733");
    await expect(frozen).toContainText("demo-corpus");
    const draft = page
      .getByRole("row")
      .filter({ has: page.getByRole("link", { name: "demo-papers v2" }) });
    await expect(draft).toContainText("Draft");
    await expect(draft).toContainText("4 of 7 decided");
    await expect(draft).toContainText("beto");
    await page.getByRole("link", { name: "demo-papers v2" }).click();
    await expect(page).toHaveURL(/\/sets\/demo-papers\/v2$/);
    await expect(page.getByRole("heading", { level: 1, name: "demo-papers v2" })).toBeFocused();
    await expect(page.getByTestId("version-md5")).toHaveText("08ff57f82af04a41374b1f145e868a6b");
  });

  test("filters by form, content, language, split, tag, review state and text, and keeps them in the address", async ({
    page,
  }) => {
    await signInAs(page, "ana", "/sets/demo-papers/v2");
    await expect(page.getByRole("table", { name: "Items 1 to 7 of 7" })).toBeVisible();

    await page.getByLabel("Review", { exact: true }).selectOption("needs_second");
    await expect(page).toHaveURL(/\?review=needs_second$/);
    await expect.poll(() => rowIds(page)).toEqual(["DP-007"]);

    await page.getByLabel("Review", { exact: true }).selectOption("");
    await page.getByLabel("Form", { exact: true }).selectOption("single");
    await page.getByLabel("Split", { exact: true }).selectOption("dev");
    await expect.poll(() => rowIds(page)).toEqual(["DP-001", "DP-001-es", "DP-004"]);
    await page.getByLabel("Language", { exact: true }).selectOption("es");
    await expect.poll(() => rowIds(page)).toEqual(["DP-001-es"]);

    // The address holds the filters: a reload and a copy of the link show the same list.
    await page.reload();
    await expect(page.getByLabel("Language", { exact: true })).toHaveValue("es");
    await expect.poll(() => rowIds(page)).toEqual(["DP-001-es"]);

    await page.getByRole("button", { name: "Clear filters" }).click();
    await expect(page).toHaveURL(/\/sets\/demo-papers\/v2$/);
    await page.getByLabel("Content", { exact: true }).selectOption("circuit");
    await expect(page.getByText("No item matches these filters.")).toBeVisible();
    await page.getByRole("button", { name: "Clear filters" }).click();

    await page.getByLabel("Text", { exact: true }).fill("Beta");
    await page.getByLabel("Tag", { exact: true }).fill("category=chain");
    await page.getByRole("button", { name: "Search" }).click();
    await expect.poll(() => rowIds(page)).toEqual(["DP-002"]);
    await expect(page).toHaveURL(/q=Beta/);
    await expect(page).toHaveURL(/tag=category%3Dchain/);
  });

  test("pages through a long set", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page, { count: 120 });
    await page.goto(`/sets/${set.name}/v1`);
    await expect(page.getByRole("table", { name: "Items 1 to 50 of 120" })).toBeVisible();
    await page.getByRole("button", { name: "Next page" }).click();
    await expect(page.getByRole("table", { name: "Items 51 to 100 of 120" })).toBeVisible();
    await expect(page).toHaveURL(/offset=50$/);
    await page.getByRole("button", { name: "Next page" }).click();
    await expect(page.getByRole("table", { name: "Items 101 to 120 of 120" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Next page" })).toBeDisabled();
    await page.getByLabel("Text", { exact: true }).fill("B-077");
    await page.getByRole("button", { name: "Search" }).click();
    await expect(page.getByRole("table", { name: "Items 1 to 1 of 1" })).toBeVisible();
  });

  test("a version that does not exist says so", async ({ page }) => {
    await signInAs(page, "ana", "/sets/nothing-here/v1");
    await expect(page.getByText("Not found", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Back to the question sets" })).toBeVisible();
  });
});

test.describe("S2: editing an item, with the checks of qset/1", () => {
  test("saves a change, and it is there after a reload and in the API", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Question").fill("Which paper reports a relaxation time above an hour?");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved.", { exact: true })).toBeVisible();
    const stored = await set.api.json(`/sets/${set.name}/versions/v1/items/DP-003`);
    expect(stored.item.question).toBe("Which paper reports a relaxation time above an hour?");
    await page.reload();
    await expect(page.getByLabel("Question")).toHaveValue(
      "Which paper reports a relaxation time above an hour?",
    );
    await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  test("shows the fields that the form kind asks for", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-002`);
    await expect(page.getByLabel("Form", { exact: true })).toHaveValue("multi_chain");
    await expect(page.getByLabel("Necessity (JSON)")).toBeVisible();
    await expect(page.getByLabel("Statement")).toHaveValue(
      "Beta generalizes the coupling defined in Alpha.",
    );
    await page.goto(`/sets/${set.name}/v1/items/DP-004`);
    await expect(page.getByLabel("Necessity (JSON)")).toHaveCount(0);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await expect(page.getByLabel("Search record (JSON)")).toBeVisible();
  });

  test("shows each problem at its field, and saves nothing", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-004`);
    await page.getByLabel("Form", { exact: true }).selectOption("multi_chain");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("The item is not a valid qset/1 item.")).toBeVisible();
    await expect(page.getByLabel("Necessity (JSON)")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText("'necessity' is a required property").first()).toBeVisible();
    await expect(page.getByText("'synthesis' is a required property").first()).toBeVisible();
    const stored = await set.api.json(`/sets/${set.name}/versions/v1/items/DP-004`);
    expect(stored.item.form).toBe("single");
    await expectNoAxeViolations(page);

    // Filling the parts in makes it valid.
    await page.getByLabel("Kind").selectOption("chain");
    await page.getByLabel("Statement").fill("Beta follows Alpha.");
    await page.getByLabel("Rubric").fill("Names both papers.");
    await page.getByLabel("Necessity (JSON)").fill(JSON.stringify({ ["a1".repeat(32)]: [0] }));
    await page.getByRole("button", { name: "Check against qset/1" }).click();
    // A multi form needs two gold papers and this item has one: the check says so at that field.
    await expect(page.getByLabel("Gold papers (JSON)")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByLabel("Necessity (JSON)")).not.toHaveAttribute("aria-invalid", "true");
  });

  test("checks an item against qset/1 without saving it", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Question").fill("Changed but not saved");
    await page.getByRole("button", { name: "Check against qset/1" }).click();
    await expect(page.getByText(/valid qset\/1 item. Nothing was saved/)).toBeVisible();
    const stored = await set.api.json(`/sets/${set.name}/versions/v1/items/DP-003`);
    expect(stored.item.question).not.toBe("Changed but not saved");
  });

  test("edits the whole item as JSON and stops while the text cannot be read", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByRole("tab", { name: "JSON" }).click();
    const box = page.getByLabel("The whole item as JSON");
    const item = JSON.parse(await box.inputValue()) as Record<string, unknown>;
    await box.fill("{ not json");
    await expect(page.getByText(/Not valid JSON/).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await box.fill(JSON.stringify({ ...item, notes: "Written in the JSON tab" }));
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved.", { exact: true })).toBeVisible();
    const stored = await set.api.json(`/sets/${set.name}/versions/v1/items/DP-003`);
    expect(stored.item.notes).toBe("Written in the JSON tab");
  });

  test("two people editing the same draft: the second save is told it changed", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Question").fill("My edit of the question");

    // Someone else saves another item of the same draft in the meantime.
    const other = (await set.api.json(`/sets/${set.name}/versions/v1/items/DP-005`)).item;
    const etag = await set.api.etag(set.name, "v1");
    const changed = await set.api.send("PUT", `/sets/${set.name}/versions/v1/items/DP-005`, {
      ifMatch: etag,
      data: { ...other, notes: "someone else" },
    });
    expect(changed.status()).toBe(200);

    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "Changed since you opened it" }),
    ).toBeVisible();
    // Nothing of the second save was applied.
    let stored = await set.api.json(`/sets/${set.name}/versions/v1/items/DP-003`);
    expect(stored.item.question).not.toBe("My edit of the question");

    await page.getByRole("button", { name: "Keep my edits" }).click();
    await expect(page.getByText(/Your edits are kept; save again/)).toBeVisible();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved.", { exact: true })).toBeVisible();
    stored = await set.api.json(`/sets/${set.name}/versions/v1/items/DP-003`);
    expect(stored.item.question).toBe("My edit of the question");
  });

  test("loading the latest drops the edits", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    const before = await page.getByLabel("Question").inputValue();
    await page.getByLabel("Question").fill("Will be dropped");
    const other = (await set.api.json(`/sets/${set.name}/versions/v1/items/DP-005`)).item;
    await set.api.send("PUT", `/sets/${set.name}/versions/v1/items/DP-005`, {
      ifMatch: await set.api.etag(set.name, "v1"),
      data: { ...other, notes: "someone else" },
    });
    await page.getByRole("button", { name: "Save changes" }).click();
    await page.getByRole("button", { name: "Load the latest" }).click();
    await expect(page.getByLabel("Question")).toHaveValue(before);
    await expect(page.getByText("Changed since you opened it")).toHaveCount(0);
  });

  test("someone changes the same item: their change is kept next to mine, and both are saved", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const path = `/sets/${set.name}/versions/v1/items/DP-003`;
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Notes", { exact: true }).fill("My note on the item");

    // Someone else rewrites the question of the same item.
    const stored = (await set.api.json(path)).item;
    const rewritten = await set.api.send("PUT", path, {
      ifMatch: await set.api.etag(set.name, "v1"),
      data: { ...stored, question: "The owner rewrote this question" },
    });
    expect(rewritten.status()).toBe(200);

    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "Changed since you opened it" }),
    ).toBeVisible();
    await expectNoAxeViolations(page);
    await page.getByRole("button", { name: "Keep my edits" }).click();
    await expect(page.getByText("Merged with the latest version")).toBeVisible();
    await expect(page.getByLabel("Question")).toHaveValue("The owner rewrote this question");
    await expect(page.getByLabel("Notes", { exact: true })).toHaveValue("My note on the item");
    await expectNoAxeViolations(page);

    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved.", { exact: true })).toBeVisible();
    const after = (await set.api.json(path)).item;
    expect(after.question).toBe("The owner rewrote this question");
    expect(after.notes).toBe("My note on the item");
  });

  test("a field both people changed is listed with both values, and mine is kept", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const path = `/sets/${set.name}/versions/v1/items/DP-003`;
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Notes", { exact: true }).fill("Ana's note");
    const stored = (await set.api.json(path)).item;
    await set.api.send("PUT", path, {
      ifMatch: await set.api.etag(set.name, "v1"),
      data: { ...stored, notes: "The other note" },
    });
    await page.getByRole("button", { name: "Save changes" }).click();
    await page.getByRole("button", { name: "Keep my edits" }).click();
    await expect(page.getByText("Yours (kept): Ana's note")).toBeVisible();
    await expect(page.getByText("Latest on the server: The other note")).toBeVisible();
    await expect(page.getByLabel("Notes", { exact: true })).toHaveValue("Ana's note");
    await page.getByRole("button", { name: "Use the latest value of notes" }).click();
    await expect(page.getByLabel("Notes", { exact: true })).toHaveValue("The other note");
  });

  test("a read under my edits stops the save instead of putting the old text back", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const path = `/sets/${set.name}/versions/v1/items/DP-003`;
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Notes", { exact: true }).fill("Typed before the reconnect");
    const stored = (await set.api.json(path)).item;
    await set.api.send("PUT", path, {
      ifMatch: await set.api.etag(set.name, "v1"),
      data: { ...stored, question: "Changed while the network was away" },
    });
    // The browser comes back online: the queries read again.
    await page.context().setOffline(true);
    await page.context().setOffline(false);
    await expect(page.getByText("This item changed while you were editing")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await expectNoAxeViolations(page);
    await page.getByRole("button", { name: "Merge with my edits" }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved.", { exact: true })).toBeVisible();
    const after = (await set.api.json(path)).item;
    expect(after.question).toBe("Changed while the network was away");
    expect(after.notes).toBe("Typed before the reconnect");
  });

  test("focus moves to the first field with a problem after a refused save", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Question").fill("");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("The item is not a valid qset/1 item.")).toBeVisible();
    await expect(page.getByLabel("Question")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByLabel("Question")).toBeFocused();
    // A check does the same.
    await page.getByLabel("Notes", { exact: true }).focus();
    await page.getByRole("button", { name: "Check against qset/1" }).click();
    await expect(page.getByLabel("Question")).toBeFocused();
  });

  test("leaving with edits that are not saved asks first, and staying keeps them", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Notes", { exact: true }).fill("Not saved yet");
    await page.getByRole("link", { name: `Back to ${set.name} v1` }).click();
    await expect(page.getByText("You have unsaved edits")).toBeVisible();
    await expect(page).toHaveURL(/items\/DP-003$/);
    await page.getByRole("button", { name: "Stay and keep editing" }).click();
    await expect(page.getByLabel("Notes", { exact: true })).toHaveValue("Not saved yet");
    await page.getByRole("link", { name: `Back to ${set.name} v1` }).click();
    await page.getByRole("button", { name: "Leave and drop my edits" }).click();
    await expect(page).toHaveURL(new RegExp(`/sets/${set.name}/v1$`));
  });

  test("text typed while a save is on its way stays as an unsaved change", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const path = `/sets/${set.name}/versions/v1/items/DP-003`;
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Notes", { exact: true }).fill("Saved note");
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**${path}`, async (route) => {
      if (route.request().method() === "PUT") await gate;
      await route.continue();
    });
    await page.getByRole("button", { name: "Save changes" }).click();
    await page.getByLabel("Rationale", { exact: true }).fill("Typed while the save was on its way");
    release();
    await expect(page.getByText("Saved.", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Rationale", { exact: true })).toHaveValue(
      "Typed while the save was on its way",
    );
    await expect(page.getByText("This item changed while you were editing")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save changes" })).toBeEnabled();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect
      .poll(async () => (await set.api.json(path)).item.rationale)
      .toBe("Typed while the save was on its way");
    expect((await set.api.json(path)).item.notes).toBe("Saved note");
  });

  test("a box that cannot be read is an edit: a remote change does not rebuild over it", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const path = `/sets/${set.name}/versions/v1/items/DP-003`;
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    const box = page.getByLabel("Gold papers (JSON)", { exact: true });
    const before = await box.inputValue();
    await box.fill(`${before.slice(0, before.length - 3)}, "WORK IN PROGRESS`);
    await expect(page.getByText(/cannot be used as written/).first()).toBeVisible();
    const stored = (await set.api.json(path)).item;
    await set.api.send("PUT", path, {
      ifMatch: await set.api.etag(set.name, "v1"),
      data: { ...stored, question: "Changed by someone else" },
    });
    await page.context().setOffline(true);
    await page.context().setOffline(false);
    await expect(page.getByText("This item changed while you were editing")).toBeVisible();
    await expect(page.getByText("This item changed while you had it open")).toHaveCount(0);
    await expect(box).toHaveValue(/WORK IN PROGRESS/);
    await expect(page.getByRole("button", { name: "Merge with my edits" })).toBeDisabled();
    await expectNoAxeViolations(page);
    // One click drops the text and shows what the server holds.
    await page.getByRole("button", { name: "Load the latest and drop my edits" }).click();
    await expect(page.getByLabel("Question")).toHaveValue("Changed by someone else");
    await expect(box).not.toHaveValue(/WORK IN PROGRESS/);
  });

  test("leaving while a box cannot be read asks first", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    const box = page.getByLabel("Gold papers (JSON)", { exact: true });
    const before = await box.inputValue();
    await box.fill(`${before.slice(0, before.length - 3)}, "WORK IN PROGRESS`);
    await page.getByRole("link", { name: `Back to ${set.name} v1` }).click();
    await expect(page.getByText("You have unsaved edits")).toBeVisible();
    await expect(page).toHaveURL(/items\/DP-003$/);
  });

  test("an address past the end of the list says so and leads to the last page", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page, { count: 120 });
    await page.goto(`/sets/${set.name}/v1?offset=99999`);
    await expect(page.getByText(/starts at item 100000, but the list has only 120/)).toBeVisible();
    await expect(page.getByText("No item matches these filters.")).toHaveCount(0);
    await page.getByRole("button", { name: "Go to the last page" }).click();
    await expect(page.getByRole("table", { name: "Items 101 to 120 of 120" })).toBeVisible();
  });

  test("the whole-item JSON tab names the field of every problem", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Question").fill("");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("The item is not a valid qset/1 item.")).toBeVisible();
    await page.getByRole("tab", { name: "JSON" }).click();
    const box = page.getByLabel("The whole item as JSON");
    await expect(box).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText(/^\/question: /)).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("adds an item and deletes it", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1`);
    await page.getByRole("link", { name: "Add item" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "New item" })).toBeVisible();
    await page.getByLabel("Id", { exact: true }).fill("NEW-1");
    await page.getByLabel("Question").fill("A question added in the browser?");
    await page.getByRole("button", { name: "Add the item" }).click();
    // gold_papers is empty and a single question needs no more, so qset/1 accepts it
    await expect(page).toHaveURL(new RegExp(`/items/NEW-1$`));
    await expect(page.getByRole("heading", { level: 1, name: "NEW-1" })).toBeVisible();
    const stored = await set.api.json(`/sets/${set.name}/versions/v1/items/NEW-1`);
    expect(stored.item.question).toBe("A question added in the browser?");

    await page.getByRole("button", { name: "Delete item" }).click();
    await page.getByRole("button", { name: "Yes, delete" }).click();
    await expect(page).toHaveURL(new RegExp(`/sets/${set.name}/v1$`));
    expect((await set.api.get(`/sets/${set.name}/versions/v1/items/NEW-1`)).status()).toBe(404);
  });

  test("a member who does not own the draft cannot save, and can still review", async ({
    page,
    browser,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const { page: dani, context } = await secondPerson(
      browser,
      "dani",
      `/sets/${set.name}/v1/items/DP-003`,
    );
    await expect(dani.getByRole("heading", { level: 1, name: "DP-003" })).toBeVisible();
    await expect(
      dani.getByText(/Only the owner \(ana\) or an admin can save changes/),
    ).toBeVisible();
    await expect(dani.getByRole("button", { name: "Save changes" })).toHaveCount(0);
    await expect(dani.getByRole("button", { name: "Delete item" })).toHaveCount(0);
    await expect(dani.getByRole("button", { name: "Accept" })).toBeEnabled();
    await context.close();
  });

  test("a frozen version cannot be edited", async ({ page }) => {
    await signInAs(page, "ana", "/sets/demo-papers/v1/items/DP-001");
    await expect(
      page.getByText("This version is frozen, so the item cannot change."),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Save changes" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Accept" })).toHaveCount(0);
  });
});

test.describe("S3: reviewing items", () => {
  test("an item needs two different people before it counts as gold", async ({ page, browser }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const path = `/sets/${set.name}/v1/items/DP-003`;
    await page.goto(path);
    await expect(page.getByText("Nobody has decided yet.")).toBeVisible();
    await page.getByRole("button", { name: "Accept" }).click();
    await expect(page.getByText("Recorded: accept.")).toBeVisible();
    await expect(
      page.getByText(/Accepted by ana. A different person must accept it too/),
    ).toBeVisible();

    // The same person accepting again is still one person.
    await page.getByRole("button", { name: "Accept" }).click();
    await expect(page.getByText("Recorded: accept.")).toBeVisible();
    await expect(
      page.getByText(/Accepted by ana. A different person must accept it too/),
    ).toBeVisible();
    await expect(page.getByText("Gold", { exact: true })).toHaveCount(0);

    const { page: dani, context } = await secondPerson(browser, "dani", path);
    await expect(
      dani.getByText(/Accepted by ana. A different person must accept it too/),
    ).toBeVisible();
    await dani.getByRole("button", { name: "Accept" }).click();
    await expect(dani.getByText("Accepted by ana, dani: two different people.")).toBeVisible();
    await expect(dani.getByText("Gold", { exact: true })).toBeVisible();
    await expect(
      dani.getByRole("list", { name: "Decisions" }).getByRole("listitem").first(),
    ).toBeVisible();
    await context.close();

    await page.goto(`/sets/${set.name}/v1?review=accepted`);
    await expect.poll(() => rowIds(page)).toEqual(["DP-003"]);
  });

  test("a rejection or an edit needs a note", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByRole("button", { name: "Reject" }).click();
    await expect(page.getByText("Write a note: an edit or a reject needs one.")).toBeVisible();
    await expect(page.getByLabel("Note", { exact: true })).toBeFocused();
    await page.getByLabel("Note", { exact: true }).fill("The paper does not say this.");
    await page.getByRole("button", { name: "Reject" }).click();
    await expect(page.getByText("Recorded: reject.")).toBeVisible();
    await expect(
      page.getByText("The latest decision is a rejection by ana; freezing removes this item."),
    ).toBeVisible();
    const history = page.getByRole("list", { name: "Decisions" });
    await expect(history).toContainText("The paper does not say this.");
  });

  test("proposes an edit with the fields that were changed and shows it in the history", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByRole("button", { name: "Propose edit" }).click();
    await expect(page.getByText("Write a note: an edit or a reject needs one.")).toBeVisible();
    await page.getByLabel("Answer").fill("There is no such paper in the corpus.");
    await expect(
      page.getByRole("heading", { name: "Changes an edit would propose" }),
    ).toBeVisible();
    await page.getByLabel("Note", { exact: true }).fill("Say it plainly.");
    await page.getByRole("button", { name: "Propose edit" }).click();
    await expect(page.getByText("Recorded: edit.")).toBeVisible();
    const history = page.getByRole("list", { name: "Decisions" });
    await expect(history).toContainText("Say it plainly.");
    await expect(history).toContainText("There is no such paper in the corpus.");
    // The draft itself did not change: an edit is applied at the freeze.
    const stored = await set.api.json(`/sets/${set.name}/versions/v1/items/DP-003`);
    expect(stored.item.answer).not.toBe("There is no such paper in the corpus.");
    expect(stored.review.state).toBe("edited");
  });

  test("a decision on a draft that moved is not recorded: the item is read again and the person decides again", async ({
    page,
    browser,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const { page: dani, context } = await secondPerson(
      browser,
      "dani",
      `/sets/${set.name}/v1/items/DP-003`,
    );
    await expect(dani.getByRole("button", { name: "Accept" })).toBeEnabled();

    // The owner saves another item while dani is reading.
    const other = (await set.api.json(`/sets/${set.name}/versions/v1/items/DP-005`)).item;
    await set.api.send("PUT", `/sets/${set.name}/versions/v1/items/DP-005`, {
      ifMatch: await set.api.etag(set.name, "v1"),
      data: { ...other, notes: "moved on" },
    });

    await dani.getByLabel("Note", { exact: true }).fill("Checked it twice.");
    await dani.getByRole("button", { name: "Accept" }).click();
    await expect(dani.getByText("The draft changed while you were reading")).toBeVisible();
    await expect(dani.getByLabel("Note", { exact: true })).toHaveValue("Checked it twice.");
    let stored = await set.api.json(`/sets/${set.name}/versions/v1/items/DP-003`);
    expect(stored.reviews).toHaveLength(0);

    await dani.getByRole("button", { name: "Accept" }).click();
    await expect(dani.getByText("Recorded: accept.")).toBeVisible();
    stored = await set.api.json(`/sets/${set.name}/versions/v1/items/DP-003`);
    expect(stored.reviews).toHaveLength(1);
    expect(stored.reviews[0].note).toBe("Checked it twice.");
    await context.close();
  });

  test("a decision on an item whose text moved is decided again on the new text", async ({
    page,
    browser,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const path = `/sets/${set.name}/versions/v1/items/DP-003`;
    const { page: dani, context } = await secondPerson(
      browser,
      "dani",
      `/sets/${set.name}/v1/items/DP-003`,
    );
    await expect(dani.getByRole("button", { name: "Accept" })).toBeEnabled();
    const oldQuestion = await dani.getByLabel("Question").inputValue();

    // The owner rewrites the question of this very item while dani is reading it.
    const stored = (await set.api.json(path)).item;
    await set.api.send("PUT", path, {
      ifMatch: await set.api.etag(set.name, "v1"),
      data: { ...stored, question: "THE OWNER REWROTE THIS QUESTION" },
    });

    await dani.getByLabel("Note", { exact: true }).fill("Checked it twice.");
    await dani.getByRole("button", { name: "Accept" }).click();
    await expect(dani.getByText("The draft changed while you were reading")).toBeVisible();
    // Nothing was recorded, and dani now sees the text that a decision would be about.
    expect((await set.api.json(path)).reviews).toHaveLength(0);
    await expect(dani.getByLabel("Question")).toHaveValue("THE OWNER REWROTE THIS QUESTION");
    await expect(dani.getByLabel("Question")).not.toHaveValue(oldQuestion);
    await expect(dani.getByText("This item changed while you had it open")).toBeVisible();
    await expect(dani.getByLabel("Note", { exact: true })).toHaveValue("Checked it twice.");
    await expectNoAxeViolations(dani);

    await dani.getByRole("button", { name: "Accept" }).click();
    await expect(dani.getByText("Recorded: accept.")).toBeVisible();
    const after = await set.api.json(path);
    expect(after.reviews).toHaveLength(1);
    expect(after.reviews[0].set_md5).toBe(after.version.md5);
    await context.close();
  });

  test("an item with decisions keeps its text: the person is led to an edit decision", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await set.api.review(set.name, "v1", "DP-003", "accept");
    await page.goto(`/sets/${set.name}/v1/items/DP-003`);
    await page.getByLabel("Question").fill("Reworded after it was accepted?");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("This item has review decisions")).toBeVisible();
    await expect(page.getByText("Changed since you opened it")).toHaveCount(0);
    await page.getByRole("button", { name: "Propose these changes as an edit decision" }).click();
    await expect(page.getByLabel("Note", { exact: true })).toBeFocused();
    await page.getByLabel("Note", { exact: true }).fill("Clearer.");
    await page.getByRole("button", { name: "Propose edit" }).click();
    await expect(page.getByText("Recorded: edit.")).toBeVisible();
    await expect(page.getByRole("list", { name: "Decisions" })).toContainText(
      "Reworded after it was accepted?",
    );
  });
});

test.describe("S4: import and export", () => {
  test("imports a qset/1 file as a new draft and reports the items read", async ({ page }) => {
    await signInAs(page, "ana");
    const text = await demoFile(page);
    const name = `e2e-import-${Date.now()}`;
    await page.goto("/sets");
    await page.getByLabel("File", { exact: true }).setInputFiles({
      name: "mine.qset.jsonl",
      mimeType: "application/x-ndjson",
      buffer: Buffer.from(text),
    });
    await page.getByLabel("Set name").fill(name);
    await page.getByRole("button", { name: "Import" }).click();
    await expect(page.getByText(`Read 7 items into ${name} v1`)).toBeVisible();
    await page.getByRole("link", { name: "Open the new draft" }).click();
    await expect(page.getByRole("table", { name: "Items 1 to 7 of 7" })).toBeVisible();
    await expect(page.getByText("ana", { exact: true }).first()).toBeVisible();
  });

  test("refuses a file whole and lists the lines at fault", async ({ page }) => {
    await signInAs(page, "ana");
    const good = await demoFile(page);
    const lines = good.split("\n");
    const send = async (text: string) => {
      await page.getByLabel("File", { exact: true }).setInputFiles({
        name: "broken.jsonl",
        mimeType: "application/x-ndjson",
        buffer: Buffer.from(text),
      });
      await page.getByLabel("Set name").fill(`e2e-broken-${Date.now()}`);
      await page.getByRole("button", { name: "Import" }).click();
    };
    await page.goto("/sets");
    // A line that is not JSON is named by its number...
    const notJson = [...lines];
    notJson[2] = "this is not json";
    await send(notJson.join("\n"));
    await expect(page.getByText("The file is not a valid qset/1 file.")).toBeVisible();
    await expect(page.getByText("/lines/3", { exact: true })).toBeVisible();
    await expect(page.getByText(/not valid JSON/)).toBeVisible();
    await expect(page.getByText(/The file was not imported/)).toBeVisible();
    // ...and a field that qset/1 refuses by its pointer.
    const badLanguage = [...lines];
    badLanguage[3] = JSON.stringify({ ...JSON.parse(lines[3]!), lang: "fr" });
    await send(badLanguage.join("\n"));
    await expect(page.getByText("/lines/4/lang", { exact: true })).toBeVisible();
    await expect(page.getByText(/'fr' is not one of/)).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("converts a file of an older shape", async ({ page }) => {
    await signInAs(page, "ana");
    const row = JSON.stringify({
      id: "M-1",
      question: "What does the paper define?",
      answer: "A coupling.",
      paper_id: "a1".repeat(32),
      category: "notation",
    });
    await page.goto("/sets");
    const section = page.getByRole("region", { name: "Import a file" });
    await section.getByLabel("Kind of file").selectOption("mathcirc-v1");
    await section.getByLabel("Corpus").fill("demo-corpus");
    await section.getByLabel("File", { exact: true }).setInputFiles({
      name: "old.jsonl",
      mimeType: "application/x-ndjson",
      buffer: Buffer.from(`${row}\n`),
    });
    await section.getByLabel("Set name").fill(`e2e-old-${Date.now()}`);
    await section.getByRole("button", { name: "Import" }).click();
    // The converter's own rules decide: either the rows convert or the page lists why not.
    await expect(
      page.getByText(/Read 1 item into|The file was not imported/).first(),
    ).toBeVisible();
  });

  test("exports the file as stored: its md5 is the md5 shown", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    await page.goto(`/sets/${set.name}/v1`);
    const shown = (await page.getByTestId("version-md5").textContent())!.trim();
    expect(shown).toBe(set.md5);
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Export file" }).click(),
    ]);
    expect(download.suggestedFilename()).toBe(`${set.name}-v1.qset.jsonl`);
    const path = await download.path();
    const bytes = readFileSync(path);
    expect(md5(bytes)).toBe(shown);
    await expect(
      page.getByText(new RegExp(`Saved ${set.name}-v1.qset.jsonl.*${shown}`)),
    ).toBeVisible();
    const fromApi = await (await set.api.get(`/sets/${set.name}/versions/v1/export`)).body();
    expect(Buffer.compare(bytes, fromApi)).toBe(0);
  });

  test("an exported file imports again with the same items", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await seedDraft(page);
    const exported = await (await set.api.get(`/sets/${set.name}/versions/v1/export`)).text();
    const again = `e2e-again-${Date.now()}`;
    await page.goto("/sets");
    await page.getByLabel("File", { exact: true }).setInputFiles({
      name: "again.jsonl",
      mimeType: "application/x-ndjson",
      buffer: Buffer.from(exported),
    });
    await page.getByLabel("Set name").fill(again);
    await page.getByRole("button", { name: "Import" }).click();
    await expect(page.getByText(`Read ${set.items} items into ${again} v1`)).toBeVisible();
    const items = (answer: VersionAnswer) => answer.items.map((item) => item.id);
    const first = await set.api.json<VersionAnswer>(`/sets/${set.name}/versions/v1?limit=200`);
    const second = await set.api.json<VersionAnswer>(`/sets/${again}/versions/v1?limit=200`);
    expect(items(second)).toEqual(items(first));
  });
});

test.describe("S5: freezing a version", () => {
  async function decide(page: Page) {
    const set = await seedDraft(page);
    await set.api.review(set.name, "v1", "DP-001", "accept");
    await set.api.review(set.name, "v1", "DP-004", "reject", { note: "Too easy." });
    await set.api.review(set.name, "v1", "DP-002", "edit", {
      note: "Clearer.",
      edit: { answer: "Beta replaces J by a tuple of three couplings." },
    });
    return set;
  }

  test("shows what freezing does, freezes, and the frozen version cannot change", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await decide(page);
    await page.goto(`/sets/${set.name}/v1/freeze`);
    await expect(
      page.getByText("The draft has 7 items; the frozen version will have 6."),
    ).toBeVisible();
    const group = (title: string) => page.locator("dt", { hasText: title }).locator("..");
    await expect(group("Accepted (kept) (1)")).toContainText("DP-001");
    await expect(group("Rejected (removed) (1)")).toContainText("DP-004");
    await expect(group("Edit proposed (the edit is applied) (1)")).toContainText("DP-002");
    await expect(group("Not reviewed (kept as they are) (4)")).toContainText("DP-003");
    await expectNoAxeViolations(page);

    await page.getByRole("button", { name: "Freeze this version" }).click();
    await expect(page.getByText(`Freeze ${set.name} v1 for good?`)).toBeVisible();
    await page.getByRole("button", { name: "Yes, freeze" }).click();
    await expect(page.getByText(`${set.name} v1 is frozen`)).toBeVisible();
    const frozenMd5 = (await page.getByTestId("frozen-md5").textContent())!.trim();
    expect(frozenMd5).toMatch(/^[0-9a-f]{32}$/);
    expect(frozenMd5).not.toBe(set.md5);

    // The version is frozen in the API, with that md5, and the file is byte for byte that md5.
    const current = await set.api.json<VersionAnswer>(`/sets/${set.name}/versions/v1?limit=200`);
    expect(current.version.status).toBe("frozen");
    expect(current.version.md5).toBe(frozenMd5);
    expect(current.items.map((item) => item.id)).not.toContain("DP-004");
    const file = await (await set.api.get(`/sets/${set.name}/versions/v1/export`)).body();
    expect(md5(file)).toBe(frozenMd5);

    // The screens close their editing actions.
    await page.goto(`/sets/${set.name}/v1`);
    await expect(page.getByText("Cannot change")).toBeVisible();
    await expect(page.getByTestId("version-md5")).toHaveText(frozenMd5);
    await expect(page.getByRole("link", { name: "Add item" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: /freeze/i })).toHaveCount(0);
    await page.getByRole("link", { name: "DP-002" }).click();
    await expect(
      page.getByText("This version is frozen, so the item cannot change."),
    ).toBeVisible();
    await expect(
      page
        .getByRole("definition")
        .filter({ hasText: "Beta replaces J by a tuple of three couplings." }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Save changes" })).toHaveCount(0);
    await page.goto(`/sets/${set.name}/v1/freeze`);
    await expect(page.getByText("This version is already frozen")).toBeVisible();

    // The API refuses any change, as the screens do not offer one.
    const refused = await set.api.send("PUT", `/sets/${set.name}/versions/v1/items/DP-003`, {
      ifMatch: `"${frozenMd5}"`,
      data: { id: "DP-003" },
    });
    expect(refused.status()).toBe(403);
  });

  test("the next version starts from the frozen one with no decisions", async ({ page }) => {
    await signInAs(page, "ana");
    const set = await decide(page);
    await page.goto(`/sets/${set.name}/v1/freeze`);
    await page.getByRole("button", { name: "Freeze this version" }).click();
    await page.getByRole("button", { name: "Yes, freeze" }).click();
    await expect(page.getByText(`${set.name} v1 is frozen`)).toBeVisible();
    await page.goto(`/sets/${set.name}/v1`);
    await page.getByRole("button", { name: "Start the next version" }).click();
    await expect(page).toHaveURL(new RegExp(`/sets/${set.name}/v2$`));
    await expect(page.getByRole("heading", { level: 1, name: `${set.name} v2` })).toBeVisible();
    await expect(page.getByRole("table", { name: "Items 1 to 6 of 6" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Add item" })).toBeVisible();
  });

  test("a decision made after the plan was read stops the freeze until the plan is read again", async ({
    page,
  }) => {
    await signInAs(page, "ana");
    const set = await decide(page);
    await page.goto(`/sets/${set.name}/v1/freeze`);
    await expect(
      page.getByText("The draft has 7 items; the frozen version will have 6."),
    ).toBeVisible();

    // Another person rejects one more item while the plan is open.
    await set.api.review(set.name, "v1", "DP-003", "reject", { note: "Not answerable." });

    await page.getByRole("button", { name: "Freeze this version" }).click();
    await page.getByRole("button", { name: "Yes, freeze" }).click();
    await expect(page.getByText("A decision or an item changed")).toBeVisible();
    expect((await set.api.json(`/sets/${set.name}/versions/v1?limit=1`)).version.status).toBe(
      "draft",
    );
    await page.getByRole("button", { name: "Read the plan again" }).click();
    await expect(
      page.getByText("The draft has 7 items; the frozen version will have 5."),
    ).toBeVisible();
    await page.getByRole("button", { name: "Freeze this version" }).click();
    await page.getByRole("button", { name: "Yes, freeze" }).click();
    await expect(page.getByText(`${set.name} v1 is frozen`)).toBeVisible();
  });

  test("another member reads the plan but cannot freeze", async ({ page, browser }) => {
    await signInAs(page, "ana");
    const set = await decide(page);
    const { page: dani, context } = await secondPerson(
      browser,
      "dani",
      `/sets/${set.name}/v1/freeze`,
    );
    await expect(
      dani.getByText("The draft has 7 items; the frozen version will have 6."),
    ).toBeVisible();
    await expect(dani.getByText("Only the owner or an admin freezes a draft")).toBeVisible();
    await expect(dani.getByRole("button", { name: "Freeze this version" })).toHaveCount(0);
    await context.close();
  });
});

test.describe("accessibility, language and small screens", () => {
  for (const colorScheme of ["light", "dark"] as const) {
    test.describe(`${colorScheme} theme`, () => {
      test.use({ colorScheme });

      test("the catalog, a version, an item, the plan and a new item have no violations", async ({
        page,
      }) => {
        await signInAs(page, "ana");
        await expect(page.getByRole("table", { name: /versions/ })).toBeVisible();
        await expectNoAxeViolations(page);
        await page.goto("/sets/demo-papers/v2");
        await expect(page.getByRole("table", { name: "Items 1 to 7 of 7" })).toBeVisible();
        await expectNoAxeViolations(page);
        await page.goto("/sets/demo-papers/v2/items/DP-002");
        await expect(page.getByRole("heading", { level: 1, name: "DP-002" })).toBeVisible();
        await expect(page.getByLabel("Question")).toBeVisible();
        await expectNoAxeViolations(page);
        await page.goto("/sets/demo-papers/v2/freeze");
        await expect(page.getByText(/The draft has 7 items/)).toBeVisible();
        await expectNoAxeViolations(page);
        await page.goto("/sets/demo-papers/v2/new-item");
        await expect(page.getByRole("heading", { level: 1, name: "New item" })).toBeVisible();
        await expectNoAxeViolations(page);
        await page.goto("/sets/demo-papers/v1/items/DP-001");
        await expect(
          page.getByText("This version is frozen, so the item cannot change."),
        ).toBeVisible();
        await expectNoAxeViolations(page);
      });
    });
  }

  test("the screens read in Spanish", async ({ page }) => {
    await signInAs(page, "ana");
    await page.evaluate(() => window.localStorage.setItem("re.lang", "es"));
    await page.reload();
    await expect(
      page.getByRole("heading", { level: 1, name: "Conjuntos de preguntas" }),
    ).toBeVisible();
    await expect(page.getByText("Conjuntos y versiones")).toBeVisible();
    await page.goto("/sets/demo-papers/v2/items/DP-002");
    await expect(page.getByRole("button", { name: "Guardar cambios" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Proponer edición" })).toBeVisible();
    await page.goto("/sets/demo-papers/v2/freeze");
    await expect(page.getByText(/El borrador tiene 7 elementos/)).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test("no page scrolls sideways at the width of a phone", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await signInAs(page, "ana");
    for (const path of [
      "/sets",
      "/sets/demo-papers/v2",
      "/sets/demo-papers/v2/items/DP-002",
      "/sets/demo-papers/v2/freeze",
    ]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, path).toBeLessThanOrEqual(0);
    }
  });

  test("the keyboard reaches an item from the list and a decision from the item", async ({
    page,
  }) => {
    await signInAs(page, "ana", "/sets/demo-papers/v2");
    await page.getByRole("link", { name: "DP-003" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/items\/DP-003$/);
    await expect(page.getByRole("heading", { level: 1, name: "DP-003" })).toBeFocused();
  });
});

test.describe("the recorded answers of the unit tests", () => {
  test("still equal what the API answers", async ({ page }) => {
    await signInAs(page, "ana");
    const recorded = JSON.parse(
      readFileSync(
        new URL("../src/features/qsets/fixtures/captured.json", import.meta.url),
        "utf8",
      ),
    ) as { name: string; method: string; path: string; status: number; response: unknown }[];
    const reads = recorded.filter(
      (entry) =>
        entry.method === "GET" &&
        entry.path.includes("demo-") &&
        entry.name !== "freeze demo-papers/v1",
    );
    expect(reads.length).toBeGreaterThan(10);
    const api = apiOf(page);
    for (const entry of reads) {
      const suffix = entry.name.startsWith("page ") ? "?limit=200" : "";
      const answer = await api.get(`${entry.path}${suffix}`);
      expect(answer.status(), entry.name).toBe(entry.status);
      expect(await answer.json(), entry.name).toEqual(entry.response);
    }
    const catalog = recorded.find((entry) => entry.name === "catalog")!;
    const now = (await (await api.get("/sets")).json()) as { name: string }[];
    const demo = (list: { name: string }[]) =>
      list.filter((entry) => entry.name.startsWith("demo-"));
    expect(demo(now)).toEqual(demo(catalog.response as { name: string }[]));
    const shapes = recorded.find((entry) => entry.name === "shapes")!;
    expect(await (await api.get("/sets/import/shapes")).json()).toEqual(shapes.response);
  });
});
