import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { PageHeader } from "@/shared/layout/PageHeader";
import { useCircuitCalls } from "./api";
import { CheckPanel } from "./CheckPanel";
import { ConventionsCard } from "./ConventionsCard";
import { createFlows } from "./flows";
import { InputPanel } from "./InputPanel";
import { OutcomeView } from "./OutcomeView";
import { CurrentProblem } from "./ProblemView";
import { ReadingView } from "./ReadingView";
import { RunPanel } from "./RunPanel";

export function CircuitsPage() {
  const { t } = useTranslation();
  const { parse, run, check } = useCircuitCalls();
  const flows = useMemo(() => createFlows({ parse, run, check }), [parse, run, check]);
  return (
    <>
      <PageHeader title={t("pages.circuits.title")}>
        <p className="max-w-prose text-muted-foreground">{t("circuits.intro")}</p>
      </PageHeader>
      <div className="grid gap-6">
        <InputPanel flows={flows} />
        <CurrentProblem />
        <ReadingView />
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <RunPanel flows={flows} />
          <CheckPanel flows={flows} />
        </div>
        <OutcomeView />
        <ConventionsCard />
      </div>
    </>
  );
}

export { CircuitsPage as Component };
