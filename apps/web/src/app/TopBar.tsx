import { Languages, Monitor, Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router";
import { UserMenu } from "@/features/auth/UserMenu";
import { StatusBadge } from "@/features/status/StatusBadge";
import { LANGUAGES } from "@/shared/i18n/detect";
import type { Me } from "@/shared/api/pending";
import { THEMES, type Theme } from "@/shared/lib/theme";
import { cn } from "@/shared/lib/utils";
import { useUiStore } from "@/shared/stores/ui-store";
import { Button } from "@/shared/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/shared/ui/dropdown-menu";

const NAV = [
  { to: "/ask", key: "nav.ask" },
  { to: "/benchmarks", key: "nav.benchmarks" },
  { to: "/sets", key: "nav.sets" },
  { to: "/circuits", key: "nav.circuits" },
] as const;

const THEME_ICON = { system: Monitor, light: Sun, dark: Moon } as const;

export function TopBar({ me }: { me: Me }) {
  const { t } = useTranslation();
  return (
    <header className="border-b bg-background">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
        <span className="font-semibold">{t("app.name")}</span>
        <nav
          aria-label={t("nav.label")}
          className="order-last flex w-full flex-wrap gap-1 md:order-none md:w-auto"
        >
          {NAV.map(({ to, key }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
                  isActive ? "bg-muted text-foreground" : "text-muted-foreground",
                )
              }
            >
              {t(key)}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          <StatusBadge />
          <LanguageSwitch />
          <ThemeSwitch />
          <UserMenu me={me} />
        </div>
      </div>
    </header>
  );
}

function LanguageSwitch() {
  const { t } = useTranslation();
  const language = useUiStore((state) => state.language);
  const setLanguage = useUiStore((state) => state.setLanguage);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`${t("language.label")}: ${t(`language.${language}`)}`}
        >
          <Languages aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={language}
          onValueChange={(value) => {
            const next = LANGUAGES.find((candidate) => candidate === value);
            if (next) setLanguage(next);
          }}
        >
          {LANGUAGES.map((code) => (
            <DropdownMenuRadioItem key={code} value={code} lang={code}>
              {t(`language.${code}`)}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ThemeSwitch() {
  const { t } = useTranslation();
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const Icon = THEME_ICON[theme];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`${t("theme.label")}: ${t(`theme.${theme}`)}`}
        >
          <Icon aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={theme}
          onValueChange={(value) => {
            const next = THEMES.find((candidate): candidate is Theme => candidate === value);
            if (next) setTheme(next);
          }}
        >
          {THEMES.map((name) => (
            <DropdownMenuRadioItem key={name} value={name}>
              {t(`theme.${name}`)}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
