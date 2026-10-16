# Conventions

quantum-sim follows Nielsen and Chuang, *Quantum Computation and Quantum Information*. These are the choices that decide whether a result is right or silently wrong: the wire order, the sign and the factor 1/2 of a rotation, and the difference between a rotation and a phase gate once they are controlled. The same table is in `quantum_sim.domain` (`CONVENTIONS`, `GATES`) and is printed by `quantum-sim conventions`; a test checks that every gate there has a row below.

## Wires and states

| Item | Convention |
|---|---|
| Wire order | Wire 1 is the top wire of a drawing and the **most significant** bit. A basis state is written `\|w1 w2 ... wn>`, and amplitude `k` of a state vector belongs to the basis label that is `k` in binary. Example: X on wire 1 of 2 maps `\|00>` (amplitude 0) to `\|10>` (amplitude 2). Same as Nielsen and Chuang, Cirq and docingest's gate lists; the **opposite of Qiskit**, whose qubit 0 is the least significant bit. |
| Wire numbers | 1-based in every user-facing structure (`Op.wires`, `controls`, `probabilities(wires=...)`); 0-based only inside the engine. |
| Matrices | Rows and columns are indexed like state amplitudes, so `unitary(c) @ v == statevector(c, v)`. A two-wire gate's matrix uses the order of `Op.wires`: the first target wire is the more significant bit. |
| Input state | `\|0...0>` unless the caller gives a basis label such as `"0110"` (wire 1 first) or `2^n` amplitudes whose norm is 1 within 1e-9. Text such as `\ket{+}` is not read. |
| Step order | Operations apply left to right, in the order of `Circuit.ops`. |

## Gates

Names are exact and lowercase; anything else is refused (`UnknownGateError`), never guessed. Every matrix is exact, global phase included, because a controlled gate exposes the phase of its target matrix.

| Name | Targets | Parameters | Matrix |
|---|---|---|---|
| `id` | 1 | 0 | I = [[1, 0], [0, 1]] |
| `h` | 1 | 0 | H = (1/√2) [[1, 1], [1, −1]] |
| `x` | 1 | 0 | X = [[0, 1], [1, 0]] |
| `y` | 1 | 0 | Y = [[0, −i], [i, 0]] |
| `z` | 1 | 0 | Z = [[1, 0], [0, −1]] |
| `s` | 1 | 0 | S = P(π/2) = diag(1, i) |
| `sdg` | 1 | 0 | S† = diag(1, −i) |
| `t` | 1 | 0 | T = P(π/4) = diag(1, e^{iπ/4}) |
| `tdg` | 1 | 0 | T† = diag(1, e^{−iπ/4}) |
| `sx` | 1 | 0 | √X = (1/2) [[1+i, 1−i], [1−i, 1+i]] |
| `sxdg` | 1 | 0 | √X† = (1/2) [[1−i, 1+i], [1+i, 1−i]] |
| `rx` | 1 | θ | R_x(θ) = exp(−iθX/2) = [[cos θ/2, −i sin θ/2], [−i sin θ/2, cos θ/2]] |
| `ry` | 1 | θ | R_y(θ) = exp(−iθY/2) = [[cos θ/2, −sin θ/2], [sin θ/2, cos θ/2]] |
| `rz` | 1 | θ | R_z(θ) = exp(−iθZ/2) = diag(e^{−iθ/2}, e^{iθ/2}) |
| `p` | 1 | θ | P(θ) = diag(1, e^{iθ}) |
| `rk` | 1 | k | R_k = diag(1, e^{2πi/2^k}), k a positive integer: the controlled rotation of the quantum Fourier transform. R_1 = Z, R_2 = S, R_3 = T. |
| `rxx` | 2 | θ | R_xx(θ) = exp(−iθ X⊗X / 2) = cos(θ/2) I − i sin(θ/2) X⊗X |
| `ryy` | 2 | θ | R_yy(θ) = exp(−iθ Y⊗Y / 2) = cos(θ/2) I − i sin(θ/2) Y⊗Y |
| `rzz` | 2 | θ | R_zz(θ) = exp(−iθ Z⊗Z / 2) = diag(e^{−iθ/2}, e^{iθ/2}, e^{iθ/2}, e^{−iθ/2}) |
| `swap` | 2 | 0 | `\|ab>` → `\|ba>` |
| `iswap` | 2 | 0 | [[1, 0, 0, 0], [0, 0, i, 0], [0, i, 0, 0], [0, 0, 0, 1]] |

Useful consequences, all tested: S² = Z, T² = S, √X² = X, HXH = Z, HZH = X, HYH = −Y, and R_z(θ) = e^{−iθ/2} P(θ).

## Controls

| Item | Convention |
|---|---|
| Closed control | `Op.controls`: the gate acts when the wire is `\|1>`. |
| Open control | `Op.open_controls`: the gate acts when the wire is `\|0>`. |
| Any number, any gate | Every gate in the table takes any number of closed and open controls. Controls are never part of a gate: CNOT is `Op("x", (t,), controls=(c,))`, Toffoli is `Op("x", (t,), controls=(c1, c2))`, Fredkin is `Op("swap", (a, b), controls=(c,))`, and two more controls on a CNOT make a 3-control X. |
| Controlled R_z is not controlled P | R_z(θ) and P(θ) differ by the global phase e^{−iθ/2} alone, but controlled that phase becomes relative: C-R_z(θ) = diag(1, 1, e^{−iθ/2}, e^{iθ/2}) and C-P(θ) = diag(1, 1, 1, e^{iθ}) are different gates. This is why the label resolver (it comes with the circuit format) refuses labels such as `R(\theta)`, `R_\phi`, `U_1` or `Ph(...)` instead of mapping them. |

## Equality

Two states or two unitaries are **equal up to global phase** when max |a − e^{iφ} b| < 1e-9, with φ taken from the largest entry of b (φ in (−π, π]). Exact equality (max |a − b| < 1e-9) is reported too. Two circuits are compared through their unitaries up to 10 wires; from 11 to 20 wires through the images of 8 random states (drawn with `numpy.random.default_rng(seed)`, default seed 0) with one common phase, read at the largest entry of the first image, without building a unitary.

## Measurement

Version 1 measures in the computational basis at the **end of a wire** only. After a wire's measurement no operation may use that wire (as target, control or open control), and classically controlled operations (`Op.classical_controls`) are refused. Results are outcome probabilities; `statevector` returns the state just before the final measurements. Deferred measurement (exact for outcome statistics) is a possible version 2.

## Limits

| Quantity | Limit | Memory at the limit (complex128) |
|---|---|---|
| Statevector, probabilities, equality by random states | 20 wires | 16 MiB per state |
| Unitary | 10 wires | 16 MiB |
| Operations per circuit | 10,000 | |

A request above a limit is refused from the circuit's description, before any array is created (`LimitExceededError`).

## Comparing with other libraries

| Library | Order | To compare with quantum-sim |
|---|---|---|
| Qiskit | qubit 0 is the least significant bit | place wire w on qubit w − 1 and reverse the operator's qubit order (`Operator(qc).reverse_qargs()`), as `tools/make_reference.py` does |
| Cirq | the first qubit is the most significant bit | the same order |
