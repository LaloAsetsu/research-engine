import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuestionsTable } from "./QuestionsTable";
import { questionCells } from "./domain";
import { useCompareStore } from "./store";
import type { ItemPage, ScoreItem } from "./types";

beforeEach(() => useCompareStore.getState().reset());

const COUNT = 45;
const questions = Array.from({ length: COUNT }, (_, index) => ({
  id: `Q-${String(index + 1).padStart(3, "0")}`,
  question: `Question number ${index + 1}`,
})) as unknown as ItemPage["items"];

function item(
  id: string,
  run_id: string,
  value: number,
  keys: Record<string, unknown>[] | null,
): ScoreItem {
  return { id, run_id, repeat: 1, metrics: { all_keys: value }, keys };
}

// every third question is better in the variant, every fifth worse
const items = questions.flatMap((question, index) => [
  item(question.id, "a", index % 5 === 0 ? 1 : 0, [
    { i: 0, exact: index % 5 === 0, lenient: false },
  ]),
  item(question.id, "b", index % 3 === 0 ? 1 : 0, [
    { i: 0, exact: index % 3 === 0, lenient: false },
  ]),
]);

function renderTable(runIds = ["a", "b"]) {
  return render(
    <QuestionsTable
      questions={questions}
      cells={questionCells(items, "all_keys")}
      runIds={runIds}
      labels={{ a: "base run", b: "variant run" }}
      metric="all_keys"
    />,
  );
}

const rows = () => document.querySelectorAll("[data-question]");

describe("the question table of a long set", () => {
  it("shows twenty questions at a time and moves between pages", async () => {
    const user = userEvent.setup({ delay: null });
    renderTable();
    expect(rows()).toHaveLength(20);
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    expect(rows()[0]?.getAttribute("data-question")).toBe("Q-021");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(rows()).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
  });

  it("goes back to the first page when the filter changes", async () => {
    const user = userEvent.setup({ delay: null });
    renderTable();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.selectOptions(screen.getByLabelText("Show"), "The arms disagree");
    expect(screen.queryByText(/Page \d of/)).not.toBeInTheDocument();
    // multiples of 5 or 3 (counting from 0) except those of both: they agree
    const expected = questions.filter((_, index) => (index % 5 === 0) !== (index % 3 === 0)).length;
    expect(screen.getByTestId("question-count")).toHaveTextContent(
      `${expected} of ${COUNT} questions`,
    );
  });

  it("has a name for the pager and for the table", () => {
    renderTable();
    expect(screen.getByRole("navigation", { name: "Pages of questions" })).toBeInTheDocument();
    expect(
      screen.getByRole("table", {
        name: "Questions with the mean of all_keys and the keys found, by run",
      }),
    ).toBeInTheDocument();
  });

  it("has no filter and no change column for one run", () => {
    renderTable(["a"]);
    expect(screen.queryByLabelText("Show")).not.toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).queryByRole("columnheader", { name: "Change" })).not.toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "base run" })).toBeInTheDocument();
  });

  it("marks the open question for assistive technology", async () => {
    const user = userEvent.setup({ delay: null });
    renderTable();
    const button = screen.getByRole("button", { name: "Open question Q-002 side by side" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
  });
});

describe("the text of a question", () => {
  it("shows math between dollar signs as math and any other text as written", () => {
    const mixed = [
      { id: "M-1", question: "What is $E = mc^2$ here?" },
      { id: "M-2", question: "Is 2 * 3 * 4 the same as _x_?" },
      { id: "M-3", question: "Does it cost from $5 to $10 per run?" },
    ] as unknown as ItemPage["items"];
    render(
      <QuestionsTable
        questions={mixed}
        cells={questionCells(
          mixed.map((entry) => item(entry.id, "a", 1, [{ i: 0, exact: true, lenient: false }])),
          "all_keys",
        )}
        runIds={["a"]}
        labels={{ a: "base run" }}
        metric="all_keys"
      />,
    );
    const math = document.querySelector("[data-question='M-1']");
    const plain = document.querySelector("[data-question='M-2']");
    expect(math?.querySelector(".katex")).not.toBeNull();
    expect(math).not.toHaveTextContent("$");
    expect(plain?.querySelector(".katex")).toBeNull();
    expect(plain).toHaveTextContent("Is 2 * 3 * 4 the same as _x_?");
    // two prices are not a formula
    const prices = document.querySelector("[data-question='M-3']");
    expect(prices?.querySelector(".katex")).toBeNull();
    expect(prices).toHaveTextContent("Does it cost from $5 to $10 per run?");
  });
});
