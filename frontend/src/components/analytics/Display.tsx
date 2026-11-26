import type { ReactNode } from "react";

/** Карточки показателей, таблицы и состояния загрузки для обоих экранов. */
export function Metrics({ values }: { values: [string, ReactNode][] }) {
  return (
    <dl className="analytics-metrics">
      {values.map(([label, value]) => (
        <div className="analytics-card" key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DataTable({
  label, columns, children,
}: { label: string; columns: string[]; children: ReactNode }) {
  return (
    <div className="analytics-scroll" role="region" aria-label={label} tabIndex={0}>
      <table aria-label={label}>
        <thead>
          <tr>{columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function LoadError({ message, retry }: { message: string; retry: () => void }) {
  return (
    <div className="analytics-error" role="alert">
      <span>{message}</span>
      <button type="button" onClick={retry}>Повторить</button>
    </div>
  );
}
