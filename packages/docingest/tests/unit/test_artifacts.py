from docingest.domain.artifacts import ArtifactKind, extract_layout_artifacts


def test_extract_layout_artifacts_keeps_equations_tables_and_circuits() -> None:
    markdown = """# Paper

$$
E = mc^2
$$

| gate | phase |
| --- | --- |
| RZ | theta |

```latex
\\begin{quantikz}
\\gate{H}
\\end{quantikz}
```
"""

    artifacts = extract_layout_artifacts(markdown)

    assert [artifact.kind for artifact in artifacts] == [
        ArtifactKind.EQUATION,
        ArtifactKind.TABLE,
        ArtifactKind.CIRCUIT,
    ]
    assert all(artifact.source == "markdown" for artifact in artifacts)
    assert all(artifact.start is not None and artifact.end is not None for artifact in artifacts)
