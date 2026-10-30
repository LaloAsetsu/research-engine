import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useJobLog } from "./api";
import { ProblemAlert } from "./ProblemAlert";
import { Section } from "./Section";

/**
 * The log of a job, read from where the last read stopped. It is a `log` region (not announced: the
 * status line is) a keyboard can scroll; it follows the end while the reader is at the end.
 */
export function LogTail({ jobId, settled }: { jobId: string; settled: boolean | undefined }) {
  const { t } = useTranslation(undefined, { keyPrefix: "benchmarks.log" });
  const log = useJobLog(jobId, settled);
  const box = useRef<HTMLPreElement>(null);
  const follow = useRef(true);
  const text = log.data?.text ?? "";

  useEffect(() => {
    const element = box.current;
    if (element && follow.current) element.scrollTop = element.scrollHeight;
  }, [text]);

  return (
    <Section title={t("title")} description={t("description")} id="log">
      {log.isError ? <ProblemAlert error={log.error} onRetry={() => void log.refetch()} /> : null}
      <pre
        ref={box}
        role="log"
        // The status line above says how far the job is; the log would speak every 2 s.
        aria-live="off"
        aria-label={t("label")}
        // The box scrolls, so a keyboard must be able to reach it.
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        onScroll={(event) => {
          const element = event.currentTarget;
          follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
        }}
        className="max-h-80 min-h-24 overflow-auto rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {text === "" ? (log.isPending ? t("loading") : t("empty")) : text}
      </pre>
    </Section>
  );
}
