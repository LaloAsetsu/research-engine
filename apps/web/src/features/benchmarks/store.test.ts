import { useCompareStore } from "./store";

beforeEach(() => useCompareStore.getState().reset());

describe("the compare store", () => {
  it("starts with nothing chosen", () => {
    expect(useCompareStore.getState()).toMatchObject({
      metrics: null,
      focus: null,
      groups: null,
      filter: "all",
      text: "",
      hidden: [],
      open: null,
      repeat: 1,
      page: 0,
    });
  });

  it("goes back to the first page when the list changes, and keeps the page when only the view does", () => {
    const store = useCompareStore.getState();
    store.setPage(3);
    store.setRepeat(2);
    expect(useCompareStore.getState().page).toBe(3);
    store.setFilter("disagree");
    expect(useCompareStore.getState()).toMatchObject({ filter: "disagree", page: 0 });
    store.setPage(2);
    store.setText("DP-0");
    expect(useCompareStore.getState()).toMatchObject({ text: "DP-0", page: 0 });
    store.setPage(2);
    store.setFocus("key_share");
    expect(useCompareStore.getState()).toMatchObject({ focus: "key_share", page: 0 });
  });

  it("shows and hides a run, and opens a question at its first repeat", () => {
    const store = useCompareStore.getState();
    store.toggleHidden("a");
    store.toggleHidden("b");
    store.toggleHidden("a");
    expect(useCompareStore.getState().hidden).toEqual(["b"]);
    store.setRepeat(3);
    store.openQuestion("DP-001");
    expect(useCompareStore.getState()).toMatchObject({ open: "DP-001", repeat: 1 });
    store.openQuestion(null);
    expect(useCompareStore.getState().open).toBeNull();
  });

  it("resets without sharing arrays between resets", () => {
    const store = useCompareStore.getState();
    store.setMetrics(["all_keys"]);
    store.setGroups(["all"]);
    store.toggleHidden("a");
    store.reset();
    const after = useCompareStore.getState();
    expect(after).toMatchObject({ metrics: null, groups: null, hidden: [] });
    store.toggleHidden("z");
    store.reset();
    expect(useCompareStore.getState().hidden).toEqual([]);
  });
});
