/** Блок 5. Все допущения модели с основанием и источником — из /model/params. */

import { withUnit } from "./format";
import type { Parameter } from "./types";

export default function Assumptions({ params }: { params: Parameter[] }) {
  return (
    <>
      <p className="model-synthetic">Синтетические допущения, не тарифы RWB (п. 7.5 положения)</p>
      <div className="model-scroll">
        <table className="model-table">
          <thead>
            <tr>
              <th>Допущение</th>
              <th className="num">Значение</th>
              <th>Основание</th>
              <th>Источник</th>
            </tr>
          </thead>
          <tbody>
            {params.map((p) => (
              <tr key={p.id}>
                <td>
                  {p.label}
                  <code>{p.id}</code>
                </td>
                <td className="num">{withUnit(p.value, p.unit)}</td>
                <td>{p.basis}</td>
                <td>
                  {p.source?.startsWith("http") ? (
                    <a href={p.source} target="_blank" rel="noreferrer">
                      ссылка
                    </a>
                  ) : (
                    p.source
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
