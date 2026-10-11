import { currentUser } from "../store";
import type { Owner } from "../types";

export const isMine = (owner: Owner) => owner.id === currentUser()?.id;

/** Whose session this is; shown only when it isn't the viewer's own (admins). */
export function OwnerLabel({ owner, after = "" }: { owner: Owner; after?: string }) {
  if (isMine(owner)) return null;
  return (
    <>
      <span class="owner" data-testid="owner" title="Owner">
        <span class="visually-hidden">Owner: </span>
        {owner.display_name}
      </span>
      {after}
    </>
  );
}
