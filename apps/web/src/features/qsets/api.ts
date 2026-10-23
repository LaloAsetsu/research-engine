import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { $api, EXPECTS_UNAUTHORIZED, fetchClient } from "@/shared/api/client";
import { PAGE_SIZE, type ItemFilters } from "./filters";
import type { Item, ItemDetail, NewReview } from "./types";

const SETS = "/api/v1/sets";
const IMPORT = "/api/v1/sets/import";
const SHAPES = "/api/v1/sets/import/shapes";
const VALIDATE = "/api/v1/sets/validate";
const VERSION = "/api/v1/sets/{name}/versions/{version}";
const EXPORT = "/api/v1/sets/{name}/versions/{version}/export";
const FREEZE = "/api/v1/sets/{name}/versions/{version}/freeze";
const ITEMS = "/api/v1/sets/{name}/versions/{version}/items";
const ITEM = "/api/v1/sets/{name}/versions/{version}/items/{item_id}";
const REVIEWS = "/api/v1/sets/{name}/versions/{version}/reviews";
const ME = "/api/v1/auth/me";

/** The signed-in user. It shares its cache entry with the session check of the app shell. */
export function useMe() {
  return $api.useQuery(
    "get",
    ME,
    {},
    { staleTime: 5 * 60_000, retry: false, meta: EXPECTS_UNAUTHORIZED },
  );
}

export function useCatalog() {
  return $api.useQuery("get", SETS);
}

export function useImportShapes() {
  return $api.useQuery("get", SHAPES, {}, { staleTime: Infinity });
}

export function useVersionPage(
  name: string,
  version: string,
  filters: ItemFilters,
  offset: number,
  limit: number = PAGE_SIZE,
) {
  return $api.useQuery(
    "get",
    VERSION,
    {
      params: {
        path: { name, version },
        query: { ...filters, offset, limit },
      },
    },
    {
      // Keep the last page on screen while the next one loads, but only for the same version.
      placeholderData: (previous, previousQuery) => {
        const init = previousQuery?.queryKey[2] as
          { params?: { path?: { name?: string; version?: string } } } | undefined;
        const path = init?.params?.path;
        return path?.name === name && path.version === version ? previous : undefined;
      },
    },
  );
}

export function useItemDetail(name: string, version: string, id: string, enabled = true) {
  return $api.useQuery(
    "get",
    ITEM,
    { params: { path: { name, version, item_id: id } } },
    { enabled },
  );
}

export function useFreezePlan(name: string, version: string, enabled: boolean) {
  return $api.useQuery(
    "get",
    FREEZE,
    { params: { path: { name, version } } },
    { enabled, retry: false, gcTime: 0, staleTime: 0 },
  );
}

/** Everything the screens cache about sets. A write changes the etag of a whole draft, so all of it is read again. */
export function invalidateSets(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => {
      const path: unknown = query.queryKey[1];
      return typeof path === "string" && path.startsWith(SETS);
    },
  });
}

export interface VersionRef {
  name: string;
  version: string;
}

export function useCreateSet() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string; corpus?: string; copy_from?: string }) => {
      const { data } = await fetchClient.POST(SETS, { body });
      return data;
    },
    onSuccess: () => invalidateSets(queryClient),
  });
}

/** An empty box is no value: the query leaves it out and the API uses its default. */
function orNull(value: string | undefined): string | null {
  return value === undefined || value === "" ? null : value;
}

export interface ImportRequest {
  file: File;
  name?: string;
  version?: string;
  shape?: string;
  corpus?: string;
  sourceFile?: string;
}

export function useImportSet() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (request: ImportRequest) => {
      const { data } = await fetchClient.POST(IMPORT, {
        params: {
          query: {
            name: orNull(request.name),
            version: orNull(request.version),
            shape: orNull(request.shape),
            corpus: orNull(request.corpus),
            source_file: orNull(request.sourceFile) ?? request.file.name,
          },
        },
        // The file itself is the body: no multipart, no re-encoding.
        body: request.file as unknown as string,
        bodySerializer: (body: unknown) => body as BodyInit,
        headers: { "content-type": "application/octet-stream" },
      });
      return data;
    },
    onSuccess: () => invalidateSets(queryClient),
  });
}

/**
 * The answer to the PUT replaces the cached item, and `onStored` sees it before the cached copies
 * are read again, so no copy of the old text is left to be taken for a change by someone else.
 * It also gets the item that was sent: the editor may hold more text by then (a pending mutation
 * is handed the newest callbacks), so the sent item is what the answer must be compared with.
 */
export function useSaveItem(
  ref: VersionRef,
  onStored?: (stored: ItemDetail | undefined, sent: Item) => void,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, item, etag }: { id: string; item: Item; etag: string }) => {
      const { data } = await fetchClient.PUT(ITEM, {
        params: { path: { ...ref, item_id: id }, header: { "if-match": etag } },
        body: item,
      });
      return data;
    },
    onSuccess: (stored, { id, item }) => {
      // The answer is the item as stored, with the new etag: put it where the item screen reads from.
      if (stored?.item) {
        const key = $api.queryOptions("get", ITEM, {
          params: { path: { ...ref, item_id: id } },
        }).queryKey;
        queryClient.setQueryData(key, stored);
      }
      onStored?.(stored, item);
      return invalidateSets(queryClient);
    },
  });
}

export function useAddItem(ref: VersionRef) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ item, etag }: { item: Item; etag: string }) => {
      const { data } = await fetchClient.POST(ITEMS, {
        params: { path: ref, header: { "if-match": etag } },
        body: item,
      });
      return data;
    },
    onSuccess: () => invalidateSets(queryClient),
  });
}

export function useDeleteItem(ref: VersionRef) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, etag }: { id: string; etag: string }) => {
      const { data } = await fetchClient.DELETE(ITEM, {
        params: { path: { ...ref, item_id: id }, header: { "if-match": etag } },
      });
      return data;
    },
    onSuccess: () => invalidateSets(queryClient),
  });
}

export function useAddReview(ref: VersionRef) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ review, etag }: { review: NewReview; etag: string }) => {
      const { data } = await fetchClient.POST(REVIEWS, {
        params: { path: ref, header: { "if-match": etag } },
        body: review,
      });
      return data;
    },
    // Settled, not only success: a 409 means the draft moved, so every cached copy is stale.
    onSettled: () => invalidateSets(queryClient),
  });
}

export function useFreeze(ref: VersionRef) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ planEtag }: { planEtag: string }) => {
      const { data } = await fetchClient.POST(FREEZE, {
        params: { path: ref, header: { "if-match": planEtag } },
      });
      return data;
    },
    onSettled: () => invalidateSets(queryClient),
  });
}

/** A dry run against `qset/1`: nothing is stored. */
export function useValidateItem() {
  return useMutation({
    mutationFn: async (item: Item) => {
      const { data } = await fetchClient.POST(VALIDATE, { body: { item } });
      return data;
    },
  });
}

/** The file as stored, as the browser's own download. Returns the bytes' name and the md5 the server names. */
export async function downloadExport(ref: VersionRef): Promise<{ filename: string }> {
  const { data } = await fetchClient.GET(EXPORT, {
    params: { path: ref },
    parseAs: "blob",
  });
  const filename = `${ref.name}-${ref.version}.qset.jsonl`;
  const url = URL.createObjectURL(data!);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return { filename };
}
