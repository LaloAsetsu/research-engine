import { render } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { App, createApp, type AppInstance } from "@/app/App";

/** Renders the whole app on an in-memory router at `path`. */
export function renderApp(path: string): AppInstance {
  const app = createApp((routes) => createMemoryRouter(routes, { initialEntries: [path] }));
  render(<App app={app} />);
  return app;
}
