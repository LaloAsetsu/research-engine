import { useTranslation } from "react-i18next";
import { Badge } from "@/shared/ui/badge";
import { Card, CardContent } from "@/shared/ui/card";
import { PageHeader } from "./PageHeader";

/** Stand-in for a screen that a later release builds. */
export function PlaceholderPage({ title, description }: { title: string; description: string }) {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={title}>
        <div>
          <Badge variant="secondary">{t("pages.comingSoon")}</Badge>
        </div>
      </PageHeader>
      <Card>
        <CardContent>
          <p className="max-w-prose text-muted-foreground">{description}</p>
        </CardContent>
      </Card>
    </>
  );
}
