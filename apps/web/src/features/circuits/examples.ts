/** Circuits to try. The LaTeX is quantikz and Qcircuit source, the same shapes the paper reader meets. */

export interface CircuitExample {
  id: string;
  kind: "latex" | "json";
  text: string;
}

const BELL_JSON = JSON.stringify(
  {
    format: "circuit/1",
    wires: 2,
    parts: [
      [
        [{ kind: "gate", label: "H", targets: [1] }],
        [{ kind: "x", label: "X", targets: [2], controls: [1] }],
      ],
    ],
  },
  null,
  2,
);

export const EXAMPLES: readonly CircuitExample[] = [
  {
    id: "bell",
    kind: "latex",
    text: `\\begin{quantikz}
\\lstick{\\ket{0}} & \\gate{H} & \\ctrl{1} & \\qw \\\\
\\lstick{\\ket{0}} & \\qw & \\targ{} & \\qw
\\end{quantikz}`,
  },
  {
    id: "ghz",
    kind: "latex",
    text: `\\Qcircuit @C=1em @R=1em {
& \\gate{H} & \\ctrl{1} & \\qw & \\qw \\\\
& \\qw & \\targ & \\ctrl{1} & \\qw \\\\
& \\qw & \\qw & \\targ & \\qw
}`,
  },
  {
    id: "rotation",
    kind: "latex",
    text: `\\begin{quantikz}
& \\gate{R_y(\\theta)} & \\qw
\\end{quantikz}`,
  },
  {
    id: "hzhEqualsX",
    kind: "latex",
    text: `\\begin{quantikz}
& \\gate{H} & \\gate{Z} & \\gate{H} & \\qw & \\push{=} & \\gate{X} & \\qw
\\end{quantikz}`,
  },
  {
    id: "cnotFromCz",
    kind: "latex",
    text: `\\begin{quantikz}
& \\qw & \\ctrl{1} & \\qw & \\qw & \\push{=} & \\ctrl{1} & \\qw \\\\
& \\gate{H} & \\control{} & \\gate{H} & \\qw & & \\targ{} & \\qw
\\end{quantikz}`,
  },
  {
    id: "qft3",
    kind: "latex",
    text: `\\begin{quantikz}
& \\gate{H} & \\gate{R_2} & \\gate{R_3} & \\qw & \\qw & \\qw & \\swap{2} & \\qw \\\\
& \\qw & \\ctrl{-1} & \\qw & \\gate{H} & \\gate{R_2} & \\qw & \\qw & \\qw \\\\
& \\qw & \\qw & \\ctrl{-2} & \\qw & \\ctrl{-1} & \\gate{H} & \\targX{} & \\qw
\\end{quantikz}`,
  },
  {
    id: "measured",
    kind: "latex",
    text: `\\begin{quantikz}
& \\gate{H} & \\ctrl{1} & \\qw & \\meter{} \\\\
& \\qw & \\targ{} & \\qw & \\meter{}
\\end{quantikz}`,
  },
  {
    id: "partial",
    kind: "latex",
    text: `\\begin{quantikz}
\\lstick{$q_1$} & \\ctrl{1} & \\qw \\\\
\\lstick{$q_2$} & \\targ{} & \\ctrl{1} \\wire[l][1]["\\iddots"{below,pos=0.1}]{a} \\\\
\\lstick{$q_3$} & \\qw & \\targ{}
\\end{quantikz}`,
  },
  {
    id: "bundle",
    kind: "latex",
    text: `\\begin{quantikz}
& \\gate{H} & \\ctrl{1} & \\qw \\\\
\\lstick{$a$} & \\qwbundle{m} & \\targ{} & \\qw
\\end{quantikz}`,
  },
  { id: "bellJson", kind: "json", text: BELL_JSON },
];

export function exampleById(id: string): CircuitExample | undefined {
  return EXAMPLES.find((example) => example.id === id);
}
