import { create } from "zustand";
import type { QuestionFilter } from "./domain";

/**
 * What the person chose on the compare screen: which metrics and group to look at, which
 * questions to list and which arms to show. Nothing here comes from the server and nothing is
 * kept after the tab closes (answers hold corpus text); the run ids live in the address.
 */
export interface CompareState {
  /** Metrics of the summary and compare tables. Null until the score says which exist. */
  metrics: string[] | null;
  /** The metric that decides "better", "worse" and the question column. Null: the first of `metrics`. */
  focus: string | null;
  /** Groups to list; empty means all of them. Null until chosen: the screen starts with `defaultGroups`. */
  groups: string[] | null;
  filter: QuestionFilter;
  text: string;
  /** The runs whose column is shown in the side by side. Empty means all. */
  hidden: string[];
  /** The question open in the side by side. */
  open: string | null;
  repeat: number;
  page: number;
  setMetrics: (metrics: string[]) => void;
  setFocus: (metric: string) => void;
  setGroups: (groups: string[]) => void;
  setFilter: (filter: QuestionFilter) => void;
  setText: (text: string) => void;
  toggleHidden: (runId: string) => void;
  openQuestion: (id: string | null) => void;
  setRepeat: (repeat: number) => void;
  setPage: (page: number) => void;
  reset: () => void;
}

const INITIAL = {
  metrics: null,
  focus: null,
  groups: null,
  filter: "all",
  text: "",
  hidden: [],
  open: null,
  repeat: 1,
  page: 0,
} as const;

export const useCompareStore = create<CompareState>()((set) => ({
  ...INITIAL,
  hidden: [],
  setMetrics: (metrics) => set({ metrics }),
  setFocus: (focus) => set({ focus, page: 0 }),
  setGroups: (groups) => set({ groups }),
  setFilter: (filter) => set({ filter, page: 0 }),
  setText: (text) => set({ text, page: 0 }),
  toggleHidden: (runId) =>
    set((state) => ({
      hidden: state.hidden.includes(runId)
        ? state.hidden.filter((id) => id !== runId)
        : [...state.hidden, runId],
    })),
  openQuestion: (open) => set({ open, repeat: 1 }),
  setRepeat: (repeat) => set({ repeat }),
  setPage: (page) => set({ page }),
  reset: () => set({ ...INITIAL, hidden: [] }),
}));
