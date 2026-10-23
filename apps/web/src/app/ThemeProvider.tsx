import { useEffect, type ReactNode } from "react";
import { applyTheme, watchSystemTheme } from "@/shared/lib/theme";
import { useUiStore } from "@/shared/stores/ui-store";

/** Keeps the `dark` class in step with the chosen theme, and with the system while it is "system". */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const theme = useUiStore((state) => state.theme);
  useEffect(() => {
    applyTheme(theme);
    return theme === "system" ? watchSystemTheme(() => applyTheme(theme)) : undefined;
  }, [theme]);
  return children;
}
