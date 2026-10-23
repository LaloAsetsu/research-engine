import { CircleUser, LogOut } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router";
import { isApiError } from "@/shared/api/errors";
import type { Me } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/shared/ui/dropdown-menu";
import { useLogout } from "./api";
import { LOGIN_PATH } from "./guards";

export function UserMenu({ me }: { me: Me }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const logout = useLogout();
  const { pathname } = useLocation();
  const { reset } = logout;

  // A failed sign-out message belongs to the screen it happened on.
  useEffect(() => {
    reset();
  }, [pathname, reset]);

  return (
    <div className="relative">
      <DropdownMenu onOpenChange={(open) => open && logout.reset()}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            aria-label={t("auth.menu.label", { name: me.display_name })}
          >
            <CircleUser aria-hidden="true" />
            <span className="max-sm:sr-only">{me.display_name}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuLabel>
            {me.username}
            <span className="block text-xs font-normal text-muted-foreground">
              {t(`auth.menu.role.${me.role}`)}
            </span>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() =>
              logout.mutate(undefined, { onSuccess: () => void navigate(LOGIN_PATH) })
            }
          >
            <LogOut aria-hidden="true" />
            {t("auth.menu.logout")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {logout.isError && !isApiError(logout.error, 401) ? (
        <p
          role="alert"
          className="absolute top-full right-0 z-40 mt-1 w-64 rounded-md border bg-background p-2 text-sm text-destructive shadow-md"
        >
          {t("auth.menu.logoutFailed")}
        </p>
      ) : null}
    </div>
  );
}
