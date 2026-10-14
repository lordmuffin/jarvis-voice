import { Text } from "../ds/components";
import { audioRetentionDays } from "../store";

/** How long audio is kept, from the server's own setting. Says nothing while unknown or off. */
export function RetentionNote() {
  const days = audioRetentionDays.value;
  if (!days || days < 1) return null;
  return (
    <Text size="meta" tone="muted">
      Audio is kept for {days} {days === 1 ? "day" : "days"}. The transcript and note are kept.
    </Text>
  );
}
