import { BADGES } from "./catalog";

/** Official, Verified publisher or Community: the label the signed catalog gives a skill. */
export function Badge({ badge }: { badge: string }) {
  const known = BADGES[badge] ? badge : "community";
  const { label, hint } = BADGES[known];
  return (
    <span className={`pill badge-${known}`} title={hint}>
      {label}
    </span>
  );
}
