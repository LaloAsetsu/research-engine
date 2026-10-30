import { readStreamEvent, STREAM_EVENTS, type StreamEvent } from "./types";

/** The part of the browser's `EventSource` that the screen uses, so a test can stand in for it. */
export interface EventSourceLike {
  readonly readyState: number;
  close(): void;
  addEventListener(type: string, listener: (event: Event) => void): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

/** `EventSource.CLOSED`: the browser gave up (an HTTP error, or the server ended the stream). */
export const CLOSED = 2;

export const browserEventSource: EventSourceFactory = (url) => new EventSource(url);

export interface StreamHandlers {
  onEvent: (event: StreamEvent) => void;
  /** An event the API does not send, or one with a body that is not what it promises. */
  onMalformed: () => void;
  /** The connection broke. `closed` is true when the browser will not try again by itself. */
  onBroken: (closed: boolean) => void;
}

export interface Stream {
  close: () => void;
  /** Closed by us, or given up on by the browser: either way no event will come. */
  readonly closed: boolean;
}

export function askEventsUrl(askId: string): string {
  return `/api/v1/asks/${encodeURIComponent(askId)}/events`;
}

/**
 * Follows the events of one ask. An `error` event is two different things: the server sends one
 * (a `MessageEvent` with data) when the question failed, and the browser fires a plain `Event`
 * of the same name when the connection broke. Only the first has data, so that tells them apart.
 */
export function followStream(
  askId: string,
  handlers: StreamHandlers,
  factory: EventSourceFactory = browserEventSource,
): Stream {
  const source = factory(askEventsUrl(askId));
  let closed = false;
  for (const name of STREAM_EVENTS) {
    source.addEventListener(name, (event) => {
      if (closed) return;
      if (!(event instanceof MessageEvent)) {
        if (name === "error") handlers.onBroken(source.readyState === CLOSED);
        return;
      }
      const parsed = readStreamEvent(name, event.data);
      if (parsed === null) handlers.onMalformed();
      else handlers.onEvent(parsed);
    });
  }
  return {
    close() {
      closed = true;
      source.close();
    },
    get closed() {
      return closed || source.readyState === CLOSED;
    },
  };
}
