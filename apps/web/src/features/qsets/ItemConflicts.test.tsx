import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { ANA } from "@/test/auth-mock";
import { capturedBody, meAs, problem, problemBody } from "@/test/sets-api";
import { renderApp } from "@/test/render";
import { authMock, server } from "@/test/server";
import { invalidateSets } from "./api";

// These tests run a draft that behaves like the API for the conflicts that matter here: the etag
// names the whole draft, a write or a review with an old etag is refused with a 409, and a write
// moves the etag. Someone else's change is `served.change(...)`.

beforeEach(() => {
  authMock.handle({
    method: "POST",
    path: "/api/v1/auth/login",
    body: { username: ANA.username, password: ANA.password },
  });
});

const BASE = "*/api/v1/sets/demo-papers/versions/v2";

interface Posted {
  ifMatch: string | null;
  body: Record<string, any>;
}

function serve(id: string) {
  const original = capturedBody<Record<string, any>>(`item demo-papers/v2/${id}`);
  const state = {
    item: original.item as Record<string, any>,
    etag: original.version.etag as string,
    moves: 0,
    puts: [] as Posted[],
    reviews: [] as Posted[],
    reads: 0,
  };
  const detail = () => ({
    ...original,
    item: state.item,
    version: { ...original.version, etag: state.etag },
  });
  const move = () => {
    state.moves += 1;
    state.etag = `"moved-${state.moves}"`;
  };
  server.use(
    http.get(`${BASE}/items/${id}`, () => {
      state.reads += 1;
      return HttpResponse.json(detail(), { headers: { etag: state.etag } });
    }),
    http.put(`${BASE}/items/${id}`, async ({ request }) => {
      const posted = {
        ifMatch: request.headers.get("if-match"),
        body: (await request.json()) as Record<string, any>,
      };
      state.puts.push(posted);
      if (posted.ifMatch !== state.etag) return problem("put 409 stale");
      state.item = posted.body;
      move();
      return HttpResponse.json(detail());
    }),
    http.post(`${BASE}/reviews`, async ({ request }) => {
      const posted = {
        ifMatch: request.headers.get("if-match"),
        body: (await request.json()) as Record<string, any>,
      };
      state.reviews.push(posted);
      if (posted.ifMatch !== state.etag) return problem("put 409 stale");
      return HttpResponse.json({}, { status: 201 });
    }),
  );
  return {
    state,
    /** Someone else saves this item: other text, and the draft's etag moves. */
    change(fields: Record<string, unknown>) {
      state.item = { ...state.item, ...fields };
      move();
    },
    /** Someone else saves another item: the draft's etag moves, this item stays as it is. */
    touchOther() {
      move();
    },
  };
}

async function openItem(id: string) {
  const user = userEvent.setup();
  const app = renderApp(`/sets/demo-papers/v2/items/${id}`);
  await screen.findByRole("heading", { level: 1, name: id });
  await screen.findByRole("button", { name: "Check against qset/1" });
  return { user, app };
}

const question = () => screen.getByLabelText<HTMLTextAreaElement>("Question");
const notes = () => screen.getByLabelText<HTMLTextAreaElement>("Notes", { exact: true });
const save = () => screen.getByRole("button", { name: "Save changes" });

describe("someone else changes the same item while it is open (S2, O-126)", () => {
  it("keeps their change and mine when I keep my edits after a refused save", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "THE OWNER REWROTE THIS QUESTION" });

    await user.click(save());
    expect(await screen.findByText("Changed since you opened it")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep my edits" }));

    expect(await screen.findByText("Merged with the latest version")).toBeInTheDocument();
    expect(screen.getByText(/because you had not changed them: question/)).toBeInTheDocument();
    expect(question()).toHaveValue("THE OWNER REWROTE THIS QUESTION");
    expect(notes().value).toMatch(/ mine$/);

    await user.click(save());
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    const ok = served.state.puts.at(-1)!;
    expect(ok.ifMatch).not.toBe(served.state.puts[0]!.ifMatch);
    expect(served.state.item.question).toBe("THE OWNER REWROTE THIS QUESTION");
    expect(String(served.state.item.notes)).toMatch(/ mine$/);
  });

  it("lists a field that both of us changed, keeps mine, and lets me take theirs", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(question(), " mine");
    const mine = question().value;
    served.change({ question: "Their version of the question" });

    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));

    expect(await screen.findByText("Merged with the latest version")).toBeInTheDocument();
    expect(screen.getByText(/You and someone else both changed these fields/)).toBeInTheDocument();
    expect(screen.getByText(`Yours (kept): ${mine}`)).toBeInTheDocument();
    expect(
      screen.getByText("Latest on the server: Their version of the question"),
    ).toBeInTheDocument();
    expect(question()).toHaveValue(mine);

    await user.click(screen.getByRole("button", { name: "Use the latest value of question" }));
    expect(question()).toHaveValue("Their version of the question");
    expect(screen.queryByText("Merged with the latest version")).not.toBeInTheDocument();
  });

  it("does not need a merge when only another item changed", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.touchOther();
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));
    expect(await screen.findByText(/Your edits are kept; save again/)).toBeInTheDocument();
    expect(screen.queryByText("Merged with the latest version")).not.toBeInTheDocument();
    await user.click(save());
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(served.state.puts).toHaveLength(2);
  });

  it("stops and asks when the item is read again under my edits (a reconnect)", async () => {
    const served = serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed while I typed" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });

    expect(await screen.findByText("This item changed while you were editing")).toBeInTheDocument();
    expect(screen.getByText(/Someone else changed question\./)).toBeInTheDocument();
    // What I see is still what I had, and nothing can be saved or decided on it.
    expect(question()).not.toHaveValue("Changed while I typed");
    expect(save()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(screen.getByText(/no decision can be sent until then/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Merge with my edits" }));
    expect(question()).toHaveValue("Changed while I typed");
    expect(notes().value).toMatch(/ mine$/);
    expect(screen.queryByText("This item changed while you were editing")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();

    await user.click(save());
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(served.state.puts).toHaveLength(1);
    expect(served.state.puts[0]!.body.question).toBe("Changed while I typed");
    expect(String(served.state.item.notes)).toMatch(/ mine$/);
  });

  it("can drop my edits and load what the server holds", async () => {
    const served = serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed while I typed" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    await user.click(
      await screen.findByRole("button", { name: "Load the latest and drop my edits" }),
    );
    expect(question()).toHaveValue("Changed while I typed");
    expect(notes().value).not.toMatch(/ mine$/);
    expect(save()).toBeDisabled();
    expect(served.state.puts).toHaveLength(0);
  });

  it("says the latest version was loaded after a refused save, and moves focus there", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed by the owner" });
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Load the latest" }));
    const loaded = await screen.findByText(/The latest version was loaded/);
    expect(question()).toHaveValue("Changed by the owner");
    expect(notes().value).not.toMatch(/ mine$/);
    await waitFor(() => expect(loaded).toHaveFocus());
  });

  it("keeps the editor and its edits when a read fails while they are open", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " unsaved words");
    server.use(
      http.get(`${BASE}/items/DP-003`, () => problemBody(404, "not-found", "No such item.")),
    );
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    expect(await screen.findByText("No such item.")).toBeInTheDocument();
    expect(notes().value).toMatch(/ unsaved words$/);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("shows the latest text, and says so, when there is nothing of mine to lose", async () => {
    const served = serve("DP-003");
    const { app } = await openItem("DP-003");
    served.change({ question: "Someone rewrote the question" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    expect(await screen.findByText("This item changed while you had it open")).toBeInTheDocument();
    expect(screen.getByText(/Someone else changed question\./)).toBeInTheDocument();
    expect(question()).toHaveValue("Someone rewrote the question");
    expect(save()).toBeDisabled();
  });

  it("is not thrown off by its own save", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await user.click(save());
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    await waitFor(() => expect(served.state.reads).toBeGreaterThan(1));
    expect(notes().value).toMatch(/ mine$/);
    expect(screen.queryByText("This item changed while you had it open")).not.toBeInTheDocument();
    expect(screen.queryByText("This item changed while you were editing")).not.toBeInTheDocument();
    expect(save()).toBeDisabled();
    // A second change goes out with the etag the first one returned.
    await user.type(notes(), " again");
    await user.click(save());
    await waitFor(() => expect(served.state.puts).toHaveLength(2));
    expect(served.state.puts[1]!.ifMatch).toBe('"moved-1"');
  });

  it("sends the etag of the text it was built from when a save and a read cross", async () => {
    const served = serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed while I typed" });
    await act(async () => {
      await invalidateSets(app.queryClient);
    });
    await screen.findByText("This item changed while you were editing");
    // Save is refused in the page, so the old copy is never put over the new text.
    await user.click(save());
    expect(served.state.puts).toHaveLength(0);
    expect(served.state.item.question).toBe("Changed while I typed");
  });
});

describe("a review against text that moved (O-126 a)", () => {
  it("shows the reviewer the new text, keeps the note, and records against what they saw", async () => {
    server.use(meAs("dani"));
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    served.change({ question: "THE OWNER REWROTE THIS QUESTION" });

    await user.type(screen.getByLabelText("Note"), "Looks fine to me.");
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("The draft changed while you were reading")).toBeInTheDocument();
    expect(served.state.reviews).toHaveLength(1);

    // The editor was rebuilt from the item that was read again, and the reviewer is told.
    await waitFor(() => expect(question()).toHaveValue("THE OWNER REWROTE THIS QUESTION"));
    expect(screen.getByText("This item changed while you had it open")).toBeInTheDocument();
    expect(
      screen.getByText(/The text above changed while you had it open \(question\)/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Note")).toHaveValue("Looks fine to me.");
    // The change list shows nothing of the owner's change as if it were the reviewer's.
    expect(screen.getByText(/You have not changed any field/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("Recorded: accept.")).toBeInTheDocument();
    expect(served.state.reviews).toHaveLength(2);
    expect(served.state.reviews[1]!.ifMatch).toBe(served.state.etag);
    expect(
      screen.queryByText(/The text above changed while you had it open/),
    ).not.toBeInTheDocument();
  });

  it("asks a reviewer who also has edits to choose before deciding again", async () => {
    server.use(meAs("dani"));
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " dani's idea");
    served.change({ question: "THE OWNER REWROTE THIS QUESTION" });
    await user.type(screen.getByLabelText("Note"), "A note.");
    await user.click(screen.getByRole("button", { name: "Propose edit" }));
    expect(await screen.findByText("The draft changed while you were reading")).toBeInTheDocument();
    expect(await screen.findByText("This item changed while you were editing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Propose edit" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Merge with my edits" }));
    await user.click(screen.getByRole("button", { name: "Propose edit" }));
    expect(await screen.findByText("Recorded: edit.")).toBeInTheDocument();
    // Only the reviewer's own field is proposed.
    expect(Object.keys(served.state.reviews[1]!.body.edit)).toEqual(["notes"]);
  });

  it("goes on when only another item moved: the reviewer decides again with the new etag", async () => {
    server.use(meAs("dani"));
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    served.touchOther();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("The draft changed while you were reading")).toBeInTheDocument();
    expect(screen.queryByText("This item changed while you had it open")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("Recorded: accept.")).toBeInTheDocument();
  });
});

describe("focus after a refused form (scope 5.7)", () => {
  it("moves to the first field with a problem and keeps the save button focusable while it runs", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.put(`${BASE}/items/DP-003`, async () => {
        await gate;
        return problem("put 422");
      }),
    );
    await user.type(notes(), " x");
    await user.click(save());
    // Busy: the button says so, is not removed from the keyboard, and ignores a second click.
    await waitFor(() => expect(save()).toHaveAttribute("aria-disabled", "true"));
    expect(save()).toHaveFocus();
    await user.click(save());
    release();
    expect(await screen.findByText("The item is not a valid qset/1 item.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Language")).toHaveFocus());
    expect(served.state.puts).toHaveLength(0);
  });

  it("moves to the problems of a part this form does not show", async () => {
    serve("DP-004");
    const { user } = await openItem("DP-004");
    server.use(
      http.put(`${BASE}/items/DP-004`, () =>
        problemBody(422, "invalid-input", "The item is not a valid qset/1 item.", {
          errors: [{ path: "/necessity", message: "'necessity' is a required property" }],
        }),
      ),
    );
    await user.type(question(), " x");
    await user.click(save());
    const group = await screen.findByRole("group", {
      name: "Problems at parts this form does not show",
    });
    await waitFor(() => expect(group).toHaveFocus());
  });

  it("moves to the problems of a check", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    server.use(
      http.post("*/api/v1/sets/validate", () =>
        HttpResponse.json({
          ok: false,
          errors: [{ path: "/lang", message: "'fr' is not one of ['en', 'es']" }],
        }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Check against qset/1" }));
    await waitFor(() => expect(screen.getByLabelText("Language")).toHaveFocus());
  });

  it("moves to a refusal that is not about a field", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    server.use(http.put(`${BASE}/items/DP-003`, () => problem("put 403 member")));
    await user.type(notes(), " x");
    await user.click(save());
    const alert = (await screen.findByText("Not allowed")).closest("[data-first-error]");
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it("moves to the refusal of a delete", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    server.use(
      http.delete(`${BASE}/items/DP-003`, () =>
        problemBody(409, "conflict", "DP-003 is the original of a mirror."),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Delete item" }));
    await user.click(screen.getByRole("button", { name: "Yes, delete" }));
    const alert = (await screen.findByText("DP-003 is the original of a mirror.")).closest(
      "[data-first-error]",
    );
    await waitFor(() => expect(alert).toHaveFocus());
  });

  it("moves to the notice after a merge, since the button that was pressed is gone", async () => {
    const served = serve("DP-003");
    const { user } = await openItem("DP-003");
    await user.type(notes(), " mine");
    served.change({ question: "Changed by the owner" });
    await user.click(save());
    await user.click(await screen.findByRole("button", { name: "Keep my edits" }));
    const heading = await screen.findByText("Merged with the latest version");
    await waitFor(() => expect(heading.closest("[data-note-focus]")).toHaveFocus());
  });
});

describe("the whole item as JSON shows where each problem is", () => {
  it("prints the whole pointer of every problem", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    server.use(
      http.put(`${BASE}/items/DP-003`, () =>
        problemBody(422, "invalid-input", "The item is not a valid qset/1 item.", {
          errors: [
            { path: "/question", message: "'' is too short" },
            { path: "/key_facts/2/rel", message: "-1 is less than the minimum of 0" },
            { path: "", message: "the item as a whole" },
          ],
        }),
      ),
    );
    await user.type(notes(), " x");
    await user.click(save());
    await screen.findByText("The item is not a valid qset/1 item.");
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    const box = screen.getByLabelText("The whole item as JSON");
    const described = (box.getAttribute("aria-describedby") ?? "")
      .split(" ")
      .map((id) => document.getElementById(id)?.textContent ?? "")
      .join(" ");
    expect(described).toContain("/question: '' is too short");
    expect(described).toContain("/key_facts/2/rel: -1 is less than the minimum of 0");
    expect(described).toContain("the item as a whole");
    expect(within(box.parentElement!).getAllByRole("listitem").length).toBeGreaterThanOrEqual(3);
  });
});

describe("edits that are not saved are not lost by leaving", () => {
  const back = () => screen.getByRole("link", { name: "Back to demo-papers v2" });

  it("asks before a link leaves the page with edits, and stays on request", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await user.click(back());

    const alert = await screen.findByText("You have unsaved edits");
    expect(alert).toBeInTheDocument();
    expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2/items/DP-003");
    await waitFor(() => expect(alert.closest('[role="alert"]')).toHaveFocus());

    await user.click(screen.getByRole("button", { name: "Stay and keep editing" }));
    expect(screen.queryByText("You have unsaved edits")).not.toBeInTheDocument();
    expect(notes().value).toMatch(/ mine$/);
  });

  it("leaves and drops the edits when asked", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await user.click(back());
    await user.click(await screen.findByRole("button", { name: "Leave and drop my edits" }));
    await waitFor(() => expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2"));
  });

  it("does not ask when there is nothing to lose, or once the edits are saved", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await user.click(save());
    await screen.findByText("Saved.");
    await user.click(back());
    await waitFor(() => expect(app.router.state.location.pathname).toBe("/sets/demo-papers/v2"));
    expect(screen.queryByText("You have unsaved edits")).not.toBeInTheDocument();
  });

  it("warns the browser before the tab is closed, only while there are edits", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    const closing = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(closing()).toBe(false);
    await user.type(notes(), " mine");
    expect(closing()).toBe(true);
  });

  it("does not stand in the way of the sign-in a lost session leads to", async () => {
    serve("DP-003");
    const { user, app } = await openItem("DP-003");
    await user.type(notes(), " mine");
    await act(async () => {
      await app.router.navigate("/login?next=%2Fsets", { replace: true });
    });
    // The signed-in test user is sent on to `next`: the page was left, nothing asked first.
    expect(app.router.state.location.pathname).toBe("/sets");
    expect(screen.queryByText("You have unsaved edits")).not.toBeInTheDocument();
  });

  it("keeps the person in the view whose text cannot be read, and says why", async () => {
    serve("DP-003");
    const { user } = await openItem("DP-003");
    const box = screen.getByLabelText("Other fields (JSON)");
    await user.click(box);
    await user.clear(box);
    await user.paste("{ nope");
    await screen.findAllByText(/Not valid JSON/);
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    expect(screen.getByRole("tab", { name: "Fields" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("before you change the view");
    // The text is still there to fix.
    expect(screen.getByLabelText<HTMLTextAreaElement>("Other fields (JSON)").value).toBe("{ nope");

    await user.clear(screen.getByLabelText("Other fields (JSON)"));
    await user.click(screen.getByRole("tab", { name: "JSON" }));
    expect(screen.getByRole("tab", { name: "JSON" })).toHaveAttribute("aria-selected", "true");
  });
});
