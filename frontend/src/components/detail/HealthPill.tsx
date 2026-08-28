import { HEALTH_COLOR } from "../../cy/palette";

export function HealthPill({ health }: { health: string }) {
  return (
    <span className="pill" style={{ background: HEALTH_COLOR[health as keyof typeof HEALTH_COLOR] ?? "#8b949e" }}>
      {health}
    </span>
  );
}