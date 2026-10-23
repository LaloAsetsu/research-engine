import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { Badge } from "@/shared/ui/badge";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui/table";
import { ReviewBadge } from "./ReviewBadge";
import { itemPath, type ItemPage } from "./types";

/** The items of one page: id, form, language, split, question, tags and who decided what. */
export function ItemsTable({ page }: { page: ItemPage }) {
  const { t } = useTranslation();
  const { name, version } = page.version;
  const first = page.total === 0 ? 0 : page.offset + 1;
  const last = page.offset + page.items.length;
  return (
    <Table scrollLabel={t("sets.items.scroll")}>
      <TableCaption>{t("sets.items.caption", { first, last, total: page.total })}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">{t("sets.items.id")}</TableHead>
          <TableHead scope="col">{t("sets.items.form")}</TableHead>
          <TableHead scope="col">{t("sets.items.content")}</TableHead>
          <TableHead scope="col">{t("sets.items.lang")}</TableHead>
          <TableHead scope="col">{t("sets.items.split")}</TableHead>
          <TableHead scope="col">{t("sets.items.question")}</TableHead>
          <TableHead scope="col">{t("sets.items.tags")}</TableHead>
          <TableHead scope="col">{t("sets.items.review")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {page.items.map((item) => (
          <TableRow key={item.id}>
            <TableCell className="whitespace-nowrap">
              <Link
                to={itemPath(name, version, item.id)}
                className="font-medium underline-offset-4 hover:underline focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {item.id}
              </Link>
              {item.mirror_of ? (
                <div className="text-xs text-muted-foreground">
                  {t("sets.items.mirrorOf", { id: item.mirror_of })}
                </div>
              ) : null}
            </TableCell>
            <TableCell className="whitespace-nowrap">{item.form}</TableCell>
            <TableCell>{item.content ?? ""}</TableCell>
            <TableCell>{item.lang}</TableCell>
            <TableCell>{item.split ?? ""}</TableCell>
            <TableCell className="max-w-sm min-w-64 whitespace-normal">{item.question}</TableCell>
            <TableCell>
              <div className="flex flex-wrap gap-1">
                {Object.entries(item.tags).map(([key, value]) => (
                  <Badge key={key} variant="outline">
                    {key}={value}
                  </Badge>
                ))}
              </div>
            </TableCell>
            <TableCell>
              <div className="grid gap-1">
                <ReviewBadge state={item.review} />
                {item.accepted_by.length > 0 ? (
                  <span className="text-xs text-muted-foreground">
                    {t("sets.items.acceptedBy", { names: item.accepted_by.join(", ") })}
                  </span>
                ) : null}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
