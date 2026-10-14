import { Text, VisuallyHidden } from "../ds/components";
import { currentUser } from "../store";
import type { Owner } from "../types";

export const isMine = (owner: Owner) => owner.id === currentUser()?.id;

/** Whose session this is; shown only when it isn't the viewer's own (admins). */
export function OwnerLabel({ owner, after = "" }: { owner: Owner; after?: string }) {
  if (isMine(owner)) return null;
  return (
    <>
      <Text data-testid="owner" title="Owner" strong>
        <VisuallyHidden>Owner: </VisuallyHidden>
        {owner.display_name}
      </Text>
      {after}
    </>
  );
}
