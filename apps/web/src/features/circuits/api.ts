import { useMutation, useQuery } from "@tanstack/react-query";
import { fetchClient } from "@/shared/api/client";
import {
  readConventions,
  readReading,
  readResult,
  type Conventions,
  type Reading,
  type SimulatorResult,
} from "./types";

const PARSE = "/api/v1/circuits/parse";
const RUN = "/api/v1/circuits/run";
const CHECK = "/api/v1/circuits/check";
const CONVENTIONS = "/api/v1/circuits/conventions";

export interface RunInput {
  circuitJson: string;
  input: string | unknown[] | undefined;
  values: Record<string, string | number> | undefined;
  unitary: boolean;
}

export type CheckTarget =
  { state: string | unknown[] } | { unitary: unknown[][] } | { circuit_json: string } | undefined;

export interface CheckInput {
  circuitJson: string;
  target: CheckTarget;
  input: string | unknown[] | undefined;
  values: Record<string, string | number> | undefined;
}

export interface CircuitCalls {
  parse: (latex: string) => Promise<Reading>;
  run: (request: RunInput) => Promise<SimulatorResult>;
  check: (request: CheckInput) => Promise<SimulatorResult>;
}

export async function parseCircuit(latex: string): Promise<Reading> {
  const { data } = await fetchClient.POST(PARSE, { body: { latex } });
  return readReading(data);
}

export async function runCircuit(request: RunInput): Promise<SimulatorResult> {
  const { data } = await fetchClient.POST(RUN, {
    body: {
      circuit_json: request.circuitJson,
      ...(request.input === undefined ? {} : { input: request.input }),
      ...(request.values === undefined ? {} : { values: request.values }),
      unitary: request.unitary,
    },
  });
  return readResult(data);
}

export async function checkCircuit(request: CheckInput): Promise<SimulatorResult> {
  const { data } = await fetchClient.POST(CHECK, {
    body: {
      circuit_json: request.circuitJson,
      // A target left out means the two sides of a drawn equality; null would be refused.
      ...(request.target === undefined ? {} : { target: request.target }),
      ...(request.input === undefined ? {} : { input: request.input }),
      ...(request.values === undefined ? {} : { values: request.values }),
    },
  });
  return readResult(data);
}

export function useCircuitCalls(): CircuitCalls {
  const parse = useMutation({ mutationFn: parseCircuit });
  const run = useMutation({ mutationFn: runCircuit });
  const check = useMutation({ mutationFn: checkCircuit });
  // mutateAsync is stable; the mutation cache sees every failure (a 401 sends the user to sign in)
  return { parse: parse.mutateAsync, run: run.mutateAsync, check: check.mutateAsync };
}

/** The conventions, the gate table and the limits of the simulator. They do not change while the app is open. */
export function useConventions() {
  return useQuery<Conventions>({
    queryKey: ["circuits", "conventions"],
    queryFn: async () => {
      const { data } = await fetchClient.GET(CONVENTIONS);
      return readConventions(data);
    },
    staleTime: Infinity,
    retry: false,
  });
}
