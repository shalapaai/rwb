import { useEffect, useState, type ComponentType } from "react";
import { api, type Role } from "./api";
import Logist from "./pages/Logist";
import Model from "./pages/Model";
import Pvz from "./pages/Pvz";
import PvzAnalytics from "./pages/PvzAnalytics";
import Scenarios from "./pages/Scenarios";
import SellerAnalytics from "./pages/SellerAnalytics";

type Screen = { id: string; title: string; Component: ComponentType };

/** Роли и их экраны. Новый экран — новая страница в src/pages и строка здесь. */
const ROLES: { id: Role; title: string; screens: Screen[] }[] = [
  {
    id: "pvz_operator",
    title: "Оператор ПВЗ",
    screens: [
      { id: "pvz", title: "Полка и задания", Component: Pvz },
      { id: "pvz-analytics", title: "Аналитика ПВЗ", Component: PvzAnalytics },
    ],
  },
  {
    id: "seller",
    title: "Продавец",
    screens: [{ id: "seller-analytics", title: "Аналитика продавца", Component: SellerAnalytics }],
  },
  {
    id: "logist",
    title: "Логист",
    screens: [
      { id: "logist", title: "Граф и план", Component: Logist },
      { id: "model", title: "Модель", Component: Model },
    ],
  },
  {
    id: "admin",
    title: "Сценарии",
    screens: [{ id: "scenarios", title: "Сценарии", Component: Scenarios }],
  },
];

type Health = { status: string; db: string };

export default function App() {
  const [role, setRole] = useState<Role>("pvz_operator");
  const [screenId, setScreenId] = useState("pvz");
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Health>("/health", role)
      .then((h) => {
        setHealth(h);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [role]);

  const current = ROLES.find((r) => r.id === role)!;
  const screen = current.screens.find((s) => s.id === screenId) ?? current.screens[0];

  return (
    <div className="layout">
      <header>
        <strong>Mesh ПВЗ</strong>
        <nav>
          {ROLES.map((r) => (
            <button
              key={r.id}
              className={r.id === role ? "active" : ""}
              onClick={() => {
                setRole(r.id);
                setScreenId(r.screens[0].id);
              }}
            >
              {r.title}
            </button>
          ))}
        </nav>
        <span className="status">
          API: {error ? "недоступен" : health ? `${health.status}, БД ${health.db}` : "…"}
        </span>
      </header>
      {current.screens.length > 1 && (
        <nav className="tabs">
          {current.screens.map((s) => (
            <button
              key={s.id}
              className={s.id === screen.id ? "active" : ""}
              onClick={() => setScreenId(s.id)}
            >
              {s.title}
            </button>
          ))}
        </nav>
      )}
      <main>
        <screen.Component />
      </main>
    </div>
  );
}
