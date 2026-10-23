import { render, screen } from "@testing-library/react";
import "@/shared/i18n";
import { ApiError } from "@/shared/api/errors";
import { ProblemView } from "./ProblemView";

function show(
  status: number,
  detail = "The reason.",
  extra: Partial<ConstructorParameters<typeof ApiError>[0]> = {},
) {
  render(
    <ProblemView problem={{ kind: "api", error: new ApiError({ status, detail, ...extra }) }} />,
  );
}

describe("ProblemView", () => {
  it.each([
    [400, "The server refused the text"],
    [413, "The request is too large"],
    [422, "This circuit is too big for the screen"],
    [503, "The circuit service is busy"],
    [401, "You are not allowed to do this"],
    [403, "You are not allowed to do this"],
    [500, "The request failed"],
  ])("titles a %s", (status, title) => {
    show(status);
    expect(screen.getByText(title)).toBeInTheDocument();
    expect(screen.getByText("The reason.")).toBeInTheDocument();
  });

  it("lists the fields the server points at, without repeating the detail", () => {
    show(422, "Too big.", {
      errors: [
        { path: "/circuit_json", message: "Too many wires." },
        { path: "/other", message: "Too big." },
      ],
    });
    expect(screen.getByText("/circuit_json")).toBeInTheDocument();
    expect(screen.getByText(/Too many wires\./)).toBeInTheDocument();
    expect(screen.queryByText("/other")).not.toBeInTheDocument();
  });

  it("says when to try again", () => {
    show(503, "Busy.", { retryAfter: 4.2 });
    expect(screen.getByText("Try again in 5 seconds.")).toBeInTheDocument();
  });
});
