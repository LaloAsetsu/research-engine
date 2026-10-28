# Circuit retrieval and assembly

This document defines the planned paper-to-circuit workflow. It is the contract for extracting
quantum circuits from LaTeX, textual descriptions, and paper figures, making them available to an
agent, and preparing validated circuits for OpenQASM 3 export.

## Goals and safety boundary

The pipeline preserves the author's evidence and never turns an uncertain model interpretation
into executable code. A circuit is executable only when:

1. its wire order, operations, controls, measurements, and parameters are structurally resolved;
2. its representation passes the `circuit/1` schema and supported-operation checks;
3. `quantum-sim` accepts the circuit and produces a validation result; and
4. source citation, extraction method, confidence, and parser/model provenance are present.

The agent must refuse simulation or assembly for `needs_review`, `rejected`, partial, or
unsupported records. It may still retrieve and summarize their original evidence, including the
reason for refusal.

## Extraction sources

### LaTeX and source text

The deterministic reader supports `quantikz`, `Qcircuit`, `yquant`, and supported `tikzcd`
circuits. The original code block remains part of the evidence. The reader emits a `circuit/1`
record only when it consumes the complete circuit source; unknown commands, ambiguous controls,
unresolved wire transitions, and unsupported operations are recorded as refusal reasons.

Textual descriptions use a deliberately constrained grammar for explicit statements such as
“apply H to q0, then CNOT with control q0 and target q1.” The parser does not infer a missing
wire order, gate arity, parameter value, or control direction. A description that lacks one of
these facts remains a candidate for review rather than executable data.

### Images and rendered figures

MinerU/RAG-Anything layout output identifies figure and image regions. A future vision/OCR adapter
will return bounded observations for:

- wires and wire order;
- gates and labels;
- controls, targets, swaps, and measurements;
- symbolic parameters and phase factors; and
- source page, region, bounding box, model, and confidence.

Image observations are evidence, not circuits. Reconstruction is accepted only when geometry and
semantics are unambiguous. Low-confidence labels, crossing wires, unresolved controls, and
uncertain parameter text result in `needs_review`. The original image and all observations remain
available for annotation or reprocessing.

## Evidence and candidate record

The implementation should preserve records with these fields:

| Field | Meaning |
|---|---|
| `id` | Stable record identifier |
| `document_id` | Content-addressed source document |
| `page` / `region` | Page and figure/layout region |
| `source_span` | Character or byte span when source text exists |
| `source_kind` | `latex`, `text`, `image`, or `layout` |
| `original_evidence` | Original code, description, OCR, or image reference |
| `observations` | Gates, wires, controls, measurements, and parameters |
| `normalized_circuit` | Validated `circuit/1`, when available |
| `confidence` | Extraction confidence from 0 to 1 |
| `review_status` | `accepted`, `needs_review`, or `rejected` |
| `validation` | Schema, simulator, and export checks with refusal reasons |
| `extractor` | Parser/vision adapter and version |
| `model_revision` | Vision/OCR model revision, if used |
| `artifact_hash` | Hash of the evidence and normalized representation |

Records are immutable processing artifacts. A new parser or model revision creates a new
extraction revision rather than overwriting evidence.

## `circuit/1` compatibility

The current `circuit/1` format remains the executable interchange format between `docingest`,
`quantum-sim`, agents, and future assembly tools. It uses 1-based wires, ordered steps, explicit
targets, closed/open controls, and explicit operation kinds. Existing filesystem sidecars remain
valid:

```json
{
  "format": "circuits/1",
  "artifacts": []
}
```

New records may add provenance and validation around the circuit, but must not alter the meaning
of existing `circuit/1` documents. Unsupported operations stay explicit and are refused by
`quantum-sim`; they must not be silently approximated.

## Persistence and retrieval

The filesystem sidecar is the immutable ingestion result beside `document.md` and `manifest.json`.
It stores accepted circuits, rejected candidates, review records, raw evidence references, and
validation output. Writes remain atomic.

The research API will project these records into SQLite using an idempotent key based on document,
source region/span, extraction revision, and artifact hash. The projection should support filters
for document citation, page, gate family, symbolic parameter, extraction source, confidence,
review status, and validation status. It must be rebuildable from filesystem artifacts.

Circuit-aware search metadata is additive to existing text retrieval. A result must identify the
same index generation as its text evidence; stale or missing embeddings must not be presented as
searchable circuit vectors.

## Agent contract

An agent may:

- retrieve a circuit and its source evidence;
- explain gate steps, parameters, provenance, and validation results;
- compare two validated circuits through `quantum-sim`; and
- request human review for an unresolved candidate.

An agent may not simulate, export, or assemble a record that is not accepted and validated. Tool
responses for refused records should include the exact unresolved evidence and a structured reason,
for example `unknown gate`, `wire order unresolved`, `image confidence below threshold`, or
`unsupported measurement`.

## OpenQASM 3 preparation

OpenQASM 3 export is deterministic and occurs only after `circuit/1` validation. The exporter will
declare qubit and classical registers, emit supported gates and parameters, preserve operation
order, and emit measurements only where the simulator and target contract support them. It will
refuse unsupported operations, unresolved symbols, ambiguous controls, and circuits that exceed
configured target limits.

Exports retain document, page/region, source hash, extraction revision, validation result, and
exporter version. Round-trip tests should compare the exported circuit's supported semantics with
`quantum-sim`; textual similarity to the paper is not a substitute for semantic validation.

## Operational rollout

The implementation is staged:

1. complete LaTeX and constrained textual extraction;
2. add the vision/OCR observation adapter and review queue;
3. project sidecars into SQLite and add circuit-aware retrieval;
4. enforce the agent validation boundary;
5. add OpenQASM 3 export and round-trip checks; and
6. measure accepted, rejected, and review-required circuits on the remote corpus.

For MinerU scans, keep concurrency at one and process failed documents independently. A native
exit code `-11` is a runtime crash, not evidence that a paper has no circuit. Retrying a failed
paper with `flash`/`txt` is an explicit fallback for papers whose content is recoverable from a
text layer or alternate parser output; it must not silently replace OCR for genuinely image-only
documents.

