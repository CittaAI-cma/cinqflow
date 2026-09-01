/**
 * Control Operations, and the drawer that overlays it.
 *
 * The parallel slot lives HERE rather than at the app root: `(.)` intercepts a
 * segment at the same level as the folder holding the marker, so the slot has
 * to sit beside the `batch` segment it intercepts. A root-level slot reaching
 * three segments down is not a shape the router supports — it throws
 * `initialTree is not iterable` on the first soft navigation.
 *
 * The consequence is the correct behaviour, not a compromise: clicking a run
 * from the list overlays the drawer, and arriving at the same URL from
 * anywhere else — a citation chip in an answer, a pasted link, a bookmark —
 * renders the full page. A shared address must not depend on the route you
 * came from.
 */
export default function ControlLayout({
  children,
  drawer,
}: {
  children: React.ReactNode;
  drawer: React.ReactNode;
}) {
  return (
    <>
      {children}
      {drawer}
    </>
  );
}
