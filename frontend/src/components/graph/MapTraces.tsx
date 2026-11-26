/**
 * Рисует рёбра и выделенные узлы поверх маршрутов WB.
 * Геометрия использует те же экранные координаты, что и основная карта.
 */

import { traceStyles, type MapTrace } from "./plan-graph";

export type Point = { x: number; y: number };

export default function MapTraces({
  traces,
  positions,
  mapId,
}: {
  traces: MapTrace[];
  positions: Map<string, Point>;
  mapId: string;
}) {
  const kinds = new Set(traces.map((trace) => trace.kind));
  // Выноски размещаем в свободном поле, чтобы сохранить читаемость подписей ПВЗ и ETA.
  const calloutRows = new Map(
    traces.filter((trace) => trace.kind === "detour" && trace.nodes.length === 1)
      .map((trace, index) => [trace.id, 26 + index * 36]),
  );
  return (
    <>
      <defs>
        {[...kinds].map((kind) => (
          <marker
            key={kind}
            id={`${mapId}-trace-${kind}`}
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="9"
            markerHeight="9"
            markerUnits="userSpaceOnUse"
            orient="auto"
          >
            <path d="M 1 1 L 9 5 L 1 9" fill="none" stroke={traceStyles[kind].color} strokeWidth="2" />
          </marker>
        ))}
      </defs>
      {traces.map((trace) => {
        const points = trace.nodes.map((node) => positions.get(node));
        if (!points.every((point) => point !== undefined) || !points.length) return null;
        const { kind, selected, muted, label } = trace;
        const color = traceStyles[kind].color;
        const markerEnd = `url(#${mapId}-trace-${kind})`;
        const start = points[0];
        const destination = points[points.length - 1];
        const lastStop = points[points.length - 2];
        const offset = kind === "movement" ? -6 : kind === "van" ? 6 : 0;
        const curveHeight = Math.min(40, Math.abs(destination.x - start.x) / 3 + 10);
        const calloutRow = calloutRows.get(trace.id);
        const arrows = kind === "detour"
          ? points.slice(1).map((to, index) => ({ from: points[index], to }))
          : lastStop ? [{ from: lastStop, to: destination }] : [];

        return (
          <g
            key={trace.id}
            className={`city-map-trace city-map-trace--${kind}`}
            data-trace={kind}
            data-trace-id={trace.id}
            data-selected={selected || undefined}
            opacity={muted ? 0.19 : undefined}
            stroke={color}
          >
            <title>{label}</title>
            {!lastStop ? (
              <g transform={`translate(${start.x} ${start.y})`}>
                <circle className="city-map-trace-pin" r={selected ? 15 : 12} />
                {calloutRow !== undefined && (
                  <g transform={`translate(${26 - start.x} ${calloutRow - start.y})`} className="city-map-callout">
                    <line x1="300" y1="14" x2={start.x - 26} y2={start.y - calloutRow} />
                    <rect width="300" height="28" rx="6" />
                    <text x="10" y="18">{label}</text>
                  </g>
                )}
              </g>
            ) : kind === "pair" ? (
              <path
                d={`M ${start.x} ${start.y - 8} Q ${(start.x + destination.x) / 2} ${Math.min(start.y, destination.y) - curveHeight} ${destination.x} ${destination.y - 9}`}
                markerEnd={markerEnd}
              />
            ) : (
              <g transform={`translate(0 ${offset})`}>
                <polyline points={points.map(({ x, y }) => `${x},${y}`).join(" ")} />
                {arrows.map(({ from, to }, index) => (
                  <line
                    key={index}
                    x1={from.x}
                    y1={from.y}
                    x2={from.x + (to.x - from.x) * 0.67}
                    y2={from.y + (to.y - from.y) * 0.67}
                    stroke="none"
                    markerEnd={markerEnd}
                  />
                ))}
              </g>
            )}
          </g>
        );
      })}
    </>
  );
}
