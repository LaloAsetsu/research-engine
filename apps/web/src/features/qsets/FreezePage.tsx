import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { PageHeader } from "@/shared/layout/PageHeader";
import { Alert, AlertDescription, AlertTitle } from "@/shared/ui/alert";
import { Button } from "@/shared/ui/button";
import { Skeleton } from "@/shared/ui/skeleton";
import { useFreeze, useFreezePlan, useMe, useVersionPage } from "./api";
import { conflictKind } from "./problems";
import { ProblemView } from "./ProblemView";
import { StatusBadge } from "./ReviewBadge";
import { canEditDraft } from "./rules";
import { Section } from "./Section";
import { itemPath, versionPath, type FreezePlan } from "./types";

type Group =
  "accepted" | "edited" | "rejected" | "unreviewed" | "needs_second" | "stale" | "unknown";

const GROUPS: readonly Group[] = [
  "accepted",
  "edited",
  "rejected",
  "needs_second",
  "unreviewed",
  "stale",
  "unknown",
];

function IdList({
  name,
  version,
  ids,
  linked,
}: {
  name: string;
  version: string;
  ids: readonly string[];
  linked: boolean;
}) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1">
      {ids.map((id) => (
        <li key={id}>
          {linked ? (
            <Link
              className="underline underline-offset-4 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
              to={itemPath(name, version, id)}
            >
              {id}
            </Link>
          ) : (
            <span>{id}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function PlanView({ plan, name, version }: { plan: FreezePlan; name: string; version: string }) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-4">
      <p className="text-sm">
        {t("sets.freeze.counts", { before: plan.items_before, after: plan.items_after })}
      </p>
      <dl className="grid gap-4">
        {GROUPS.map((group) => {
          const ids = plan[group];
          return (
            <div key={group} className="grid gap-1" data-group={group}>
              <dt className="text-sm font-medium">
                {t(`sets.freeze.group.${group}`)} ({ids.length})
              </dt>
              <dd className="grid gap-1 text-sm">
                <span className="text-muted-foreground">{t(`sets.freeze.groupHelp.${group}`)}</span>
                {ids.length > 0 ? (
                  <IdList name={name} version={version} ids={ids} linked={group !== "unknown"} />
                ) : (
                  <span className="text-muted-foreground">{t("sets.freeze.noneInGroup")}</span>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
      {plan.stale.length > 0 ? (
        <Alert>
          <AlertTitle>{t("sets.freeze.staleTitle")}</AlertTitle>
          <AlertDescription>
            <p>{t("sets.freeze.staleBody")}</p>
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}

/** What freezing would do, and the freeze itself. The freeze names the plan that was read. */
export function FreezePage() {
  const { t } = useTranslation();
  const { name = "", version = "" } = useParams();
  const page = useVersionPage(name, version, {}, 0);
  const me = useMe();
  const [confirming, setConfirming] = useState(false);
  const title = t("sets.freeze.title", { name, version });

  const current = page.data?.version;
  const mayFreeze = current !== undefined && canEditDraft(me.data, current);
  const plan = useFreezePlan(name, version, current?.status === "draft");
  const freeze = useFreeze({ name, version });

  if (page.isPending) {
    return (
      <>
        <PageHeader title={title} />
        <div role="status" aria-label={t("sets.loading")}>
          <Skeleton className="h-32 w-full" />
        </div>
      </>
    );
  }
  if (page.isError) {
    return (
      <>
        <PageHeader title={title} />
        <ProblemView error={page.error} />
      </>
    );
  }
  const back = (
    <Link
      to={versionPath(name, version)}
      className="text-sm underline underline-offset-4 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      {t("sets.item.back", { name, version })}
    </Link>
  );

  if (freeze.isSuccess) {
    const done = freeze.data;
    return (
      <>
        <PageHeader title={title}>{back}</PageHeader>
        <Alert role="status">
          <AlertTitle>{t("sets.freeze.done", { name, version })}</AlertTitle>
          <AlertDescription>
            <p>
              {t("sets.freeze.doneMd5")} <code data-testid="frozen-md5">{done?.version.md5}</code>
            </p>
            <p>
              {t("sets.freeze.doneCounts", {
                before: done?.plan.items_before ?? 0,
                after: done?.plan.items_after ?? 0,
              })}
            </p>
          </AlertDescription>
        </Alert>
      </>
    );
  }

  if (current?.status === "frozen") {
    return (
      <>
        <PageHeader title={title}>
          <div className="flex items-center gap-2">
            <StatusBadge status="frozen" />
            {back}
          </div>
        </PageHeader>
        <Alert role="status">
          <AlertTitle>{t("sets.freeze.alreadyTitle")}</AlertTitle>
          <AlertDescription>
            <p>
              {t("sets.freeze.alreadyBody")} <code>{current.md5}</code>
            </p>
          </AlertDescription>
        </Alert>
      </>
    );
  }

  const stale = conflictKind(freeze.error) !== null;

  return (
    <>
      <PageHeader title={title}>
        <div className="flex flex-wrap items-center gap-2">
          {current ? <StatusBadge status={current.status} /> : null}
          {back}
        </div>
      </PageHeader>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        {!mayFreeze ? (
          <Alert>
            <AlertTitle>{t("sets.freeze.notYoursTitle")}</AlertTitle>
            <AlertDescription>
              <p>{t("sets.freeze.notYours", { owner: current?.owner ?? "" })}</p>
            </AlertDescription>
          </Alert>
        ) : null}
        <Section title={t("sets.freeze.planTitle")} description={t("sets.freeze.planDescription")}>
          {plan.isPending ? (
            <div role="status" aria-label={t("sets.loading")}>
              <Skeleton className="h-32 w-full" />
            </div>
          ) : plan.isError ? (
            <div className="grid gap-3">
              <ProblemView error={plan.error} />
              <div>
                <Button variant="outline" onClick={() => void plan.refetch()}>
                  {t("sets.tryAgain")}
                </Button>
              </div>
            </div>
          ) : (
            <>
              <PlanView plan={plan.data.plan} name={name} version={version} />
              {stale ? (
                <Alert variant="destructive">
                  <AlertTitle>{t("sets.freeze.changedTitle")}</AlertTitle>
                  <AlertDescription>
                    <p>{t("sets.freeze.changedBody")}</p>
                    <div className="mt-3">
                      <Button
                        variant="outline"
                        onClick={() => {
                          freeze.reset();
                          setConfirming(false);
                          void plan.refetch();
                        }}
                      >
                        {t("sets.freeze.readAgain")}
                      </Button>
                    </div>
                  </AlertDescription>
                </Alert>
              ) : freeze.isError ? (
                <ProblemView error={freeze.error} />
              ) : null}
              {mayFreeze && !stale && !confirming ? (
                <div>
                  <Button onClick={() => setConfirming(true)}>{t("sets.freeze.start")}</Button>
                </div>
              ) : null}
              {mayFreeze && !stale && confirming ? (
                <Alert>
                  <AlertTitle>{t("sets.freeze.confirmTitle", { name, version })}</AlertTitle>
                  <AlertDescription>
                    <p>
                      {t("sets.freeze.confirmBody", {
                        before: plan.data.plan.items_before,
                        after: plan.data.plan.items_after,
                      })}
                    </p>
                    <div className="mt-3 flex gap-2">
                      <Button
                        disabled={freeze.isPending}
                        onClick={() => freeze.mutate({ planEtag: plan.data.plan.etag })}
                      >
                        {freeze.isPending ? t("sets.freeze.freezing") : t("sets.freeze.confirm")}
                      </Button>
                      <Button variant="outline" onClick={() => setConfirming(false)}>
                        {t("sets.freeze.cancel")}
                      </Button>
                    </div>
                  </AlertDescription>
                </Alert>
              ) : null}
            </>
          )}
        </Section>
      </div>
    </>
  );
}

export { FreezePage as Component };
