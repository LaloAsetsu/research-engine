import { render } from "@testing-library/react";
import { MathExpr, MathText } from "./MathText";

describe("text that may hold math", () => {
  it("renders a $...$ pair or a $$...$$ block as math", () => {
    const { container } = render(
      <>
        <MathText>{"Why does $E = mc^2$ hold?"}</MathText>
        <MathText>{"$$\\int_0^1 x\\,dx$$"}</MathText>
      </>,
    );
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(2);
  });

  it("keeps prices and a lone $ as text", () => {
    const { container } = render(
      <>
        <MathText>{"From $5 to $10 per run"}</MathText>
        <MathText>{"It costs $5 per run"}</MathText>
      </>,
    );
    expect(container.querySelector(".katex")).toBeNull();
    expect(container).toHaveTextContent("From $5 to $10 per runIt costs $5 per run");
  });

  it("shows an expression as inline math, and nothing for an empty one", () => {
    const { container, rerender } = render(<MathExpr>{" x^2 + y^2 "}</MathExpr>);
    expect(container.querySelector(".katex")).not.toBeNull();
    rerender(<MathExpr>{"   "}</MathExpr>);
    expect(container).toBeEmptyDOMElement();
  });
});
