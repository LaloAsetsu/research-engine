import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { PageHeader } from "@/shared/layout/PageHeader";
import { Button } from "@/shared/ui/button";
import { Skeleton } from "@/shared/ui/skeleton";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui/table";
import { useCatalog } from "./api";
import { formatDate, shortMd5 } from "./format";
import { ImportSection } from "./ImportSection";
import { NewSetSection } from "./NewSetSection";
import { ProblemView } from "./ProblemView";
import { StatusBadge } from "./ReviewBadge";
import { decidedCount } from "./rules";
import { Section } from "./Section";
import { versionPath, type SetVersion } from "./types";

function ReviewProgress({ version }: { version: SetVersion }) {
  const { t } = useTranslation();
  const review = version.review;
  if (!review) return <span className="text-muted-foreground">{t("sets.catalog.noCount")}</span>;
  return (
    <div className="grid gap-0.5">
      <span>{t("sets.catalog.decided", { done: decidedCount(review), total: review.items })}</span>
      <span className="text-xs text-muted-foreground">
        {t("sets.catalog.breakdown", {
          accepted: review.accepted,
          needsSecond: review.needs_second,
          edited: review.edited,
          rejected: review.rejected,
        })}
      </span>
    </div>
  );
}

function CatalogTable() {
  const { t, i18n } = useTranslation();
  const catalog = useCatalog();

  if (catalog.isPending) {
    return (
      <div className="grid gap-2" role="status" aria-label={t("sets.loading")}>
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }
  if (catalog.isError) {
    return (
      <div className="grid gap-3">
        <ProblemView error={catalog.error} />
        <div>
          <Button variant="outline" onClick={() => void catalog.refetch()}>
            {t("sets.tryAgain")}
          </Button>
        </div>
      </div>
    );
  }
  const rows = catalog.data.flatMap((entry) => entry.versions);
  if (rows.length === 0) {
    return <p className="text-muted-foreground">{t("sets.catalog.empty")}</p>;
  }
  return (
    <Table scrollLabel={t("sets.catalog.scroll")}>
      <TableCaption>{t("sets.catalog.caption", { count: rows.length })}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t("sets.catalog.set")}</TableHead>
          <TableHead scope="col">{t("sets.catalog.status")}</TableHead>
          <TableHead scope="col">{t("sets.catalog.items")}</TableHead>
          <TableHead scope="col">{t("sets.catalog.review")}</TableHead>
          <TableHead scope="col">{t("sets.catalog.md5")}</TableHead>
          <TableHead scope="col">{t("sets.catalog.corpus")}</TableHead>
          <TableHead scope="col">{t("sets.catalog.owner")}</TableHead>
          <TableHead scope="col">{t("sets.catalog.updated")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((version) => (
          <TableRow key={`${version.name}/${version.version}`}>
            <TableCell>
              <Link
                to={versionPath(version.name, version.version)}
                className="font-medium underline-offset-4 hover:underline focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {version.name} {version.version}
              </Link>
            </TableCell>
            <TableCell>
              <StatusBadge status={version.status} />
            </TableCell>
            <TableCell>{version.items}</TableCell>
            <TableCell>
              <ReviewProgress version={version} />
            </TableCell>
            <TableCell>
              <code title={version.md5} className="text-xs">
                {shortMd5(version.md5)}
              </code>
            </TableCell>
            <TableCell>{version.corpus ?? ""}</TableCell>
            <TableCell>{version.owner}</TableCell>
            <TableCell className="whitespace-nowrap">
              {formatDate(version.updated_at, i18n.language)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function SetsPage() {
  const { t } = useTranslation();
  const catalog = useCatalog();
  return (
    <>
      <PageHeader title={t("pages.sets.title")}>
        <p className="max-w-prose text-muted-foreground">{t("sets.intro")}</p>
      </PageHeader>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        <Section title={t("sets.catalog.title")} description={t("sets.catalog.description")}>
          <CatalogTable />
        </Section>
        <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          <NewSetSection entries={catalog.data ?? []} />
          <ImportSection />
        </div>
      </div>
    </>
  );
}

export { SetsPage as Component };
