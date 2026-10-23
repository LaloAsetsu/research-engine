import { useTranslation } from "react-i18next";
import { useCircuitStore } from "./store";

/**
 * One short sentence for screen readers when a reading or a result arrives. The tables and lists
 * are not live regions: announcing 64 state rows or 256 matrix cells would drown the sentence.
 * Refusals and missing values are announced by their own alerts. The element is always on the
 * page, because a live region is announced reliably only when it exists before its text changes.
 */
export function LiveStatus() {
  const { t } = useTranslation();
  const reading = useCircuitStore((state) => state.reading);
  const outcome = useCircuitStore((state) => state.outcome);
  let message = "";
  if (outcome) {
    const { result } = outcome;
    if (result.status === "ok") {
      if (outcome.command === "run") message = t("circuits.result.live.run");
      else if (result.equalToTarget === null) message = t("circuits.result.live.check");
      else
        message = t(
          result.equalToTarget ? "circuits.result.live.equal" : "circuits.result.live.notEqual",
        );
    }
  } else if (reading) {
    message = t("circuits.reading.live", {
      status: t(`circuits.reading.liveStatus.${reading.status}`),
      found: reading.circuits.length,
    });
  }
  return (
    <p role="status" className="sr-only" data-testid="live-status">
      {message}
    </p>
  );
}
