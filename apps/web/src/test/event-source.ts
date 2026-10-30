/**
 * A stand-in for the browser's `EventSource`, which jsdom does not have. A test installs it with
 * `vi.stubGlobal("EventSource", FakeEventSource)`, then plays the server's part by hand: `emit`
 * sends an event, `drop` breaks the connection the way a browser reports it.
 */
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  static reset(): void {
    FakeEventSource.instances = [];
  }

  /** The stream opened most recently. */
  static get last(): FakeEventSource {
    const source = FakeEventSource.instances.at(-1);
    if (!source) throw new Error("no EventSource was opened");
    return source;
  }

  readonly url: string;
  readyState: number = FakeEventSource.CONNECTING;
  private readonly listeners = new Map<string, ((event: Event) => void)[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
    this.readyState = FakeEventSource.OPEN;
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }

  /** An event sent by the server. `data` is the text of the event, or an object to be written as JSON. */
  emit(name: string, data: unknown = {}): void {
    const text = typeof data === "string" ? data : JSON.stringify(data);
    this.dispatch(name, new MessageEvent(name, { data: text }));
  }

  /** The connection broke. `closed` is what the browser reports when it will not try again. */
  drop(closed: boolean): void {
    this.readyState = closed ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
    this.dispatch("error", new Event("error"));
  }

  private dispatch(name: string, event: Event): void {
    for (const listener of this.listeners.get(name) ?? []) listener(event);
  }
}

export interface RecordedEvent {
  event: string;
  data: string;
  id: string | null;
}

/** Plays recorded events of the real API, in order, up to and including `through` (a count). */
export function replay(
  source: FakeEventSource,
  events: readonly RecordedEvent[],
  through = events.length,
): void {
  for (const event of events.slice(0, through)) source.emit(event.event, event.data);
}
