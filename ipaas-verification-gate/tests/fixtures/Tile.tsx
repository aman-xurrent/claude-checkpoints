import { Button } from "./Button";

type Props = { canEdit?: boolean };

export function Tile({ canEdit, solution }: Props & { solution: any }) {
  const name = solution.name ?? "untitled";
  if (!solution) return null;
  return (
    <div className="group/icon" key={solution.id} onClick={() => open(solution)}>
      {canEdit && <Button className="hidden group-hover/icon:flex">Edit</Button>}
      <span>{name}</span>
    </div>
  );
}
const props = { icon_url: solution.icon_url, name };
