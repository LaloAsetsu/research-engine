import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedBody, meAs, problemBody } from "@/test/sets-api";
import { renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";

beforeEach(() => {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

async function openNew(path = "/sets/demo-papers/v2/new-item") {
  const user = userEvent.setup();
  const app = renderApp(path);
  await screen.findByRole("heading", { level: 1, name: "New item" });
  return { user, app };
}

const etag = capturedBody<Record<string, any>>("page demo-papers/v2").version.etag as string;

describe("adding an item", () => {
  it("starts from the corpus of the version, with a new id to choose", async () => {
    await openNew();
    const id = await screen.findByLabelText("Id");
    expect(id).not.toHaveAttribute("readonly");
    expect(id).toHaveValue("");
    expect(screen.getByLabelText("Corpus")).toHaveValue("demo-corpus");
    expect(screen.getByLabelText("Form")).toHaveValue("single");
    expect(screen.getByRole("button", { name: "Add the item" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Delete item" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Review" })).not.toBeInTheDocument();
  });

  it("sends the item with the etag of the draft and opens it", async () => {
    const { user, app } = await openNew();
    let seen: { ifMatch: string | null; body: Record<string, any> } | undefined;
    server.use(
      http.post("*/api/v1/sets/demo-papers/versions/v2/items", async ({ request }) => {
        seen = {
          ifMatch: request.headers.get("if-match"),
          body: (await request.json()) as Record<string, any>,
        };
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    await user.type(await screen.findByLabelText("Id"), "NEW-1");
    await user.type(screen.getByLabelText("Question"), "A new question?");
    await user.click(screen.getByRole("button", { name: "Add the item" }));
    await waitFor(() =>
      expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2/items/NEW-1"),
    );
    expect(seen?.ifMatch).toBe(etag);
    expect(seen?.body).toEqual({
      id: "NEW-1",
      lang: "en",
      form: "single",
      content: "other",
      corpus: "demo-corpus",
      question: "A new question?",
      gold_papers: [],
    });
  });

  it("shows what qset/1 refuses at the field", async () => {
    const { user } = await openNew();
    server.use(
      http.post("*/api/v1/sets/demo-papers/versions/v2/items", () =>
        problemBody(422, "invalid-input", "The item is not a valid qset/1 item.", {
          errors: [
            { path: "/id", message: "'' does not match '^[A-Za-z0-9][A-Za-z0-9._-]*$'" },
            { path: "/question", message: "'' is too short" },
          ],
        }),
      ),
    );
    await user.click(await screen.findByRole("button", { name: "Add the item" }));
    expect((await screen.findAllByText("'' is too short")).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Id")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Question")).toHaveAttribute("aria-invalid", "true");
  });

  it("tells a member who does not own the draft that only the owner adds", async () => {
    server.use(meAs("dani"));
    renderApp("/sets/demo-papers/v2/new-item");
    expect(
      await screen.findByText(/Only the owner \(beto\) or an admin can add items/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add the item" })).not.toBeInTheDocument();
  });

  it("does not offer a frozen version", async () => {
    renderApp("/sets/demo-papers/v1/new-item");
    expect(
      await screen.findByText(/Only the owner \(ana\) or an admin can add items/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add the item" })).not.toBeInTheDocument();
  });
});
