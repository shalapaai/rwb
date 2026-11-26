import { useId, useState } from "react";
import MapTraces, { type Point } from "./MapTraces";
import { traceStyles, type City, type MapTrace, type TraceKind } from "./plan-graph";
import "./city-map.css";

const WIDTH = 1000;
const HEIGHT = 490;
const PADDING = 76;
const ROUTE_COLORS = ["#713bd2", "#087481", "#ab4f16"];

/** Масштаб одинаков по обеим осям, чтобы сохранять пропорции города. */
function projectNodes(nodes: City["nodes"]) {
  const xs = nodes.map((node) => node.x_km);
  const ys = nodes.map((node) => node.y_km);
  const minX = Math.min(...xs);
  const maxY = Math.max(...ys);
  const spanX = Math.max(...xs) - minX;
  const spanY = maxY - Math.min(...ys);
  const scale = Math.min(
    (WIDTH - 2 * PADDING) / Math.max(spanX, 1),
    (HEIGHT - 2 * PADDING) / Math.max(spanY, 1),
  );
  const offsetX = (WIDTH - spanX * scale) / 2;
  const offsetY = (HEIGHT - spanY * scale) / 2;
  const positions = new Map<string, Point>(
    nodes.map((node) => [
      node.id,
      { x: offsetX + (node.x_km - minX) * scale, y: offsetY + (maxY - node.y_km) * scale },
    ]),
  );
  return { positions, scale };
}

/** Базовые рейсы служат подложкой для выбранного шага ночного плана. */
export default function CityMap({
  city,
  traces,
  legend,
  onSelectPvz,
  selectedPvz,
}: {
  city: City;
  traces: MapTrace[];
  legend: TraceKind[];
  onSelectPvz?: (id: string) => void;
  selectedPvz?: string | null;
}) {
  const id = useId();
  const [showRoutes, setShowRoutes] = useState(true);
  const [showStock, setShowStock] = useState(true);
  const [showEta, setShowEta] = useState(true);

  if (!city.nodes.length) return <p role="status">В демо-городе пока нет узлов.</p>;

  const { positions, scale } = projectNodes(city.nodes);
  const stockByNode = new Map<string, number>();
  for (const { node_id, qty } of city.stock) {
    stockByNode.set(node_id, (stockByNode.get(node_id) ?? 0) + qty);
  }
  const routes = city.routes.map((route, index) => ({
    ...route,
    color: ROUTE_COLORS[index % ROUTE_COLORS.length],
    markerId: `${id}-arrow-${index}`,
  }));
  const arrivals = new Map<string, { eta: number; color: string }[]>();
  for (const route of routes) {
    for (const stop of route.stops) {
      const labels = arrivals.get(stop.node_id) ?? [];
      labels.push({ eta: stop.eta_min, color: route.color });
      arrivals.set(stop.node_id, labels);
    }
  }

  return (
    <div className="city-map-card">
      <div className="city-map-toolbar" role="group" aria-label="Слои схемы">
        <span>Слои схемы</span>
        <label>
          <input
            type="checkbox"
            checked={showRoutes}
            onChange={(event) => setShowRoutes(event.target.checked)}
          />
          Маршруты
        </label>
        <label>
          <input
            type="checkbox"
            checked={showStock}
            onChange={(event) => setShowStock(event.target.checked)}
          />
          Остатки
        </label>
        <label>
          <input
            type="checkbox"
            checked={showRoutes && showEta}
            disabled={!showRoutes}
            onChange={(event) => setShowEta(event.target.checked)}
          />
          Время прибытия
        </label>
      </div>
      <div className="city-map-scroll" tabIndex={0} role="region" aria-label="Область схемы города">
        <svg
          className="city-map-svg"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role={onSelectPvz ? "group" : "img"}
          aria-labelledby={`${id}-title`}
          aria-describedby={`${id}-description`}
        >
          <title id={id + "-title"}>Схема ПВЗ и маршрутов газелей WB</title>
          <desc id={id + "-description"}>
            Стрелки показывают порядок остановок. Время указано в минутах после выезда из хаба.
          </desc>
          <defs>
            {routes.map(({ markerId, color }) => (
              <marker
                key={markerId}
                id={markerId}
                viewBox="0 0 11 10"
                refX="9"
                refY="5"
                markerWidth="11"
                markerHeight="10"
                markerUnits="userSpaceOnUse"
                orient="auto"
              >
                <path d="M 1 1 L 9 5 L 1 9" fill="none" stroke={color} strokeWidth="2.6" />
              </marker>
            ))}
          </defs>
          {showRoutes && routes.map((route) => (
            <g key={route.id} data-route-id={route.id} opacity={traces.length ? 0.38 : undefined}>
              <title>Газель {route.id}</title>
              {route.stops.map((stop, index) => {
                const from = positions.get(route.stops[index - 1]?.node_id ?? route.depot_id);
                const to = positions.get(stop.node_id);
                if (!from || !to || from === to) return null;
                const midX = from.x + (to.x - from.x) * 0.6;
                const midY = from.y + (to.y - from.y) * 0.6;
                return (
                  <polyline
                    key={index}
                    points={`${from.x},${from.y} ${midX},${midY} ${to.x},${to.y}`}
                    fill="none"
                    stroke={route.color}
                    strokeWidth="2.6"
                    markerMid={`url(#${route.markerId})`}
                  />
                );
              })}
            </g>
          ))}
          <MapTraces traces={traces} mapId={id} positions={positions} />
          {city.nodes.map((node) => {
            const { x, y } = positions.get(node.id)!;
            const qty = stockByNode.get(node.id) ?? 0;
            const isHub = node.kind === "hub";
            const label = isHub ? "Хаб" : node.id;
            const select = onSelectPvz && !isHub ? () => onSelectPvz(node.id) : undefined;
            return (
              <g
                key={node.id}
                transform={`translate(${x} ${y})`}
                data-node-kind={node.kind}
                role={select ? "button" : undefined}
                tabIndex={select ? 0 : undefined}
                aria-label={select ? label : undefined}
                aria-pressed={select ? selectedPvz === node.id : undefined}
                onClick={select}
                onKeyDown={select && ((event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    select();
                  }
                })}
              >
                <title>{!isHub && showStock ? `${label}, остаток ${qty} шт.` : label}</title>
                {select && <circle r="15" fill="transparent" />}
                {showRoutes && showEta && arrivals.get(node.id)?.map(({ eta, color }, index) => {
                  const width = 32 + String(eta).length * 7;
                  return (
                    <g
                      key={index}
                      transform={`translate(-19 ${-24 - 21 * index})`}
                      className="city-map-eta"
                    >
                      <rect x={-width / 2} y="-9" width={width} height="18" rx="6" />
                      <text fill={color} textAnchor="middle" dominantBaseline="central">
                        {eta} мин
                      </text>
                    </g>
                  );
                })}
                {isHub ? (
                  <rect className="city-map-hub" x="-11" y="-11" width="22" height="22" rx="3" />
                ) : (
                  <circle className="city-map-pvz" r="7" />
                )}
                <text className="city-map-node-label" textAnchor="middle" y="27">
                  {label}
                </text>
                {!isHub && showStock && (
                  <g className="city-map-stock" data-node-id={node.id}>
                    <rect x="-16" y="33" width="32" height="19" rx="6" />
                    <text textAnchor="middle" dominantBaseline="central" y="42.5">
                      {qty}
                    </text>
                  </g>
                )}
              </g>
            );
          })}
          <g className="city-map-scale" transform={`translate(28 ${HEIGHT - 24})`} aria-hidden="true">
            <path d={`M 0 -5 V 0 H ${2 * scale} V -5`} fill="none" />
            <text x={scale} y="-9" textAnchor="middle">2 км</text>
          </g>
        </svg>
      </div>
      <div className="city-map-footer">
        <ul className="city-map-legend" aria-label="Условные обозначения">
          <li><span className="city-map-legend-pvz" /> ПВЗ</li>
          <li><span className="city-map-legend-hub" /> Хаб</li>
          {routes.map(({ id: routeId, color }) => (
            <li key={routeId}><span className="city-map-legend-route" style={{ color }} /> {routeId}</li>
          ))}
        </ul>
        {legend.length > 0 && (
          <ul className="city-map-legend city-map-trace-legend" aria-label="Дополнительные слои">
            {legend.map((kind) => (
              <li key={kind}>
                <span
                  className={`city-map-legend-trace city-map-legend-trace--${kind}`}
                  style={{ color: traceStyles[kind].color }}
                />
                {traceStyles[kind].label}
              </li>
            ))}
          </ul>
        )}
        <p>Бейджи — исходные остатки в штуках. Время — от выезда из хаба.</p>
      </div>
    </div>
  );
}
